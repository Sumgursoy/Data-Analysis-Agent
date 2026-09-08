"""Faz 5 testi — SSRF koruması, DOM haritası, tablo çıkarma, fetch_url.

Yerel bir HTTP sunucusu ayağa kaldırılıp gerçek istek yapılıyor.
"""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi.testclient import TestClient

gecti = basarisiz = 0

SAYFA = """<!doctype html>
<html><head><title>Şube Ciro Raporu 2024</title></head>
<body>
  <h1>Şubeler</h1>
  <p>Bu sayfa Kadıköy, Çankaya ve Konak şubelerinin 2024 cirolarını listeler.
     Veriler her gece güncellenir ve resmi kayıtlarla eşleşir.</p>
  <table>
    <tr><th>Şube</th><th>Şehir</th><th>Ciro (TL)</th><th>Açılış</th></tr>
    <tr><td>Kadıköy</td><td>İstanbul</td><td>1.250.000,50</td><td>01.03.2019</td></tr>
    <tr><td>Çankaya</td><td>Ankara</td><td>980.400,00</td><td>15.06.2020</td></tr>
    <tr><td>Konak</td><td>İzmir</td><td>1.100.750,25</td><td>22.09.2018</td></tr>
    <tr><td>Nilüfer</td><td>Bursa</td><td>760.000,00</td><td>05.01.2021</td></tr>
  </table>
  <ul><li>not 1</li><li>not 2</li></ul>
  <script>
    fetch("/api/v2/subeler?page=1").then(r => r.json());
    const alt = "/ajax/detay.json";
  </script>
</body></html>
"""

BOS_SAYFA = "<html><head><title>Bos</title></head><body><p>tablo yok</p></body></html>"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            govde, tip = b"User-agent: *\nDisallow: /gizli\n", "text/plain"
        elif self.path.startswith("/gizli"):
            govde, tip = b"gizli", "text/html"
        elif self.path.startswith("/bos"):
            govde, tip = BOS_SAYFA.encode("utf-8"), "text/html; charset=utf-8"
        elif self.path.startswith("/yonlendir"):
            self.send_response(302)
            self.send_header("Location", "/rapor")
            self.end_headers()
            return
        else:
            govde, tip = SAYFA.encode("utf-8"), "text/html; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", tip)
        self.send_header("Content-Length", str(len(govde)))
        self.end_headers()
        self.wfile.write(govde)

    def log_message(self, *a):
        pass


def kontrol(baslik, kosul, detay=""):
    global gecti, basarisiz
    if kosul:
        gecti += 1
        print(f"  [OK ] {baslik}")
    else:
        basarisiz += 1
        print(f"  [X  ] {baslik}  {detay}")


