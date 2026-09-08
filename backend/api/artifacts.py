"""Agent'ın ürettiği grafik ve dosyaların servisi.

Grafikler modelin context'ine base64 olarak GİRMEZ; dosya olarak kaydedilir,
tool sadece yolunu döner, tarayıcı buradan çeker.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend import config

router = APIRouter()

# Yalnız bu tipler servis edilir — sandbox'ın yazdığı klasörü
# rastgele dosya sunucusuna çevirmeyelim.
IZINLI = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".csv": "text/csv",
    ".json": "application/json",
}


@router.get("/artifacts/{session_id}/{filename}")
def get_artifact(session_id: str, filename: str) -> FileResponse:
    guvenli = Path(filename).name  # yol geçişi engellenir
    if Path(guvenli).suffix.lower() not in IZINLI:
        raise HTTPException(415, f"Desteklenmeyen dosya tipi: {guvenli}")

    yol = config.SESSIONS_DIR / session_id / "artifacts" / guvenli

    # Sembolik link vb. ile klasörün dışına çıkılmadığını doğrula.
    kok = (config.SESSIONS_DIR / session_id / "artifacts").resolve()
    try:
        cozulmus = yol.resolve(strict=True)
        cozulmus.relative_to(kok)
    except (OSError, ValueError):
        raise HTTPException(404, f"Bulunamadı: {guvenli}") from None

    return FileResponse(
        cozulmus,
        media_type=IZINLI[cozulmus.suffix.lower()],
        headers={"Cache-Control": "no-cache"},
    )
