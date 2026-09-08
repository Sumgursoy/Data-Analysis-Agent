"""DuckDB sorgu motoru — agent'ın SQL tarafı.

Sandbox'ın DIŞINDA, backend sürecinde çalışır. Sebep: sandbox
`--network none` ile koşuyor, oradan veritabanına bağlanılamaz ve
bağlanılmamalı. SQL burada koşar, büyük sonuç parquet olarak /data'ya
düşer, agent onu sandbox'ta okur.

Bu ayrım agent'ı doğal olarak doğru desene itiyor:
    SQL ile daralt → Python ile derinleş
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp

from backend import config
from backend.ingest.catalog import Catalog

log = logging.getLogger(__name__)

# Sonuç bundan büyükse modele tablo basmak yerine parquet'e yazılır.
INLINE_SATIR_LIMITI = 200
OTOMATIK_LIMIT = 1000

# Salt-okuma sayılan ifade tipleri. Bunların dışında hiçbir şey çalışmaz.
IZINLI_IFADELER = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


class UnsafeQuery(Exception):
    """Sorgu salt-okuma değil veya ayrıştırılamadı."""


def assert_readonly(query: str, dialect: str = "duckdb") -> exp.Expression:
    """Sorguyu PARSE EDEREK salt-okuma olduğunu doğrular.

    Metinde "DROP" aramak güvenlik değildir:
      - `SELECT * FROM t WHERE not = 'DROP'`  → yanlış alarm
      - `sELeCt/**/1; drop table x`           → kaçar
    Tek gerçek yöntem sorguyu ayrıştırıp ifade tipine bakmaktır.
    """
    try:
        ifadeler = [i for i in sqlglot.parse(query, read=dialect) if i is not None]
    except Exception as e:
        raise UnsafeQuery(f"SQL ayrıştırılamadı: {e}") from e

    if not ifadeler:
        raise UnsafeQuery("Boş sorgu.")
    if len(ifadeler) > 1:
        raise UnsafeQuery(
            "Tek seferde tek sorgu çalıştırılabilir. Noktalı virgülle "
            "birden fazla ifade gönderme."
        )

    ifade = ifadeler[0]
    if not isinstance(ifade, IZINLI_IFADELER):
        raise UnsafeQuery(
            f"Sadece SELECT çalıştırılabilir (gelen: {type(ifade).__name__}). "
            "Veri değiştiren sorgular kapalı."
        )
    return ifade


def _limit_ekle(ifade: exp.Expression, limit: int) -> str:
    """Kullanıcı limit koymadıysa koy — kazara 10M satır çekilmesin."""
    if isinstance(ifade, exp.Select) and ifade.args.get("limit") is None:
        ifade = ifade.limit(limit)
    return ifade.sql(dialect="duckdb")


class QueryEngine:
    """Bir session'ın SQL bağlamı.

    Katalogtaki her veri seti bir VIEW olarak açılır; agent
    `SELECT ... FROM satis` yazabilir, dosya yolu bilmesine gerek kalmaz.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.data_dir: Path = config.session_dir(session_id) / "data"
        self._con: duckdb.DuckDBPyConnection | None = None
        self._kayitli: set[str] = set()

    def _baglanti(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            self._con = duckdb.connect(":memory:")
        return self._con

    def refresh_views(self) -> list[str]:
        """Katalogdaki parquet'ler için view'ları (yeniden) kurar."""
        con = self._baglanti()
        katalog = Catalog(self.session_id)
        acilan = []

        for ds in katalog.all():
            if ds.kind == "raw":
                continue  # ayrıştırılmamış dosya, SQL'e konu olamaz
            yol = self.data_dir / Path(ds.location).name
            if not yol.exists():
                continue
            try:
                con.execute(
                    f'CREATE OR REPLACE VIEW "{ds.name}" AS '
                    f"SELECT * FROM read_parquet(?)",
                    [str(yol)],
                )
            except duckdb.Error:
                # Parametreli view bazı sürümlerde desteklenmiyor — yolu göm.
                kacisli = str(yol).replace("'", "''")
                con.execute(
                    f'CREATE OR REPLACE VIEW "{ds.name}" AS '
                    f"SELECT * FROM read_parquet('{kacisli}')"
                )
            acilan.append(ds.name)

        self._kayitli = set(acilan)
        return acilan

    def run(self, query: str) -> tuple[pd.DataFrame, str]:
        """Sorguyu doğrulayıp çalıştırır. Döner: (sonuç, çalıştırılan sql)."""
        ifade = assert_readonly(query)
        self.refresh_views()  # arada yeni dataset eklenmiş olabilir
        sql = _limit_ekle(ifade, OTOMATIK_LIMIT)
        df = self._baglanti().execute(sql).fetchdf()
        return df, sql

    def tables(self) -> list[str]:
        return sorted(self._kayitli)

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None


_MOTORLAR: dict[str, QueryEngine] = {}


def get_engine(session_id: str) -> QueryEngine:
    motor = _MOTORLAR.get(session_id)
    if motor is None:
        motor = QueryEngine(session_id)
        motor.refresh_views()
        _MOTORLAR[session_id] = motor
    return motor


def drop_engine(session_id: str) -> None:
    motor = _MOTORLAR.pop(session_id, None)
    if motor is not None:
        motor.close()


def frame_to_text(df: pd.DataFrame, limit: int = 50) -> str:
    """Sonucu modele gösterilecek metne çevirir."""
    if df.empty:
        return "(sonuç boş — 0 satır)"

    with pd.option_context("display.max_columns", 30, "display.width", 200):
        govde = df.head(limit).to_string(index=False, max_colwidth=32)

    satir = f"{len(df):,} satır × {df.shape[1]} kolon".replace(",", ".")
    if len(df) > limit:
        return f"{satir} (ilk {limit} gösteriliyor)\n\n{govde}"
    return f"{satir}\n\n{govde}"


_GUVENSIZ_AD = re.compile(r"[^\w]+")


def sonuc_dosya_adi(query: str) -> str:
    """Sorgudan okunabilir bir parquet adı üretir."""
    ilk = _GUVENSIZ_AD.sub("_", query.strip().lower())[:40].strip("_")
    return f"sorgu_{ilk or 'sonuc'}"
