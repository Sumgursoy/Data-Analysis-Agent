"""FastAPI uygulaması — router'ları bağlar, CORS'u açar.

Çalıştırma:
    python run_dev.py                      # geliştirme (otomatik yeniden yükleme)
    uvicorn backend.main:app --port 8000   # düz çalıştırma

WINDOWS'TA `uvicorn --reload` KULLANMA. O modda uvicorn SelectorEventLoop
seçiyor; o loop alt süreç açamadığı için sandbox'ın tamamı (docker/local
kernel ve prewarm) `NotImplementedError` ile ölüyor — üstelik `str(e)` boş
olduğu için hata mesajı hiçbir şey söylemiyor. Aynı faydayı bozmadan veren
sarmalayıcı: `run_dev.py`.
"""

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import config
from backend.api import artifacts, chat, export, health, sessions, sources
from backend.sandbox.manager import manager

def _loglama_kur() -> None:
    """Konsol + döner dosya.

    Konsol sade kalsın (LOG_LEVEL, varsayılan INFO); dosyaya HER ZAMAN
    DEBUG yazılsın. Demo sırasında konsol kaydırıp gidiyor ve bir hatanın
    öncesindeki adımları geri okumak imkânsızlaşıyordu — dosya o boşluğu
    kapatıyor. Tek kaynak: storage/app.log
    """
    kok = logging.getLogger()
    kok.setLevel(logging.DEBUG)
    for h in list(kok.handlers):        # basicConfig'in kalıntısını temizle
        kok.removeHandler(h)

    bicim = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)-28s  %(message)s",
        datefmt="%H:%M:%S",
    )

    konsol = logging.StreamHandler()
    konsol.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    konsol.setFormatter(bicim)
    kok.addHandler(konsol)

    try:
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        dosya = RotatingFileHandler(
            config.LOG_FILE,
            maxBytes=config.LOG_FILE_MAX_MB * 1024 * 1024,
            backupCount=config.LOG_FILE_BACKUPS,
            encoding="utf-8",
        )
        dosya.setLevel(logging.DEBUG)
        dosya.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s  %(name)-28s  %(message)s"
        ))
        kok.addHandler(dosya)
    except OSError as e:
        # Log dosyası açılamıyorsa uygulamayı düşürme — konsol yeter.
        kok.warning("log dosyası açılamadı (%s): %s", config.LOG_FILE, e)

    # Gürültülü kütüphaneler: DEBUG'da her HTTP isteğinin gövdesini basıp
    # log dosyasını kullanılmaz hale getiriyorlar. Bizim satırlarımız kaybolmasın.
    for ad in ("httpx", "httpcore", "openai", "urllib3", "watchfiles", "docker"):
        logging.getLogger(ad).setLevel(logging.WARNING)


_loglama_kur()

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    log.info(
        "Agentic Data Analyst hazır — model=%s sandbox=%s. "
        "Durum için /api/health",
        config.MODEL,
        config.SANDBOX_BACKEND,
    )
    log.info("log dosyası: %s (seviye: konsol=%s, dosya=DEBUG)",
             config.LOG_FILE, config.LOG_LEVEL)
    # Docker katmanlarını ısıt — ilk gerçek container hızlı açılsın.
    await manager.prewarm()

    yield

    # Açık kalan container'ları kapat; yoksa docker'da artık birikir.
    await manager.shutdown()


app = FastAPI(title="Agentic Data Analyst", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(artifacts.router, prefix="/api")
app.include_router(export.router, prefix="/api")

# Sıradaki router'lar (MIMARI.md bölüm 9):
#   sources.py   → POST /api/sources         (Faz 2)
#   sessions.py  → POST /api/sessions        (Faz 2)
#   chat.py      → POST /api/chat  [SSE]     (Faz 3)
#   artifacts.py → GET  /api/artifacts/...   (Faz 3)
#   export.py    → GET  /api/export/....ipynb (Faz 7)
