"""Kernel yaşam döngüsü — hangi session'ın kernel'ı nerede.

PLANDAN SAPMA (MIMARI.md bölüm 7, "warm pool"):
Plan, önceden açılmış container'lardan oluşan genel bir havuz öngörüyordu.
Uygulanabilir değil: her container o session'ın `data/` ve `artifacts/`
klasörlerine bağlanıyor. Session'dan bağımsız açılmış bir container ya
hiçbir veriyi görür ya da hepsini — ikincisi session'lar arası veri
sızıntısı demek.

Yerine konan çözüm aynı faydayı veriyor: kernel, sohbetin ilk mesajında
değil **session açılır açılmaz** başlatılıyor. Jüri dosyayı yüklerken
container zaten ayağa kalkıyor; soru yazıldığında hazır oluyor.
Buna ek olarak açılışta bir kez `prewarm()` çalışıp Docker'ın imaj
katmanlarını ısıtıyor — ilk gerçek container'ın açılışı da hızlanıyor.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from backend import config
from backend.sandbox.base import PipeKernel
from backend.sandbox.docker_kernel import DockerKernel
from backend.sandbox.local_kernel import LocalKernel

log = logging.getLogger(__name__)

_BACKENDS: dict[str, type[PipeKernel]] = {
    "docker": DockerKernel,
    "local": LocalKernel,
}


class SandboxUnavailable(RuntimeError):
    """Kernel hiç açılamadı — altyapı sorunu, kod sorunu değil.

    Modelin düzeltebileceği bir şey olmadığı için agent döngüsü bunu
    ölümcül sayıp durur. Aksi halde 25 iterasyon boyunca aynı duvara
    toslar ve kullanıcı ne olduğunu anlamaz.
    """


def _loop_alt_surec_acabilir_mi() -> str | None:
    """Windows'ta çalışan event loop alt süreç açabiliyor mu?

    uvicorn `--reload` (veya `--workers>1`) verildiğinde Windows'ta
    SelectorEventLoop seçiyor (uvicorn/loops/asyncio.py) ve o loop
    `asyncio.create_subprocess_exec` desteklemiyor — `NotImplementedError`
    fırlatıyor, üstelik `str(e)` BOŞ. Yani sandbox tamamen ölüyor ama
    hata mesajı hiçbir şey söylemiyor.

    Sorun varsa eyleme dönük sebebi, yoksa None döner.
    """
    if sys.platform != "win32":
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if not isinstance(loop, asyncio.SelectorEventLoop):
        return None
    return (
        "Windows'ta uvicorn --reload ile açıldın: bu modda seçilen "
        "SelectorEventLoop alt süreç açamıyor, yani sandbox hiç "
        "çalışmaz. --reload'u kaldır ya da 'python run_dev.py' kullan "
        "(otomatik yeniden yükleme sağlar, sandbox'ı bozmaz)."
    )


class SandboxManager:
    def __init__(self) -> None:
        self._kernels: dict[str, PipeKernel] = {}
        self._lock = asyncio.Lock()

    def _kernel_class(self) -> type[PipeKernel]:
        cls = _BACKENDS.get(config.SANDBOX_BACKEND)
        if cls is None:
            raise RuntimeError(
                f"Bilinmeyen SANDBOX_BACKEND={config.SANDBOX_BACKEND!r}. "
                f"Geçerli: {', '.join(_BACKENDS)}"
            )
        return cls

    async def get(self, session_id: str) -> PipeKernel:
        """Session'ın kernel'ı. Yoksa açar, ölmüşse yeniden başlatır."""
        async with self._lock:
            kernel = self._kernels.get(session_id)
            if kernel is None:
                sdir = config.session_dir(session_id)
                kernel = self._kernel_class()(
                    session_id, sdir / "data", sdir / "artifacts"
                )
                self._kernels[session_id] = kernel

        if not kernel.alive:
            try:
                await kernel.start()
            except Exception as e:
                await self.drop(session_id)  # bozuk kernel'ı havuzda tutma
                raise SandboxUnavailable(self._tanı(e)) from e
        return kernel

    def _tanı(self, e: Exception) -> str:
        """Hatayı kullanıcının yapabileceği bir eyleme çevir."""
        # Bunu EN BAŞTA sor: NotImplementedError'ın str(e)'si boş geliyor,
        # aşağıdaki metin eşlemelerinin hiçbiri onu yakalayamaz.
        if isinstance(e, NotImplementedError):
            sebep = _loop_alt_surec_acabilir_mi()
            if sebep:
                return sebep
        detay = str(e)
        if config.SANDBOX_BACKEND == "docker":
            dusuk = detay.lower()
            if "pipe" in dusuk or "connect" in dusuk or "daemon" in dusuk:
                return (
                    "Docker'a bağlanılamıyor — Docker Desktop çalışmıyor olabilir. "
                    "Başlat, ya da .env dosyasına SANDBOX_BACKEND=local yazıp "
                    "backend'i yeniden başlat (yedek mod, izolasyon yok)."
                )
            if "no such image" in dusuk or "not found" in dusuk:
                return (
                    f"Sandbox imajı yok. Şunu çalıştır: "
                    f"docker build -t {config.SANDBOX_IMAGE} ./sandbox_image"
                )
        return f"Kod çalıştırma ortamı açılamadı: {detay[:300]}"

    async def ensure(self, session_id: str) -> None:
        """Kernel'ı arka planda hazırla, çağıranı bekletme.

        Session açılırken ve veri yüklenirken çağrılır — soru geldiğinde
        container çoktan ayakta olsun diye.
        """
        try:
            await self.get(session_id)
        except Exception:
            # Hazırlık başarısızsa akışı kesme; ilk execute'ta tekrar denenir
            # ve hata oradan kullanıcıya düzgün şekilde ulaşır.
            log.exception("kernel önceden hazırlanamadı (session=%s)", session_id)

    async def drop(self, session_id: str) -> None:
        async with self._lock:
            kernel = self._kernels.pop(session_id, None)
        if kernel is not None:
            await kernel.stop()

    async def prewarm(self) -> None:
        """Açılışta Docker'ı ısıt: imaj var mı, katmanlar cache'de mi.

        Gerçek bir session container'ı değil — tek seferlik, mount'suz,
        hemen ölen bir container. Amacı ilk gerçek açılışın hızlı olması.
        """
        if config.SANDBOX_BACKEND != "docker" or not config.SANDBOX_PREWARM:
            return

        # prewarm bir OPTİMİZASYON — açılışı asla çökertmemeli.
        sebep = _loop_alt_surec_acabilir_mi()
        if sebep:
            log.warning("prewarm atlandı — %s", sebep)
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "run", "--rm", "--network", "none",
                "--entrypoint", "python", config.SANDBOX_IMAGE,
                "-c", "import pandas, numpy, duckdb, matplotlib",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        except (asyncio.TimeoutError, FileNotFoundError, OSError,
                NotImplementedError) as e:
            log.warning("prewarm atlandı: %s", e)
            return

        if proc.returncode == 0:
            log.info("sandbox imajı hazır ve ısıtıldı (%s)", config.SANDBOX_IMAGE)
        else:
            log.warning(
                "sandbox imajı çalışmıyor — SANDBOX_BACKEND=local'e düşmen "
                "gerekebilir. docker: %s",
                (err or b"").decode("utf-8", "replace").strip()[:300],
            )

    async def shutdown(self) -> None:
        async with self._lock:
            kernels = list(self._kernels.values())
            self._kernels.clear()
        for kernel in kernels:
            try:
                await kernel.stop()
            except Exception:
                log.exception("kernel kapatılamadı (session=%s)", kernel.session_id)


manager = SandboxManager()
