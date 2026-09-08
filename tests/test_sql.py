"""Faz 4 testi — run_sql güvenliği + SQLite veritabanı ingest'i."""

import asyncio
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend import config
from backend import query as Q
from backend.agent import tools
from backend.agent.session import AgentSession
from backend.ingest.router import ingest_file
from backend.main import app

TMP = Path(__file__).parent / "veri"
TMP.mkdir(exist_ok=True)
gecti = basarisiz = 0


def kontrol(baslik, kosul, detay=""):
    global gecti, basarisiz
    if kosul:
        gecti += 1
        print(f"  [OK ] {baslik}")
    else:
        basarisiz += 1
        print(f"  [X  ] {baslik}  {detay}")


def sqlite_kur() -> Path:
    yol = TMP / "kurum.db"
    yol.unlink(missing_ok=True)
    con = sqlite3.connect(yol)
    con.executescript("""
        CREATE TABLE musteriler (id INTEGER PRIMARY KEY, ad TEXT, segment TEXT);
        CREATE TABLE islemler (id INTEGER PRIMARY KEY, musteri_id INTEGER,
                               tutar REAL, tarih TEXT, durum TEXT);
        CREATE TABLE bos_tablo (x INTEGER);
    """)
    con.executemany("INSERT INTO musteriler VALUES (?,?,?)", [
        (1, "Yılmaz Ltd", "kurumsal"), (2, "Öz Ticaret", "kobi"),
        (3, "Şahin A.Ş.", "kurumsal"), (4, "Çelik San.", "kobi"),
    ])
    con.executemany("INSERT INTO islemler VALUES (?,?,?,?,?)", [
        (i, (i % 4) + 1, 1000.0 * i, f"2024-0{(i % 9) + 1}-15",
         "tamamlandı" if i % 5 else "iptal")
        for i in range(1, 41)
    ])
    con.commit()
    con.close()
    return yol


def csv_kur(sid):
    yol = TMP / "satis.csv"
    yol.write_text(
        "bolge;tutar;durum\n"
        "Ege;1.234,56;tamamlandı\nMarmara;12.000,00;tamamlandı\n"
        "Ege;8.750,25;tamamlandı\nMarmara;3.100,00;iptal\n"
        "İç Anadolu;5.400,00;tamamlandı\n",
        encoding="utf-8",
    )
    return ingest_file(sid, yol)


def test_guvenlik():
    print("\n=== SQL güvenliği (parse ederek, string arayarak değil) ===")
    tehlikeli = [
        ("DROP TABLE satis", "DROP"),
        ("DELETE FROM satis", "DELETE"),
        ("UPDATE satis SET tutar = 0", "UPDATE"),
        ("INSERT INTO satis VALUES (1)", "INSERT"),
        ("SELECT 1; DROP TABLE satis", "zincirli ifade"),
        ("CREATE TABLE x (a int)", "CREATE"),
        ("ATTACH 'x.db' AS y", "ATTACH"),
        ("COPY satis TO 'out.csv'", "COPY"),
    ]
    for sorgu, etiket in tehlikeli:
        try:
            Q.assert_readonly(sorgu)
            kontrol(f"engellendi: {etiket}", False, "GEÇTİ AMA GEÇMEMELİYDİ")
        except Q.UnsafeQuery:
            kontrol(f"engellendi: {etiket}", True)

    guvenli = [
        "SELECT * FROM satis",
        "SELECT bolge, sum(tutar) FROM satis GROUP BY bolge",
        "WITH t AS (SELECT * FROM satis) SELECT count(*) FROM t",
        "SELECT * FROM satis UNION ALL SELECT * FROM satis",
        "SELECT * FROM satis WHERE durum = 'DROP TABLE'",   # yanlış alarm olmamalı
    ]
    for sorgu in guvenli:
        try:
            Q.assert_readonly(sorgu)
            kontrol(f"izin verildi: {sorgu[:42]}", True)
        except Q.UnsafeQuery as e:
            kontrol(f"izin verildi: {sorgu[:42]}", False, str(e))


