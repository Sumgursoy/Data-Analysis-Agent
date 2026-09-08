"""Docker tabanlı kernel — asıl sandbox.

Container `docker run -i` ile açılır ve ayakta kalır; stdin/stdout borusu
üzerinden kernel_server.py ile konuşulur. Kapatılan sınırlar:

    --network none      internet yok → veri sızmaz, .env güvende
    --memory / --cpus   RAM ve CPU tavanı
    --pids-limit        fork bomb koruması
    --read-only         kök dosya sistemi salt-okunur
    --user 1000:1000    root değil
    /data ro            veri salt-okunur bağlanır
    /artifacts rw       tek yazılabilir mount

Docker SDK yerine bilerek CLI kullanılıyor: SDK'nın attach akışında
stdin'i canlı tutmak kırılgan, `docker run -i` bir subprocess olarak
LocalKernel ile birebir aynı boru modelini veriyor.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from backend import config
from backend.sandbox.base import PipeKernel

log = logging.getLogger(__name__)

_UNSAFE_NAME = re.compile(r"[^a-zA-Z0-9_.-]")


def _mount(path: Path) -> str:
    """Windows yollarını Docker'ın anladığı biçime çevirir."""
    return path.resolve().as_posix()


class DockerKernel(PipeKernel):
    @property
    def container_name(self) -> str:
        safe = _UNSAFE_NAME.sub("-", self.session_id)[:48]
        return f"sandbox-{safe}"

    def _run_args(self) -> list[str]:
        return [
            "docker", "run", "-i", "--rm",
            "--name", self.container_name,
            "--network", "none",
            "--memory", config.SANDBOX_MEMORY,
            "--memory-swap", config.SANDBOX_MEMORY,  # swap'a taşmayı engelle
            "--cpus", str(config.SANDBOX_CPUS),
            "--pids-limit", str(config.SANDBOX_PIDS_LIMIT),
            "--read-only",
            "--tmpfs", "/tmp:size=512m,exec",
            "--user", "1000:1000",
            "--security-opt", "no-new-privileges",
            "-e", "DATA_DIR=/data",
            "-e", "ARTIFACTS_DIR=/artifacts",
            "-e", "PYTHONIOENCODING=utf-8",
            "-v", f"{_mount(self.data_dir)}:/data:ro",
            "-v", f"{_mount(self.artifacts_dir)}:/artifacts:rw",
            config.SANDBOX_IMAGE,
        ]

    async def _spawn(self) -> asyncio.subprocess.Process:
        # Aynı isimde artık kalmışsa (önceki çökme) önce temizle,
        # yoksa `docker run --name` çakışıp hata verir.
        await self._force_remove()

        log.info("container açılıyor: %s", self.container_name)
        return await asyncio.create_subprocess_exec(
            *self._run_args(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _cleanup(self) -> None:
        # --rm çoğu durumda yeterli; kill edilen container'lar için garanti.
        await self._force_remove()

    async def _force_remove(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", self.container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=15)
        except (asyncio.TimeoutError, FileNotFoundError, OSError):
            pass  # container zaten yok veya docker erişilemez — start hatayı verir
