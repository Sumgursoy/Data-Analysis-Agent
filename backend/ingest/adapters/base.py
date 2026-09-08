"""Adaptörlerin ortak dönüş tipi.

Bir kaynak birden fazla tablo üretebilir (Excel'de sheet'ler, ZIP'te
dosyalar, HTML'de tablolar) — o yüzden adaptörler liste döner.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Extracted:
    """Adaptörün çıkardığı ham tablo. Henüz normalize edilmedi."""

    name: str                              # önerilen ad: "satislar_sheet1"
    df: pd.DataFrame
    origin: str = ""                       # "satislar.xlsx → Sheet1"
    notes: list[str] = field(default_factory=list)
    kind: str = "file"


class UnsupportedSource(Exception):
    """Adaptör bu kaynağı işleyemedi — router bir sonrakini dener."""
