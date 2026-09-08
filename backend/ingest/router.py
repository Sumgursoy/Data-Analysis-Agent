"""Ingest boru hattı — kaynağı tanır, doğru adaptöre yollar, kataloğa yazar.

    sniff → extract → normalize → materialize → profile → catalog

Bu hattın çıktısı agent'ın gördüğü tek şeydir. Buradan sonra "bu bir
Excel'di" bilgisi kaybolur; agent için sadece parquet + şema kartı vardır.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

import pandas as pd

from backend import config
from backend.ingest import normalize as N
from backend.ingest import profile as P
from backend.ingest.adapters import archive as archive_adapter
from backend.ingest.adapters import excel as excel_adapter
from backend.ingest.adapters import pdf as pdf_adapter
from backend.ingest.adapters import raw as raw_adapter
from backend.ingest.adapters import tabular
from backend.ingest.adapters.base import Extracted, UnsupportedSource
from backend.ingest.catalog import Catalog, Dataset

log = logging.getLogger(__name__)

# ── 1) Tanıma ───────────────────────────────────────────────────

# Uzantıya güvenme, magic byte'a bak: yarışmada .txt uzantılı bir Excel
# veya uzantısız bir parquet gelebilir.
IMZALAR: list[tuple[bytes, str]] = [
    (b"PK\x03\x04", "zip_family"),   # xlsx, ods, docx → içine bakılacak
    (b"PAR1", "parquet"),
    (b"SQLite format 3\x00", "sqlite"),
    (b"\xd0\xcf\x11\xe0", "xls"),    # eski OLE2 Excel
    (b"%PDF", "pdf"),
    (b"\x1f\x8b", "gzip"),
]


def sniff(path: Path) -> str:
    """Dosya formatını belirler."""
    with path.open("rb") as f:
        bas = f.read(config.SNIFF_BYTES)

    for imza, tip in IMZALAR:
        if bas.startswith(imza):
            return _zip_ailesi(path) if tip == "zip_family" else tip

    if not bas:
        return "empty"

    # Metin mi? İçeriğe bak.
    try:
        metin = bas.decode("utf-8")
    except UnicodeDecodeError:
        metin = bas.decode("cp1254", "replace")

    kirpik = metin.lstrip()
    dusuk = kirpik[:2048].lower()

    if kirpik.startswith(("{", "[")):
        return "json"
    if "<html" in dusuk or "<!doctype html" in dusuk or "<table" in dusuk:
        return "html"
    if kirpik.startswith("<?xml"):
        return "xml"

    satirlar = [s for s in metin.splitlines() if s.strip()][:20]
    if len(satirlar) >= 2:
        for aday in ";,\t|":
            sayilar = [s.count(aday) for s in satirlar]
            if sayilar[0] > 0 and sum(1 for c in sayilar if c == sayilar[0]) > len(satirlar) * 0.8:
                return "csv"

    return "text"


def _zip_ailesi(path: Path) -> str:
    """ZIP imzalı dosya: xlsx mi, ods mi, düz arşiv mi?"""
    try:
        with zipfile.ZipFile(path) as z:
            adlar = set(z.namelist())
    except zipfile.BadZipFile:
        return "unknown"

    if "[Content_Types].xml" in adlar and any(a.startswith("xl/") for a in adlar):
        return "xlsx"
    if "mimetype" in adlar or any(a == "content.xml" for a in adlar):
        return "ods"
    return "archive"


# ── 2) Ayıklama ─────────────────────────────────────────────────


def extract(path: Path, name: str, bicim: str) -> list[Extracted]:
    if bicim in ("xlsx", "ods", "xls", "xlsb"):
        return excel_adapter.read_excel(path, name)
    if bicim == "csv":
        return tabular.read_csv(path, name)
    if bicim == "parquet":
        return tabular.read_parquet(path, name)
    if bicim == "json":
        return tabular.read_json(path, name)
    if bicim == "pdf":
        return pdf_adapter.read_pdf(path, name)
    if bicim == "html":
        from backend.ingest.adapters import web as web_adapter

        encoding = tabular.detect_encoding(path)
        return web_adapter.extract_tables(
            path.read_text(encoding=encoding, errors="replace"), name, origin=path.name
        )
    if bicim == "text":
        # Ayıraç bulunamadı ama metin — yine de CSV olarak denemeye değer
        try:
            return tabular.read_csv(path, name)
        except Exception as e:
            raise UnsupportedSource(f"metin dosyası tabloya çevrilemedi: {e}") from e
    raise UnsupportedSource(f"'{bicim}' için adaptör yok")


# ── 3-6) Normalize → materyalize → profil → katalog ─────────────


def _materialize(df: pd.DataFrame, hedef: Path) -> tuple[Path | None, pd.DataFrame, int]:
    """Parquet yaz. Büyükse ayrıca örneklem yaz ve profili onun üstünde çıkar."""
    toplam = len(df)
    df.to_parquet(hedef, index=False)

    if toplam <= config.SAMPLE_THRESHOLD_ROWS:
        return None, df, toplam

    ornek = df.sample(n=config.SAMPLE_ROWS, random_state=0).sort_index()
    ornek_yolu = hedef.with_name(f"{hedef.stem}__sample.parquet")
    ornek.to_parquet(ornek_yolu, index=False)
    return ornek_yolu, ornek, toplam


def ingest_file(session_id: str, path: Path, *, ad: str | None = None) -> list[Dataset]:
    """Bir dosyayı uçtan uca kataloğa sokar."""
    katalog = Catalog(session_id)
    sdir = config.session_dir(session_id)
    data_dir = sdir / "data"

    taban = N.slug(ad or path.stem, fallback="veri")
    bicim = sniff(path)
    log.info("ingest: %s → biçim=%s", path.name, bicim)

    # Arşiv = birden çok veri seti. Her üye normal hattan yeniden geçer,
    # böylece zip içindeki Excel de CSV de aynı muameleyi görür.
    if bicim == "archive":
        return _arsiv_isle(session_id, path, taban)

    # Kaçış kapısı: tanınmayan format ham haliyle geçer
    try:
        parcalar = extract(path, taban, bicim)
    except Exception as e:  # UnsupportedSource dahil her başarısızlık
        log.warning("adaptör başarısız (%s): %s — ham geçişe düşülüyor", bicim, e)
        return [_ham_kaydet(katalog, path, taban, data_dir, sebep=str(e), bicim=bicim)]

    return _kaydet(katalog, parcalar, data_dir)


def _arsiv_isle(session_id: str, path: Path, taban: str) -> list[Dataset]:
    hedef = config.session_dir(session_id) / "raw" / f"{taban}_arsiv"
    uyeler = archive_adapter.cikar(path, hedef)

    sonuc: list[Dataset] = []
    for uye in uyeler:
        try:
            sonuc += ingest_file(session_id, uye)
        except Exception:
            # Tek bozuk üye yüzünden arşivin tamamını kaybetme.
            log.exception("arşiv üyesi işlenemedi: %s", uye.name)

    if not sonuc:
        raise UnsupportedSource(
            f"Arşivdeki {len(uyeler)} dosyanın hiçbiri işlenemedi."
        )
    return sonuc


def ingest_database(
    session_id: str,
    dsn: str,
    *,
    tablolar: list[str] | None = None,
) -> list[Dataset]:
    """Bir veritabanının tablolarını kataloğa sokar.

    DSN backend'de kalır — agent'a asla gösterilmez. Agent sadece
    kataloğa girmiş parquet'leri görür (İlke 1).
    """
    from backend.ingest.adapters import sql as sql_adapter

    katalog = Catalog(session_id)
    data_dir = config.session_dir(session_id) / "data"

    log.info("ingest: veritabanı %s", sql_adapter.maskele(dsn))
    parcalar = sql_adapter.read_database(dsn, tablolar=tablolar)
    return _kaydet(katalog, parcalar, data_dir)


def ingest_html(
    session_id: str, html: str, url: str, *, ad: str | None = None
) -> list[Dataset]:
    """Bir web sayfasındaki tabloları kataloğa sokar.

    Sayfa indirme işi backend'de (fetch/client.py) yapılıp buraya metin
    olarak geliyor — bu fonksiyon ağa çıkmaz, saf dönüşümdür.
    """
    from backend.ingest.adapters import web as web_adapter

    katalog = Catalog(session_id)
    data_dir = config.session_dir(session_id) / "data"
    taban = N.slug(ad or web_adapter.url_to_name(url), fallback="sayfa")

    parcalar = web_adapter.extract_tables(html, taban, origin=url)
    return _kaydet(katalog, parcalar, data_dir)


def _kaydet(
    katalog: Catalog, parcalar: list[Extracted], data_dir: Path
) -> list[Dataset]:
    """normalize → materyalize → profil → katalog. Tüm kaynaklar için ortak."""
    sonuc: list[Dataset] = []
    normalize_edilmis: dict[str, pd.DataFrame] = {}

    for parca in parcalar:
        df, notlar = N.normalize_frame(N.bos_degerleri_temizle(parca.df))
        notlar = parca.notes + notlar

        if df.empty or df.shape[1] == 0:
            log.warning("boş tablo atlandı: %s", parca.name)
            continue

        # Adı dosya yazılmadan ÖNCE çöz — yoksa aynı adlı ikinci veri seti
        # birincinin parquet'ini ezer.
        ad = katalog.unique_name(parca.name)
        hedef = data_dir / f"{ad}.parquet"
        ornek_yolu, profil_df, toplam = _materialize(df, hedef)

        kart = P.build_schema_card(
            profil_df,
            name=ad,
            origin=parca.origin,
            location=f"/data/{hedef.name}",
            sampled=ornek_yolu is not None,
            total_rows=toplam,
            extra_notes=notlar,
        )

        ds = katalog.add(
            Dataset(
                name=ad,
                kind=parca.kind,
                location=f"/data/{hedef.name}",
                origin=parca.origin,
                row_count=toplam,
                col_count=int(df.shape[1]),
                schema_card=kart,
                summary=P.build_summary(profil_df, parca.origin),
                sampled=ornek_yolu is not None,
                sample_location=f"/data/{ornek_yolu.name}" if ornek_yolu else None,
                notes=notlar,
            )
        )
        sonuc.append(ds)
        normalize_edilmis[ds.name] = profil_df

    # İlişki adayları: yeni gelenler + kataloğun tamamı
    if len(katalog.all()) > 1:
        _iliskileri_isle(katalog, normalize_edilmis, data_dir, sonuc)

    return sonuc


def _ham_kaydet(
    katalog: Catalog, path: Path, taban: str, data_dir: Path, *, sebep: str, bicim: str
) -> Dataset:
    hedef = data_dir / path.name
    shutil.copy2(path, hedef)
    location = f"/data/{hedef.name}"
    return katalog.add(
        Dataset(
            name=taban,
            kind="raw",
            location=location,
            origin=path.name,
            schema_card=raw_adapter.describe_raw(hedef, location),
            summary=f"tanınmayan format ({bicim})",
            notes=[f"otomatik ayrıştırılamadı: {sebep}"],
        )
    )


def _iliskileri_isle(
    katalog: Catalog,
    yeni: dict[str, pd.DataFrame],
    data_dir: Path,
    guncellenecek: list[Dataset],
) -> None:
    """Tablolar arası join adaylarını bulup şema kartlarına ekler."""
    frames = dict(yeni)
    for ds in katalog.all():
        if ds.name in frames or ds.kind == "raw":
            continue
        yol = data_dir / Path(ds.sample_location or ds.location).name
        if not yol.exists():
            continue
        try:
            frames[ds.name] = pd.read_parquet(yol)
        except Exception:
            log.debug("ilişki taraması için okunamadı: %s", ds.name)

    if len(frames) < 2:
        return

    adaylar = P.find_relations(frames)
    if not adaylar:
        return

    # Her şema kartına SADECE kendisini ilgilendiren ilişkiler yazılır.
    # Hepsini her karta basmak modeli alakasız join'lere iter.
    for ds in guncellenecek:
        kendi = [
            metin for sol, sag, metin in adaylar if ds.name in (sol, sag)
        ]
        if not kendi or "🔗 İlişki adayları" in ds.schema_card:
            continue
        ds.schema_card += "\n\n  🔗 İlişki adayları:\n" + "\n".join(
            f"    {m}" for m in kendi
        )
    katalog.save()
