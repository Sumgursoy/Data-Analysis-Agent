"""Kolon adı ve değer normalizasyonu — özellikle Türkçe veri tuzakları.

Bu katman atlanırsa agent doğru kodu yazar ama YANLIŞ sonuç alır.
Sessiz hatalar en tehlikelisidir: "1.250,00 ₺" metin olarak kalırsa
toplam alınamaz, alınırsa da string birleştirmesi yapar.

Kapsanan tuzaklar (MIMARI.md bölüm 12):
  - binlik nokta / ondalık virgül        "1.234,56"  → 1234.56
  - para birimi ve yüzde                "1.234,56 ₺" → 1234.56
  - muhasebe negatifi                   "(1.250)"   → -1250.0
  - gg.aa.yyyy tarih, gün/ay belirsizliği
  - kolon adında Türkçe karakter ve .lower() bozukluğu
"""

from __future__ import annotations

import logging
import re
import unicodedata

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ── Kolon adları ────────────────────────────────────────────────

# Python'da "İ".lower() İKİ karakter üretir (i + U+0307 birleşen nokta),
# "I".lower() ise Türkçedeki 'ı' yerine 'i' verir. Bu yüzden .lower()'a
# güvenilmez; harfleri açıkça eşliyoruz.
_TR_HARF = str.maketrans(
    "İIıŞşĞğÜüÖöÇçÂâÎîÛû",
    "IIiSsGgUuOoCcAaIiUu",
)

_COK_ALT_CIZGI = re.compile(r"_+")
_GECERSIZ = re.compile(r"[^a-z0-9_]")


def slug(name: object, *, fallback: str = "kolon") -> str:
    """Kolon adını güvenli bir tanımlayıcıya çevirir.

    "Müşteri No "      → musteri_no
    "TOPLAM TUTAR(₺)"  → toplam_tutar
    "2024 Yılı %"      → c2024_yili_yuzde  (rakamla başlayamaz)
    """
    text = str(name).strip()
    if not text or text.lower().startswith("unnamed:"):
        return fallback

    text = text.replace("%", " yuzde ").replace("₺", " tl ").replace("&", " ve ")
    text = text.translate(_TR_HARF)
    # Kalan aksanları ayrıştırıp at (é → e)
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    text = _GECERSIZ.sub("_", text.lower())
    text = _COK_ALT_CIZGI.sub("_", text).strip("_")

    if not text:
        return fallback
    if text[0].isdigit():
        text = f"c{text}"
    return text


def uniquify(names: list[str]) -> list[str]:
    """Çakışan kolon adlarına sonek ekler: tutar, tutar_2, tutar_3."""
    goruldu: dict[str, int] = {}
    sonuc = []
    for n in names:
        if n in goruldu:
            goruldu[n] += 1
            sonuc.append(f"{n}_{goruldu[n]}")
        else:
            goruldu[n] = 1
            sonuc.append(n)
    return sonuc


# ── Sayılar ─────────────────────────────────────────────────────

_TEMIZLE = re.compile(r"[₺$€£]|\bTL\b|\bTRY\b|\s| ", re.IGNORECASE)
_MUHASEBE_NEG = re.compile(r"^\((.*)\)$")
_SAYI_GOVDE = re.compile(r"^-?[\d.,]+$")

BOS_DEGERLER = {"", "-", "—", "–", "n/a", "na", "null", "none", "nan", "#yok", "yok"}


def _tek_sayi(ham: str, ondalik: str) -> float | None:
    """Tek bir metni verilen ondalık ayıraca göre sayıya çevirir."""
    s = _TEMIZLE.sub("", ham)
    if s.lower() in BOS_DEGERLER:
        return None

    negatif = False
    m = _MUHASEBE_NEG.match(s)
    if m:  # muhasebe negatifi: (1.250) → -1250
        negatif, s = True, m.group(1)
    s = s.replace("%", "")

    if not s or not _SAYI_GOVDE.match(s):
        return None

    if ondalik == ",":
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")

    try:
        deger = float(s)
    except ValueError:
        return None
    return -deger if negatif else deger


