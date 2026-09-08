"""ReAct döngüsü — projenin kalbi.

    düşün → bir tool çağır → sonucu gör → tekrar düşün

Baştan tam plan yapmak yerine adım adım ilerlenir; veri analizinde doğru
desen budur, çünkü bir sonraki adım önceki adımın çıktısına bağlıdır.

Döngü event üretir, HTTP bilmez. api/chat.py bu event'leri SSE'ye çevirir.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from backend import config
from backend.agent import prompts, tools
from backend.agent.events import Event
from backend.agent.llm import LLMBackend, LLMError, QuotaError, ToolCall
from backend.agent.session import AgentSession
from backend.sandbox.manager import SandboxUnavailable

log = logging.getLogger(__name__)


async def run(
    session: AgentSession, user_message: str, backend: LLMBackend
) -> AsyncIterator[Event]:
    """Bir kullanıcı mesajını uçtan uca işler ve akış üretir."""
    session.finished = False
    session.trace("user", text=user_message)

    mesajlar: list[dict] = [
        {"role": "system", "content": prompts.build_system_prompt(
            session.catalog.as_prompt_block()
        )},
        *session.history,
        {"role": "user", "content": user_message},
    ]

    ardisik_hata = 0
    adim = 0
    giris_token = cikis_token = 0

    log.info("[%s] TUR BAŞLADI · model=%s · soru=%r",
             session.id, config.MODEL, user_message[:120])
    yield Event.status("Analiz başlıyor…")

    for adim in range(1, config.MAX_ITERATIONS + 1):
        try:
            sonuc = await backend.complete(mesajlar, tools=tools.TOOLS)
        except QuotaError as e:
            # Beklemek çözmez — kullanıcıyı doğru yere yönlendir.
            yield Event.error(str(e))
            return
        except LLMError as e:
            yield Event.error(str(e))
            return

        giris_token += sonuc.usage.input_tokens
        cikis_token += sonuc.usage.output_tokens
        log.info(
            "[%s] adım %d/%d · token %d giriş / %d çıkış (toplam %d/%d) · "
            "istenen tool: %s",
            session.id, adim, config.MAX_ITERATIONS,
            sonuc.usage.input_tokens, sonuc.usage.output_tokens,
            giris_token, cikis_token,
            ", ".join(c.name for c in sonuc.tool_calls) or "(yok — cevap veriyor)",
        )

        # Modelin düz metni: tool çağırıyorsa ara düşünce, çağırmıyorsa cevap.
        if sonuc.text.strip():
            yield (
                Event.thinking(sonuc.text)
                if sonuc.wants_tools
                else Event.message(sonuc.text)
            )

        if not sonuc.wants_tools:
            session.history += [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": sonuc.text},
            ]
            session.trace("assistant", text=sonuc.text)
            log.info("[%s] TUR BİTTİ · %d adım · %d giriş / %d çıkış token",
                     session.id, adim, giris_token, cikis_token)
            yield Event.done(
                steps=adim, input_tokens=giris_token, output_tokens=cikis_token
            )
            return

        mesajlar.append(sonuc.raw_message or {
            "role": "assistant",
            "content": sonuc.text or None,
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": "{}"}}
                for c in sonuc.tool_calls
            ],
        })

        for cagri in sonuc.tool_calls:
            # Kodu ANINDA göster — kullanıcı beklerken ne yapıldığını görsün.
            yield Event.tool_call(cagri.id, cagri.name, cagri.arguments)

            # Argümanları TAM logla: SQL ekranda gizli olsa bile burada durur.
            log.debug("[%s] adım %d · %s çağrılıyor · args=%r",
                      session.id, adim, cagri.name, cagri.arguments)
            try:
                tool_sonuc = await tools.dispatch(session, cagri.name, cagri.arguments)
            except SandboxUnavailable as e:
                log.error("[%s] adım %d · SANDBOX ÖLÜ · %s", session.id, adim, e)
                # Altyapı sorunu — model bunu kod yazarak çözemez, durdur.
                yield Event.error(str(e))
                return

            if tool_sonuc.ok:
                log.info("[%s] adım %d · %s OK · %d ms · çıktı %d karakter%s",
                         session.id, adim, cagri.name, tool_sonuc.duration_ms,
                         len(tool_sonuc.text or ""),
                         f" · artifact: {tool_sonuc.artifacts}" if tool_sonuc.artifacts else "")
            else:
                # Hata mesajının TAMAMI dosyaya gitsin — kırpılmış traceback
                # ile hata aramak zaman kaybı.
                log.warning("[%s] adım %d · %s HATA (%s)\n%s",
                            session.id, adim, cagri.name,
                            tool_sonuc.error_type, tool_sonuc.text)

            for ev in _sonuc_eventleri(session, cagri, tool_sonuc):
                yield ev

            mesajlar.append({
                "role": "tool",
                "tool_call_id": cagri.id,
                "content": tool_sonuc.text or "(çıktı yok)",
            })

            if not tool_sonuc.ok:
                ardisik_hata += 1
                if ardisik_hata >= config.MAX_CONSECUTIVE_ERRORS:
                    # Aynı duvara toslamayı sürdürmesin: kernel'ı sıfırla,
                    # yaklaşımını değiştirmesi için açık talimat ver.
                    log.warning(
                        "[%s] adım %d · %d ardışık hata → KERNEL SIFIRLANIYOR",
                        session.id, adim, config.MAX_CONSECUTIVE_ERRORS,
                    )
                    yield Event.status(
                        "Aynı hata tekrarlıyor — kernel sıfırlanıyor, "
                        "yaklaşım değiştiriliyor…"
                    )
                    kernel = await session.kernel()
                    await kernel.restart()
                    mesajlar.append(
                        {"role": "user", "content": prompts.KURTARMA_NOTU}
                    )
                    ardisik_hata = 0
            else:
                ardisik_hata = 0

        if session.finished:
            ozet = session.final_summary
            if ozet.strip():
                yield Event.message(ozet)
            session.history += [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": ozet},
            ]
            yield Event.done(
                steps=adim, input_tokens=giris_token, output_tokens=cikis_token
            )
            return

    # Bütçe bitti — yarım kalmış olabilir, kullanıcıya açıkça söyle.
    log.warning("[%s] BÜTÇE BİTTİ · %d adım sınırına ulaşıldı · %d/%d token",
                session.id, config.MAX_ITERATIONS, giris_token, cikis_token)
    yield Event.status(
        f"{config.MAX_ITERATIONS} adım sınırına ulaşıldı. "
        "Soruyu daraltarak tekrar sorabilirsin."
    )
    yield Event.done(
        steps=adim,
        input_tokens=giris_token,
        output_tokens=cikis_token,
        reason="iteration_budget_exhausted",
    )


def _sonuc_eventleri(
    session: AgentSession, cagri: ToolCall, sonuc: tools.ToolResult
):
    """Bir tool sonucundan çıkan event'ler."""
    if sonuc.ok:
        yield Event.tool_result(cagri.id, cagri.name, sonuc.text, sonuc.duration_ms)
    else:
        # Hatayı GİZLEME. Agent'ın toparlandığını görmek güven artırır.
        yield Event.tool_error(
            cagri.id, cagri.name, sonuc.error_type or "Error", sonuc.text
        )

    for dosya in sonuc.artifacts:
        yield Event.artifact(session.id, dosya)

    if cagri.name == "add_dataset" and sonuc.ok and session.catalog.all():
        ds = session.catalog.all()[-1]
        yield Event.dataset_ready(ds.name, ds.row_count, ds.col_count)
