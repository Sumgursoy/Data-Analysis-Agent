"""Agent'ın çalışma bağlamı — bir kullanıcının kendi alanı.

Session; kataloğu, kernel'ı, sohbet geçmişini ve trace kaydını bir arada
tutar. Tool handler'larının ilk parametresi hep bu nesnedir: hangi
container'a kod göndereceğini, hangi kataloğu okuyacağını buradan bilir.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import config
from backend.ingest.catalog import Catalog
from backend.sandbox.base import PipeKernel
from backend.sandbox.manager import manager

log = logging.getLogger(__name__)


class AgentSession:
    def __init__(self, session_id: str):
        self.id = session_id
        self.dir: Path = config.session_dir(session_id)
        self.catalog = Catalog(session_id)
        self.history: list[dict[str, Any]] = []
        self.finished: bool = False
        self.final_summary: str = ""

    async def kernel(self) -> PipeKernel:
        return await manager.get(self.id)

    # ── trace ──────────────────────────────────────────────────

    @property
    def trace_path(self) -> Path:
        return self.dir / "trace.jsonl"

    def trace(self, kind: str, **alanlar: Any) -> None:
        """Her adımı diske yaz. .ipynb export'unun tek kaynağı budur.

        Faz 7'de notebook üretimi bu dosyadan okunacak — sonradan
        çalışan kodu geri toplamak imkânsıza yakın, o yüzden baştan yazıyoruz.
        """
        kayit = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "type": kind,
            **alanlar,
        }
        try:
            with self.trace_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(kayit, ensure_ascii=False, default=str) + "\n")
        except OSError:
            log.exception("trace yazılamadı (session=%s)", self.id)

    def read_trace(self) -> list[dict[str, Any]]:
        if not self.trace_path.exists():
            return []
        kayitlar = []
        for satir in self.trace_path.read_text(encoding="utf-8").splitlines():
            satir = satir.strip()
            if not satir:
                continue
            try:
                kayitlar.append(json.loads(satir))
            except json.JSONDecodeError:
                continue
        return kayitlar
