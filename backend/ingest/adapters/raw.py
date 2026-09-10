"""Kaçış kapısı — hiçbir adaptör eşleşmediğinde.

Dosya olduğu gibi /data'ya kopyalanır ve kataloğa `kind="raw"` girer.
Şema kartı yerine agent'a dosyanın "şekli" verilir: hex önizleme, satır
sayısı, tekrar eden karakter kalıpları. Agent kendi parser'ını
`run_python` içinde yazar.

Sabit genişlikli metin, log dosyası, egzotik ayıraç, hiç görülmemiş bir
uzantı — hepsi buradan geçer. "Bilinmeyen veri tipi" riskini sıfıra
indiren tek mekanizma budur.
"""

from __future__ import annotations

import string
from collections import Counter
from pathlib import Path

ONIZLEME_BAYT = 512
SATIR_TARAMA_BAYT = 256 * 1024
YAZDIRILABILIR = set(bytes(string.printable, "ascii"))


def _hex_dokum(ham: bytes, satir_genisligi: int = 16) -> str:
    satirlar = []
    for offset in range(0, len(ham), satir_genisligi):
        parca = ham[offset : offset + satir_genisligi]
        hexler = " ".join(f"{b:02x}" for b in parca).ljust(satir_genisligi * 3 - 1)
        asciiler = "".join(chr(b) if 32 <= b < 127 else "." for b in parca)
        satirlar.append(f"    {offset:08x}  {hexler}  |{asciiler}|")
    return "\n".join(satirlar)


def _ayirac_ipuclari(metin: str) -> list[str]:
    satirlar = [s for s in metin.splitlines() if s.strip()][:50]
    if len(satirlar) < 2:
        return []

    ipuclari = []
    for aday in ";,\t|:":
        sayilar = [s.count(aday) for s in satirlar]
        if not sayilar or sayilar[0] == 0:
            continue
        tutarli = sum(1 for c in sayilar if c == sayilar[0]) / len(sayilar)
        if tutarli > 0.8:
            gorunen = {"\t": "\\t"}.get(aday, aday)
            ipuclari.append(f"'{gorunen}' (satır başına {sayilar[0]}, %{tutarli * 100:.0f} tutarlı)")
    return ipuclari


def describe_raw(path: Path, location: str) -> str:
    """Tanınmayan dosya için şema kartı yerine geçen tanım."""
    boyut = path.stat().st_size
    ham = path.read_bytes()[:SATIR_TARAMA_BAYT]

    yazdirilabilir_oran = (
        sum(1 for b in ham if b in YAZDIRILABILIR) / len(ham) if ham else 0.0
    )
    ikili = yazdirilabilir_oran < 0.85

    satirlar = [
        f"{path.name} — {boyut / 1024 / 1024:.1f} MB, TANINMAYAN FORMAT",
        f"  konum: {location}",
        f"  içerik: {'ikili (binary)' if ikili else 'metin'} "
        f"(yazdırılabilir karakter oranı %{yazdirilabilir_oran * 100:.0f})",
    ]

    if not ikili:
        metin = ham.decode("utf-8", "replace")
        satir_sayisi = metin.count("\n")
        if boyut > len(ham):  # örneklemden tahmin
            tahmin = int(satir_sayisi * boyut / len(ham))
            satirlar.append(f"  tahmini satır sayısı: ~{tahmin:,}".replace(",", "."))
        else:
            satirlar.append(f"  satır sayısı: {satir_sayisi:,}".replace(",", "."))

        ipuclari = _ayirac_ipuclari(metin)
        if ipuclari:
            satirlar.append("  olası ayıraçlar: " + ", ".join(ipuclari))

        ilk = [s for s in metin.splitlines() if s.strip()][:5]
        if ilk:
            satirlar.append("  ilk satırlar:")
            satirlar += [f"    {s[:160]}" for s in ilk]
    else:
        sik = Counter(ham[:4096]).most_common(3)
        satirlar.append(
            "  en sık baytlar: "
            + ", ".join(f"0x{b:02x}×{n}" for b, n in sik)
        )

    satirlar.append("  ilk 512 bayt (hex + ascii):")
    satirlar.append(_hex_dokum(ham[:ONIZLEME_BAYT]))
    satirlar.append("")
    satirlar.append(
        "  → Bu dosya otomatik ayrıştırılamadı. run_python ile kendi parser'ını "
        "yaz; dosya yukarıdaki konumda okunmaya hazır."
    )
    # Kütüphaneleri BURADA da say. Sistem prompt'unda yazıyor ama kaçış kapısı
    # metni "parser'ını yaz" derken hangi araçla yazılacağını söylemiyordu:
    # gerçek turlarda agent PyPDF2 → pypdf → fitz → pdftotext diye deneyip
    # 9 adımın 4'ünü yalnızca kütüphane aramaya harcadı.
    satirlar.append(
        "     Kurulu ayrıştırıcılar: pdfplumber (PDF) · selectolax, lxml "
        "(HTML/XML) · openpyxl (xlsx) · xlrd (xls) · pyxlsb (xlsb) · odfpy "
        "(ods). Deneyerek arama, bunlar var. PyPDF2/pypdf/fitz/pdftotext YOK."
    )
    return "\n".join(satirlar)
