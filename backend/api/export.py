"""Analizin çalıştırılabilir notebook olarak dışa aktarımı.

Kaynak `trace.jsonl`: agent'ın çalıştırdığı her kod, her çıktı, her grafik
oraya yazıldı. Notebook buradan üretiliyor — frontend'in elindeki kod
listesinden değil.

Neden backend tarafı daha sağlam:
  - çıktılar da gömülebiliyor (frontend sadece kodu biriktiriyordu)
  - sayfa yenilense de kayıp olmuyor
  - SQL adımları da yorum olarak giriyor
  - jüri "sonuç doğrulanabilir mi" diye sorduğunda tek tık cevap

Yarışma açısından değeri: çoğu yarışmacı reprodüksiyon sunmaz.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend import config
from backend.agent.session import AgentSession
from backend.ingest.catalog import Catalog

log = logging.getLogger(__name__)
router = APIRouter()

BASLANGIC_HUCRESI = """\
# Sandbox'taki hazır ortamın yerel karşılığı.
# Bu notebook'u agent'ın çalıştığı klasörde açarsan yollar birebir uyar.
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "artifacts"))
ARTIFACTS_DIR.mkdir(exist_ok=True)

def data_path(name=""):     return str(DATA_DIR / name) if name else str(DATA_DIR)
def artifact_path(name=""): return str(ARTIFACTS_DIR / name) if name else str(ARTIFACTS_DIR)
"""


def _md(satirlar: list[str]) -> dict[str, Any]:
    return {"cell_type": "markdown", "metadata": {}, "source": satirlar}


def _kod(kaynak: str, cikti: str = "") -> dict[str, Any]:
    hucre: dict[str, Any] = {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": kaynak.splitlines(keepends=True),
    }
    if cikti.strip():
        # Agent'ın gerçekten aldığı çıktıyı gömüyoruz: notebook yeniden
        # çalıştırılmadan da ne olduğu okunabilsin.
        hucre["outputs"] = [{
            "output_type": "stream",
            "name": "stdout",
            "text": cikti.splitlines(keepends=True),
        }]
    return hucre


def build_notebook(session_id: str) -> dict[str, Any]:
    oturum = AgentSession(session_id)
    izler = oturum.read_trace()
    katalog = Catalog(session_id)

    girisler = [
        "# Analiz Defteri\n", "\n",
        f"Session `{session_id}` içinde agent tarafından üretilen ve "
        "çalıştırılan kod.\n", "\n",
    ]
    if katalog.all():
        girisler += ["## Veri setleri\n", "\n"]
        girisler += [
            f"- `{d.name}` — {d.row_count or '?'} satır × {d.col_count or '?'} "
            f"kolon ({d.origin or d.kind})\n"
            for d in katalog.all()
        ]
        girisler.append("\n")

    hucreler: list[dict[str, Any]] = [_md(girisler), _kod(BASLANGIC_HUCRESI)]

    bekleyen_cikti = ""
    for iz in izler:
        tip = iz.get("type")

        if tip == "user":
            hucreler.append(_md([f"### Soru\n", "\n", f"> {iz.get('text', '')}\n"]))

        elif tip == "code":
            hucreler.append(_kod(str(iz.get("code", "")), bekleyen_cikti))
            bekleyen_cikti = ""

        elif tip == "stdout":
            # stdout kaydı kendisinden ÖNCEKİ kod hücresine aittir.
            if hucreler and hucreler[-1]["cell_type"] == "code":
                hucreler[-1]["outputs"] = [{
                    "output_type": "stream", "name": "stdout",
                    "text": str(iz.get("text", "")).splitlines(keepends=True),
                }]

        elif tip == "sql":
            hucreler.append(
                _kod(
                    "# SQL adımı (agent DuckDB üzerinde çalıştırdı)\n"
                    "import duckdb\n"
                    f"duckdb.sql(\"\"\"{iz.get('query', '')}\"\"\").df()\n"
                )
            )

        elif tip == "fetch":
            hucreler.append(
                _md([f"*Sayfa indirildi:* `{iz.get('url', '')}` "
                     f"→ `{iz.get('file', '')}`\n"])
            )

        elif tip == "artifact":
            hucreler.append(_md([f"*Üretilen grafik:* `{iz.get('path', '')}`\n"]))

        elif tip == "finish":
            hucreler.append(
                _md(["## Bulgular\n", "\n", f"{iz.get('summary', '')}\n"])
            )

    return {
        "cells": hucreler,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3", "language": "python", "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


@router.get("/export/{session_id}.ipynb")
def export_notebook(session_id: str) -> Response:
    if not (config.SESSIONS_DIR / session_id).is_dir():
        raise HTTPException(404, f"Session bulunamadı: {session_id}")

    notebook = build_notebook(session_id)
    kod_sayisi = sum(1 for h in notebook["cells"] if h["cell_type"] == "code")
    if kod_sayisi <= 1:  # sadece başlangıç hücresi
        raise HTTPException(404, "Bu session'da henüz çalıştırılmış kod yok.")

    return Response(
        content=json.dumps(notebook, ensure_ascii=False, indent=1),
        media_type="application/x-ipynb+json",
        headers={
            "Content-Disposition": f'attachment; filename="analiz_{session_id}.ipynb"'
        },
    )
