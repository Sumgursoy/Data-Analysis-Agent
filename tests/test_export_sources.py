"""Faz 7 testi — örnek veri, notebook export, PDF, ZIP."""

import asyncio
import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from backend import config
from backend.agent.session import AgentSession
from backend.main import app

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


def zip_kur() -> Path:
    yol = TMP / "paket.zip"
    yol.unlink(missing_ok=True)
    with zipfile.ZipFile(yol, "w") as z:
        z.writestr(
            "satis.csv",
            "bolge;tutar\nEge;1.234,56\nMarmara;12.000,00\nEge;8.750,25\n",
        )
        z.writestr(
            "urunler.csv",
            "urun_kodu;ad;fiyat\nU1;Kalem;12,50\nU2;Defter;45,00\nU3;Silgi;7,25\n",
        )
        z.writestr("okuma.txt", "bu bir aciklama dosyasi")
        # Güvenlik: yol geçişi denemesi ve çalıştırılabilir dosya
        z.writestr("../../kacak.csv", "a;b\n1;2\n3;4\n")
        z.writestr("virus.exe", "MZ")
    return yol


def pdf_kur() -> Path | None:
    """Basit bir tablo PDF'i üretir (reportlab yoksa None)."""
    yol = TMP / "rapor.pdf"
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        return None

    c = canvas.Canvas(str(yol), pagesize=A4)
    c.setFont("Helvetica", 10)
    y = 780
    for satir in [
        ["Sube", "Sehir", "Ciro"],
        ["Kadikoy", "Istanbul", "1250000"],
        ["Cankaya", "Ankara", "980400"],
        ["Konak", "Izmir", "1100750"],
    ]:
        x = 60
        for hucre in satir:
            c.drawString(x, y, hucre)
            x += 150
        y -= 20
    c.save()
    return yol


async def main():
    with TestClient(app) as c:
        print("\n=== örnek veri ===")
        sid = c.post("/api/sessions").json()["session_id"]
        r = c.post(f"/api/sessions/{sid}/sources/sample")
        kontrol("HTTP 200", r.status_code == 200, r.text[:300])

        if r.status_code == 200:
            setler = {d["name"]: d for d in r.json()["datasets"]}
            kontrol("iki set üretildi", len(setler) == 2, str(list(setler)))
            kontrol("satislar 6000 satır",
                    setler.get("satislar", {}).get("row_count") == 6000,
                    str(setler.get("satislar", {}).get("row_count")))
            kart = setler.get("satislar", {}).get("schema_card", "")
            kontrol("negatif değer uyarısı var (iadeler)", "negatif" in kart, kart[:600])
            kontrol("boş kolon uyarısı var", "boş" in kart)
            kontrol("tarih boşluğu yakalandı", "hiç kayıt yok" in kart, kart[:900])
            kontrol("çarpıklık tespit edildi", "çarpık" in kart, kart[:900])
            kontrol("ilişki adayı bulundu", "İlişki adayları" in kart, kart[-400:])
            kontrol("Türkçe bölgeler doğru", "İç Anadolu" in kart)

        print("\n=== notebook export ===")
        # Boşken 404 vermeli
        r0 = c.get(f"/api/export/{sid}.ipynb")
        kontrol("kod yokken 404", r0.status_code == 404, str(r0.status_code))

        # Sahte bir analiz izi yaz
        oturum = AgentSession(sid)
        oturum.trace("user", text="Bölgelere göre ciroyu göster")
        oturum.trace("code", code="df = pd.read_parquet(data_path('satislar.parquet'))\ndf.shape",
                     ok=True, duration_ms=12)
        oturum.trace("stdout", text="(6000, 9)")
        oturum.trace("sql", query="SELECT bolge, sum(tutar) FROM satislar GROUP BY bolge",
                     rows=6)
        oturum.trace("artifact", path="bolge_ciro.png")
        oturum.trace("finish", summary="Marmara cironun %34'ünü oluşturuyor.")

        r2 = c.get(f"/api/export/{sid}.ipynb")
        kontrol("HTTP 200", r2.status_code == 200, str(r2.status_code))
        kontrol("dosya adı doğru", "analiz_" in r2.headers.get("content-disposition", ""),
                r2.headers.get("content-disposition", ""))

        nb = json.loads(r2.text)
        kontrol("geçerli nbformat", nb.get("nbformat") == 4)
        kodlar = [h for h in nb["cells"] if h["cell_type"] == "code"]
        mdler = [h for h in nb["cells"] if h["cell_type"] == "markdown"]
        kontrol("başlangıç + kod + sql hücreleri var", len(kodlar) >= 3,
                f"{len(kodlar)} kod hücresi")
        kontrol("data_path bootstrap'ta tanımlı",
                any("def data_path" in "".join(h["source"]) for h in kodlar))
        kontrol("çıktı gömüldü",
                any(h.get("outputs") and "(6000, 9)" in "".join(
                    h["outputs"][0]["text"]) for h in kodlar))
        kontrol("SQL adımı girdi",
                any("GROUP BY bolge" in "".join(h["source"]) for h in kodlar))
        kontrol("veri setleri listelendi",
                any("satislar" in "".join(h["source"]) for h in mdler))
        kontrol("bulgular bölümü var",
                any("Bulgular" in "".join(h["source"]) for h in mdler))
        kontrol("soru markdown olarak girdi",
                any("Bölgelere göre" in "".join(h["source"]) for h in mdler))

        print("\n=== ZIP arşivi ===")
        sid2 = c.post("/api/sessions").json()["session_id"]
        z = zip_kur()
        with z.open("rb") as f:
            r3 = c.post(f"/api/sessions/{sid2}/sources/file",
                        files={"file": (z.name, f, "application/zip")})
        kontrol("HTTP 200", r3.status_code == 200, r3.text[:300])
        if r3.status_code == 200:
            adlar = [d["name"] for d in r3.json()["datasets"]]
            kontrol("iki CSV işlendi", {"satis", "urunler"} <= set(adlar), str(adlar))
            kontrol("exe atlandı", not any("virus" in a for a in adlar), str(adlar))
            kacak = Path(config.SESSIONS_DIR).parent / "kacak.csv"
            kontrol("zip slip engellendi (dışarı yazılmadı)", not kacak.exists())

        print("\n=== PDF ===")
        pdf = pdf_kur()
        if pdf is None:
            print("  (reportlab yok — PDF üretimi atlandı, adaptör importu test edilecek)")
            from backend.ingest.adapters import pdf as pdf_adapter
            kontrol("pdf adaptörü import edilebiliyor", hasattr(pdf_adapter, "read_pdf"))
        else:
            sid3 = c.post("/api/sessions").json()["session_id"]
            with pdf.open("rb") as f:
                r4 = c.post(f"/api/sessions/{sid3}/sources/file",
                            files={"file": (pdf.name, f, "application/pdf")})
            kontrol("PDF işlendi veya ham geçişe düştü",
                    r4.status_code == 200, r4.text[:300])
            if r4.status_code == 200:
                ds = r4.json()["datasets"][0]
                kontrol("bir veri seti çıktı", ds["name"] != "")
                print(f"       → {ds['name']} ({ds['kind']}, {ds['row_count']} satır)")

    from backend.sandbox.manager import manager
    await manager.shutdown()
    print(f"\n{'=' * 62}\nGECTI: {gecti}   BASARISIZ: {basarisiz}")


asyncio.run(main())
