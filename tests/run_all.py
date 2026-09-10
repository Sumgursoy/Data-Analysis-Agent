"""Tüm testleri sırayla koşturur ve tek bir özet basar.

    python tests/run_all.py

Notlar:
  - Testler LocalKernel ile koşar (Docker gerekmez). Docker izolasyonunu
    ayrıca doğrulamak için: python tests/smoke_sandbox.py
  - Her test öncesi session klasörleri temizlenir; testler birbirine
    sızmasın diye.
  - Ağ testleri yalnızca yerelde açılan bir HTTP sunucusuna çıkar.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent

# Testler AYRI bir depolama kökü kullanır. Gerçek oturumların (ve canlı
# analizlerin trace'lerinin) test temizliğiyle silinmesini engeller.
TEST_STORAGE = KOK / "tests" / ".storage"
SESSIONS = TEST_STORAGE / "sessions"

TESTLER = [
    ("normalize", "test_normalize.py", "Türkçe sayı/tarih/kolon adı"),
    ("ingest", "test_ingest.py", "Bozuk Excel, cp1254 CSV, tanınmayan dosya"),
    ("agent", "test_agent.py", "Döngü, hata düzeltme, SSE, trace"),
    ("sql", "test_sql.py", "SQL güvenliği, veritabanı ingest"),
    ("web", "test_web.py", "SSRF, robots, DOM haritası"),
    ("export", "test_export_sources.py", "Örnek veri, notebook, PDF, ZIP"),
    ("pdf", "test_pdf_kalite.py", "PDF kalite kapısı, başlık tespiti, metin kanalı"),
]


def temizle() -> None:
    if SESSIONS.exists():
        for d in SESSIONS.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    ortam = {
        **os.environ,
        "PYTHONPATH": str(KOK),
        "PYTHONIOENCODING": "utf-8",
        "SANDBOX_BACKEND": os.environ.get("SANDBOX_BACKEND", "local"),
        "STORAGE_DIR": str(TEST_STORAGE),
    }

    toplam_gecti = toplam_kaldi = 0
    basarisiz_testler: list[str] = []

    for ad, dosya, aciklama in TESTLER:
        temizle()
        print(f"\n{'─' * 66}\n▶ {ad:<10} {aciklama}\n{'─' * 66}")

        sonuc = subprocess.run(
            [sys.executable, str(Path(__file__).parent / dosya)],
            env=ortam, capture_output=True, text=True, encoding="utf-8",
        )
        cikti = sonuc.stdout or ""
        print(cikti.rstrip())

        ozet = [s for s in cikti.splitlines() if s.startswith("GECTI:")]
        if ozet:
            parcalar = ozet[-1].replace("GECTI:", "").replace("BASARISIZ:", "").split()
            toplam_gecti += int(parcalar[0])
            toplam_kaldi += int(parcalar[1])
            if int(parcalar[1]):
                basarisiz_testler.append(ad)
        elif sonuc.returncode != 0:
            basarisiz_testler.append(ad)
            print(f"[ÇÖKTÜ] {ad}\n{(sonuc.stderr or '')[-1500:]}")

    temizle()

    print(f"\n{'═' * 66}")
    print(f"TOPLAM   geçti: {toplam_gecti}   başarısız: {toplam_kaldi}")
    if basarisiz_testler:
        print(f"Sorunlu testler: {', '.join(basarisiz_testler)}")
        return 1
    print("Hepsi geçti ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
