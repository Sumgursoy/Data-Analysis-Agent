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

from backend.ingest.adapters.base import Extracted, UnsupportedSource
from backend.ingest.normalize import find_header_row, slug

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
