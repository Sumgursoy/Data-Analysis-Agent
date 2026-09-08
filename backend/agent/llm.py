"""LLM erişimi için ince soyutlama katmanı.

Neden var: yarışma "veri dışarı çıkamaz, yerel model kullanın" derse
agent döngüsünü baştan yazmak zorunda kalmayalım. Döngü sadece
`LLMBackend` arayüzünü görür; hangi sunucuya bağlanıldığı config meselesi.

İki arayüz var:
  responses → /v1/responses — OpenAI'a özel.
  chat      → /v1/chat/completions — evrensel standart.
              Ollama, vLLM, LM Studio, OpenRouter hepsi bunu konuşur.

ÖLÇÜMLE BULUNAN KISIT (gpt-5.6 ailesi):
    chat + function tools + reasoning  →  HTTP 400
    "Function tools with reasoning_effort are not supported ...
     use /v1/responses or set reasoning_effort to 'none'"

Yani chat arayüzünde tool kullanmak için akıl yürütmeyi KAPATMAK gerekiyor.
Kod yazıp hatasını düzelten bir agent için bu ciddi kalite kaybı; üstelik
ölçümde responses aynı istek için daha az girdi token'ı harcadı (55 vs 137).

Bu yüzden varsayılan: OpenAI'da `responses`, farklı bir sunucuya
bağlanıldığında (LLM_BASE_URL doluysa) `chat`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai

from backend import config

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
#  Nötr veri tipleri — döngünün gördüğü tek şey bunlar
# ─────────────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """Modelin 'şu tool'u şu argümanlarla çağır' önerisi.

    Model hiçbir şey çalıştırmaz; bu sadece bir niyet beyanı.
    Çağrıyı agent/loop.py yapar.
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw_message: dict[str, Any] | None = None  # geçmişe eklenecek ham mesaj

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMError(RuntimeError):
    """Backend'den bağımsız, kullanıcıya gösterilebilir LLM hatası."""


class QuotaError(LLMError):
    """Kredi/kota bitti — beklemek işe yaramaz, para yüklemek gerekir.

    OpenAI bunu da HTTP 429 ile döndürüyor, yani gerçek rate limit ile
    aynı koda düşüyor. Ayırmazsak demo günü 'biraz bekle' deyip
    boşuna zaman kaybedersin.
    """


def _kota_hatasi_mi(e: openai.RateLimitError) -> bool:
    govde = getattr(e, "body", None)
    hata = govde.get("error", govde) if isinstance(govde, dict) else {}
    imzalar = {str(hata.get("type", "")), str(hata.get("code", ""))}
    if imzalar & {"insufficient_quota", "credit_balance_exhausted"}:
        return True
    return "credit" in str(getattr(e, "message", "")).lower()


# ─────────────────────────────────────────────────────────────────
#  Arayüz
# ─────────────────────────────────────────────────────────────────


class LLMBackend(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResult: ...


# ─────────────────────────────────────────────────────────────────
#  Chat Completions — evrensel
# ─────────────────────────────────────────────────────────────────


class ChatCompletionsBackend:
    """/v1/chat/completions konuşan her sunucu ile çalışır."""

    def __init__(self, *, api_key: str, base_url: str | None, model: str):
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def complete(self, messages, tools=None) -> LLMResult:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_completion_tokens": config.MAX_OUTPUT_TOKENS,
        }
        if tools:
            kwargs["tools"] = tools
            # gpt-5.6 ailesi bu uçta tool + akıl yürütmeyi birlikte kabul
            # etmiyor. Açık modellerde bu parametre yok sayılır.
            if config.CHAT_REASONING_EFFORT:
                kwargs["reasoning_effort"] = config.CHAT_REASONING_EFFORT

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as e:
            raise LLMError("API anahtarı geçersiz. .env dosyasını kontrol et.") from e
        except openai.RateLimitError as e:
            if _kota_hatasi_mi(e):
                raise QuotaError(
                    "Hesapta kredi kalmadı. platform.openai.com → Settings → "
                    "Billing üzerinden kredi yükle. (Beklemek çözmez.)"
                ) from e
            raise LLMError("Rate limit'e takıldık. Biraz bekleyip tekrar dene.") from e
        except openai.APIConnectionError as e:
            target = config.LLM_BASE_URL or "OpenAI"
            raise LLMError(f"{target} adresine bağlanılamadı: {e}") from e
        except openai.APIStatusError as e:
            raise LLMError(f"LLM API hatası ({e.status_code}): {e.message}") from e

        msg = response.choices[0].message

        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=_parse_arguments(tc.function.arguments, tc.function.name),
                )
            )

        usage = Usage()
        if response.usage:
            usage = Usage(
                input_tokens=response.usage.prompt_tokens or 0,
                output_tokens=response.usage.completion_tokens or 0,
            )

        return LLMResult(
            text=msg.content or "",
            tool_calls=calls,
            usage=usage,
            raw_message=msg.model_dump(exclude_none=True),
        )


# ─────────────────────────────────────────────────────────────────
#  Responses — OpenAI'a özel
# ─────────────────────────────────────────────────────────────────


