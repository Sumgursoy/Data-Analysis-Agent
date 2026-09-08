"""Agent sohbeti — SSE akışı.

Tüm agent turu TEK endpoint içinde geçer. Tool'lar HTTP ucu değildir;
döngü backend sürecinde döner ve her adımı bu akıştan yayınlar.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend import config
from backend.agent import loop
from backend.agent.events import Event
from backend.agent.llm import LLMError, build_backend
from backend.agent.session import AgentSession

log = logging.getLogger(__name__)
router = APIRouter()

# Session'lar tur boyunca hafızada tutulur ki sohbet geçmişi korunsun.
_OTURUMLAR: dict[str, AgentSession] = {}


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1)


def _oturum(session_id: str) -> AgentSession:
    oturum = _OTURUMLAR.get(session_id)
    if oturum is None:
        oturum = AgentSession(session_id)
        _OTURUMLAR[session_id] = oturum
    else:
        oturum.catalog = type(oturum.catalog)(session_id)  # katalogu tazele
    return oturum


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    if not (config.SESSIONS_DIR / req.session_id).is_dir():
        raise HTTPException(404, f"Session bulunamadı: {req.session_id}")

    oturum = _oturum(req.session_id)

    async def akis() -> AsyncIterator[str]:
        try:
            backend = build_backend()
        except LLMError as e:
            yield Event.error(str(e)).to_sse()
            return

        try:
            async for ev in loop.run(oturum, req.message, backend):
                yield ev.to_sse()
        except Exception as e:
            log.exception("agent döngüsü patladı (session=%s)", req.session_id)
            yield Event.error(f"Beklenmedik hata: {e}").to_sse()

    return StreamingResponse(
        akis(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # ara sunucu akışı tamponlamasın
        },
    )


@router.get("/sessions/{session_id}/trace")
def get_trace(session_id: str) -> dict:
    """Birikmiş adımlar — sağdaki 'analiz defteri' paneli ve ipynb export için."""
    if not (config.SESSIONS_DIR / session_id).is_dir():
        raise HTTPException(404, f"Session bulunamadı: {session_id}")
    return {"session_id": session_id, "steps": AgentSession(session_id).read_trace()}
