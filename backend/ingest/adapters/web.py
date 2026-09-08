"""Web sayfası adaptörü.

İki iş yapar:

  1. `dom_map()`  — sayfanın YAPISINI özetler (tablolar, listeler, ana metin,
     arka planda çağrılan API uçları). Modele ham HTML BASILMAZ: 400 KB'lık
     bir sayfa 100k+ token yakar ve model dağılır. Onun yerine ~40 satırlık
     bir harita verilir, agent buna bakıp kendi seçicisini yazar.

  2. `extract_tables()` — sayfada <table> varsa doğrudan tabloya çevirir.
     Katmanlı yaklaşımın ilk basamağı: tablo varsa iş burada biter.

Agent'a öğretilen sıra (ucuzdan pahalıya):
    read_html  →  CSS seçici  →  arka plandaki JSON API  →  Playwright
"""

from __future__ import annotations

import io
import logging
import re
from urllib.parse import urljoin, urlparse

import pandas as pd

from backend.ingest.adapters.base import Extracted, UnsupportedSource
from backend.ingest.normalize import slug

log = logging.getLogger(__name__)

MIN_TABLO_SATIRI = 2
MAX_TABLO = 12

# Sayfanın arkada çektiği veri uçları — genelde en temiz kaynak budur.
_API_IZI = re.compile(
    r"""["'](?P<yol>(?:https?://[^"'\s]+|/)[^"'\s]*?"""
    r"""(?:/api/|/ajax/|\.json|/graphql|/v\d+/)[^"'\s]*)["']""",
    re.IGNORECASE,
)


def _agac(html: str):
    try:
        from selectolax.parser import HTMLParser

        return HTMLParser(html)
    except ImportError:
        return None


def _ana_metin(html: str) -> str:
    try:
        import trafilatura

        return trafilatura.extract(html) or ""
    except ImportError:
        return ""


def _api_uclari(html: str, taban_url: str) -> list[str]:
    bulunan: list[str] = []
    gorulen: set[str] = set()
    for m in _API_IZI.finditer(html):
        yol = m.group("yol")
        if len(yol) > 200:
            continue
        tam = urljoin(taban_url, yol)
        if tam in gorulen:
            continue
        gorulen.add(tam)
        bulunan.append(tam)
        if len(bulunan) >= 8:
            break
    return bulunan


def _tablo_ozetleri(html: str) -> list[tuple[int, int, list[str]]]:
    """Her <table> için (satır, kolon, başlıklar)."""
    agac = _agac(html)
    if agac is None:
        return []

    ozetler = []
    for tablo in agac.css("table")[:MAX_TABLO]:
        satirlar = tablo.css("tr")
        basliklar = [
            (h.text(strip=True) or "?")[:24] for h in tablo.css("th")[:8]
        ]
        if not basliklar and satirlar:
            basliklar = [
                (h.text(strip=True) or "?")[:24] for h in satirlar[0].css("td")[:8]
            ]
        kolon = len(satirlar[0].css("td, th")) if satirlar else 0
        ozetler.append((len(satirlar), kolon, basliklar))
    return ozetler


def dom_map(html: str, url: str, *, dosya_adi: str, rendered: bool = False) -> str:
    """Modele gösterilecek sayfa haritası — ham HTML'in yerine geçer."""
    boyut_kb = len(html.encode("utf-8")) / 1024
    agac = _agac(html)

    satirlar = [
        f"{dosya_adi} — {boyut_kb:.0f} KB{'  (JS render edildi)' if rendered else ''}",
        f"  kaynak: {url}",
        f"  oku: open(data_path('{dosya_adi}'), encoding='utf-8').read()",
        "",
    ]

    if agac is not None:
        baslik = agac.css_first("title")
        if baslik:
            satirlar.append(f"  başlık: {baslik.text(strip=True)[:100]}")

    tablolar = _tablo_ozetleri(html)
    if tablolar:
        satirlar.append(f"  <table> × {len(tablolar)}:")
        for i, (sat, kol, basliklar) in enumerate(tablolar, 1):
            b = " | ".join(basliklar) if basliklar else "(başlık yok)"
            satirlar.append(f"    [{i}] {sat} satır × {kol} kolon → {b}")
    else:
        satirlar.append("  <table> yok")

    if agac is not None:
        for etiket in ("ul", "ol", "form", "article"):
            adet = len(agac.css(etiket))
            if adet:
                satirlar.append(f"  <{etiket}> × {adet}")

    metin = _ana_metin(html)
    if metin:
        satirlar.append(f"  ana metin (boilerplate atılmış): {len(metin)} karakter")
        onizleme = " ".join(metin.split())[:220]
        satirlar.append(f"    « {onizleme}… »")

    uclar = _api_uclari(html, url)
    if uclar:
        satirlar.append("  arka planda çağrılan veri uçları (genelde en temiz kaynak):")
        satirlar += [f"    {u}" for u in uclar]

    satirlar += [
        "",
        "  → Sırayla dene: (1) pd.read_html ile tablolar, (2) selectolax + CSS",
        "    seçici, (3) yukarıdaki JSON uçları, (4) hâlâ boşsa render=True.",
    ]
    return "\n".join(satirlar)


def extract_tables(html: str, name: str, origin: str = "") -> list[Extracted]:
    """Sayfadaki <table> etiketlerini tabloya çevirir — katmanın ilk basamağı."""
    try:
        # thousands=None kritik: pandas varsayılan olarak ',' karakterini
        # binlik ayıracı sayıyor ve Türkçe "1,250" değerini 1250'ye çeviriyor
        # (doğrusu 1,25). Ayıraç kararını normalize.py kolonun tamamına
        # bakarak verecek — burada ham bırakıyoruz.
        cerceveler = pd.read_html(io.StringIO(html), thousands=None)
    except ValueError as e:
        raise UnsupportedSource(f"Sayfada okunabilir tablo yok: {e}") from e
    except ImportError as e:
        raise UnsupportedSource(f"read_html için lxml gerekiyor: {e}") from e

    cikti: list[Extracted] = []
    for i, df in enumerate(cerceveler[:MAX_TABLO], 1):
        df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
        if len(df) < MIN_TABLO_SATIRI or df.shape[1] < 2:
            continue
        # Her şeyi metne çevir — diğer adaptörlerle aynı sözleşme:
        # tip kararı tek yerde, normalize.py'de verilir.
        df = df.map(lambda v: v if pd.isna(v) else str(v))
        cikti.append(
            Extracted(
                name=name if len(cerceveler) == 1 else f"{name}_tablo{i}",
                df=df,
                origin=origin or f"{name} → tablo {i}",
                notes=[f"HTML tablosu {i}/{len(cerceveler)}"],
                kind="web",
            )
        )

    if not cikti:
        raise UnsupportedSource(
            f"Sayfada {len(cerceveler)} tablo bulundu ama hiçbiri kullanılabilir "
            "boyutta değil (en az 2 satır ve 2 kolon gerekiyor)."
        )
    return cikti


def url_to_name(url: str) -> str:
    """URL'den okunabilir bir dataset adı üretir."""
    parca = urlparse(url)
    yol = (parca.path or "").strip("/").replace("/", "_")
    taban = yol or parca.hostname or "sayfa"
    return slug(taban.rsplit(".", 1)[0][:40], fallback="sayfa")
