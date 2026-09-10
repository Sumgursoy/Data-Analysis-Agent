"""Subprocess tabanlı kernel — Docker'ın yedeği.

UYARI: bu backend İZOLE DEĞİLDİR. Kod senin makinende, senin
yetkilerinle koşar; ağ erişimi ve dosya sistemi açıktır. Sonsuz döngü
ve zaman aşımı korumaları çalışır ama güvenlik sınırı yoktur.

Sadece Docker çalışmadığında kullan (SANDBOX_BACKEND=local).
Docker ile aynı kernel_server.py'ı ve aynı protokolü kullanır, bu yüzden
demo sırasında yedeğe düşmek davranış değiştirmez.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from backend import config
from backend.sandbox.base import KERNEL_SCRIPT, PipeKernel

log = logging.getLogger(__name__)

# Windows'ta süreç açılmıyorsa bunlar eksik olduğu içindir.
_PASSTHROUGH_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC")


class LocalKernel(PipeKernel):
    async def _spawn(self) -> asyncio.subprocess.Process:
        log.warning(
            "LocalKernel kullanılıyor — kod İZOLE DEĞİL. "
            "Mümkünse SANDBOX_BACKEND=docker."
        )

        env = {k: os.environ[k] for k in _PASSTHROUGH_ENV if k in os.environ}
        env.update(
            {
                "DATA_DIR": str(self.data_dir),
                "ARTIFACTS_DIR": str(self.artifacts_dir),
                "PYTHONIOENCODING": "utf-8",
                "MPLBACKEND": "Agg",
            }
        )

        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            str(KERNEL_SCRIPT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.data_dir),
            env=env,
            # Varsayılan 64 KB, kernel'ın üretebileceğinin çok altında (config).
            limit=config.SANDBOX_PIPE_LIMIT,
        )
