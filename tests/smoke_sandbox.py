"""Docker sandbox doğrulaması — izolasyon sınırları ve kaynak limitleri.

    python tests/smoke_sandbox.py

Docker'ın çalışıyor ve imajın güncel olmasını gerektirir:
    docker build -t analyst-sandbox:latest ./sandbox_image

run_all.py içinde DEĞİL: diğer testler Docker'sız da koşabilsin diye ayrı.
Demo öncesi mutlaka bir kez çalıştır — izolasyonun gerçekten kapalı
olduğunu doğrulayan tek test budur.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SANDBOX_BACKEND", "docker")

from backend import config  # noqa: E402
from backend.sandbox.manager import manager  # noqa: E402

SESSION = "smoke_sandbox"
gecti = basarisiz = 0


def kontrol(baslik: str, kosul: bool, detay: str = "") -> None:
    global gecti, basarisiz
    if kosul:
        gecti += 1
        print(f"  [OK ] {baslik}")
    else:
        basarisiz += 1
        print(f"  [X  ] {baslik}  {detay}")


async def main() -> int:
    print(f"backend = {config.SANDBOX_BACKEND}   imaj = {config.SANDBOX_IMAGE}\n")
    await manager.prewarm()

    # Önceki koşudan kalan dosyalar "yeni artifact" tespitini bozar.
    shutil.rmtree(config.SESSIONS_DIR / SESSION, ignore_errors=True)

    sdir = config.session_dir(SESSION)
    (sdir / "data" / "ornek.csv").write_text(
        "bolge,tutar\nEge,10\nEge,20\nMarmara,70\n", encoding="utf-8"
    )

    try:
        k = await manager.get(SESSION)
    except Exception as e:
        print(f"KERNEL AÇILAMADI: {e}")
        print("\nİmaj güncel mi? → docker build -t analyst-sandbox:latest ./sandbox_image")
        return 1

    async def calistir(kod: str, timeout: int | None = None):
        return await k.execute(kod, timeout=timeout)

    try:
        print("── kalıcılık ve temel davranış " + "─" * 32)
        await calistir("x = 5")
        kontrol("değişken çağrılar arası yaşıyor",
                (await calistir("print(x)")).stdout.strip() == "5")
        kontrol("son ifadenin değeri dönüyor",
                (await calistir("x * 7")).result_repr.strip() == "35")

        print("\n── yol yardımcıları (mod bağımsız) " + "─" * 28)
        r = await calistir("data_path('ornek.csv')")
        kontrol("data_path tanımlı ve /data'yı gösteriyor",
                "/data/ornek.csv" in r.result_repr, r.result_repr or r.traceback or "")
        r = await calistir("artifact_path('x.png')")
        kontrol("artifact_path tanımlı", "/artifacts/x.png" in r.result_repr,
                r.result_repr or r.traceback or "")
        r = await calistir(
            "df = pd.read_csv(data_path('ornek.csv'))\n"
            "df.groupby('bolge').tutar.sum().to_dict()"
        )
        kontrol("mount okunuyor (Türkçe karakterli host yolu)",
                "Marmara" in r.result_repr, r.result_repr or r.traceback or "")

        print("\n── grafik stili " + "─" * 47)
        r = await calistir(
            "import matplotlib as m\n"
            "(m.rcParams['axes.spines.top'], m.rcParams['font.family'][0])"
        )
        kontrol("analyst.mplstyle yüklendi",
                "False" in r.result_repr and "DejaVu" in r.result_repr,
                r.result_repr or r.traceback or "")
        r = await calistir(
            "fig, ax = plt.subplots()\n"
            "df.groupby('bolge').tutar.sum().plot.bar(ax=ax, title='Bölge ŞĞİÜÖÇ')\n"
            "fig.savefig(artifact_path('smoke.png'))\n"
            "plt.close(fig)"
        )
        kontrol("grafik üretildi ve tespit edildi", "smoke.png" in r.new_artifacts,
                str(r.new_artifacts) + (r.traceback or ""))
        png = sdir / "artifacts" / "smoke.png"
        kontrol("grafik host tarafında görünüyor",
                png.exists() and png.stat().st_size > 5000,
                f"{png.stat().st_size if png.exists() else 0} bayt")

        print("\n── İZOLASYON (hepsi başarısız OLMALI) " + "─" * 25)
        r = await calistir("import socket; socket.create_connection(('1.1.1.1', 53), 4)")
        kontrol("internet erişimi yok", not r.ok, r.result_repr)
        r = await calistir("open('/data/sizinti.txt','w').write('x')")
        kontrol("/data salt-okunur", not r.ok, r.result_repr)
        r = await calistir("open('/kok.txt','w').write('x')")
        kontrol("kök dosya sistemi salt-okunur", not r.ok, r.result_repr)
        r = await calistir("import os; os.getuid()")
        kontrol("root olarak koşmuyor", r.result_repr.strip() != "0", r.result_repr)
        r = await calistir("import os; os.listdir('/')")
        kontrol(".env görünmüyor", ".env" not in r.result_repr)
        r = await calistir("import os; os.environ.get('OPENAI_API_KEY')")
        kontrol("API anahtarı container'a geçmiyor",
                r.result_repr.strip() in ("", "None"), r.result_repr)

        print("\n── kaynak sınırları " + "─" * 43)
        r = await calistir("while True: pass", timeout=5)
        kontrol("sonsuz döngü kesildi", r.error_type == "TimeoutError", str(r.error_type))
        kontrol("timeout sonrası kernel yaşıyor",
                (await calistir("'yasiyor'")).ok)
        r = await calistir(
            "import numpy as np; a = np.ones((3*1024**3)//8); a.sum()"
        )
        kontrol("RAM tavanı uygulanıyor (2 GB)", not r.ok, str(r.error_type))
        kontrol("OOM sonrası kernel toparlandı",
                (await calistir("'hala yasiyor'")).ok)
    finally:
        await manager.shutdown()

    print(f"\n{'=' * 62}\nGECTI: {gecti}   BASARISIZ: {basarisiz}")
    return 1 if basarisiz else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
