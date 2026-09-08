"""Şema kartı üretimi — modelin ham veri yerine gördüğü şey.

Deterministik, LLM yok, hedef <300 ms. Çıktı ~1-1.5k token.

`ydata-profiling` bilerek kullanılmıyor: yavaş, devasa HTML üretiyor ve
token'a çevrilebilir bir şey vermiyor. Buradaki tek amaç modelin doğru
kodu İLK SEFERDE yazması.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

ORNEK_SATIR = 5
KATEGORI_LISTE_LIMIT = 8
YUKSEK_NULL_ESIGI = 0.5
MAX_KOLON_DETAY = 60  # çok geniş tablolarda kartı şişirme


def _tr_sayi(x: float | int) -> str:
    """1234567 → 1.234.567 (Türkçe binlik ayıracı)."""
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    if isinstance(x, float) and not float(x).is_integer():
        return f"{x:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return f"{int(x):,}".replace(",", ".")


def _kisa(deger: object, limit: int = 28) -> str:
    s = str(deger).replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ── kolon özetleri ──────────────────────────────────────────────


def _sayisal_ozet(s: pd.Series) -> tuple[str, list[str]]:
    dolu = s.dropna()
    if dolu.empty:
        return "tümü boş", []

    q = dolu.quantile([0.5, 0.9, 0.99])
    ozet = (
        f"min {_tr_sayi(dolu.min())}  p50 {_tr_sayi(q[0.5])}  "
        f"p90 {_tr_sayi(q[0.9])}  p99 {_tr_sayi(q[0.99])}  maks {_tr_sayi(dolu.max())}"
    )

    uyarilar = []
    if len(dolu) > 2:
        try:
            carpiklik = float(dolu.skew())
            if abs(carpiklik) > 2:
                yon = "sağa" if carpiklik > 0 else "sola"
                ozet += f"  ({yon} çarpık, skew {carpiklik:.1f})"
        except (TypeError, ValueError):
            pass

    negatif = int((dolu < 0).sum())
    if negatif and dolu.min() < 0 <= dolu.max():
        uyarilar.append(f"{_tr_sayi(negatif)} negatif değer var (iade/düzeltme olabilir)")

    # IQR × 3 dışı — kaba aykırı değer sayısı
    try:
        q1, q3 = dolu.quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr > 0:
            aykiri = int(((dolu < q1 - 3 * iqr) | (dolu > q3 + 3 * iqr)).sum())
            if aykiri:
                ozet += f", {_tr_sayi(aykiri)} aykırı"
    except (TypeError, ValueError):
        pass

    return ozet, uyarilar


_ANAHTAR_ADI = ("_id", "_no", "_kod", "_key", "_ref")


def _anahtar_adi_mi(kolon: object) -> bool:
    ad = str(kolon).lower()
    return ad.endswith(_ANAHTAR_ADI) or ad in ("id", "no", "kod", "key")


def _kimlik_gibi(s: pd.Series, kolon: object) -> bool:
    """Tamsayı kolonu bir kimlik mi (müşteri no, sipariş id)?

    İki yoldan biri yeterli:
      - adı anahtar gibi (`musteri_no`, `siparis_id`)
      - değerleri neredeyse tekil (adı ne olursa olsun)

    Neden ad yetiyor: yabancı anahtar kolonunda tekillik DÜŞÜKTÜR
    (6.000 siparişte 240 müşteri). Sadece tekilliğe bakarsak yabancı
    anahtarı sayısal sanıp modele `p50 10.121` gibi anlamsız bir
    istatistik gösteririz.
    """
    dolu = s.dropna()
    if len(dolu) < 5:
        return False
    if _anahtar_adi_mi(kolon):
        return True
    return int(dolu.nunique()) / len(dolu) > 0.95


def _kimlik_ozet(s: pd.Series) -> tuple[str, list[str]]:
    dolu = s.dropna()
    tekil = int(dolu.nunique())
    ornekler = ", ".join(str(v) for v in dolu.head(3))

    if tekil == len(dolu):
        etiket = "tekil  ← birincil anahtar adayı"
    else:
        oran = tekil / len(dolu) * 100
        etiket = f"%{oran:.0f} tekil  ← yabancı anahtar adayı"

    return f"{_tr_sayi(tekil)} uniq ({etiket}); ör. {ornekler}", []


def _tarih_ozet(s: pd.Series) -> tuple[str, list[str]]:
    dolu = s.dropna()
    if dolu.empty:
        return "tümü boş", []

    bas, son = dolu.min(), dolu.max()
    ozet = f"{bas:%Y-%m-%d} → {son:%Y-%m-%d}"

    uyarilar = []
    try:  # aylık boşluk var mı — zaman serisi analizinde kritik
        aylar = dolu.dt.to_period("M")
        beklenen = pd.period_range(aylar.min(), aylar.max(), freq="M")
        eksik = len(beklenen) - aylar.nunique()
        if eksik > 0 and len(beklenen) > 2:
            ozet += f"  ({len(beklenen)} aydan {eksik} tanesinde hiç kayıt yok)"
    except (TypeError, ValueError, AttributeError):
        pass

    return ozet, uyarilar


def _kategorik_ozet(s: pd.Series, satir: int) -> tuple[str, list[str]]:
    dolu = s.dropna()
    if dolu.empty:
        return "tümü boş", []

    tekil = int(dolu.nunique())
    uyarilar: list[str] = []

    if tekil == 1:
        return f"tek değer: {_kisa(dolu.iloc[0])}  ← sabit kolon", [
            "sabit kolon, analizde bilgi taşımıyor"
        ]

    if tekil <= KATEGORI_LISTE_LIMIT:
        pay = dolu.value_counts(normalize=True)
        parcalar = [f"{_kisa(k)} %{v * 100:.0f}" for k, v in pay.head(KATEGORI_LISTE_LIMIT).items()]
        return f"{tekil} uniq: " + ", ".join(parcalar), uyarilar

    if satir and tekil / satir > 0.95:
        ornekler = ", ".join(_kisa(v, 16) for v in dolu.head(3))
        return f"{_tr_sayi(tekil)} uniq (neredeyse tekil)  ← anahtar adayı: {ornekler}", uyarilar

    ilk = ", ".join(_kisa(v, 16) for v in dolu.value_counts().head(4).index)
    return f"{_tr_sayi(tekil)} uniq, en sık: {ilk}", uyarilar


def _metin_ozet(s: pd.Series) -> tuple[str, list[str]]:
    dolu = s.dropna().astype(str)
    if dolu.empty:
        return "tümü boş", []
    uzunluk = dolu.str.len()
    return (
        f"serbest metin, ort. {uzunluk.mean():.0f} / maks {uzunluk.max()} karakter; "
        f"ör. {_kisa(dolu.iloc[0], 40)}"
    ), []


def _dtype_etiketi(s: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    if isinstance(s.dtype, pd.CategoricalDtype):
        return "category"
    if pd.api.types.is_bool_dtype(s):
        return "bool"
    if pd.api.types.is_integer_dtype(s):
        return "int"
    if pd.api.types.is_float_dtype(s):
        return "float"
    return "text"


# ── ana giriş ───────────────────────────────────────────────────


def build_schema_card(
    df: pd.DataFrame,
    *,
    name: str,
    origin: str = "",
    location: str = "",
    sampled: bool = False,
    total_rows: int | None = None,
    extra_notes: list[str] | None = None,
) -> str:
    """Bir veri setinin modele gösterilecek metin özeti."""
    satir = int(total_rows if total_rows is not None else len(df))
    kolon = int(df.shape[1])

    baslik = f"{name} — {_tr_sayi(satir)} satır × {kolon} kolon"
    if origin:
        baslik += f"   (kaynak: {origin})"

    satirlar = [baslik]
    if location:
        # Yolu doğrudan yazdırmıyoruz: Docker'da /data, yerel yedekte
        # host klasörü. data_path() iki modda da doğru yolu verir.
        dosya = location.rsplit("/", 1)[-1]
        satirlar.append(f"  oku: pd.read_parquet(data_path('{dosya}'))")
    if sampled:
        satirlar.append(
            f"  ⚠ profil {_tr_sayi(len(df))} satırlık ÖRNEKLEM üzerinden çıkarıldı. "
            "Kesin sonuç için tam dosyayı oku."
        )
    satirlar.append("")

    uyarilar: list[str] = list(extra_notes or [])
    gosterilecek = list(df.columns[:MAX_KOLON_DETAY])

    for kol in gosterilecek:
        s = df[kol]
        tip = _dtype_etiketi(s)
        bos_oran = float(s.isna().mean())

        if tip == "int" and _kimlik_gibi(s, kol):
            ozet, ek = _kimlik_ozet(s)
        elif tip in ("int", "float"):
            ozet, ek = _sayisal_ozet(s)
        elif tip == "datetime":
            ozet, ek = _tarih_ozet(s)
        elif tip in ("category", "bool"):
            ozet, ek = _kategorik_ozet(s, satir)
        else:
            tekil = int(s.nunique(dropna=True))
            if tekil and satir and tekil <= max(20, satir * 0.05):
                ozet, ek = _kategorik_ozet(s, satir)
            else:
                ozet, ek = _metin_ozet(s)

        satirlar.append(f"  {str(kol):<22} {tip:<9} boş %{bos_oran * 100:.1f}   {ozet}")
        uyarilar += [f"`{kol}`: {u}" for u in ek]

        if bos_oran >= YUKSEK_NULL_ESIGI:
            uyarilar.append(f"`{kol}` kolonunun %{bos_oran * 100:.0f}'i boş")

    if len(df.columns) > MAX_KOLON_DETAY:
        kalan = len(df.columns) - MAX_KOLON_DETAY
        satirlar.append(f"  … ve {kalan} kolon daha (detay için veriyi kendin incele)")

    # Örnek satırlar
    satirlar.append("")
    satirlar.append(f"  İlk {min(ORNEK_SATIR, len(df))} satır:")
    with pd.option_context("display.max_columns", 20, "display.width", 200):
        onizleme = df.head(ORNEK_SATIR).to_string(index=False, max_colwidth=24)
    satirlar += [f"    {r}" for r in onizleme.splitlines()]

    if uyarilar:
        satirlar.append("")
        satirlar.append("  ⚠ Uyarılar:")
        satirlar += [f"    - {u}" for u in dict.fromkeys(uyarilar)]  # tekrarı at

    return "\n".join(satirlar)


def build_summary(df: pd.DataFrame, origin: str = "") -> str:
    """Katalog listesinde görünen tek satırlık özet."""
    tarih_kolonlari = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    if tarih_kolonlari:
        s = df[tarih_kolonlari[0]].dropna()
        if not s.empty:
            return f"{s.min():%Y-%m} – {s.max():%Y-%m} aralığında"
    if origin:
        return origin
    return ", ".join(str(c) for c in df.columns[:4])


# ── ilişki adayları ─────────────────────────────────────────────


def find_relations(
    frames: dict[str, pd.DataFrame], *, ornek: int = 10_000, esik: float = 0.9
) -> list[tuple[str, str, str]]:
    """Tablolar arası join adaylarını bulur.

    İki sinyalin kesişimi: kolon adı benzerliği ve değer örtüşmesi.
    Join'i modele tahmin ettirme — en sık hata kaynağı budur.

    Döner: (sol_dataset, sağ_dataset, açıklama) üçlüleri. Dataset adları
    ayrı veriliyor ki her şema kartına sadece kendisini ilgilendiren
    ilişkiler yazılabilsin.
    """
    adaylar: list[tuple[str, str, str]] = []
    isimler = list(frames)

    def anahtar_gibi(kol: str) -> bool:
        k = kol.lower()
        return k.endswith(("_id", "_no", "_kod", "id", "no", "kod"))

    for i, sol_ad in enumerate(isimler):
        for sag_ad in isimler[i + 1 :]:
            sol, sag = frames[sol_ad], frames[sag_ad]
            for sk in sol.columns:
                if not anahtar_gibi(str(sk)):
                    continue
                for gk in sag.columns:
                    if not anahtar_gibi(str(gk)):
                        continue
                    try:
                        sol_d = sol[sk].dropna().head(ornek)
                        sag_d = sag[gk].dropna().head(ornek)
                        a, b = set(sol_d), set(sag_d)
                    except TypeError:  # hashlenemeyen değerler
                        continue

                    if len(a) < 3 or len(b) < 3:
                        continue

                    # Gerçek bir PK/FK ilişkisinde bir taraf ANAHTARDIR:
                    # değerleri neredeyse tekildir. Bu şart olmadan iki
                    # `durum_kod` kolonu {1,2} değerleriyle %100 örtüşür ve
                    # modele uydurma bir join önerilir.
                    sol_tekil = len(a) / max(len(sol_d), 1)
                    sag_tekil = len(b) / max(len(sag_d), 1)
                    if max(sol_tekil, sag_tekil) < 0.95:
                        continue

                    ortusme = len(a & b) / min(len(a), len(b))
                    if ortusme >= esik:
                        adaylar.append((
                            sol_ad,
                            sag_ad,
                            f"{sol_ad}.{sk} ⟷ {sag_ad}.{gk}  "
                            f"(örneklemde %{ortusme * 100:.1f} değer örtüşmesi)",
                        ))
    return adaylar
