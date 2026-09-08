"""Kaynak ekleme — dosya yükleme (Faz 2), URL ve SQL sonraki fazlarda.

Tek kapı: kaynak tipi ne olursa olsun aynı boru hattından geçer ve
kataloğa aynı yapıda girer.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend import config
from backend.fetch import client as fetch_client
from backend.ingest.adapters import sql as sql_adapter
from backend.ingest.adapters import web as web_adapter
from backend.ingest.adapters.base import UnsupportedSource
from backend.ingest.catalog import Catalog
from backend.ingest.router import ingest_database, ingest_file, ingest_html
from backend.sandbox.manager import manager

log = logging.getLogger(__name__)
router = APIRouter()

PARCA_BOYUTU = 1024 * 1024
_GUVENSIZ_AD = re.compile(r"[^\w.\- ]", re.UNICODE)


class IngestedDataset(BaseModel):
    name: str
    kind: str
    row_count: int | None
    col_count: int | None
    summary: str
    sampled: bool
    notes: list[str]
    schema_card: str


class SourceResponse(BaseModel):
    session_id: str
    datasets: list[IngestedDataset]


class UrlRequest(BaseModel):
    url: str = Field(min_length=8)
    render: bool = Field(default=False, description="JS render (pahalı)")


class SqlRequest(BaseModel):
    dsn: str = Field(min_length=8, description="SQLAlchemy bağlantı dizesi")
    tables: list[str] | None = Field(
        default=None, description="Boş bırakılırsa tüm tablolar indirilir"
    )


class TableInfo(BaseModel):
    name: str
    schema_name: str | None
    row_count: int | None
    columns: list[str]


class DiscoverResponse(BaseModel):
    tables: list[TableInfo]


def guvenli_ad(ham: str | None) -> str:
    """Yüklenen dosya adını güvenli hale getirir (path traversal koruması)."""
    taban = Path(ham or "veri").name          # yol bileşenlerini at
    taban = _GUVENSIZ_AD.sub("_", taban).strip(". ")
    return taban[:120] or "veri"


@router.post("/sessions/{session_id}/sources/file", response_model=SourceResponse)
async def upload_file(
    session_id: str,
    background: BackgroundTasks,
    file: UploadFile = File(...),
) -> SourceResponse:
    if not (config.SESSIONS_DIR / session_id).is_dir():
        raise HTTPException(404, f"Session bulunamadı: {session_id}")

    sdir = config.session_dir(session_id)
    hedef = sdir / "raw" / guvenli_ad(file.filename)

    # Diske parça parça yaz — büyük dosya belleği doldurmasın.
    tavan = config.MAX_UPLOAD_MB * 1024 * 1024
    yazilan = 0
    with hedef.open("wb") as f:
        while parca := await file.read(PARCA_BOYUTU):
            yazilan += len(parca)
            if yazilan > tavan:
                f.close()
                hedef.unlink(missing_ok=True)
                raise HTTPException(
                    413, f"Dosya {config.MAX_UPLOAD_MB} MB sınırını aştı."
                )
            f.write(parca)

    if yazilan == 0:
        hedef.unlink(missing_ok=True)
        raise HTTPException(400, "Boş dosya yüklendi.")

    log.info("yüklendi: %s (%.1f MB)", hedef.name, yazilan / 1024 / 1024)

    # Ingest CPU-bağımlı ve bloklayıcı — event loop'u tıkamasın.
    try:
        datasets = await run_in_threadpool(ingest_file, session_id, hedef)
    except Exception as e:
        log.exception("ingest başarısız: %s", hedef.name)
        raise HTTPException(422, f"Dosya işlenemedi: {e}") from e

    if not datasets:
        raise HTTPException(422, "Dosyada kullanılabilir tablo bulunamadı.")

    # Veri geldi — kernel hazır değilse şimdi hazırlansın.
    background.add_task(manager.ensure, session_id)

    return _yanit(session_id, datasets)


@router.post("/sessions/{session_id}/sources/sample", response_model=SourceResponse)
async def add_sample(
    session_id: str, background: BackgroundTasks
) -> SourceResponse:
    """Hazır örnek veri — jüri kendi dosyasını getirmezse demo kurtarıcı.

    Veri kasten temiz değil: iadeler, aykırı değerler, boş kolonlar ve
    bir tarih boşluğu var. Şema kartındaki uyarılar demoda görünsün diye.
    """
    _session_var_mi(session_id)

    def _uret():
        from backend.ingest.adapters.base import Extracted
        from backend.ingest.router import _kaydet
        from backend.ingest.sample import uret

        satislar, musteriler = uret()
        return _kaydet(
            Catalog(session_id),
            [
                Extracted(name="satislar", df=satislar, origin="örnek veri (2023-2024)"),
                Extracted(name="musteriler", df=musteriler, origin="örnek veri"),
            ],
            config.session_dir(session_id) / "data",
        )

    datasets = await run_in_threadpool(_uret)
    background.add_task(manager.ensure, session_id)
    return _yanit(session_id, datasets)


@router.post("/sessions/{session_id}/sources/url", response_model=SourceResponse)
async def add_url(
    session_id: str, req: UrlRequest, background: BackgroundTasks
) -> SourceResponse:
    """Bir web sayfasındaki tabloları kataloğa alır.

    Ağ erişimi olan tek yer backend — sandbox'ın interneti yok.
    """
    _session_var_mi(session_id)

    try:
        sayfa = await fetch_client.fetch(req.url.strip(), render=req.render)
    except fetch_client.FetchError as e:
        raise HTTPException(422, str(e)) from e

    # HTML'i ham olarak da sakla — agent gerekirse kendi parser'ını yazar.
    sdir = config.session_dir(session_id)
    (sdir / "raw" / f"{web_adapter.url_to_name(sayfa.url)}.html").write_text(
        sayfa.text, encoding="utf-8", errors="replace"
    )

    try:
        datasets = await run_in_threadpool(
            ingest_html, session_id, sayfa.text, sayfa.url
        )
    except UnsupportedSource as e:
        raise HTTPException(
            422,
            f"{e} Sayfa kaydedildi; agent'a `fetch_url` ile inceletip kendi "
            "ayrıştırıcısını yazdırabilirsin.",
        ) from e
    except Exception as e:
        log.exception("url ingest başarısız: %s", req.url)
        raise HTTPException(502, f"Sayfa işlenemedi: {e}") from e

    background.add_task(manager.ensure, session_id)
    return _yanit(session_id, datasets)


@router.post("/sessions/{session_id}/sources/sql/discover", response_model=DiscoverResponse)
async def discover_database(session_id: str, req: SqlRequest) -> DiscoverResponse:
    """Bağlanır ve hangi tablolar olduğunu listeler — indirmeden önce bakış.

    Bağlantı dizesi burada kalır; yanıtta da, agent'ta da görünmez.
    """
    _session_var_mi(session_id)

    def _kesfet():
        motor = sql_adapter.baglan(req.dsn)
        try:
            return sql_adapter.kesfet(motor)
        finally:
            motor.dispose()

    try:
        tablolar = await run_in_threadpool(_kesfet)
    except UnsupportedSource as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        log.exception("şema keşfi başarısız")
        raise HTTPException(502, f"Veritabanı okunamadı: {e}") from e

    return DiscoverResponse(
        tables=[
            TableInfo(
                name=t.name, schema_name=t.schema,
                row_count=t.row_count, columns=t.columns,
            )
            for t in tablolar
        ]
    )


@router.post("/sessions/{session_id}/sources/sql", response_model=SourceResponse)
async def add_database(
    session_id: str, req: SqlRequest, background: BackgroundTasks
) -> SourceResponse:
    """Veritabanı tablolarını parquet'e indirip kataloğa sokar."""
    _session_var_mi(session_id)

    try:
        datasets = await run_in_threadpool(
            ingest_database, session_id, req.dsn, tablolar=req.tables
        )
    except UnsupportedSource as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        log.exception("veritabanı ingest başarısız")
        raise HTTPException(502, f"Veritabanı işlenemedi: {e}") from e

    if not datasets:
        raise HTTPException(422, "İndirilecek tablo bulunamadı.")

    background.add_task(manager.ensure, session_id)
    return _yanit(session_id, datasets)


def _session_var_mi(session_id: str) -> None:
    if not (config.SESSIONS_DIR / session_id).is_dir():
        raise HTTPException(404, f"Session bulunamadı: {session_id}")


def _yanit(session_id: str, datasets) -> SourceResponse:
    return SourceResponse(
        session_id=session_id,
        datasets=[
            IngestedDataset(
                name=d.name, kind=d.kind, row_count=d.row_count,
                col_count=d.col_count, summary=d.summary, sampled=d.sampled,
                notes=d.notes, schema_card=d.schema_card,
            )
            for d in datasets
        ],
    )
