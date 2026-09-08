"""Durum kontrolü — 'her şey ayakta mı' sorusunun tek cevabı.

Demo günü ilk bakılacak yer burası. Docker'ın gerçekten çalışıp
çalışmadığını da söyler; sunum sırasında sürpriz yaşamamak için.
"""

import logging

from fastapi import APIRouter

from backend import config
from backend.sandbox.manager import _loop_alt_surec_acabilir_mi

log = logging.getLogger(__name__)
router = APIRouter()


def _docker_status() -> dict:
    """Docker gerçekten konuşuyor mu ve imaj yüklü mü?"""
    if config.SANDBOX_BACKEND != "docker":
        return {"checked": False, "reason": f"SANDBOX_BACKEND={config.SANDBOX_BACKEND}"}

    try:
        import docker
    except ImportError:
        return {"available": False, "error": "docker paketi kurulu değil"}

    try:
        client = docker.from_env()
        client.ping()
        images = {t for img in client.images.list() for t in (img.tags or [])}
        return {
            "available": True,
            "image_ready": config.SANDBOX_IMAGE in images,
            "image": config.SANDBOX_IMAGE,
        }
    except Exception as e:  # docker.errors.DockerException ve alt tipleri
        return {"available": False, "error": str(e)[:200]}


@router.get("/health")
async def health():
    # async OLMALI: _loop_alt_surec_acabilir_mi() çalışan loop'a bakıyor,
    # senkron endpoint threadpool'da koşar ve orada loop görünmez.
    engel = _loop_alt_surec_acabilir_mi()
    return {
        "status": "degraded" if engel else "ok",
        "llm": {
            "backend": config.LLM_BACKEND,
            "model": config.MODEL,
            "base_url": config.LLM_BASE_URL or "OpenAI (varsayılan)",
            "api_key_set": bool(config.API_KEY),
        },
        "sandbox": {
            "backend": config.SANDBOX_BACKEND,
            "timeout_sec": config.SANDBOX_TIMEOUT_SEC,
            "memory": config.SANDBOX_MEMORY,
            "prewarm": config.SANDBOX_PREWARM,
            "docker": _docker_status(),
            # Docker ayakta olsa BİLE sandbox ölü olabilir: Windows'ta
            # uvicorn --reload alt süreç açamayan bir loop seçiyor.
            # Bu satır olmadan health yeşil görünür ama run_python patlar.
            "blocker": _loop_alt_surec_acabilir_mi(),
        },
        "agent": {
            "max_iterations": config.MAX_ITERATIONS,
        },
    }
