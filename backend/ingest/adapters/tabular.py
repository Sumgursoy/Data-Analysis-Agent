"""Düz tablo dosyaları: csv, tsv, json, jsonl, parquet.

TASARIM KARARI — her şey önce METİN olarak okunur.
Pandas'ın veya DuckDB'nin kendi tip çıkarımına bırakırsak Türkçe veride
sessizce yanlış sonuç alırız: "1.234" float 1.234 olur (bin iki yüz otuz
dört değil), "31.12.2024" metin kalır. Tip kararını normalize.py veriyor —
kolonun tamamına bakarak, çoğunluk kuralıyla.

Parquet istisna: tipleri zaten dosyada yazılı, güvenilir.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path

import pandas as pd

from backend import config
from backend.ingest.adapters.base import Extracted, UnsupportedSource

log = logging.getLogger(__name__)

# Türkiye'den gelen dosyalarda sık görülenler, olasılık sırasına göre.
YEDEK_ENCODINGLER = ("utf-8-sig", "utf-8", "cp1254", "iso-8859-9", "latin-1")
AYIRAC_ADAYLARI = ";,\t|"


# Tek baytlık kod sayfaları birbirine çok benzer; istatistiksel tespit
# cp1254 (Türkçe) ile cp1257 (Baltık) arasında rahatlıkla yanılır ve
# "Şube" yerine "Žube" okur — patlamaz, sessizce bozar. Bu yüzden
# tespitin üstüne Türkçe lehine bir puanlama koyuyoruz.
TR_HARFLER = set("çğıİöşüÇĞÖŞÜâîû")
# Yanlış kod sayfası seçildiğinde beliren, Türkçede bulunmayan harfler:
SUPHELI_HARFLER = set("ŻżĒēĢģĶķĻļŅņŖŗŠšŽžĄąĖėĮįŲųŪūĆćŁłŃńŚśŹź")


def _turkce_puani(metin: str) -> int:
    return sum(c in TR_HARFLER for c in metin) - 3 * sum(
        c in SUPHELI_HARFLER for c in metin
    )


def detect_encoding(path: Path) -> str:
    """Dosyanın encoding'ini tespit eder.

    Uzantı `.csv` diye UTF-8 varsayma: yanlış encoding patlamaz, sessizce
    'Ã¼' gibi bozuk metin üretir ve bunu sonra fark etmek çok zor.
    """
    ham = path.read_bytes()[: 256 * 1024]
    if not ham:
        return "utf-8"

    # UTF-8 kesin olarak doğrulanabiliyorsa tartışma yok.
    for enc in ("utf-8-sig", "utf-8"):
        try:
            ham.decode(enc)
            return enc
        except UnicodeDecodeError:
            pass

    adaylar: list[str] = []
    try:
        from charset_normalizer import from_bytes

        sonuc = from_bytes(ham).best()
        if sonuc is not None and sonuc.encoding:
            adaylar.append(sonuc.encoding)
    except Exception:
        log.debug("charset-normalizer karar veremedi: %s", path.name)

    adaylar += [e for e in YEDEK_ENCODINGLER if e not in adaylar]

    en_iyi, en_iyi_puan = "latin-1", -10**9
    for enc in adaylar:
        try:
            metin = ham.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        puan = _turkce_puani(metin)
        # Beraberlikte listedeki ilk aday kazanır (tespit > yedek sırası)
        if puan > en_iyi_puan:
            en_iyi, en_iyi_puan = enc, puan

    return en_iyi


def detect_delimiter(path: Path, encoding: str) -> str:
    ornek = path.read_bytes()[: config.SNIFF_BYTES].decode(encoding, "replace")
    try:
        return csv.Sniffer().sniff(ornek, delimiters=AYIRAC_ADAYLARI).delimiter
    except csv.Error:
        pass

    # Sniffer başarısızsa: ilk satırlarda en tutarlı sayıda geçen ayıraç
    satirlar = [s for s in ornek.splitlines()[:20] if s.strip()]
    if not satirlar:
        return ","
    en_iyi, en_iyi_puan = ",", -1
    for aday in AYIRAC_ADAYLARI:
        sayilar = [s.count(aday) for s in satirlar]
        if not sayilar or sayilar[0] == 0:
            continue
        tutarli = sum(1 for c in sayilar if c == sayilar[0])
        puan = tutarli * 10 + sayilar[0]
        if puan > en_iyi_puan:
            en_iyi, en_iyi_puan = aday, puan
    return en_iyi


def _onsoz_satiri(path: Path, encoding: str, ayirac: str) -> int:
    """Tablonun başlamadığı baştaki satır sayısı.

    Log ve rapor dosyalarında ilk satırlar açıklama olur ("#LOG v2.1"),
    ayıraç içermez. Bunları atlamazsak pandas onları başlık sanar ve
    gerçek alanların çoğunu sessizce düşürür — veri kaybı.
    """
    ornek = path.read_bytes()[: config.SNIFF_BYTES].decode(encoding, "replace")
    satirlar = [s for s in ornek.splitlines() if s.strip()][:40]
    if len(satirlar) < 3:
        return 0

    sayilar = [s.count(ayirac) for s in satirlar]
    govde = [c for c in sayilar if c > 0]
    if not govde:
        return 0

    modal = max(set(govde), key=govde.count)
    if modal == 0:
        return 0

    atla = 0
    for c in sayilar:
        if c == modal:
            break
        atla += 1
    return atla if atla < len(satirlar) - 1 else 0


_SAYI_GIBI = re.compile(r"^[-+]?[\d.,]+$")
_TARIH_GIBI = re.compile(r"^\d{1,4}[./-]\d{1,2}[./-]\d{2,4}")


def _hucre_tipi(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "bos"
    s = str(v).strip()
    if _TARIH_GIBI.match(s):
        return "tarih"
    if _SAYI_GIBI.match(s) and any(ch.isdigit() for ch in s):
        return "sayi"
    return "metin"


def _basliksiz_mi(ham: pd.DataFrame) -> bool:
    """İlk satır başlık mı, yoksa o da veri mi?

    Log ve sistem çıktılarında başlık satırı hiç olmaz. Veriyi başlık
    sanarsak bir kaydı kaybederiz ve kolon adları saçmalar (`c2024_03_01`).

    Karar: ilk satırın TİP PROFİLİ gövdenin baskın profiliyle aynıysa
    o satır da veridir. Başlık satırı gövdeden tip olarak ayrışır —
    metin başlıkların altında sayı ve tarih kolonları olur.
    """
    if len(ham) < 3:
        return False

    ilk = tuple(_hucre_tipi(v) for v in ham.iloc[0])
    govde = [
        tuple(_hucre_tipi(v) for v in ham.iloc[i])
        for i in range(1, min(len(ham), 11))
    ]
    if not govde:
        return False

    baskin = max(set(govde), key=govde.count)

    # Gövdenin tamamı metinse ayrışma sinyali yok; başlık var varsayılır
    # (yanlış pozitif, gerçek başlığı kaybetmekten daha ucuz).
    if all(t in ("metin", "bos") for t in baskin):
        return False

    return ilk == baskin


def read_csv(path: Path, name: str) -> list[Extracted]:
    encoding = detect_encoding(path)
    ayirac = detect_delimiter(path, encoding)
    atla = _onsoz_satiri(path, encoding, ayirac)

    okuma_ayarlari = dict(
        sep=ayirac,
        encoding=encoding,
        dtype=str,              # tip kararını normalize.py verir
        keep_default_na=True,
        na_filter=True,
        engine="python",        # tuhaf tırnak/kaçış kombinasyonlarına dayanıklı
        skiprows=atla or None,
        on_bad_lines="warn",
    )

    # Önce başlıksız oku: ilk satırın veri mi başlık mı olduğuna
    # tip profiline bakarak karar vereceğiz.
    ham = pd.read_csv(path, header=None, **okuma_ayarlari)
    basliksiz = _basliksiz_mi(ham)

    if basliksiz:
        df = ham
        df.columns = [f"kolon_{i + 1}" for i in range(df.shape[1])]
    else:
        df = pd.read_csv(path, **okuma_ayarlari)

    # Ayıraç gövdede varken tek kolon çıktıysa ayrıştırma tutmamıştır.
    # Yanlış tabloyla devam etmektense ham geçişe düşmek daha doğru:
    # agent kendi parser'ını yazar, veri kaybı olmaz.
    if df.shape[1] < 2:
        ornek = path.read_bytes()[: config.SNIFF_BYTES].decode(encoding, "replace")
        govde = [s for s in ornek.splitlines()[atla:] if s.strip()][1:6]
        if govde and all(ayirac in s for s in govde):
            raise UnsupportedSource(
                f"ayıraç {ayirac!r} gövdede var ama tek kolon çıktı — "
                "düzensiz yapı, ham geçişe bırakılıyor"
            )

    notlar = [f"encoding: {encoding}", f"ayıraç: {ayirac!r}"]
    if atla:
        notlar.append(f"baştaki {atla} açıklama satırı atlandı")
    if basliksiz:
        notlar.append(
            "dosyada başlık satırı yok; kolonlar kolon_1..N olarak adlandırıldı"
        )
    return [Extracted(name=name, df=df, origin=path.name, notes=notlar)]


def read_parquet(path: Path, name: str) -> list[Extracted]:
    # Parquet tipleri kendi içinde taşır — metne çevirmeye gerek yok.
    return [Extracted(name=name, df=pd.read_parquet(path), origin=path.name)]


def read_json(path: Path, name: str) -> list[Extracted]:
    encoding = detect_encoding(path)
    metin = path.read_text(encoding=encoding, errors="replace").strip()
    if not metin:
        raise UnsupportedSource("boş JSON dosyası")

    # JSONL mi tek JSON mu?
    ilk_satir = metin.splitlines()[0].strip()
    satir_sayisi = metin.count("\n") + 1
    jsonl = satir_sayisi > 1 and ilk_satir.startswith("{") and not metin.startswith("[")

    if jsonl:
        kayitlar = []
        for i, satir in enumerate(metin.splitlines()):
            satir = satir.strip()
            if not satir:
                continue
            try:
                kayitlar.append(json.loads(satir))
            except json.JSONDecodeError:
                log.warning("%s: %d. satır atlandı (bozuk JSON)", path.name, i + 1)
        if not kayitlar:
            raise UnsupportedSource("JSONL içinde geçerli kayıt yok")
        df = pd.json_normalize(kayitlar)
        return [Extracted(name=name, df=df, origin=f"{path.name} (jsonl)")]

    try:
        veri = json.loads(metin)
    except json.JSONDecodeError as e:
        raise UnsupportedSource(f"JSON çözülemedi: {e}") from e

    if isinstance(veri, list):
        return [Extracted(name=name, df=pd.json_normalize(veri), origin=path.name)]

    if isinstance(veri, dict):
        # En uzun liste alanını tablo kabul et — API yanıtlarında yaygın kalıp
        # ({"status": "ok", "data": [...]} gibi).
        listeler = {k: v for k, v in veri.items() if isinstance(v, list) and v}
        if listeler:
            cikti = []
            for anahtar, deger in sorted(listeler.items(), key=lambda kv: -len(kv[1])):
                cikti.append(
                    Extracted(
                        name=f"{name}_{anahtar}" if len(listeler) > 1 else name,
                        df=pd.json_normalize(deger),
                        origin=f"{path.name} → {anahtar}",
                    )
                )
            return cikti
        return [Extracted(name=name, df=pd.json_normalize([veri]), origin=path.name)]

    raise UnsupportedSource("JSON tabloya çevrilebilir yapıda değil")
