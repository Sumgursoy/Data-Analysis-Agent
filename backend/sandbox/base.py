"""Kernel'ların ortak gövdesi.

Docker da subprocess de aynı şeyi yapıyor: bir süreç aç, stdin'e JSON yaz,
stdout'tan JSON oku. Tek fark sürecin nasıl başlatıldığı.

O yüzden protokol, zaman aşımı, çökme toparlama ve kilitleme burada;
alt sınıflar sadece `_spawn()` yazıyor. Böylece "iki backend aynı davranır"
bir temenni değil, yapısal bir garanti oluyor.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import uuid
from pathlib import Path

from backend import config
from backend.sandbox.protocol import EXPECTED_PROTOCOL_VERSION, ExecResult, encode

log = logging.getLogger(__name__)

KERNEL_SCRIPT = config.BASE_DIR / "sandbox_image" / "kernel_server.py"

READY_TIMEOUT_SEC = 90  # imaj soğuksa container açılışı uzayabiliyor


class PipeKernel(abc.ABC):
    """stdin/stdout üzerinden JSON konuşan kalıcı Python kernel'ı."""

    def __init__(self, session_id: str, data_dir: Path, artifacts_dir: Path):
        self.session_id = session_id
        self.data_dir = data_dir
        self.artifacts_dir = artifacts_dir
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    # ── alt sınıflar dolduruyor ────────────────────────────────

    @abc.abstractmethod
    async def _spawn(self) -> asyncio.subprocess.Process:
        """Kernel sürecini başlat ve borularını bağla."""

    async def _cleanup(self) -> None:
        """Süreç öldükten sonra kalan artıkları temizle (varsa)."""

    # ── yaşam döngüsü ──────────────────────────────────────────

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        if self.alive:
            return

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self._proc = await self._spawn()

        try:
            line = await asyncio.wait_for(
                self._proc.stdout.readline(), timeout=READY_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            await self.stop()
            raise RuntimeError(
                f"Kernel {READY_TIMEOUT_SEC} saniyede hazır olmadı "
                f"({type(self).__name__}, session={self.session_id})."
            )
        except ValueError as e:
            # Karşılama satırı küçüktür; buraya düşmek kernel'ın protokol dışı
            # (ör. dev bir traceback) yazdığı anlamına gelir.
            await self.stop()
            raise RuntimeError(f"Kernel açılışta protokol dışı çıktı verdi: {e}")

        if not line:
            detail = await self._drain_stderr()
            await self.stop()
            raise RuntimeError(f"Kernel açılamadı: {detail or 'çıktı yok'}")

        try:
            hello = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Bayat/bozuk imaj ya da stdout'a sızan bir kütüphane çıktısı.
            # Ham JSONDecodeError yukarı kaçarsa hata mesajı hiçbir şey söylemez.
            detail = await self._drain_stderr()
            await self.stop()
            raise RuntimeError(
                f"Kernel karşılama satırı okunamadı ({e}). "
                f"Gelen: {line[:200]!r}" + (f"\nstderr: {detail}" if detail else "")
            )

        # Bayat imaj sessizce yanlış davranır (ör. data_path tanımsız kalır).
        # Açılışta yakala ki demo ortasında ortaya çıkmasın.
        surum = hello.get("protocol", 1)
        if surum != EXPECTED_PROTOCOL_VERSION:
            await self.stop()
            raise RuntimeError(
                f"Sandbox imajı güncel değil (imaj protokolü v{surum}, "
                f"beklenen v{EXPECTED_PROTOCOL_VERSION}). Yeniden build et:\n"
                f"  docker build -t {config.SANDBOX_IMAGE} ./sandbox_image"
            )

        log.info(
            "kernel hazır — %s session=%s pid=%s protokol=v%s",
            type(self).__name__,
            self.session_id,
            hello.get("pid"),
            surum,
        )

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            try:
                proc.stdin.write(encode(uuid.uuid4().hex, op="shutdown").encode())
                await proc.stdin.drain()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ConnectionError, ValueError, OSError):
                proc.kill()
                await proc.wait()
        await self._cleanup()

    async def restart(self) -> None:
        """Kernel kilitlendiğinde veya durum bozulduğunda temiz sayfa.

        Değişkenler kaybolur — agent'a bunu söylemek çağıranın işi.
        """
        await self.stop()
        await self.start()

    # ── çalıştırma ─────────────────────────────────────────────

    async def execute(self, code: str, timeout: int | None = None) -> ExecResult:
        timeout = timeout or config.SANDBOX_TIMEOUT_SEC

        async with self._lock:  # tek kernel → sıralı çalıştırma
            if not self.alive:
                await self.start()

            msg_id = uuid.uuid4().hex
            try:
                self._proc.stdin.write(encode(msg_id, code=code).encode("utf-8"))
                await self._proc.stdin.drain()
            except (ConnectionError, BrokenPipeError, ValueError, OSError) as e:
                await self.restart()
                return ExecResult.crashed(str(e))

            try:
                line = await asyncio.wait_for(
                    self._proc.stdout.readline(), timeout=timeout
                )
            except asyncio.TimeoutError:
                # Sonsuz döngü ya da çok ağır işlem. Kernel'ı yeniden kur;
                # değişkenler gider ama backend ayakta kalır.
                log.warning(
                    "kernel timeout (%s sn) — yeniden başlatılıyor (session=%s)",
                    timeout,
                    self.session_id,
                )
                await self.restart()
                return ExecResult.timeout(timeout)
            except ValueError:
                # Satır boru tamponunu aştı (asyncio LimitOverrunError'ı
                # ValueError'a sarıyor). config.SANDBOX_PIPE_LIMIT bunu pratikte
                # imkânsıza yakın kılıyor ama olursa: readline() tamponu
                # temizliyor, yani kernel'ın durumu belirsiz — sıfırla.
                #
                # Modele giden mesaj EYLEME DÖNÜK olmalı. Ham asyncio metni
                # ("Separator is found, but chunk is longer than limit")
                # gittiğinde model ne yapacağını çıkaramayıp aynı kodu tekrar
                # denemişti.
                log.warning(
                    "kernel çıktısı boru tamponunu (%.1f MB) aştı — "
                    "yeniden başlatılıyor (session=%s)",
                    config.SANDBOX_PIPE_LIMIT / 1024 / 1024,
                    self.session_id,
                )
                await self.restart()
                return ExecResult.crashed(
                    "Çıktı çok büyük olduğu için okunamadı; kernel sıfırlandı "
                    "(tüm değişkenler silindi). Aynı kodu tekrar çalıştırma — "
                    "daha az yazdır: df.head(20), df.shape, df.describe() "
                    "kullan, tüm tabloyu print() etme."
                )

            if not line:  # süreç öldü (OOM, kill, çökme)
                detail = await self._drain_stderr()
                await self.restart()
                return ExecResult.crashed(detail or "kernel beklenmedik şekilde kapandı")

            try:
                return ExecResult.from_json(json.loads(line.decode("utf-8")))
            except json.JSONDecodeError:
                return ExecResult.crashed(
                    f"kernel bozuk yanıt döndü: {line[:300]!r}"
                )

    async def _drain_stderr(self) -> str:
        if self._proc is None or self._proc.stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(self._proc.stderr.read(4000), timeout=2)
        except (asyncio.TimeoutError, ValueError):
            return ""
        return data.decode("utf-8", "replace").strip()
