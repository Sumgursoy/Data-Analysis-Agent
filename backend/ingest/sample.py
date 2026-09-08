"""Demo için hazır örnek veri.

Neden var: jüri kendi dosyasını getirmeyebilir ya da internet/USB derdi
çıkabilir. Tek tıkla gerçekçi bir veri seti üretip demoyu kurtarır.

Veri kasten "temiz değil" — boş değerler, iade (negatif tutar), aykırı
değerler ve tarih boşlukları içeriyor. Böylece şema kartındaki uyarılar
ve agent'ın bunları fark etmesi demoda görülebiliyor.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BOLGELER = ["Marmara", "Ege", "İç Anadolu", "Akdeniz", "Karadeniz", "Güneydoğu"]
BOLGE_AGIRLIK = [0.34, 0.22, 0.18, 0.13, 0.08, 0.05]

KANALLAR = ["Mağaza", "Online", "Bayi", "Kurumsal"]
DURUMLAR = ["tamamlandı", "iptal", "beklemede"]
DURUM_AGIRLIK = [0.87, 0.09, 0.04]

AD_ONEK = ["Yılmaz", "Öztürk", "Şahin", "Çelik", "Güneş", "Kaya", "Demir",
           "Aydın", "Koç", "Arslan", "Doğan", "Çetin"]
AD_SONEK = ["Ltd. Şti.", "A.Ş.", "Ticaret", "San. ve Tic.", "Group", "Holding"]
SEGMENTLER = ["kurumsal", "kobi", "bireysel"]


def uret(satir: int = 6000, musteri: int = 240, tohum: int = 7):
    """(satislar, musteriler) çifti üretir."""
    rng = np.random.default_rng(tohum)

    # ── müşteriler ────────────────────────────────────────────
    musteri_id = np.arange(10_001, 10_001 + musteri)
    musteriler = pd.DataFrame({
        "musteri_no": musteri_id,
        "unvan": [
            f"{AD_ONEK[rng.integers(len(AD_ONEK))]} "
            f"{AD_SONEK[rng.integers(len(AD_SONEK))]}"
            for _ in range(musteri)
        ],
        "segment": rng.choice(SEGMENTLER, musteri, p=[0.25, 0.5, 0.25]),
        "sehir": rng.choice(BOLGELER, musteri, p=BOLGE_AGIRLIK),
        "kayit_tarihi": pd.to_datetime("2019-01-01")
        + pd.to_timedelta(rng.integers(0, 2000, musteri), unit="D"),
    })

    # ── satışlar ──────────────────────────────────────────────
    # Tarihler 2023-2024; 2024-06 kasten boş bırakılıyor ki şema kartındaki
    # "şu ayda hiç kayıt yok" uyarısı demoda görünsün.
    gunler = pd.date_range("2023-01-01", "2024-12-31", freq="D")
    gunler = gunler[~((gunler.year == 2024) & (gunler.month == 6))]
    tarihler = rng.choice(gunler, satir)

    bolge = rng.choice(BOLGELER, satir, p=BOLGE_AGIRLIK)

    # Sağa çarpık tutar dağılımı — gerçek ciro verisi böyle davranır.
    taban = rng.lognormal(mean=6.9, sigma=0.85, size=satir)
    bolge_carpani = {b: c for b, c in zip(BOLGELER, [1.35, 1.1, 1.0, 0.95, 0.8, 0.7])}
    tutar = taban * np.array([bolge_carpani[b] for b in bolge])

    # Aykırı değerler (büyük kurumsal siparişler)
    aykiri = rng.choice(satir, size=max(1, satir // 300), replace=False)
    tutar[aykiri] *= rng.uniform(8, 20, len(aykiri))

    durum = rng.choice(DURUMLAR, satir, p=DURUM_AGIRLIK)

    # İadeler negatif tutar olarak duruyor — agent bunu fark etmeli.
    iade = rng.random(satir) < 0.02
    tutar[iade] *= -1

    satislar = pd.DataFrame({
        "siparis_no": np.arange(500_001, 500_001 + satir),
        "siparis_tarihi": tarihler,
        "musteri_no": rng.choice(musteri_id, satir),
        "bolge": bolge,
        "kanal": rng.choice(KANALLAR, satir, p=[0.4, 0.33, 0.19, 0.08]),
        "adet": rng.integers(1, 25, satir),
        "tutar": np.round(tutar, 2),
        "durum": durum,
        "aciklama": None,
    }).sort_values("siparis_tarihi").reset_index(drop=True)

    # Serbest metin kolonu çoğunlukla boş — "%X'i boş" uyarısını tetikler.
    notlar = rng.random(satir) < 0.18
    satislar.loc[notlar, "aciklama"] = rng.choice(
        ["acil teslimat", "müşteri talebi", "kampanya", "sözleşmeli fiyat"],
        int(notlar.sum()),
    )

    # Kanal kolonunda az sayıda eksik değer
    bos = rng.random(satir) < 0.03
    satislar.loc[bos, "kanal"] = None

    return satislar, musteriler
