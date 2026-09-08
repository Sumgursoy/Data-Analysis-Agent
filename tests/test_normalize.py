"""normalize.py testleri — Türkçe veri tuzakları."""

import pandas as pd

from backend.ingest import normalize as N

gecti = basarisiz = 0


def esit(baslik, alinan, beklenen):
    global gecti, basarisiz
    ok = alinan == beklenen
    if ok:
        gecti += 1
    else:
        basarisiz += 1
        print(f"  [X] {baslik}\n      beklenen: {beklenen!r}\n      alinan  : {alinan!r}")


print("── kolon adlari " + "─" * 45)
for ham, bek in [
    ("Müşteri No ", "musteri_no"),
    ("TOPLAM TUTAR(₺)", "toplam_tutar_tl"),
    ("MÜŞTERİ_NO", "musteri_no"),
    ("Sipariş Tarihi", "siparis_tarihi"),
    ("2024 Yılı", "c2024_yili"),
    ("Kâr %", "kar_yuzde"),
    ("ÇOĞUNLUK", "cogunluk"),
    ("Unnamed: 3", "kolon"),
    ("  ", "kolon"),
]:
    esit(f"slug({ham!r})", N.slug(ham), bek)

esit("uniquify", N.uniquify(["a", "a", "b", "a"]), ["a", "a_2", "b", "a_3"])

print("\n── sayilar: Turkce format " + "─" * 35)
s = pd.Series(["1.234,56", "999,00", "1.000.000,25", "12,5", "-45,75"])
r = N.to_numeric(s)
esit("TR ondalik virgul", None if r is None else [round(v, 2) for v in r.tolist()],
     [1234.56, 999.0, 1000000.25, 12.5, -45.75])

print("\n── sayilar: EN format " + "─" * 39)
s = pd.Series(["1,234.56", "999.00", "1,000,000.25"])
r = N.to_numeric(s)
esit("EN ondalik nokta", None if r is None else [round(v, 2) for v in r.tolist()],
     [1234.56, 999.0, 1000000.25])

print("\n── sayilar: para, yuzde, muhasebe " + "─" * 28)
s = pd.Series(["1.234,56 ₺", "2.000,00 TL", "(1.250,00)", "%12,5", "-"])
r = N.to_numeric(s)
esit("para/yuzde/negatif",
     None if r is None else [None if pd.isna(v) else round(v, 2) for v in r.tolist()],
     [1234.56, 2000.0, -1250.0, 12.5, None])

print("\n── sayi olmayan kolon bozulmamali " + "─" * 27)
esit("metin -> None", N.to_numeric(pd.Series(["Ege", "Marmara", "Akdeniz"])), None)

print("\n── tarihler " + "─" * 49)
r, notu = N.to_datetime(pd.Series(["31.12.2024", "01.02.2024", "15.06.2023"]))
esit("gg.aa.yyyy (31 > 12 → gun once)",
     None if r is None else [d.strftime("%Y-%m-%d") for d in r],
     ["2024-12-31", "2024-02-01", "2023-06-15"])
esit("belirsizlik notu yok", notu, None)

r, notu = N.to_datetime(pd.Series(["05.06.2024", "03.04.2024"]))
esit("belirsiz → gun once varsayimi",
     None if r is None else [d.strftime("%Y-%m-%d") for d in r],
     ["2024-06-05", "2024-04-03"])
esit("belirsizlik notu var", notu is not None, True)

r, _ = N.to_datetime(pd.Series(["2024-12-31", "2024-02-01"]))
esit("ISO tarih", None if r is None else [d.strftime("%Y-%m-%d") for d in r],
     ["2024-12-31", "2024-02-01"])

print("\n── tum tablo " + "─" * 48)
df = pd.DataFrame({
    "Müşteri No": ["1001", "1002", "1003"],
    "Sipariş Tarihi": ["31.12.2024", "15.01.2025", "01.02.2025"],
    "TUTAR(₺)": ["1.234,56", "2.000,00", "(500,00)"],
    "Bölge": ["Ege", "Marmara", "Ege"],
    "Bos Kolon": [None, None, None],
})
tmz, notlar = N.normalize_frame(df)
esit("kolon adlari", list(tmz.columns),
     ["musteri_no", "siparis_tarihi", "tutar_tl", "bolge"])
esit("tutar sayisal", str(tmz["tutar_tl"].dtype).startswith("float"), True)
esit("tutar toplami", round(float(tmz["tutar_tl"].sum()), 2), 2734.56)
esit("tarih tipi", str(tmz["siparis_tarihi"].dtype).startswith("datetime"), True)
esit("bolge kategori", str(tmz["bolge"].dtype), "category")
esit("bos kolon atildi", "bos_kolon" in tmz.columns, False)
print("  notlar:", notlar)

print(f"\n{'=' * 60}\nGECTI: {gecti}   BASARISIZ: {basarisiz}")