async def main():
    # SSRF koruması AÇIKKEN önce engellemeyi doğrula.
    from backend import config
    from backend.fetch import client as F

    print("\n=== SSRF koruması ===")
    config.FETCH_ALLOW_PRIVATE_HOSTS = False
    for url, etiket in [
        ("http://127.0.0.1:9/x", "loopback"),
        ("http://192.168.1.1/", "özel ağ"),
        ("http://169.254.169.254/latest/meta-data/", "bulut metadata"),
        ("file:///C:/Windows/win.ini", "file://"),
        ("ftp://ornek.com/x", "ftp://"),
    ]:
        try:
            F.dogrula(url)
            kontrol(f"engellendi: {etiket}", False, "GEÇTİ AMA GEÇMEMELİYDİ")
        except F.FetchError:
            kontrol(f"engellendi: {etiket}", True)

    # Yerel sunucuyla test için korumayı gevşet.
    config.FETCH_ALLOW_PRIVATE_HOSTS = True

    sunucu = HTTPServer(("127.0.0.1", 8899), Handler)
    threading.Thread(target=sunucu.serve_forever, daemon=True).start()
    taban = "http://127.0.0.1:8899"

    try:
        print("\n=== indirme ve nezaket ===")
        sayfa = await F.fetch(f"{taban}/rapor")
        kontrol("sayfa indirildi", sayfa.status == 200)
        kontrol("Türkçe karakter bozulmadı", "Kadıköy" in sayfa.text)

        r = await F.fetch(f"{taban}/yonlendir")
        kontrol("yönlendirme takip edildi", "Kadıköy" in r.text and r.url.endswith("/rapor"),
                r.url)

        try:
            await F.fetch(f"{taban}/gizli/veri")
            kontrol("robots.txt'e uyuldu", False, "izin verilmemeliydi")
        except F.FetchError as e:
            kontrol("robots.txt'e uyuldu", "robots" in str(e).lower(), str(e))

        print("\n=== DOM haritası ===")
        from backend.ingest.adapters import web as W

        harita = W.dom_map(sayfa.text, sayfa.url, dosya_adi="rapor.html")
        kontrol("tablo sayısı ve boyutu var", "<table> × 1" in harita and "5 satır" in harita,
                harita[:400])
        kontrol("tablo başlıkları var", "Şube | Şehir" in harita, harita[:400])
        kontrol("sayfa başlığı var", "Şube Ciro Raporu" in harita)
        kontrol("API uçları bulundu",
                "/api/v2/subeler" in harita and "/ajax/detay.json" in harita,
                harita[-400:])
        kontrol("ana metin çıkarıldı", "ana metin" in harita, harita[-500:])
        # Asıl mesele: harita sayfa büyüdükçe BÜYÜMEMELİ. Modele 100 KB'lık
        # HTML basmak 100k+ token yakar. Büyük sayfayla ölçelim.
        buyuk_satir = ("<tr><td>Şube%d</td><td>Şehir</td>"
                       "<td>1.000,00</td><td>01.01.2024</td></tr>")
        buyuk = sayfa.text.replace(
            "</table>", "".join(buyuk_satir % i for i in range(2000)) + "</table>"
        )
        buyuk_harita = W.dom_map(buyuk, sayfa.url, dosya_adi="buyuk.html")
        kontrol("sayfa 100× büyüyünce harita büyümüyor",
                len(buyuk) > 100_000 and len(buyuk_harita) < 3_000,
                f"html {len(buyuk)} → harita {len(buyuk_harita)}")
        kontrol("harita ham HTML içermiyor", "<td>" not in harita and "<html" not in harita)

        print("\n=== tablo çıkarma + normalize ===")
        with TestClient(_app()) as c:
            sid = c.post("/api/sessions").json()["session_id"]

            r2 = c.post(f"/api/sessions/{sid}/sources/url",
                        json={"url": f"{taban}/rapor"})
            kontrol("url kaynağı HTTP 200", r2.status_code == 200, r2.text[:300])

            if r2.status_code == 200:
                ds = r2.json()["datasets"][0]
                kontrol("4 satır çıkarıldı", ds["row_count"] == 4, str(ds["row_count"]))
                kontrol("kind = web", ds["kind"] == "web", ds["kind"])
                kart = ds["schema_card"]
                kontrol("ciro sayıya çevrildi", "float" in kart, kart[:400])
                kontrol("tarih tanındı", "datetime" in kart, kart[:400])
                kontrol("Türkçe şehirler doğru", "İstanbul" in kart, kart[:500])

            r3 = c.post(f"/api/sessions/{sid}/sources/url", json={"url": f"{taban}/bos"})
            kontrol("tablosuz sayfa açıklayıcı hata veriyor", r3.status_code == 422,
                    str(r3.status_code))
            kontrol("hata fetch_url'ü öneriyor", "fetch_url" in r3.text, r3.text[:200])

        print("\n=== fetch_url tool'u ===")
        from backend.agent import tools
        from backend.agent.session import AgentSession

        oturum = AgentSession("web_s1")
        t = await tools.dispatch(oturum, "fetch_url", {"url": f"{taban}/rapor"})
        kontrol("tool başarılı", t.ok, t.text[:200])
        kontrol("harita döndü, HTML değil", "<table> × 1" in t.text and "<html" not in t.text)
        kayitli = list((oturum.dir / "data").glob("*.html"))
        kontrol("sayfa /data'ya kaydedildi", len(kayitli) == 1, str(kayitli))
        kontrol("trace'e fetch yazıldı",
                any(i["type"] == "fetch" for i in oturum.read_trace()))

        t2 = await tools.dispatch(oturum, "fetch_url",
                                  {"url": "http://169.254.169.254/meta-data/"})
        config.FETCH_ALLOW_PRIVATE_HOSTS = False
        t3 = await tools.dispatch(oturum, "fetch_url", {"url": "http://192.168.0.5/"})
        kontrol("tool SSRF'i reddetti (koruma açıkken)",
                not t3.ok and t3.error_type == "FetchError", f"{t3.error_type}")
        _ = t2
    finally:
        sunucu.shutdown()
        from backend.sandbox.manager import manager
        await manager.shutdown()

    print(f"\n{'=' * 62}\nGECTI: {gecti}   BASARISIZ: {basarisiz}")


def _app():
    from backend.main import app
    return app


asyncio.run(main())