class ResponsesBackend:
    """OpenAI'ın /v1/responses arayüzü — tool + akıl yürütme birlikte.

    Döngü nötr (chat biçimli) mesaj geçmişi tutuyor; çeviri burada
    yapılıyor. Böylece backend değişimi döngüyü etkilemiyor.

    Durum sunucuda TUTULMUYOR (`previous_response_id` kullanılmıyor):
    geçmişi her turda biz gönderiyoruz. Ölçüldü, çalışıyor; yeniden
    denemeler ve session yalıtımı böylece basit kalıyor.
    """

    def __init__(self, *, api_key: str, base_url: str | None, model: str):
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def complete(self, messages, tools=None) -> LLMResult:
        talimat, girdi = _responses_girdisi(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "input": girdi,
            "max_output_tokens": config.MAX_OUTPUT_TOKENS,
        }
        if talimat:
            kwargs["instructions"] = talimat
        if tools:
            kwargs["tools"] = [_responses_tool(t) for t in tools]

        try:
            yanit = await self._client.responses.create(**kwargs)
        except openai.AuthenticationError as e:
            raise LLMError("API anahtarı geçersiz. .env dosyasını kontrol et.") from e
        except openai.RateLimitError as e:
            if _kota_hatasi_mi(e):
                raise QuotaError(
                    "Hesapta kredi kalmadı. platform.openai.com → Settings → "
                    "Billing üzerinden kredi yükle. (Beklemek çözmez.)"
                ) from e
            raise LLMError("Rate limit'e takıldık. Biraz bekleyip tekrar dene.") from e
        except openai.APIConnectionError as e:
            hedef = config.LLM_BASE_URL or "OpenAI"
            raise LLMError(f"{hedef} adresine bağlanılamadı: {e}") from e
        except openai.APIStatusError as e:
            raise LLMError(f"LLM API hatası ({e.status_code}): {e.message}") from e

        calls: list[ToolCall] = []
        for parca in yanit.output or []:
            if getattr(parca, "type", "") == "function_call":
                calls.append(
                    ToolCall(
                        id=parca.call_id,
                        name=parca.name,
                        arguments=_parse_arguments(parca.arguments, parca.name),
                    )
                )

        usage = Usage()
        if yanit.usage:
            usage = Usage(
                input_tokens=yanit.usage.input_tokens or 0,
                output_tokens=yanit.usage.output_tokens or 0,
            )

        # Geçmiş nötr (chat) biçimde tutuluyor — çeviri tek yerde kalsın.
        ham: dict[str, Any] = {"role": "assistant", "content": yanit.output_text or None}
        if calls:
            ham["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments,
                                                                      ensure_ascii=False)}}
                for c in calls
            ]

        return LLMResult(
            text=yanit.output_text or "",
            tool_calls=calls,
            usage=usage,
            raw_message=ham,
        )


def _responses_tool(chat_tool: dict[str, Any]) -> dict[str, Any]:
    """Chat biçimindeki tool şemasını Responses biçimine çevirir.

    chat:      {"type":"function","function":{"name":..,"parameters":..}}
    responses: {"type":"function","name":..,"parameters":..}   (düz)
    """
    fn = chat_tool.get("function", chat_tool)
    return {
        "type": "function",
        "name": fn.get("name", ""),
        "description": fn.get("description", ""),
        "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
    }


def _responses_girdisi(messages: list[dict[str, Any]]) -> tuple[str, list[dict]]:
    """Nötr geçmişi (instructions, input) çiftine çevirir."""
    talimatlar: list[str] = []
    girdi: list[dict[str, Any]] = []

    for m in messages:
        rol = m.get("role")

        if rol == "system":
            talimatlar.append(str(m.get("content") or ""))

        elif rol == "user":
            girdi.append({"role": "user", "content": str(m.get("content") or "")})

        elif rol == "assistant":
            icerik = m.get("content")
            if icerik:
                girdi.append({"role": "assistant", "content": str(icerik)})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                girdi.append({
                    "type": "function_call",
                    "call_id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })

        elif rol == "tool":
            girdi.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output": str(m.get("content") or ""),
            })

    return "\n\n".join(t for t in talimatlar if t), girdi


# ─────────────────────────────────────────────────────────────────
#  Fabrika
# ─────────────────────────────────────────────────────────────────

_BACKENDS = {
    "chat": ChatCompletionsBackend,
    "responses": ResponsesBackend,
}


def build_backend() -> LLMBackend:
    """config'e göre doğru backend'i kurar."""
    if not config.API_KEY:
        raise LLMError(
            "OPENAI_API_KEY tanımlı değil. .env.example dosyasını .env olarak "
            "kopyalayıp anahtarını yaz, sonra backend'i yeniden başlat. "
            "(Yerel modelde de bir değer gerekir — 'ollama' gibi bir şey yazman yeterli.)"
        )

    cls = _BACKENDS.get(config.LLM_BACKEND)
    if cls is None:
        raise LLMError(
            f"Bilinmeyen LLM_BACKEND={config.LLM_BACKEND!r}. "
            f"Geçerli değerler: {', '.join(_BACKENDS)}"
        )

    log.info(
        "LLM backend=%s  model=%s  base_url=%s",
        config.LLM_BACKEND,
        config.MODEL,
        config.LLM_BASE_URL or "OpenAI (varsayılan)",
    )
    return cls(
        api_key=config.API_KEY,
        base_url=config.LLM_BASE_URL,
        model=config.MODEL,
    )


def _parse_arguments(raw: str | None, tool_name: str) -> dict[str, Any]:
    """Model bazen bozuk JSON üretir; döngüyü çökertme, hatayı tool'a taşı."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("%s için bozuk argüman JSON'u: %r", tool_name, raw[:200])
        return {"__parse_error__": raw}
    return parsed if isinstance(parsed, dict) else {"__parse_error__": raw}
