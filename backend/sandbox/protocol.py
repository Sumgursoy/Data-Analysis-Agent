"""Backend ↔ kernel arasındaki konuşma dili.

sandbox_image/kernel_server.py ile eşleşir. Satır başına bir JSON.
Bu modül saf veri — Docker'ı da subprocess'i de bilmez.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# sandbox_image/kernel_server.py:PROTOCOL_VERSION ile eşleşmeli.
# Eşleşmiyorsa container eski bir imajdan koşuyordur — build gerekir.
EXPECTED_PROTOCOL_VERSION = 6


@dataclass
class ExecResult:
    """Bir kod çalıştırmasının sonucu."""

    ok: bool
    stdout: str = ""
    stderr: str = ""
    result_repr: str = ""
    error_type: str | None = None
    traceback: str | None = None
    new_artifacts: list[str] = field(default_factory=list)
    duration_ms: int = 0

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> ExecResult:
        return cls(
            ok=bool(payload.get("ok")),
            stdout=payload.get("stdout") or "",
            stderr=payload.get("stderr") or "",
            result_repr=payload.get("result_repr") or "",
            error_type=payload.get("error_type"),
            traceback=payload.get("traceback"),
            new_artifacts=list(payload.get("new_artifacts") or []),
            duration_ms=int(payload.get("duration_ms") or 0),
        )

    @classmethod
    def timeout(cls, seconds: int) -> ExecResult:
        return cls(
            ok=False,
            error_type="TimeoutError",
            traceback=(
                f"Kod {seconds} saniyede bitmedi ve durduruldu.\n"
                "Veriyi daralt (örnekleme, filtre, LIMIT) veya işlemi böl."
            ),
        )

    @classmethod
    def crashed(cls, detail: str) -> ExecResult:
        return cls(
            ok=False,
            error_type="KernelCrash",
            traceback=f"Kernel beklenmedik şekilde kapandı: {detail}",
        )

    def as_text(self, limit: int) -> str:
        """Modele gidecek metin. Traceback'i kısaltma — asıl bilgi orada."""
        parts: list[str] = []
        if self.stdout.strip():
            parts.append(self.stdout.rstrip())
        if self.result_repr.strip():
            parts.append(self.result_repr.rstrip())
        if self.stderr.strip():
            parts.append(f"[stderr]\n{self.stderr.rstrip()}")
        if not self.ok and self.traceback:
            parts.append(self.traceback.rstrip())
        if self.new_artifacts:
            parts.append("[kaydedilen dosyalar] " + ", ".join(self.new_artifacts))

        text = "\n\n".join(parts) or "(çıktı yok)"
        return _clip(text, limit)


def encode(msg_id: str, *, code: str | None = None, op: str = "exec") -> str:
    payload: dict[str, Any] = {"id": msg_id, "op": op}
    if code is not None:
        payload["code"] = code
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _clip(text: str, limit: int) -> str:
    """Baş ve sonu koru, ortayı at — traceback'te asıl hata sonda olur."""
    if len(text) <= limit:
        return text
    head, tail = limit // 2, limit // 2
    kesilen = len(text) - limit
    return f"{text[:head]}\n\n... [{kesilen:,} karakter kırpıldı] ...\n\n{text[-tail:]}"
