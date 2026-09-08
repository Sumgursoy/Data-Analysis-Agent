"""Grafik eksen biçimlendirmesi — sandbox içinde ölçülür.

Eksen etiketleri yanlış olursa jüri yanlış sayı okur; bu yüzden ayrı test.
Docker (veya local) kernel gerektirir.
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

SESSION = "grafik_bicim"
gecti = basarisiz = 0


def kontrol(baslik, kosul, detay=""):
    global gecti, basarisiz
    if kosul:
        gecti += 1
        print(f"  [OK ] {baslik}")
    else:
        basarisiz += 1
        print(f"  [X  ] {baslik}  {detay}")


# Kısaltma kuralı: 10 ve üstünde ondalık yok (12 bin), altında bir hane (4,0 mn).
# Küçük değerlerin ondalığı eksenin adımına göre belirlenir — bu yüzden test
# tam sayı adımlı (0…5 mn) bir eksende koşuyor.
BEKLENEN = [
    (0, "0"),
    (1500, "1.500"),
    (12345, "12 bin"),
    (500_000, "500 bin"),
    (1_000_000, "1,0 mn"),
    (4_007_888, "4,0 mn"),
    (2_500_000_000, "2,5 mr"),
    (-750_000, "-750 bin"),
]


async def main() -> int:
    shutil.rmtree(config.SESSIONS_DIR / SESSION, ignore_errors=True)
    sdir = config.session_dir(SESSION)
    k = await manager.get(SESSION)

    try:
        print("── tr_sayi (tam sayı biçimi) " + "─" * 34)
        r = await k.execute("[tr_sayi(1234567.5, 1), tr_sayi(0), tr_sayi(-9876, 2)]")
        kontrol("Türkçe ayıraçlar doğru",
                "1.234.567,5" in r.result_repr and "-9.876,00" in r.result_repr,
                r.result_repr or r.traceback or "")

        print("\n── eksen kısaltması (otomatik) " + "─" * 32)
        kod = (
            "import matplotlib.pyplot as _p\n"
            "from matplotlib.ticker import ScalarFormatter, FuncFormatter\n"
            "fig, ax = _p.subplots()\n"
            "ax.plot([0, 1], [0, 5_000_000])\n"   # tam sayı adımlı eksen
            "fig.savefig(artifact_path('x.png'))\n"      # kancayı tetikler
            "f = ax.yaxis.get_major_formatter()\n"
            f"[f(d, None) for d, _ in {BEKLENEN!r}]\n"
        )
        r = await k.execute(kod)
        kontrol("kanca formatter'ı değiştirdi", r.ok, r.traceback or "")
        if r.ok:
            alinan = r.result_repr
            for deger, beklenen in BEKLENEN:
                kontrol(f"{deger:>14,} → {beklenen}".replace(",", "."),
                        f"'{beklenen}'" in alinan, alinan[:200])

        print("\n── modelin kendi biçimi korunuyor mu " + "─" * 26)
        r = await k.execute(
            "fig2, ax2 = _p.subplots()\n"
            "ax2.plot([0, 1], [0, 1000000])\n"
            "ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f'%{v:.0f}'))\n"
            "fig2.savefig(artifact_path('y.png'))\n"
            "ax2.yaxis.get_major_formatter()(500000, None)"
        )
        kontrol("özel formatter ezilmedi", "%500000" in r.result_repr,
                r.result_repr or r.traceback or "")

        print("\n── kategori ekseni bozulmuyor mu " + "─" * 30)
        r = await k.execute(
            "fig3, ax3 = _p.subplots()\n"
            "ax3.bar(['Ege', 'Marmara'], [1, 2])\n"
            "fig3.savefig(artifact_path('z.png'))\n"
            "[t.get_text() for t in ax3.get_xticklabels()]"
        )
        kontrol("kategori etiketleri metin kaldı",
                "Ege" in r.result_repr and "Marmara" in r.result_repr,
                r.result_repr or r.traceback or "")
    finally:
        await manager.shutdown()
        shutil.rmtree(sdir, ignore_errors=True)

    print(f"\n{'=' * 62}\nGECTI: {gecti}   BASARISIZ: {basarisiz}")
    return 1 if basarisiz else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
