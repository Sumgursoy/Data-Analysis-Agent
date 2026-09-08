"""Geliştirme sunucusu — Windows'ta çalışan otomatik yeniden yükleme.

    python run_dev.py            # varsayılan port 8000
    python run_dev.py --port 8001

NEDEN VAR (uvicorn --reload yerine):

uvicorn `--reload` verildiğinde Windows'ta SelectorEventLoop seçiyor
(uvicorn/loops/asyncio.py: `win32 and not use_subprocess` → Proactor,
aksi halde Selector). SelectorEventLoop `asyncio.create_subprocess_exec`
desteklemiyor — `NotImplementedError` fırlatıyor, üstelik `str(e)` boş.

Sandbox'ın tamamı alt süreç açmaya dayandığı için (docker_kernel,
local_kernel, prewarm) sonuç şu: `--reload` ile sandbox HİÇ çalışmaz.

Buradaki çözüm katmanı ayırıyor:
  - yeniden yükleme  → watchfiles, süreç seviyesinde (bu dosya)
  - sunucu           → uvicorn, `--reload` OLMADAN → Proactor loop → sandbox sağlam

Dosya değişince tüm süreç yeniden başlar. Biraz daha yavaş ama doğru
çalışır; sessizce bozuk bir sandbox'tan iyidir.
"""

from __future__ import annotations

import argparse
import sys

from watchfiles import PythonFilter, run_process

from backend import config


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="8000")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    komut = (
        f"{sys.executable} -m uvicorn backend.main:app "
        f"--host {args.host} --port {args.port}"
    )

    # SADECE .py değişimlerinde yeniden başlat ve storage'ı tamamen dışla.
    # Aksi halde: session açmak backend/storage/sessions/... yazıyor, izleyici
    # bunu değişiklik sanıp sunucuyu yeniden başlatıyor ve istek ortada
    # kalıyor (ConnectionReset). Testte gerçekten böyle oldu.
    filtre = PythonFilter(ignore_paths=[str(config.STORAGE_DIR)])

    print(f"izleniyor: backend/**.py  (storage hariç)")
    print(f"sunucu   : http://{args.host}:{args.port}")
    print("(uvicorn --reload KULLANILMIYOR — Windows'ta sandbox'ı bozuyor)\n")

    run_process("backend", target=komut, target_type="command",
                watch_filter=filtre)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