async def test_run_sql():
    print("\n=== run_sql tool'u ===")
    sid = "sql_s1"
    csv_kur(sid)
    Q.drop_engine(sid)
    oturum = AgentSession(sid)

    r = await tools.dispatch(oturum, "run_sql",
                             {"query": "SELECT bolge, sum(tutar) AS ciro FROM satis "
                                       "GROUP BY bolge ORDER BY ciro DESC"})
    kontrol("gruplama çalıştı", r.ok, r.text[:200])
    veri_satirlari = [s for s in r.text.splitlines() if "Marmara" in s or "Ege" in s]
    kontrol("Marmara ciroda ilk sırada",
            bool(veri_satirlari) and "Marmara" in veri_satirlari[0], r.text[:200])
    kontrol("Türkçe karakter bozulmadı", "İç Anadolu" in r.text if r.ok else False)

    r2 = await tools.dispatch(oturum, "run_sql", {"query": "DROP TABLE satis"})
    kontrol("DROP tool seviyesinde reddedildi",
            not r2.ok and r2.error_type == "UnsafeQuery", f"{r2.error_type}")

    r3 = await tools.dispatch(oturum, "run_sql", {"query": "SELECT yok_kolon FROM satis"})
    kontrol("hatalı kolon: hata modele döndü", not r3.ok)
    kontrol("hata mesajı tabloları listeliyor", "satis" in r3.text, r3.text[:150])

    r4 = await tools.dispatch(oturum, "run_sql",
                              {"query": "SELECT * FROM satis", "save_as": "satis_kopya"})
    kontrol("save_as kataloğa ekledi",
            AgentSession(sid).catalog.get("satis_kopya") is not None)
    kontrol("kaydedilen sete SQL'den erişilebilir", "satis_kopya" in r4.text)

    r5 = await tools.dispatch(oturum, "run_sql", {"query": "SELECT * FROM satis_kopya"})
    kontrol("türetilmiş set sorgulanabildi", r5.ok, r5.text[:150])

    izler = oturum.read_trace()
    kontrol("sql trace'e yazıldı", any(i["type"] == "sql" for i in izler))


def test_veritabani():
    print("\n=== SQLite veritabanı ingest'i ===")
    db = sqlite_kur()
    dsn = f"sqlite:///{db.as_posix()}"

    with TestClient(app) as c:
        sid = c.post("/api/sessions").json()["session_id"]

        r = c.post(f"/api/sessions/{sid}/sources/sql/discover", json={"dsn": dsn})
        kontrol("keşif HTTP 200", r.status_code == 200, r.text[:200])
        if r.status_code == 200:
            tablolar = {t["name"]: t for t in r.json()["tables"]}
            kontrol("tablolar bulundu",
                    {"musteriler", "islemler"} <= set(tablolar), str(list(tablolar)))
            kontrol("satır sayıları okundu",
                    tablolar.get("islemler", {}).get("row_count") == 40,
                    str(tablolar.get("islemler")))
            kontrol("kolonlar okundu",
                    "musteri_id" in tablolar.get("islemler", {}).get("columns", []))

        r2 = c.post(f"/api/sessions/{sid}/sources/sql",
                    json={"dsn": dsn, "tables": ["musteriler", "islemler"]})
        kontrol("ingest HTTP 200", r2.status_code == 200, r2.text[:300])
        if r2.status_code == 200:
            setler = {d["name"]: d for d in r2.json()["datasets"]}
            kontrol("iki tablo indirildi", len(setler) == 2, str(list(setler)))
            kontrol("kind = sql_table",
                    all(d["kind"] == "sql_table" for d in setler.values()))
            kontrol("islemler 40 satır",
                    setler.get("islemler", {}).get("row_count") == 40)
            kart = setler.get("islemler", {}).get("schema_card", "")
            kontrol("şema kartı kaynak tabloyu yazıyor", "kaynak tablo" in kart)
            kontrol("ilişki adayı bulundu", "İlişki adayları" in kart, kart[-300:])

        # Kritik: DSN hiçbir yanıtta görünmemeli
        tum_metin = r.text + r2.text
        kontrol("DSN yanıtlara sızmadı", "sqlite:///" not in tum_metin)

        r3 = c.post(f"/api/sessions/{sid}/sources/sql",
                    json={"dsn": "postgresql://user:gizli@yok.example/db"})
        kontrol("bağlanamama düzgün hata veriyor", r3.status_code in (422, 502),
                str(r3.status_code))
        kontrol("parola hata mesajında maskelendi", "gizli" not in r3.text, r3.text[:200])

        c.delete(f"/api/sessions/{sid}")


async def main():
    test_guvenlik()
    await test_run_sql()
    test_veritabani()
    print(f"\n{'=' * 62}\nGECTI: {gecti}   BASARISIZ: {basarisiz}")


asyncio.run(main())