def _ondalik_ayirac(ornekler: list[str]) -> str:
    """Kolonun tamamına bakarak ondalık ayıracına karar verir.

    Satır bazında karar verme — aynı kolonda "1.234" hem bin iki yüz otuz dört
    hem 1.234 olabilir. Çoğunluk kuralı:
      - hem '.' hem ',' varsa → SONDAKİ ondalıktır
      - tek ayıraç varsa → arkasında tam 3 hane ve tek geçiş ise binlik,
        değilse ondalık
    """
    virgul = nokta = 0

    for s in ornekler:
        temiz = _TEMIZLE.sub("", s).replace("%", "").strip("()")
        son_nokta, son_virgul = temiz.rfind("."), temiz.rfind(",")

        if son_nokta >= 0 and son_virgul >= 0:
            (virgul, nokta) = (
                (virgul + 1, nokta) if son_virgul > son_nokta else (virgul, nokta + 1)
            )
            continue

        for ayirac, sayac_arttir in ((",", "virgul"), (".", "nokta")):
            konum = temiz.rfind(ayirac)
            if konum < 0:
                continue
            kuyruk = len(temiz) - konum - 1
            tek_gecis = temiz.count(ayirac) == 1
            # 3 hane + tek geçiş → büyük ihtimalle binlik ayıracı, kanıt sayma
            if kuyruk == 3 and tek_gecis:
                break
            if sayac_arttir == "virgul":
                virgul += 1
            else:
                nokta += 1
            break

    # Türkçe veride beraberlik virgül lehine bozulur.
    return "," if virgul >= nokta else "."


def to_numeric(s: pd.Series, *, esik: float = 0.8) -> pd.Series | None:
    """Metin kolonunu sayıya çevirmeyi dener.

    Boş olmayan değerlerin en az `esik` oranı çevrilebiliyorsa çevrilmiş
    seriyi, aksi halde None döner (kolon metin olarak kalır).
    """
    metin = s.astype("string").str.strip()
    dolu = metin[metin.notna() & ~metin.str.lower().isin(BOS_DEGERLER)]
    if dolu.empty:
        return None

    ornek = dolu.head(1000).tolist()
    ondalik = _ondalik_ayirac(ornek)

    cevrilen = metin.map(lambda v: _tek_sayi(v, ondalik) if pd.notna(v) else None)
    basari = cevrilen.notna().sum() / len(dolu)
    if basari < esik:
        return None

    sayisal = pd.to_numeric(cevrilen, errors="coerce")

    # Tamsayıysa float'ta bırakma: müşteri no 1001 olsun, 1001.0 değil.
    # Float ID'ler join'de ve raporda sorun çıkarır. Int64 (nullable) ile
    # boş değerler de korunur.
    gecerli = sayisal.dropna()
    if not gecerli.empty and np.isfinite(gecerli).all() and (gecerli % 1 == 0).all():
        if gecerli.abs().max() < 2**63 - 1:
            return sayisal.astype("Int64")

    return sayisal


# ── Tarihler ────────────────────────────────────────────────────

_TARIH_KALIBI = re.compile(r"^\s*(\d{1,4})[./-](\d{1,2})[./-](\d{2,4})")


def _gun_once_mu(ornekler: list[str]) -> bool | None:
    """gg.aa.yyyy mi aa.gg.yyyy mi — kolonun tamamına bakarak karar ver."""
    ilk_buyuk = ikinci_buyuk = False
    for s in ornekler:
        m = _TARIH_KALIBI.match(s)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if len(m.group(1)) == 4:  # yyyy-aa-gg → ISO, karar gerekmez
            return None
        if a > 12:
            ilk_buyuk = True
        if b > 12:
            ikinci_buyuk = True

    if ilk_buyuk and not ikinci_buyuk:
        return True
    if ikinci_buyuk and not ilk_buyuk:
        return False
    return None  # ayırt edilemedi


