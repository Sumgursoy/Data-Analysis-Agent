"""SSE event tipleri — frontend'in gördüğü akış.

Tasarım ilkesi 4: her şey şeffaf. Agent'ın yazdığı kod, aldığı çıktı,
YAPTIĞI HATA ve düzeltmesi canlı akar. Hataları gizlemek jüride
kaybettirir; hata yapıp toparlandığını görmek güven verir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backend import config


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """text/event-stream biçimi. Tek satır JSON — newline kaçırılır."""
        govde = json.dumps({"type": self.type, **self.data}, ensure_ascii=False)
        return f"data: {govde}\n\n"

    # ── üreticiler ─────────────────────────────────────────────

    @staticmethod
    def status(text: str) -> Event:
        """Kullanıcıya 'ne oluyor' bilgisi: 'veri profilleniyor…' gibi."""
        return Event("status", {"text": text})

    @staticmethod
    def thinking(text: str) -> Event:
        return Event("thinking", {"text": text})

    @staticmethod
    def message(text: str) -> Event:
        return Event("message", {"text": text})

    @staticmethod
    def tool_call(call_id: str, name: str, args: dict) -> Event:
        # Kod ayrı alanda gidiyor ki frontend syntax highlight yapabilsin.
        kod = args.get("code") or args.get("query") or ""

        # SQL metnini ekrandan gizle (config.SHOW_SQL_IN_CHAT). Sadece
        # GÖSTERİM kısıtı: sorgu loga, trace.jsonl'a ve .ipynb'ye tam gider.
        if name == "run_sql" and not config.SHOW_SQL_IN_CHAT:
            kod = ""

        return Event(
            "tool_call",
            {
                "id": call_id,
                "name": name,
                "code": kod,
                # Defter panelinin doğru sayması için: bu adım çalıştırılabilir
                # mi? Kod metni gizlense bile notebook'ta bir hücre oluşturur.
                "executable": name in ("run_python", "run_sql"),
                "args": {k: v for k, v in args.items() if k not in ("code", "query")},
            },
        )

    @staticmethod
    def tool_result(call_id: str, name: str, text: str, duration_ms: int = 0) -> Event:
        return Event(
            "tool_result",
            {"id": call_id, "name": name, "text": text, "duration_ms": duration_ms},
        )

    @staticmethod
    def tool_error(call_id: str, name: str, error_type: str, text: str) -> Event:
        return Event(
            "tool_error",
            {"id": call_id, "name": name, "error_type": error_type, "text": text},
        )

    @staticmethod
    def artifact(session_id: str, filename: str, caption: str = "") -> Event:
        return Event(
            "artifact",
            {
                "kind": "chart",
                "filename": filename,
                "url": f"/api/artifacts/{session_id}/{filename}",
                "caption": caption,
            },
        )

    @staticmethod
    def dataset_ready(name: str, rows: int | None, cols: int | None) -> Event:
        return Event("dataset_ready", {"name": name, "rows": rows, "cols": cols})

    @staticmethod
    def error(text: str, fatal: bool = True) -> Event:
        return Event("error", {"text": text, "fatal": fatal})

    @staticmethod
    def done(
        *, steps: int, input_tokens: int, output_tokens: int, reason: str = "completed"
    ) -> Event:
        return Event(
            "done",
            {
                "steps": steps,
                "reason": reason,
                "usage": {"input": input_tokens, "output": output_tokens},
            },
        )
