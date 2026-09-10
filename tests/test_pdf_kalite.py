"""PDF çıkarım kalitesi — karma belge (anlatı + tablo) testi.

GERÇEK HATALAR (#29, #30): Bir kurumsal PDF (TBB banka raporu, 11 sayfa,
4 mantıksal tablo) kataloğa 10 veri seti olarak giriyordu — 4'ü tamamen çöp
(içindekiler sayfası, grafik eksen etiketleri), kolon adları ise sayfa
başlığının parçalanmış hâliydi (`tablo_1_a`, `ralik_202`, `banka_su`).
Model 10 veri setinin hepsine `get_schema` çağırıp tek soru için 75.177
token yaktı ve sonunda cevap veremedi.

İki kök neden:
  #29  stratejiler SIRAYLA deneniyor, ilk sonuç üreten kazanıyordu. Çizgi
       stratejisi 21 satırı 6 hücreye tıkıştırıp 3 satır döndürüyor, eşiği
       geçtiği için doğru okuma (46 satır) HİÇ denenmiyordu.
  #30  başlık koşulsuz `tablo[0]` sayılıyordu; doküman başlığı sayfanın
       tamamına yayıldığı için kolon adı oluyordu.

Bu test her ikisini de karma bir PDF üstünde doğruluyor.
"""

import asyncio
from pathlib import Path

from backend.ingest.adapters.base import UnsupportedSource
from backend.ingest.adapters.pdf import read_pdf

TMP = Path(__file__).parent / "veri"
TMP.mkdir(exist_ok=True)

gecti = basarisiz = 0


def kontrol(baslik, kosul, detay=""):
    global gecti, basarisiz
    if kosul:
        gecti += 1
        print(f"  [OK ] {baslik}")
    else:
        basarisiz += 1
        print(f"  [X  ] {baslik}  {detay}")


def karma_pdf_kur() -> Path | None:
    """4 sayfa: anlatı · başlıklı tablo · devamı · saçılmış sayılı anlatı.

    reportlab yoksa None döner (opsiyonel bağımlılık, pdf_kur() deseni).
    """
    yol = TMP / "karma.pdf"
    yol.unlink(missing_ok=True)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        return None

    c = canvas.Canvas(str(yol), pagesize=A4)

    # ── Sayfa 1: saf anlatı, tablo değil ────────────────────────
    c.setFont("Helvetica", 11)
    for i, satir in enumerate([
        "Sektor Raporu 2025",
        "",
        "Bu rapor sube performansini degerlendirmektedir. Katilim",
        "bankalari 9 tanedir ve toplam calisan sayisi artmistir.",
        "Raporun devaminda bolgesel kirilim sunulmaktadir.",
    ]):
        c.drawString(60, 780 - i * 20, satir)
    c.showPage()

    # ── Sayfa 2: gerçek tablo, ÜSTÜNDE doküman başlığı ──────────
    # Başlık satırı 1. satır DEĞİL: find_header_row onu atlayıp
    # "Sube / Sehir / Ciro / Calisan" satırını bulmalı (hata #30).
    c.setFont("Helvetica", 11)
    c.drawString(60, 780, "Tablo 1 Aralik 2025 Itibariyla Sube Bilgileri")
    basliklar = ["Sube", "Sehir", "Ciro", "Calisan"]
    satirlar = [
        ["Kadikoy", "Istanbul", "1250000", "48"],
        ["Cankaya", "Ankara", "980400", "36"],
        ["Konak", "Izmir", "1100750", "41"],
        ["Nilufer", "Bursa", "760300", "29"],
        ["Seyhan", "Adana", "645900", "24"],
    ]
    x = [60, 200, 340, 470]
    for j, b in enumerate(basliklar):
        c.drawString(x[j], 740, b)
    for i, satir in enumerate(satirlar):
        for j, h in enumerate(satir):
            c.drawString(x[j], 715 - i * 22, h)
    c.showPage()

    # ── Sayfa 3: 2. sayfanın DEVAMI — aynı yerleşim, başlık tekrarı ─
    # Kurumsal tablolar sayfaya sığmayınca böyle devam eder. Aynı x
    # konumları kullanıldığı için kolon imzası eşleşmeli ve iki sayfa
    # TEK veri seti olmalı (hata #32: yarım tablo üstünde hesap yapılıyordu).
    c.setFont("Helvetica", 11)
    for j, b in enumerate(basliklar):
        c.drawString(x[j], 780, b)
    devam = [
        ["Muratpasa", "Antalya", "590100", "22"],
        ["Selcuklu", "Konya", "512800", "19"],
        ["Osmangazi", "Bursa", "488600", "18"],
    ]
    for i, satir in enumerate(devam):
        for j, h in enumerate(satir):
            c.drawString(x[j], 755 - i * 22, h)
    c.showPage()

    # ── Sayfa 4: anlatı + saçılmış sayılar (grafik etiketi gibi) ─
    # Gerçek PDF'in 3-5. sayfaları böyleydi: sayılar sayfaya saçılmış,
    # bir kolonda dikey dizilmemiş. Kapı bunu reddetmeli.
    c.setFont("Helvetica", 11)
    c.drawString(60, 780, "Sube Sayisi Grafigi")
    c.drawString(60, 750, "Aralik 2025 itibariyle sube sayisi 9158 adettir.")
    c.drawString(90, 700, "71")
    c.drawString(250, 660, "9589")
    c.drawString(400, 620, "83")
    c.drawString(150, 580, "2022 2023 2024 2025")
    c.showPage()

    c.save()
    return yol


