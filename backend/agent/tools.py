"""Tool şemaları ve handler'ları.

Model bu şemaları görür ve "şu tool'u şu argümanlarla çağır" diyen bir
JSON döndürür. Çağrıyı model DEĞİL, buradaki handler yapar — HTTP yok,
MCP yok; bir sözlük ve düz fonksiyon çağrısı.

Handler imzası her zaman `(session, **args)`. Session ilk parametre
olduğu için hangi kernel'a, hangi kataloğa gidileceği belirsiz kalmaz.

Faz 3 kapsamı 5 tool. `run_sql` Faz 4'te, `fetch_url` Faz 5'te eklenecek.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from backend import config
from backend import query as query_engine
from backend.agent.session import AgentSession
from backend.fetch import client as fetch_client
from backend.ingest import profile as P
from backend.ingest.adapters import web as web_adapter
from backend.ingest.catalog import Dataset
from backend.ingest.normalize import slug
from backend.sandbox.manager import SandboxUnavailable

log = logging.getLogger(__name__)


@dataclass
class ToolResult:
    text: str
    ok: bool = True
    error_type: str | None = None
    artifacts: list[str] = field(default_factory=list)
    duration_ms: int = 0


# ── şemalar ─────────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_datasets",
            "description": "Kataloğdaki tüm veri setlerini listeler (isim + tek satır özet).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": (
                "Bir veri setinin ayrıntılı şema kartını döner: kolon adları, "
                "tipleri, boş oranları, dağılım özetleri, uyarılar ve ilişki "
                "adayları. Veriye dokunmadan ÖNCE bunu çağır."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Veri setinin adı"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Kalıcı Python kernel'ında kod çalıştırır. Değişkenler çağrılar "
                "arasında korunur. pd, np, plt, duckdb hazır. Dosya yolu için "
                "data_path('dosya.parquet') ve artifact_path('grafik.png') "
                "kullan — sabit yol yazma. İnternet yok. Son satır bir ifadeyse "
                "değeri de döner."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Çalıştırılacak Python kodu"}
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Veri setleri üzerinde SQL (DuckDB lehçesi) çalıştırır. Her veri "
                "seti kendi adıyla tablo gibi sorgulanır: SELECT * FROM satis. "
                "Sadece SELECT. Büyük veride ÖNCE bunu kullanıp daralt, sonra "
                "run_python ile derinleş. Sonuç büyükse otomatik olarak "
                "kataloğa yeni bir veri seti olarak kaydedilir."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "SELECT sorgusu"},
                    "save_as": {
                        "type": "string",
                        "description": (
                            "Sonucu bu adla kataloğa kaydet (isteğe bağlı). "
                            "Büyük sonuçlarda ad verilmezse otomatik üretilir."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Bir web sayfasını indirir, /data altına kaydeder ve sayfanın "
                "YAPI HARİTASINI döner (tablolar, listeler, ana metin, arka "
                "planda çağrılan veri uçları). Ham HTML dönmez. Haritaya bakıp "
                "run_python ile kendi ayrıştırıcını yaz: önce pd.read_html, "
                "olmazsa selectolax + CSS seçici, olmazsa JSON uçları. "
                "İçerik JS ile geliyorsa render=true ver (pahalı, son çare)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http/https adresi"},
                    "render": {
                        "type": "boolean",
                        "description": "JS çalıştırılsın mı (varsayılan false)",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_dataset",
            "description": (
                "Türettiğin bir tabloyu kataloğa kaydeder, böylece sonraki "
                "adımlar üstüne inşa eder. Önce run_python ile "
                "df.to_parquet(artifact_path('ad.parquet')) çalıştır, "
                "sonra dosya adını buraya ver."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "/artifacts altındaki parquet dosyasının adı",
                    },
                    "name": {"type": "string", "description": "Kataloğa girecek ad"},
                    "note": {"type": "string", "description": "Bu tablo nedir, nasıl üretildi"},
                },
                "required": ["filename", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "Analizi bitirir ve bulguları sunar. Bulguları önem sırasına diz, "
                "her birini sayıyla destekle."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Bulgular, Türkçe"}
                },
                "required": ["summary"],
            },
        },
    },
]


# ── handler'lar ─────────────────────────────────────────────────


async def handle_list_datasets(session: AgentSession) -> ToolResult:
    return ToolResult(text=session.catalog.as_prompt_block())


async def handle_get_schema(session: AgentSession, name: str = "") -> ToolResult:
    ds = session.catalog.get(name)
    if ds is None:
        mevcut = ", ".join(session.catalog.names()) or "(katalog boş)"
        return ToolResult(
            text=f"'{name}' adlı veri seti yok. Mevcut olanlar: {mevcut}",
            ok=False,
            error_type="DatasetNotFound",
        )
    return ToolResult(text=ds.schema_card)


async def handle_run_python(session: AgentSession, code: str = "") -> ToolResult:
    if not code.strip():
        return ToolResult(text="Boş kod gönderildi.", ok=False, error_type="EmptyCode")

    kernel = await session.kernel()
    res = await kernel.execute(code)

    session.trace("code", code=code, ok=res.ok, duration_ms=res.duration_ms)
    if res.stdout.strip():
        session.trace("stdout", text=res.stdout)
    for a in res.new_artifacts:
        session.trace("artifact", path=a)

    return ToolResult(
        text=res.as_text(config.MAX_TOOL_OUTPUT_CHARS),
        ok=res.ok,
        error_type=res.error_type,
        artifacts=res.new_artifacts,
        duration_ms=res.duration_ms,
    )


async def handle_run_sql(
    session: AgentSession, query: str = "", save_as: str = ""
) -> ToolResult:
    if not query.strip():
        return ToolResult(text="Boş sorgu.", ok=False, error_type="EmptyQuery")

    motor = query_engine.get_engine(session.id)

    # Ekranda gizli olabilir (config.SHOW_SQL_IN_CHAT) — logda ASLA gizli değil.
    log.info("[%s] SQL:\n%s", session.id, query.strip())

    try:
        df, calisan_sql = motor.run(query)
    except query_engine.UnsafeQuery as e:
        log.warning("[%s] SQL REDDEDİLDİ: %s", session.id, e)
        return ToolResult(text=str(e), ok=False, error_type="UnsafeQuery")
    except Exception as e:
        # DuckDB hataları modele aynen gitsin — kolon adı hatasını kendi düzeltir.
        tablolar = ", ".join(motor.tables()) or "(yok)"
        return ToolResult(
            text=f"{type(e).__name__}: {e}\n\nSorgulanabilir tablolar: {tablolar}",
            ok=False,
            error_type=type(e).__name__,
        )

    session.trace("sql", query=calisan_sql, rows=len(df))

    kucuk = len(df) <= query_engine.INLINE_SATIR_LIMITI
    if kucuk and not save_as:
        return ToolResult(text=query_engine.frame_to_text(df))

    # Büyük sonuç modele basılmaz: parquet'e yazılıp kataloğa girer,
    # agent sandbox'ta okuyarak devam eder.
    ad = slug(save_as or query_engine.sonuc_dosya_adi(query), fallback="sorgu_sonucu")
    hedef = session.dir / "data" / f"{ad}.parquet"
    df.to_parquet(hedef, index=False)

    kart = P.build_schema_card(
        df, name=ad, origin=f"SQL: {calisan_sql[:120]}",
        location=f"/data/{hedef.name}", total_rows=len(df),
    )
    ds = session.catalog.add(
        Dataset(
            name=ad, kind="derived", location=f"/data/{hedef.name}",
            origin=f"SQL sorgusu ({len(df)} satır)", row_count=len(df),
            col_count=int(df.shape[1]), schema_card=kart,
            summary=f"SQL sonucu: {calisan_sql[:80]}", created_by="agent",
        )
    )
    motor.refresh_views()
    session.trace("dataset", name=ds.name, rows=len(df), cols=int(df.shape[1]))

    return ToolResult(
        text=(
            f"{len(df):,} satır döndü — kataloğa '{ds.name}' olarak kaydedildi.\n"
            f"Python'da: pd.read_parquet(data_path('{hedef.name}'))\n"
            f"SQL'de:    SELECT * FROM {ds.name}\n\n"
            f"{query_engine.frame_to_text(df, limit=15)}"
        ).replace(",", ".")
    )


async def handle_fetch_url(
    session: AgentSession, url: str = "", render: bool = False
) -> ToolResult:
    if not url.strip():
        return ToolResult(text="Boş URL.", ok=False, error_type="EmptyUrl")

    try:
        sayfa = await fetch_client.fetch(url.strip(), render=bool(render))
    except fetch_client.FetchError as e:
        return ToolResult(text=str(e), ok=False, error_type="FetchError")
    except Exception as e:
        log.exception("fetch patladı: %s", url)
        return ToolResult(text=f"İndirme hatası: {e}", ok=False,
                          error_type=type(e).__name__)

    # Sayfa /data'ya düşer; agent sandbox'tan okuyabilir.
    dosya_adi = f"{web_adapter.url_to_name(sayfa.url)}.html"
    hedef = session.dir / "data" / dosya_adi
    hedef.write_text(sayfa.text, encoding="utf-8", errors="replace")

    session.trace("fetch", url=sayfa.url, file=dosya_adi,
                  rendered=sayfa.rendered, ms=sayfa.elapsed_ms)

    harita = web_adapter.dom_map(
        sayfa.text, sayfa.url, dosya_adi=dosya_adi, rendered=sayfa.rendered
    )
    return ToolResult(text=harita, duration_ms=sayfa.elapsed_ms)


async def handle_add_dataset(
    session: AgentSession, filename: str = "", name: str = "", note: str = ""
) -> ToolResult:
    # Yol enjeksiyonuna kapalı: sadece dosya adı kabul edilir.
    guvenli = Path(filename).name
    kaynak = session.dir / "artifacts" / guvenli

    if not kaynak.exists():
        return ToolResult(
            text=(
                f"/artifacts/{guvenli} bulunamadı. Önce run_python ile "
                f"`df.to_parquet('/artifacts/{guvenli}')` çalıştır."
            ),
            ok=False,
            error_type="FileNotFound",
        )

    try:
        df = pd.read_parquet(kaynak)
    except Exception as e:
        return ToolResult(
            text=f"Parquet okunamadı: {e}", ok=False, error_type="ReadError"
        )

    if df.empty:
        return ToolResult(text="Tablo boş, kataloğa eklenmedi.", ok=False,
                          error_type="EmptyFrame")

    hedef_ad = slug(name or kaynak.stem, fallback="turetilmis")
    hedef = session.dir / "data" / f"{hedef_ad}.parquet"
    df.to_parquet(hedef, index=False)

    kart = P.build_schema_card(
        df,
        name=hedef_ad,
        origin=note or "agent tarafından türetildi",
        location=f"/data/{hedef.name}",
        total_rows=len(df),
    )

    ds = session.catalog.add(
        Dataset(
            name=hedef_ad,
            kind="derived",
            location=f"/data/{hedef.name}",
            origin=note or "agent tarafından türetildi",
            row_count=len(df),
            col_count=int(df.shape[1]),
            schema_card=kart,
            summary=note or P.build_summary(df),
            created_by="agent",
        )
    )
    session.trace("dataset", name=ds.name, rows=len(df), cols=int(df.shape[1]))

    return ToolResult(
        text=(
            f"'{ds.name}' kataloğa eklendi ({len(df)} satır × {df.shape[1]} kolon).\n"
            f"Sandbox'ta /data/{hedef.name} olarak okunabilir.\n\n{kart}"
        )
    )


async def handle_finish(session: AgentSession, summary: str = "") -> ToolResult:
    session.finished = True
    session.final_summary = summary
    session.trace("finish", summary=summary)
    return ToolResult(text="Analiz tamamlandı.")


HANDLERS = {
    "list_datasets": handle_list_datasets,
    "get_schema": handle_get_schema,
    "run_sql": handle_run_sql,
    "run_python": handle_run_python,
    "fetch_url": handle_fetch_url,
    "add_dataset": handle_add_dataset,
    "finish": handle_finish,
}


async def dispatch(session: AgentSession, name: str, args: dict) -> ToolResult:
    """Model'in istediği tool'u çalıştırır."""
    handler = HANDLERS.get(name)
    if handler is None:
        return ToolResult(
            text=f"'{name}' diye bir tool yok. Kullanılabilir: {', '.join(HANDLERS)}",
            ok=False,
            error_type="UnknownTool",
        )

    if "__parse_error__" in args:
        return ToolResult(
            text=(
                "Argümanların geçerli JSON değildi. Tool'u tekrar çağır ve "
                "argümanları doğru JSON olarak ver."
            ),
            ok=False,
            error_type="BadArguments",
        )

    # Süreyi BURADA ölç: tek tek handler'lara bırakılınca çoğu unutuyordu ve
    # log/arayüz her adım için "0 ms" gösteriyordu — yavaş adımı bulmak
    # imkânsızdı. Handler kendi süresini yazdıysa (run_python gibi, o
    # sandbox'ın ölçtüğü gerçek yürütme süresi) ona dokunma.
    baslangic = time.perf_counter()
    try:
        sonuc = await handler(session, **args)
        if not sonuc.duration_ms:
            sonuc.duration_ms = int((time.perf_counter() - baslangic) * 1000)
        return sonuc
    except SandboxUnavailable:
        # Altyapı hatası — modele "tekrar dene" dedirtmenin anlamı yok,
        # döngü yakalayıp turu sonlandırsın.
        raise
    except TypeError as e:
        beklenen = ", ".join(
            p["function"]["parameters"].get("required", [])
            for p in TOOLS if p["function"]["name"] == name
        )
        return ToolResult(
            text=f"Argüman hatası: {e}. Zorunlu alanlar: {beklenen or '—'}",
            ok=False,
            error_type="BadArguments",
        )
    except Exception as e:
        log.exception("tool patladı: %s", name)
        return ToolResult(text=f"Tool hatası: {e}", ok=False, error_type=type(e).__name__)
