"""PDF tablo adaptörü.

Sürpriz kaynak: kurumsal raporlar sık sık PDF gelir. `pdfplumber` seçildi
çünkü saf Python — `camelot` daha güçlü ama Ghostscript kurulumu gerektiriyor
ve demo günü kurulum bağımlılığı risktir.

TASARIM: "üret → ele → puanla → seç"

Eskiden stratejiler SIRAYLA deneniyor, ilk sonuç üreten kazanıyordu. Gerçek
bir kurumsal PDF'te (TBB banka raporu) bu şunu yaptı: çizgi stratejisi 21
satırlık bir tabloyu 6 hücreye tıkıştırıp 3 satır olarak döndürdü, eşiği
geçtiği için `return` edildi ve 46 satırlık doğru okuma HİÇ denenmedi.

Şimdi her strateji çalıştırılıp iki ayrı süzgeçten geçiyor:

  kalite kapısı  İKİLİ  — "bu gerçek bir veri tablosu mu?"  (mutlak eşik)
  puanlama       SÜREKLİ — "aynı sayfanın hangi okuması temiz?" (göreli)

Bu ayrım şart: "sayfa 7'de metin stratejisi kazansın" ile "içindekiler
sayfası elensin" farklı sorulardır. Tek skorda birleştirilince çelişiyorlar,
ayrı tutulunca çelişki kayboluyor — puan yalnızca kapıyı geçenler arasında
kullanılır.

Tablo çıkarılamayan sayfalar KAYBOLMAZ: metinleri `kind="raw"` bir veri seti
olarak kataloğa girer (aşağıda `_metin_kanali`). Kurumsal raporlarda cevap
çoğu zaman tabloda değil, anlatı cümlesindedir.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backend.ingest.adapters.base import Extracted, UnsupportedSource
from backend.ingest.normalize import find_header_row, sayi_gibi

log = logging.getLogger(__name__)

MAX_SAYFA = 50

# ── Stratejiler ─────────────────────────────────────────────────
#
# "cizgi" artık varsayılan/güvenilir değil — ÖLÇÜLDÜ: tests/veri/rapor.pdf'te
# hiçbir şey üretmiyor, TBB raporunun 6 tablo sayfasında ise kapıyı geçemeyen
# tıkışmış çöp üretiyor. Yine de kümede: çerçeveli tablolarda en temizi odur.
#
# "metin_seyrek": bir dikey kolon kenarının kabul edilmesi için o hizada en az
# SEYREK_KOLON_ESIGI kelime bulunmalı. Gerçek kolonlar 20-40 satır boyunca
# hizalı, sahte kenarlar (başlıktaki kelime aralıkları) 2-5 kelimelik.
# Ölçülen etki: hayalet kolonlar eleniyor (17→15, 23→22, 24→21), değerler yerinde.
#
# "metin" KÜMEDEN ÇIKARILMAMALI: kısa tablolarda (rapor.pdf 4 satır)
# min_words_vertical=12 hiç kolon kenarı üretemez ve tabloyu yok eder.
SEYREK_KOLON_ESIGI = 12

STRATEJILER: list[tuple[str, dict | None]] = [
    ("cizgi", None),
    ("metin", {"vertical_strategy": "text", "horizontal_strategy": "text"}),
    ("metin_seyrek", {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "min_words_vertical": SEYREK_KOLON_ESIGI,
    }),
]

# ── Kalite kapısı eşikleri ──────────────────────────────────────
#
# Hepsi gerçek PDF'in 11 sayfası + test fixture'ı üzerinde ölçüldü.
# En güçlü ayırt edici SAYISAL KOLON SAYISI; ölçüm:
#
#   sayfa 1-5 (içindekiler, grafik etiketleri, anlatı) →  0
#   sayfa 6-11 (gerçek tablolar)                       →  4, 14, 14, 21, 20, 6
#   tests/veri/rapor.pdf                               →  1
#
# Uçurum mutlak. Sebebi de sağlam: anlatı metni parçalandığında sayılar
# (grafik ekseni etiketleri) sayfaya SAÇILIR, bir kolonda dikey dizilmez.
MIN_KOLON = 2
MIN_VERI_SATIRI = 2
MIN_SAYISAL_KOLON = 1
MIN_SAYISAL_KOLON_ORANI = 0.20   # en düşük "tut" 0.33, en yüksek "reddet" 0.00
MAX_PARCALANMA = 0.30            # en kötü gerçek tablo 0.10, çöp 1.00'a çıkıyor
SAYISAL_KOLON_SAFLIK = 0.6       # kolonun sayısal sayılması için gereken oran
MIN_KOLON_DOLU_HUCRE = 3         # bu kadar dolu hücresi olmayan kolon sayılmaz

# `doluluk` bilerek kapıda YOK: sayfa 6 (gerçek veri) 0.27, sayfa 3 (çöp) 0.33
# — ayırmıyor. Sadece puanda ödül olarak duruyor.

# ── Puan ağırlıkları ────────────────────────────────────────────
AGIRLIK_SAYISAL_KOLON = 3.0
AGIRLIK_DOLULUK = 2.0
AGIRLIK_HACIM = 1.5
CEZA_PARCALANMA = 4.0   # ödüllerin en büyüğünden BÜYÜK: \n dolu hücre kesin hata
CEZA_BITISIKLIK = 3.0

# Tek hücrede birden fazla sayı: "69.354 83.4" gibi. Kolon ayrımının sayının
# ortasından geçtiğinin işareti.
_COKLU_SAYI = re.compile(r"\d[\d.,]*\s+[\d(]")

# ── Çok sayfalı tablo birleştirme ───────────────────────────────
#
# Kurumsal tablolar sayfaya sığmayınca devam eder (TBB'de Tablo 2 = s7+s8,
# Tablo 3 = s9+s10). Ayrı veri seti olarak kalırlarsa agent yarım tablo
# üstünde hesap yapar.
IMZA_TOLERANS_PT = 4.0        # aynı kolon kenarı sayılma toleransı (punto)
IMZA_BENZERLIK_ESIGI = 0.70   # ölçülen: en düşük "evet" 0.87, en yüksek "hayır" 0.26
MAX_KOLON_FARKI = 2

_TABLO_BASLIGI = re.compile(r"\bTablo\s+(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Olcum:
    satir: int
    kolon: int
    doluluk: float
    parcalanma: float
    bitisiklik: float
    sayisal_kolon: int
    kullanilan_kolon: int
    sayisal_kolon_orani: float
    veri_satiri: int


@dataclass
class Aday:
    sayfa_no: int
    strateji: str
    izgara: list[list[str | None]]
    olcum: Olcum
    puan: float
    tablo: object = None  # pdfplumber Table — başlık onarımı için geometri lazım
    sayfa: object = None  # pdfplumber Page — sadece pdf açıkken geçerli


# ── 1) Ölçme ve karar ───────────────────────────────────────────


def _hucre(v: object) -> str:
    return "" if v is None else str(v).strip()


def _olc(izgara: list[list]) -> Olcum:
    satir = len(izgara)
    kolon = max((len(r) for r in izgara), default=0)
    if not satir or not kolon:
        return Olcum(satir, kolon, 0.0, 0.0, 0.0, 0, 0, 0.0, 0)

    dolular = [_hucre(c) for r in izgara for c in r if _hucre(c)]
    n = len(dolular)

    doluluk = n / (satir * kolon)
    parcalanma = sum(1 for c in dolular if "\n" in c) / n if n else 0.0
    bitisiklik = sum(1 for c in dolular if _COKLU_SAYI.search(c)) / n if n else 0.0

    sayisal_kolon = kullanilan = 0
    for j in range(kolon):
        sutun = [_hucre(r[j]) for r in izgara if j < len(r) and _hucre(r[j])]
        if len(sutun) < MIN_KOLON_DOLU_HUCRE:
            continue
        kullanilan += 1
        if sum(1 for v in sutun if sayi_gibi(v)) / len(sutun) >= SAYISAL_KOLON_SAFLIK:
            sayisal_kolon += 1

    # Veri satırı: en az 2 dolu VE en az 1 sayısal hücre.
    # "≥2 sayısal" demek fixture'ı öldürüyordu (her satırda tek sayı var).
    veri_satiri = 0
    for r in izgara:
        d = [_hucre(c) for c in r if _hucre(c)]
        if len(d) >= 2 and any(sayi_gibi(v) for v in d):
            veri_satiri += 1

    return Olcum(
        satir=satir,
        kolon=kolon,
        doluluk=doluluk,
        parcalanma=parcalanma,
        bitisiklik=bitisiklik,
        sayisal_kolon=sayisal_kolon,
        kullanilan_kolon=kullanilan,
        sayisal_kolon_orani=(sayisal_kolon / kullanilan) if kullanilan else 0.0,
        veri_satiri=veri_satiri,
    )


def _puanla(o: Olcum) -> float:
    """Aynı sayfanın farklı okumaları arasında sıralama puanı.

    NEDEN göreli: bu puan sayfalar arasında KARŞILAŞTIRILMAZ. Elemeyi puan
    değil `_kalite_kapisi` yapar; puan sadece kapıyı geçenler arasında seçer.
    """
    hacim = math.log10(o.veri_satiri + 1)  # 133 satır 34'ü sırf uzunlukla ezmesin
    return (
        AGIRLIK_SAYISAL_KOLON * o.sayisal_kolon_orani
        + AGIRLIK_DOLULUK * o.doluluk
        + AGIRLIK_HACIM * hacim
        - CEZA_PARCALANMA * o.parcalanma
        - CEZA_BITISIKLIK * o.bitisiklik
    )


def _kalite_kapisi(o: Olcum) -> tuple[bool, str]:
    """Bu aday gerçek bir veri tablosu mu? Döner: (karar, gerekçe).

    Gerekçe, reddedilen sayfanın metin kaydına yazılır. Sessizce veri
    düşürmek, yanlış veri kadar kötüdür.
    """
    if o.kolon < MIN_KOLON:
        return False, f"{o.kolon} kolon (en az {MIN_KOLON} gerekiyor)"
    if o.veri_satiri < MIN_VERI_SATIRI:
        return False, f"{o.veri_satiri} veri satırı (en az {MIN_VERI_SATIRI})"
    if o.sayisal_kolon < MIN_SAYISAL_KOLON:
        return False, "hiç sayısal kolon yok — tablo değil anlatı metni"
    if o.sayisal_kolon_orani < MIN_SAYISAL_KOLON_ORANI:
        return False, (
            f"kolonların sadece %{o.sayisal_kolon_orani * 100:.0f}'i sayısal"
        )
    if o.parcalanma > MAX_PARCALANMA:
        return False, (
            f"hücrelerin %{o.parcalanma * 100:.0f}'i satır içi kırılma içeriyor "
            "— tablo tek hücreye çökmüş"
        )
    return True, ""


# ── 2) Sayfadan aday üretme ─────────────────────────────────────


def _sayfa_adaylari(sayfa, sayfa_no: int) -> tuple[list[Aday], list[str]]:
    """Bir sayfanın tüm strateji × tablo adayları. Döner: (geçenler, gerekçeler)."""
    gecenler: list[Aday] = []
    gerekceler: list[str] = []

    for ad, ayar in STRATEJILER:
        try:
            # extract_tables değil find_tables: dönen Table nesnesinin satır ve
            # kolon bbox'ları başlık onarımı için gerekiyor (_basliklari_onar).
            tablolar = sayfa.find_tables(ayar) if ayar else sayfa.find_tables()
        except Exception as e:  # bozuk sayfa tüm PDF'i düşürmesin
            log.debug("sayfa %d strateji %s patladı: %s", sayfa_no, ad, e)
            continue

        for tablo in tablolar or []:
            try:
                izgara = tablo.extract()
            except Exception:
                continue
            if not izgara:
                continue
            olcum = _olc(izgara)
            tamam, gerekce = _kalite_kapisi(olcum)
            if tamam:
                gecenler.append(
                    Aday(sayfa_no, ad, izgara, olcum, _puanla(olcum), tablo, sayfa)
                )
            elif gerekce:
                gerekceler.append(f"{ad}: {gerekce}")

    return gecenler, gerekceler


def _en_iyi_adaylar(gecenler: list[Aday]) -> list[Aday]:
    """Kazanan stratejinin TÜM tablolarını döndürür.

    Aday birimi tablo değil strateji: aksi halde aynı içerik iki farklı
    stratejiden iki kez kataloğa girebilir.
    """
    if not gecenler:
        return []

    en_iyi_strateji = max(
        {a.strateji for a in gecenler},
        key=lambda s: max(a.puan for a in gecenler if a.strateji == s),
    )
    return [a for a in gecenler if a.strateji == en_iyi_strateji]


# ── 3) Çerçeveye çevirme ────────────────────────────────────────


def _temiz_cerceve(izgara: list[list]) -> tuple[pd.DataFrame, list[int], list[int]]:
    """Ham ızgara → boş satır/kolonları atılmış DataFrame.

    Döner: (df, hayatta kalan ham satır indeksleri, ham kolon indeksleri).
    İndeksler başlık onarımında geometriye geri eşlemek için lazım.

    ŞART: boş satırlar başlık tespitinden ÖNCE atılmalı. pdfplumber metin
    stratejisinde satırları aralayarak veriyor (114 satırın yarısı boş);
    `_baslik_puani`'nın "altındaki 10 satır sayısal mı" penceresi boşlarla
    dolarsa sinyal ölür ve başlık bulunamaz.
    """
    df = pd.DataFrame(
        [[(_hucre(c).replace("\n", " ") or None) for c in r] for r in izgara],
        dtype=object,
    )
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    satir_idx = [int(i) for i in df.index]
    kolon_idx = [int(c) for c in df.columns]
    return df.reset_index(drop=True), satir_idx, kolon_idx


def _basliklari_onar(
    sayfa, tablo, ham_satir: int, kolon_idx: list[int]
) -> list[str] | None:
    """Başlık satırını kelime geometrisinden yeniden kurar.

    NEDEN GEREKLİ: metin stratejisi kolonları karakter hizasından kestiği için
    başlık hücreleri kelime ortasından bölünüyor — 'Banka Erke' | 'k Kad' |
    'ın Topl'. `slug()` bunlardan `banka_erke, k_kad, in_topl` üretiyor ve
    şema kartı kullanılamaz hâle geliyor.

    ÇÖZÜM: hücre metnine hiç bakma. Başlık satırının y bandındaki kelimeleri
    `extract_words()` ile al, her kelimeyi MERKEZİNE göre bir kolona ata.
    Kelime sınırları böylece korunuyor: 'Erkek', 'Kadın', 'Toplam'.

    Başarısız olursa None döner — çağıran hücre metnine geri düşer.
    """
    try:
        satirlar, kolonlar = tablo.rows, tablo.columns
        if ham_satir >= len(satirlar):
            return None
        _, ust, _, alt = satirlar[ham_satir].bbox
        kutular = [kolonlar[j].bbox for j in kolon_idx if j < len(kolonlar)]
        if len(kutular) != len(kolon_idx):
            return None

        parcalar: list[list[str]] = [[] for _ in kutular]
        for k in sayfa.extract_words():
            orta_y = (k["top"] + k["bottom"]) / 2
            if not (ust <= orta_y <= alt):
                continue
            orta_x = (k["x0"] + k["x1"]) / 2
            for i, (kx0, _, kx1, _) in enumerate(kutular):
                if kx0 <= orta_x <= kx1:
                    parcalar[i].append(k["text"])
                    break

        adlar = [" ".join(p).strip() for p in parcalar]
        if not any(adlar):
            return None
        return [ad or f"kolon_{i + 1}" for i, ad in enumerate(adlar)]
    except Exception as e:  # geometri yoksa/bozuksa sessizce geri düş
        log.debug("başlık onarımı yapılamadı: %s", e)
        return None


def _cerceveye_cevir(sayfa, aday: Aday) -> tuple[pd.DataFrame, list[str]] | None:
    """Adayı (gövde DataFrame, notlar) hâline getirir. Kullanılamazsa None."""
    df, satir_idx, kolon_idx = _temiz_cerceve(aday.izgara)
    if df.empty or df.shape[1] < MIN_KOLON:
        return None

    baslik_r = find_header_row(df)

    onarilan = None
    if aday.tablo is not None and baslik_r < len(satir_idx):
        onarilan = _basliklari_onar(sayfa, aday.tablo, satir_idx[baslik_r], kolon_idx)

    basliklar = onarilan or [
        _hucre(v) or f"kolon_{i + 1}" for i, v in enumerate(df.iloc[baslik_r])
    ]

    # Başlık DÂHİL üstündeki her şey atılır. Üsttekiler doküman başlığı /
    # açıklama satırlarıdır; bugüne kadar gövdeye karışıyorlardı.
    govde = df.iloc[baslik_r + 1 :].reset_index(drop=True)
    if govde.empty:
        return None

    govde.columns = basliklar

    notlar: list[str] = []
    if baslik_r > 0:
        notlar.append(
            f"başlık {baslik_r + 1}. satırda bulundu · üstündeki {baslik_r} satır "
            "(doküman başlığı/açıklama) ayrıldı"
        )

    # ÖLÇÜLMÜŞ uyarı. Eskiden her PDF tablosuna aynı genel uyarı basılıyordu,
    # bu yüzden kimse ciddiye almıyordu. Artık sadece gerçekten bozuk
    # tablolarda ve oranıyla birlikte çıkıyor.
    if aday.olcum.bitisiklik > 0:
        notlar.append(
            f"⚠ hücrelerin %{aday.olcum.bitisiklik * 100:.0f}'inde birden fazla "
            "sayı var — kolon ayrımı bu tabloda sayının ortasından geçmiş "
            "olabilir, sayıları PDF'ten doğrula"
        )

    return govde, notlar


# ── 4) Metin kanalı ─────────────────────────────────────────────


def _metin_kanali(
    sayfa_metinleri: list[tuple[int, str]],
    retler: list[tuple[int, str]],
    name: str,
    path: Path,
    toplam_sayfa: int,
) -> Extracted | None:
    """Tablo çıkarılamayan sayfaların düz metni.

    NEDEN tablo olarak modelleniyor: `read_pdf`'in dönüş tipini değiştirmek
    `router.extract()`'in imzasını, o da 7 formatın sözleşmesini bozardı.
    Metni (sayfa, satır_no, metin) tablosu yapınca mevcut hattan hiç
    değişiklik istemeden geçiyor — üstelik agent için .txt'ten daha iyi:
    `sayfa` kolonuyla filtreleyip pandas/DuckDB ile arayabiliyor.
    """
    if not sayfa_metinleri:
        return None

    sayfalar, satir_nolari, metinler = [], [], []
    for sayfa_no, metin in sayfa_metinleri:
        for i, satir in enumerate(metin.splitlines(), 1):
            if satir.strip():
                sayfalar.append(sayfa_no)
                satir_nolari.append(i)
                metinler.append(satir.strip())

    if not metinler:
        return None

    notlar = [
        "Bu veri seti TABLO DEĞİL: tablo olarak okunamayan sayfaların düz metni.",
        "→ Sayı/hesap için tablo veri setlerini kullan; tanım, dipnot, oran "
        "cümlesi ve bağlam için burayı ara.",
    ]
    if retler:
        ozet = " · ".join(f"s{no}: {sebep}" for no, sebep in retler[:6])
        notlar.append(f"Tablo çıkarılamayan sayfalar — {ozet}")

    return Extracted(
        name=f"{name}_metin",
        df=pd.DataFrame(
            {"sayfa": sayfalar, "satir_no": satir_nolari, "metin": metinler}
        ),
        origin=f"{path.name} → tablo çıkarılamayan sayfaların metni",
        kind="raw",
        notes=notlar,
    )


# ── 5) Çok sayfalı tablo birleştirme ────────────────────────────


def _kolon_kenarlari(tablo) -> list[float]:
    """Kolonların x kenarları — sayfa imzası."""
    try:
        return [k.bbox[0] for k in tablo.columns] + [tablo.columns[-1].bbox[2]]
    except Exception:
        return []


def _imza_benzerligi(a: list[float], b: list[float]) -> float:
    """İki sayfanın kolon kenarlarının örtüşme oranı.

    NEDEN kolon SAYISI değil: aynı tablonun iki sayfası pdfplumber'da 15 ve
    16 kolon çıkabiliyor (bir sahte kenar) — sayıya bakan hiçbir kural
    eşleştiremez. Fiziksel x konumları ise noktası noktasına aynı, çünkü
    sayfa yerleşimi değişmiyor.

    Ölçülen (TBB): s7↔s8 = 0.94 · s9↔s10 = 0.87 · s8↔s9 = 0.26 · s10↔s11 = 0.05
    """
    if not a or not b:
        return 0.0
    eslesen = sum(1 for x in a if any(abs(x - y) <= IMZA_TOLERANS_PT for y in b))
    return eslesen / max(len(a), len(b))


def _bloklara_ayir(kabuller: list[Aday]) -> list[list[Aday]]:
    """Ardışık sayfaları aynı tablonun parçaları olarak grupla.

    Üç şart BİRDEN aranır. Ardışıklık şart: uzak sayfalardaki farklı iki
    tablo kurumsal şablon yüzünden tesadüfen aynı yerleşimi kullanabilir,
    onları birleştirmek veri uydurmaktır.
    """
    bloklar: list[list[Aday]] = []
    for aday in kabuller:
        if bloklar:
            onceki = bloklar[-1][-1]
            benzerlik = _imza_benzerligi(
                _kolon_kenarlari(onceki.tablo), _kolon_kenarlari(aday.tablo)
            )
            ardisik = aday.sayfa_no == onceki.sayfa_no + 1
            kolon_yakin = abs(aday.olcum.kolon - onceki.olcum.kolon) <= MAX_KOLON_FARKI
            if ardisik and kolon_yakin and benzerlik >= IMZA_BENZERLIK_ESIGI:
                bloklar[-1].append(aday)
                continue
        bloklar.append([aday])
    return bloklar


def _blogu_birlestir(blok: list[Aday]) -> tuple[pd.DataFrame, list[str]] | None:
    """Bloğun sayfalarını tek DataFrame'e indirger.

    Devam sayfalarının başlık satırı (ve üstü) her sayfada ayrı ayrı atılır —
    devam sayfaları başlık bandını tekrarlıyor. Kolon adları bloğun İLK
    sayfasından alınır; orada başlık bandı daha zengin oluyor.
    """
    parcalar: list[pd.DataFrame] = []
    notlar: list[str] = []
    basliklar: list[str] | None = None

    for aday in blok:
        sonuc = _cerceveye_cevir(aday.sayfa, aday)
        if sonuc is None:
            continue
        govde, sayfa_notlari = sonuc
        if basliklar is None:
            basliklar = [str(k) for k in govde.columns]
        parcalar.append(govde)
        notlar += [f"s{aday.sayfa_no}: {n}" for n in sayfa_notlari]

    if not parcalar or basliklar is None:
        return None

    # KONUMA göre birleştir, etikete göre DEĞİL: kolon adları tekrarlı olabilir
    # ("Erkek, Kadın, Toplam" her eğitim grubu için tekrarlıyor) ve pandas
    # tekrarlı etiketle concat edemiyor (InvalidIndexError).
    genislik = min(p.shape[1] for p in parcalar)
    if any(p.shape[1] != genislik for p in parcalar):
        notlar.append(
            f"⚠ sayfalar arası kolon sayısı farklı, ortak {genislik} kolona "
            "kırpıldı — sayıları doğrula"
        )

    duz = [
        p.iloc[:, :genislik].set_axis(range(genislik), axis=1) for p in parcalar
    ]
    birlesik = pd.concat(duz, ignore_index=True)
    birlesik.columns = basliklar[:genislik]
    return birlesik, notlar


def _blok_adi(blok: list[Aday], name: str) -> tuple[str, str]:
    """(dataset adı, origin açıklaması)."""
    ilk, son = blok[0].sayfa_no, blok[-1].sayfa_no

    m = _TABLO_BASLIGI.search(blok[0].sayfa.extract_text() or "")
    if m:
        return f"{name}_tablo{m.group(1)}", (
            f"sayfa {ilk}-{son} (Tablo {m.group(1)})" if son > ilk
            else f"sayfa {ilk} (Tablo {m.group(1)})"
        )
    if son > ilk:
        return f"{name}_s{ilk}_{son}", f"sayfa {ilk}-{son}"
    return f"{name}_s{ilk}", f"sayfa {ilk}"


# ── 6) Giriş noktası ────────────────────────────────────────────


def read_pdf(path: Path, name: str) -> list[Extracted]:
    try:
        import pdfplumber
    except ImportError as e:
        raise UnsupportedSource(f"PDF okumak için pdfplumber gerekiyor: {e}") from e

    tablolar: list[Extracted] = []
    metinsiz_sayfalar: list[tuple[int, str]] = []
    retler: list[tuple[int, str]] = []
    metin_uzunlugu = 0

    try:
        with pdfplumber.open(path) as pdf:
            toplam_sayfa = len(pdf.pages)
            kabuller: list[Aday] = []

            # 1) Her sayfanın kazanan adayları
            for sayfa_no, sayfa in enumerate(pdf.pages[:MAX_SAYFA], 1):
                sayfa_metni = sayfa.extract_text() or ""
                metin_uzunlugu += len(sayfa_metni)

                gecenler, gerekceler = _sayfa_adaylari(sayfa, sayfa_no)
                kazananlar = _en_iyi_adaylar(gecenler)

                if not kazananlar:
                    sebep = gerekceler[0] if gerekceler else "tablo bulunamadı"
                    retler.append((sayfa_no, sebep))
                    if sayfa_metni.strip():
                        metinsiz_sayfalar.append((sayfa_no, sayfa_metni))
                    log.debug("sayfa %d tabloya çevrilemedi: %s", sayfa_no, sebep)
                    continue

                # Sayfada birden fazla tablo varsa birleştirmeye sokma:
                # hangi tablonun devamı olduğu belirsiz, yanlış birleştirme
                # veri uydurmaktır.
                if len(kazananlar) > 1:
                    for tablo_no, aday in enumerate(kazananlar, 1):
                        sonuc = _cerceveye_cevir(sayfa, aday)
                        if sonuc is None:
                            continue
                        govde, notlar = sonuc
                        tablolar.append(
                            Extracted(
                                name=f"{name}_s{sayfa_no}t{tablo_no}",
                                df=govde,
                                origin=f"{path.name} → sayfa {sayfa_no}, tablo {tablo_no}",
                                notes=[
                                    f"PDF sayfa {sayfa_no}/{toplam_sayfa} "
                                    f"({aday.strateji} stratejisi)",
                                    *notlar,
                                ],
                            )
                        )
                else:
                    kabuller.append(kazananlar[0])

            # 2) Ardışık sayfaları blok hâlinde birleştir
            for blok in _bloklara_ayir(kabuller):
                sonuc = _blogu_birlestir(blok)
                if sonuc is None:
                    continue
                govde, notlar = sonuc
                ad, nerede = _blok_adi(blok, name)
                sayfa_notu = (
                    f"PDF sayfa {blok[0].sayfa_no}-{blok[-1].sayfa_no}/{toplam_sayfa} "
                    f"({len(blok)} sayfa birleştirildi)"
                    if len(blok) > 1
                    else f"PDF sayfa {blok[0].sayfa_no}/{toplam_sayfa}"
                )
                tablolar.append(
                    Extracted(
                        name=ad,
                        df=govde,
                        origin=f"{path.name} → {nerede}",
                        notes=[
                            f"{sayfa_notu} ({blok[0].strateji} stratejisi)",
                            *notlar,
                        ],
                    )
                )
    except UnsupportedSource:
        raise
    except Exception as e:
        raise UnsupportedSource(f"PDF okunamadı: {e}") from e

    metin = _metin_kanali(metinsiz_sayfalar, retler, name, path, toplam_sayfa)

    # Metin katmanı varsa ASLA UnsupportedSource atma. Eskiden atıyordu ve
    # router kaçış kapısına düşüp PDF'in İKİLİ gövdesinin hex dökümünü
    # kataloğa koyuyordu — hiçbir işe yaramıyor.
    if not tablolar and metin is None:
        raise UnsupportedSource(
            f"PDF'te ne tablo ne metin bulundu ({metin_uzunlugu} karakter). "
            "Taranmış bir belge olabilir; agent metin katmanını kendi işleyebilir."
        )

    if len(tablolar) == 1:
        tablolar[0].name = name

    log.info(
        "pdf: %s → %d tablo, %d sayfa metne düştü",
        path.name, len(tablolar), len(metinsiz_sayfalar),
    )

    # Metin kanalı LİSTENİN SONUNA: datasets[0] tablo kalsın.
    return tablolar + ([metin] if metin else [])