async def main():
    print("=== karma PDF: kalite kapisi + baslik tespiti ===")
    yol = karma_pdf_kur()
    if yol is None:
        print("  (reportlab yok — atlandi)")
        kontrol("pdf adaptoru import edilebiliyor", callable(read_pdf))
        print(f"\n{'=' * 62}\nGECTI: {gecti}   BASARISIZ: {basarisiz}")
        return

    try:
        sonuc = read_pdf(yol, "karma")
    except UnsupportedSource as e:
        kontrol("PDF islenebildi", False, str(e))
        print(f"\n{'=' * 62}\nGECTI: {gecti}   BASARISIZ: {basarisiz}")
        return

    tablolar = [e for e in sonuc if e.kind != "raw"]
    metinler = [e for e in sonuc if e.kind == "raw"]
    for e in sonuc:
        print(f"       → {e.name}  {e.df.shape[0]}x{e.df.shape[1]}  [{e.kind}]")

    # ── Kalite kapısı ───────────────────────────────────────────
    kontrol("tam olarak 1 tablo cikti (anlati sayfalari elendi)",
            len(tablolar) == 1, f"{len(tablolar)} tablo: {[t.name for t in tablolar]}")

    if tablolar:
        df = tablolar[0].df
        kolonlar = [str(k) for k in df.columns]

        # ── Başlık tespiti (hata #30) ───────────────────────────
        kontrol("kolon adlari basliktan alindi",
                kolonlar[:4] == ["Sube", "Sehir", "Ciro", "Calisan"], str(kolonlar))
        kontrol("dokuman basligi kolon adi OLMADI",
                not any("Tablo" in k or "Itibariyla" in k for k in kolonlar),
                str(kolonlar))
        kontrol("dokuman basligi govdeye de karismadi",
                not df.astype(str).apply(
                    lambda s: s.str.contains("Itibariyla", na=False)).any().any())

        # ── Çok sayfalı birleştirme (hata #32) ──────────────────
        kontrol("iki sayfa TEK veri setinde birlesti", len(df) == 8,
                f"{len(df)} satir (5 + 3 bekleniyordu)")
        kontrol("birlesme notu dusuldu",
                any("birleştirildi" in n for n in tablolar[0].notes),
                str(tablolar[0].notes[:2]))
        kontrol("origin sayfa araligini gosteriyor",
                "2-3" in tablolar[0].origin, tablolar[0].origin)

        # ── Gövde bütünlüğü ─────────────────────────────────────
        ilk = [str(v) for v in df.iloc[0].tolist()]
        kontrol("ilk sayfanin ilk satiri dogru",
                ilk[:4] == ["Kadikoy", "Istanbul", "1250000", "48"], str(ilk))
        son = [str(v) for v in df.iloc[-1].tolist()]
        kontrol("devam sayfasinin son satiri dogru",
                son[:4] == ["Osmangazi", "Bursa", "488600", "18"], str(son))
        # Devam sayfasinin baslik satiri govdeye KARISMAMALI
        kontrol("tekrarlanan baslik govdeye karismadi",
                not df.astype(str).apply(
                    lambda s: s.str.fullmatch("Sehir", na=False)).any().any(),
                str(df.iloc[:, 1].tolist()))

    # ── Metin kanalı ────────────────────────────────────────────
    kontrol("metin kanali uretildi", len(metinler) == 1,
            f"{len(metinler)} metin veri seti")
    if metinler:
        m = metinler[0]
        kontrol("metin kanali raw olarak isaretli", m.kind == "raw", m.kind)
        kontrol("metin kanali kolonlari",
                list(m.df.columns) == ["sayfa", "satir_no", "metin"],
                str(list(m.df.columns)))
        govde = " ".join(m.df["metin"].astype(str))
        # Kritik: cevap tabloda DEGIL anlatida. Eskiden bu icerik kayboluyordu.
        kontrol("anlati cumlesi metin kanalinda bulunuyor",
                "Katilim" in govde and "9 tanedir" in govde, govde[:120])
        kontrol("reddedilen sayfalar metne dustu",
                set(m.df["sayfa"].unique()) == {1, 4},
                str(sorted(m.df["sayfa"].unique())))
        kontrol("ret gerekcesi notlarda",
                any("Tablo çıkarılamayan" in n for n in m.notes), str(m.notes))

    # ── Regresyon: mevcut basit fixture bozulmamali ─────────────
    print("\n=== regresyon: tests/veri/rapor.pdf ===")
    basit = TMP / "rapor.pdf"
    if not basit.exists():
        print("  (rapor.pdf yok — test_export_sources once kosmali, atlandi)")
    else:
        r = read_pdf(basit, "rapor")
        kontrol("basit tablo hala geciyor", len(r) >= 1, f"{len(r)} veri seti")
        if r:
            kontrol("basit tablonun kolon adlari korundu",
                    [str(k) for k in r[0].df.columns] == ["Sube", "Sehir", "Ciro"],
                    str(list(r[0].df.columns)))

    print(f"\n{'=' * 62}\nGECTI: {gecti}   BASARISIZ: {basarisiz}")


asyncio.run(main())
