"""Session yaşam döngüsü ve katalog erişimi."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from backend import config
from backend.ingest.catalog import Catalog
from backend.sandbox.manager import manager

log = logging.getLogger(__name__)
router = APIRouter()


class SessionResponse(BaseModel):
    session_id: str


class DatasetView(BaseModel):
    name: str
    kind: str
    location: str
    origin: str
    row_count: int | None
    col_count: int | None
    summary: str
    sampled: bool
    notes: list[str]


class CatalogResponse(BaseModel):
    session_id: str
    datasets: list[DatasetView]
    prompt_block: str


@router.post("/sessions", response_model=SessionResponse)
async def create_session(background: BackgroundTasks) -> SessionResponse:
    """Yeni bir çalışma alanı açar.

    Kernel'ı hemen ve arka planda hazırlar: jüri dosyayı yüklerken
    container ayağa kalksın, soru yazıldığında beklemesin.
    """
    session_id = uuid.uuid4().hex[:12]
    config.session_dir(session_id)
    background.add_task(manager.ensure, session_id)
    log.info("session açıldı: %s", session_id)
    return SessionResponse(session_id=session_id)


@router.get("/sessions/{session_id}/catalog", response_model=CatalogResponse)
def get_catalog(session_id: str) -> CatalogResponse:
    _dogrula(session_id)
    katalog = Catalog(session_id)
    return CatalogResponse(
        session_id=session_id,
        datasets=[
            DatasetView(
                name=d.name, kind=d.kind, location=d.location, origin=d.origin,
                row_count=d.row_count, col_count=d.col_count, summary=d.summary,
                sampled=d.sampled, notes=d.notes,
            )
            for d in katalog.all()
        ],
        prompt_block=katalog.as_prompt_block(),
    )


@router.get("/sessions/{session_id}/schema/{name}")
def get_schema(session_id: str, name: str) -> dict:
    """Bir veri setinin tam şema kartı — frontend'de 'şema göster' için."""
    _dogrula(session_id)
    ds = Catalog(session_id).get(name)
    if ds is None:
        raise HTTPException(404, f"'{name}' adlı veri seti yok.")
    return {"name": ds.name, "schema_card": ds.schema_card}


@router.delete("/sessions/{session_id}")
async def close_session(session_id: str) -> dict:
    """Session'ı kapatır ve container'ını serbest bırakır."""
    await manager.drop(session_id)
    return {"closed": session_id}


def _dogrula(session_id: str) -> None:
    if not (config.SESSIONS_DIR / session_id).is_dir():
        raise HTTPException(404, f"Session bulunamadı: {session_id}")
