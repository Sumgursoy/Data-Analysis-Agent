"""PDF tablo adaptörü.

Sürpriz kaynak: kurumsal raporlar sık sık PDF gelir. `pdfplumber` seçildi
çünkü saf Python — `camelot` daha güçlü ama Ghostscript kurulumu gerektiriyor
ve demo günü kurulum bağımlılığı risktir.

PDF tabloları güvenilmezdir (birleşik hücreler, satır taşmaları), o yüzden
çıkan her tabloya "el ile doğrula" notu düşülür ve tablo bulunamazsa metin
katmanı çıkarılıp kaçış kapısına bırakılır.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from backend.ingest.adapters.base import Extracted, UnsupportedSource

log = logging.getLogger(__name__)

MAX_SAYFA = 50
MIN_SATIR = 2

# pdfplumber varsayılanı çizgilere bakar ("lines"). Kurumsal PDF'lerin
# çoğunda tablo çerçevesi yoktur — sadece hizalanmış metin vardır. O yüzden
# çizgi stratejisi boş dönerse metin hizasına göre tekrar deniyoruz.
STRATEJILER = [
    None,  # pdfplumber varsayılanı (çizgiler)
    {"vertical_strategy": "text", "horizontal_strategy": "text"},
]


def _sayfadan_tablolar(sayfa) -> list[list[list]]:
    for ayar in STRATEJILER:
        try:
            tablolar = sayfa.extract_tables(ayar) if ayar else sayfa.extract_tables()
        except Exception:
            continue
        kullanilabilir = [t for t in (tablolar or []) if t and len(t) > MIN_SATIR]
        if kullanilabilir:
            return kullanilabilir
    return []


def read_pdf(path: Path, name: str) -> list[Extracted]:
    try:
        import pdfplumber
    except ImportError as e:
        raise UnsupportedSource(f"PDF okumak için pdfplumber gerekiyor: {e}") from e

    cikti: list[Extracted] = []
    metin_uzunlugu = 0

    try:
        with pdfplumber.open(path) as pdf:
            toplam_sayfa = len(pdf.pages)
            for sayfa_no, sayfa in enumerate(pdf.pages[:MAX_SAYFA], 1):
                metin_uzunlugu += len(sayfa.extract_text() or "")

                for tablo_no, tablo in enumerate(_sayfadan_tablolar(sayfa), 1):
                    if len(tablo) < MIN_SATIR + 1:
                        continue

                    basliklar = [
                        (h or f"kolon_{i + 1}").replace("\n", " ").strip()
                        for i, h in enumerate(tablo[0])
                    ]
                    govde = [
                        [(h or "").replace("\n", " ").strip() for h in satir]
                        for satir in tablo[1:]
                    ]
                    df = pd.DataFrame(govde, columns=basliklar)
                    df = df.dropna(axis=1, how="all")
                    if len(df) < MIN_SATIR or df.shape[1] < 2:
                        continue

                    cikti.append(
                        Extracted(
                            name=f"{name}_s{sayfa_no}t{tablo_no}",
                            df=df,
                            origin=f"{path.name} → sayfa {sayfa_no}, tablo {tablo_no}",
                            notes=[
                                f"PDF sayfa {sayfa_no}/{toplam_sayfa}",
                                "⚠ PDF tablo çıkarımı hatalı olabilir "
                                "(birleşik hücre, satır taşması) — sayıları doğrula",
                            ],
                        )
                    )
    except Exception as e:
        raise UnsupportedSource(f"PDF okunamadı: {e}") from e

    if not cikti:
        raise UnsupportedSource(
            f"PDF'te tablo bulunamadı ({metin_uzunlugu} karakter metin var). "
            "Taranmış bir belge olabilir; agent metin katmanını kendi işleyebilir."
        )

    if len(cikti) == 1:
        cikti[0].name = name
    return cikti
