"""SQL veritabanı adaptörü.

Tasarım kararı: tablolar parquet'e İNDİRİLİR, kataloğa girer, sonrası
diğer kaynaklarla birebir aynıdır (İlke 1 — agent kaynak tipini bilmez).

Neden canlı bağlantı üzerinden sorgulatmıyoruz:
  - Sandbox ağsız; oradan DB'ye erişilemez, erişilmemeli.
  - Bağlantı dizesi (DSN) backend'de kalır, agent'a asla gösterilmez.
  - Parquet'e indirince profiling, örnekleme, ilişki tespiti bedava gelir.

Çok büyük tablolarda satır tavanı uygulanır ve bu şema kartında belirtilir.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from backend import config
from backend.ingest.adapters.base import Extracted, UnsupportedSource

log = logging.getLogger(__name__)

# Bağlantı dizesindeki parolayı loglara ve hata mesajlarına sızdırma.
_PAROLA = re.compile(r"(://[^:/@]+:)([^@]+)(@)")


def maskele(dsn: str) -> str:
    return _PAROLA.sub(r"\1***\3", dsn)


@dataclass
class TabloBilgisi:
    name: str
    schema: str | None
    row_count: int | None
    columns: list[str]

    @property
    def tam_ad(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


def baglan(dsn: str) -> Engine:
    try:
        motor = create_engine(dsn, pool_pre_ping=True)
        with motor.connect():
            pass
    except Exception as e:
        raise UnsupportedSource(
            f"Veritabanına bağlanılamadı ({maskele(dsn)}): {e}"
        ) from e
    return motor


def kesfet(motor: Engine, *, satir_say: bool = True) -> list[TabloBilgisi]:
    """Şema keşfi: hangi tablolar var, kaç satır, hangi kolonlar."""
    denetci = inspect(motor)
    bulunan: list[TabloBilgisi] = []

    for sema in denetci.get_schema_names() or [None]:
        # Sistem şemalarını atla — jüriye pg_catalog listelemenin anlamı yok.
        if sema and sema.lower() in {
            "information_schema", "pg_catalog", "pg_toast", "sys", "mysql",
            "performance_schema",
        }:
            continue

        try:
            tablolar = denetci.get_table_names(schema=sema)
            gorunumler = denetci.get_view_names(schema=sema)
        except Exception:
            log.debug("şema okunamadı: %s", sema)
            continue

        for ad in list(tablolar) + list(gorunumler):
            kolonlar = []
            try:
                kolonlar = [c["name"] for c in denetci.get_columns(ad, schema=sema)]
            except Exception:
                pass

            adet = None
            if satir_say:
                nitelikli = f'"{sema}"."{ad}"' if sema else f'"{ad}"'
                try:
                    with motor.connect() as baglanti:
                        adet = baglanti.execute(
                            text(f"SELECT count(*) FROM {nitelikli}")
                        ).scalar_one()
                except Exception:
                    adet = None  # sayamadıysak akışı bozma

            bulunan.append(
                TabloBilgisi(name=ad, schema=sema, row_count=adet, columns=kolonlar)
            )

    if not bulunan:
        raise UnsupportedSource("Veritabanında okunabilir tablo bulunamadı.")
    return bulunan


def oku(motor: Engine, tablo: TabloBilgisi, *, limit: int) -> Extracted:
    """Bir tabloyu pandas'a çeker (satır tavanıyla)."""
    nitelikli = f'"{tablo.schema}"."{tablo.name}"' if tablo.schema else f'"{tablo.name}"'
    sorgu = f"SELECT * FROM {nitelikli} LIMIT {int(limit)}"

    with motor.connect() as baglanti:
        df = pd.read_sql(text(sorgu), baglanti)

    notlar = [f"kaynak tablo: {tablo.tam_ad}"]
    if tablo.row_count is not None and tablo.row_count > limit:
        notlar.append(
            f"⚠ tabloda {tablo.row_count:,} satır var, ilk {limit:,} indirildi "
            "— sonuçlar KISMİ".replace(",", ".")
        )

    return Extracted(
        name=tablo.name,
        df=df,
        origin=f"{tablo.tam_ad} (SQL)",
        notes=notlar,
        kind="sql_table",
    )


def read_database(
    dsn: str, *, tablolar: list[str] | None = None, limit: int | None = None
) -> list[Extracted]:
    """Seçilen tabloları (veya hepsini) çeker."""
    limit = limit or config.MAX_SQL_INGEST_ROWS
    motor = baglan(dsn)
    try:
        hepsi = kesfet(motor)
        if tablolar:
            istenen = {t.lower() for t in tablolar}
            secilen = [
                t for t in hepsi
                if t.name.lower() in istenen or t.tam_ad.lower() in istenen
            ]
            if not secilen:
                mevcut = ", ".join(t.tam_ad for t in hepsi[:20])
                raise UnsupportedSource(
                    f"İstenen tablolar bulunamadı. Mevcut: {mevcut}"
                )
        else:
            secilen = [t for t in hepsi if t.row_count != 0]

        return [oku(motor, t, limit=limit) for t in secilen]
    finally:
        motor.dispose()