def to_datetime(s: pd.Series, *, esik: float = 0.8) -> tuple[pd.Series | None, str | None]:
    """Metin kolonunu tarihe çevirmeyi dener.

    Döner: (seri, varsayım notu). Gün/ay ayırt edilemediyse Türkçe veride
    dayfirst=True varsayılır ve bu not şema kartına yazılır.
    """
    metin = s.astype("string").str.strip()
    dolu = metin[metin.notna() & ~metin.str.lower().isin(BOS_DEGERLER)]
    if dolu.empty:
        return None, None

    ornek = dolu.head(1000).tolist()
    if not any(_TARIH_KALIBI.match(v) for v in ornek[:50]):
        return None, None

    gun_once = _gun_once_mu(ornek)
    not_: str | None = None
    if gun_once is None:
        gun_once = True
        not_ = "gün/ay sırası verilerden ayırt edilemedi; gg.aa.yyyy varsayıldı"

    cevrilen = pd.to_datetime(metin, dayfirst=gun_once, errors="coerce", format="mixed")
    if cevrilen.notna().sum() / len(dolu) < esik:
        return None, None

    return cevrilen, not_


# ── Tüm tablo ───────────────────────────────────────────────────


def normalize_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Kolon adlarını ve değer tiplerini düzeltir.

    Döner: (temizlenmiş df, notlar). Notlar şema kartına gider —
    agent yapılan varsayımları görsün.
    """
    notlar: list[str] = []

    # 1) Tamamen boş satır ve kolonları at
    onceki = df.shape
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    if df.shape != onceki:
        atilan_k = onceki[1] - df.shape[1]
        atilan_s = onceki[0] - df.shape[0]
        if atilan_k:
            notlar.append(f"{atilan_k} tamamen boş kolon atıldı")
        if atilan_s:
            notlar.append(f"{atilan_s} tamamen boş satır atıldı")

    # 2) Kolon adları
    eski = list(df.columns)
    yeni = uniquify([slug(c, fallback=f"kolon_{i + 1}") for i, c in enumerate(eski)])
    df = df.set_axis(yeni, axis=1)
    degisen = [f"{e} → {y}" for e, y in zip(eski, yeni) if str(e) != y]
    if degisen:
        notlar.append(f"{len(degisen)} kolon adı normalize edildi")

    # 3) Değer tipleri — sadece object/string kolonlar
    for kolon in df.columns:
        s = df[kolon]
        if not (pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)):
            continue

        tarih, not_ = to_datetime(s)
        if tarih is not None:
            df[kolon] = tarih
            if not_:
                notlar.append(f"`{kolon}`: {not_}")
            continue

        sayi = to_numeric(s)
        if sayi is not None:
            df[kolon] = sayi
            continue

        # Düşük kardinaliteli metin → kategori (bellek ve profil için)
        dolu = s.notna().sum()
        if dolu and s.nunique(dropna=True) <= max(20, dolu * 0.05):
            df[kolon] = s.astype("category")

    df = df.reset_index(drop=True)
    return df, notlar


def bos_degerleri_temizle(df: pd.DataFrame) -> pd.DataFrame:
    """'', '  ', '-', 'N/A', 'yok' gibi sahte değerleri gerçek NaN yapar.

    Boş metin de listeye dahil: PDF ve HTML çıkarımında hücreler NaN yerine
    "" olarak gelir. Bunlar NaN'a çevrilmezse "tamamen boş satır" tespiti
    çalışmaz ve tabloya hayalet satırlar sızar.
    """
    def temizle(v: object) -> object:
        if v is None:
            return np.nan
        if isinstance(v, str) and v.strip().lower() in BOS_DEGERLER:
            return np.nan
        return v

    for kolon in df.columns:
        s = df[kolon]
        if pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s):
            df[kolon] = s.map(temizle)
    return df
