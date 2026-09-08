"""Excel adaptörü — ingest'te en çok burada patlanır.

Kurumsal Excel dosyaları düz tablo değildir: ilk satırlarda logo/başlık
boşlukları olur, birden fazla sheet bulunur, altta "GENEL TOPLAM" satırı
durur, hücreler birleştirilmiştir. Bunlar çözülmezse şema kartı çöp olur
ve agent en baştan yanlış veriyle çalışır.

Üç heuristik:
  1. Her sheet ayrı dataset adayı; boş ve çok küçük olanlar elenir
  2. Başlık satırı ilk 20 satır içinde puanlanarak bulunur
  3. Alt toplam satırları tespit edilip veriden ayrılır
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from backend import config
from backend.ingest.adapters.base import Extracted, UnsupportedSource
from backend.ingest.normalize import slug

log = logging.getLogger(__name__)

MOTORLAR = {
    ".xlsx": "openpyxl", ".xlsm": "openpyxl",
    ".xls": "xlrd",
    ".xlsb": "pyxlsb",
    ".ods": "odf",
}

MIN_VERI_SATIRI = 2

_TOPLAM_KALIBI = re.compile(
    r"^\s*(genel\s+)?(ara\s+)?(toplam|topalm|total|sum|yekun|yekûn)\s*:?\s*$",
    re.IGNORECASE,
)
_SAYI_KALIBI = re.compile(r"^-?[\d\s.,()%₺$€]+$")


def _sayi_gibi(deger: object) -> bool:
    if deger is None or (isinstance(deger, float) and pd.isna(deger)):
        return False
    return bool(_SAYI_KALIBI.match(str(deger).strip())) and any(
        ch.isdigit() for ch in str(deger)
    )


def _basliK_puani(df: pd.DataFrame, r: int) -> float:
    """Bir satırın başlık satırı olma puanı.

    Başlık satırı: dolu, metinsel, değerleri birbirinden farklı —
    ve ALTINDAKİ satırlardan tip olarak ayrışıyor (altı sayısal olur).
    """
    satir = df.iloc[r]
    dolu = satir.dropna()
    genislik = max(len(satir), 1)

    doluluk = len(dolu) / genislik
    if doluluk < 0.5 or len(dolu) < 2:
        return -1.0

    metinsellik = sum(1 for v in dolu if not _sayi_gibi(v)) / len(dolu)
    benzersiz = len({str(v).strip().lower() for v in dolu}) / len(dolu)

    alt = df.iloc[r + 1 : r + 11]
    alt_sayisallik = 0.0
    if not alt.empty:
        hucreler = [v for _, s in alt.items() for v in s.dropna()]
        if hucreler:
            alt_sayisallik = sum(1 for v in hucreler if _sayi_gibi(v)) / len(hucreler)

    # Başlık metinsel, altı sayısal → aradaki fark en güçlü sinyal
    ayrisma = metinsellik - (1 - alt_sayisallik)

    return doluluk * 3 + metinsellik * 2 + benzersiz * 2 + ayrisma * 2


def find_header_row(df: pd.DataFrame) -> int:
    """Başlık satırının indeksi. Bulunamazsa 0."""
    tarama = min(config.HEADER_SCAN_ROWS, len(df))
    en_iyi, en_iyi_puan = 0, -1.0
    for r in range(tarama):
        puan = _basliK_puani(df, r)
        if puan > en_iyi_puan:
            en_iyi, en_iyi_puan = r, puan
    return en_iyi if en_iyi_puan > 0 else 0


def _toplam_satirlari(df: pd.DataFrame) -> pd.Series:
    """Alt toplam satırlarını işaretler (ilk dolu hücrede TOPLAM/GENEL vb.)."""
    if df.empty:
        return pd.Series(dtype=bool)

    def isaretli(satir: pd.Series) -> bool:
        dolu = satir.dropna()
        if dolu.empty:
            return False
        return bool(_TOPLAM_KALIBI.match(str(dolu.iloc[0])))

    return df.apply(isaretli, axis=1)


def read_excel(path: Path, name: str) -> list[Extracted]:
    uzanti = path.suffix.lower()
    motor = MOTORLAR.get(uzanti)
    if motor is None:
        raise UnsupportedSource(f"desteklenmeyen Excel uzantısı: {uzanti}")

    try:
        sheetler = pd.read_excel(
            path, sheet_name=None, header=None, dtype=str, engine=motor
        )
    except ImportError as e:
        raise UnsupportedSource(
            f"{uzanti} okumak için gereken paket kurulu değil ({motor}): {e}"
        ) from e

    cikti: list[Extracted] = []
    atlanan: list[str] = []

    for sheet_adi, ham in sheetler.items():
        if ham is None or ham.dropna(how="all").empty:
            atlanan.append(f"{sheet_adi} (boş)")
            continue

        notlar: list[str] = []

        baslik_r = find_header_row(ham)
        if baslik_r > 0:
            notlar.append(f"başlık {baslik_r + 1}. satırda bulundu, üstü atıldı")

        basliklar = ham.iloc[baslik_r]
        # Birleştirilmiş başlık hücreleri NaN gelir — soldan doldur
        if basliklar.isna().any():
            basliklar = basliklar.ffill()
            notlar.append("birleştirilmiş başlık hücreleri dolduruldu")

        govde = ham.iloc[baslik_r + 1 :].reset_index(drop=True)
        govde.columns = [
            str(v) if pd.notna(v) else f"kolon_{i + 1}" for i, v in enumerate(basliklar)
        ]

        toplamlar = _toplam_satirlari(govde)
        if toplamlar.any():
            adet = int(toplamlar.sum())
            govde = govde[~toplamlar].reset_index(drop=True)
            notlar.append(f"{adet} alt toplam satırı veriden ayrıldı")

        govde = govde.dropna(axis=0, how="all").dropna(axis=1, how="all")

        if len(govde) < MIN_VERI_SATIRI:
            atlanan.append(f"{sheet_adi} ({len(govde)} satır)")
            continue

        # Ad üretimi: "satislar_2024" + sheet "Satışlar 2024" →
        # "satislar_2024_satislar_2024" olmasın. Biri diğerini kapsıyorsa
        # tekrar etme.
        sheet_slug = slug(sheet_adi, fallback="sheet")
        if len(sheetler) == 1 or sheet_slug in name or name in sheet_slug:
            ds_adi = name
        else:
            ds_adi = f"{name}_{sheet_slug}"

        cikti.append(
            Extracted(
                name=ds_adi,
                df=govde,
                origin=f"{path.name} → {sheet_adi}",
                notes=notlar,
            )
        )

    if not cikti:
        raise UnsupportedSource(
            f"Excel'de kullanılabilir tablo yok. Atlananlar: {', '.join(atlanan) or '—'}"
        )

    if atlanan:
        cikti[0].notes.append(f"atlanan sheet'ler: {', '.join(atlanan)}")

    return cikti
