"""Faz 3 testi — döngüyü senaryolanmış sahte model ile sürer.

Gerçek model olmadan doğrulananlar: tool dispatch, hata düzeltme,
ardışık hatada kernel restart, artifact tespiti, trace kaydı, SSE akışı.
"""

import asyncio
import json
from pathlib import Path

import pandas as pd

from backend import config
from backend.agent import loop
from backend.agent.llm import LLMError, LLMResult, QuotaError, ToolCall, Usage
from backend.agent.session import AgentSession
from backend.ingest.router import ingest_file

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


class SahteModel:
    """Önceden yazılmış tool çağrılarını sırayla döndürür."""

    def __init__(self, adimlar):
        self.adimlar = list(adimlar)
        self.cagri = 0
        self.gorulen_mesajlar = []

    async def complete(self, messages, tools=None):
        self.cagri += 1
        self.gorulen_mesajlar.append(list(messages))
        if not self.adimlar:
            return LLMResult(text="Bitti.", usage=Usage(10, 5))
        metin, cagrilar = self.adimlar.pop(0)
        tc = [
            ToolCall(id=f"call_{self.cagri}_{i}", name=ad, arguments=arg)
            for i, (ad, arg) in enumerate(cagrilar)
        ]
        return LLMResult(
            text=metin, tool_calls=tc, usage=Usage(100, 40),
            raw_message={"role": "assistant", "content": metin or None,
                         "tool_calls": [
                             {"id": c.id, "type": "function",
                              "function": {"name": c.name,
                                           "arguments": json.dumps(c.arguments)}}
                             for c in tc]},
        )


class KotaBiten:
    async def complete(self, messages, tools=None):
        raise QuotaError("Hesapta kredi kalmadı.")


def veri_hazirla(sid):
    yol = TMP / "satis.csv"
    yol.write_text(
        "bolge;tutar;durum\n"
        "Ege;1.234,56;tamamlandı\nMarmara;12.000,00;tamamlandı\n"
        "Ege;8.750,25;tamamlandı\nMarmara;3.100,00;iptal\n"
        "İç Anadolu;5.400,00;tamamlandı\n",
        encoding="utf-8",
    )
    return ingest_file(sid, yol)


async def senaryo_1():
    print("\n=== SENARYO 1: keşif → hata → düzeltme → grafik → kayıt → bitir ===")
    sid = "agent_s1"
    veri_hazirla(sid)
    oturum = AgentSession(sid)

    model = SahteModel([
        ("Önce şemaya bakayım.", [("get_schema", {"name": "satis"})]),
        ("Bölge kırılımını alayım.",
         [("run_python", {"code": "df = pd.read_parquet(data_path('satis.parquet'))\ndf.groupby('bolgee').tutar.sum()"})]),
        ("Kolon adını yanlış yazmışım, düzeltiyorum.",
         [("run_python", {"code": "ozet = df.groupby('bolge', observed=True).tutar.sum().sort_values(ascending=False)\nozet"})]),
        ("Grafiğe dökeyim.",
         [("run_python", {"code":
            "fig, ax = plt.subplots(figsize=(7,4))\n"
            "ozet.plot.bar(ax=ax, title='Bölgelere Göre Ciro (TL)')\n"
            "ax.set_xlabel('Bölge'); ax.set_ylabel('Ciro (TL)')\n"
            "fig.savefig(artifact_path('bolge_ciro.png'), dpi=110, bbox_inches='tight')\n"
            "plt.close(fig)\n"
            "ozet.reset_index().to_parquet(artifact_path('bolge_ozet.parquet'))"})]),
        ("Özeti kataloğa ekliyorum.",
         [("add_dataset", {"filename": "bolge_ozet.parquet", "name": "bolge_ozet",
                           "note": "bölge bazında toplam ciro"})]),
        ("", [("finish", {"summary": "Marmara ve Ege ciroyu domine ediyor."})]),
    ])

    olaylar = [ev async for ev in loop.run(oturum, "Bölgelere göre ciroyu grafikle", model)]
    tipler = [e.type for e in olaylar]

    kontrol("get_schema sonucu döndü", any(
        e.type == "tool_result" and e.data["name"] == "get_schema" for e in olaylar))
    kontrol("hatalı kolon tool_error üretti", any(
        e.type == "tool_error" and e.data["error_type"] == "KeyError" for e in olaylar),
        f"tipler={tipler}")
    kontrol("hatadan sonra başarılı çalıştırma var", any(
        e.type == "tool_result" and e.data["name"] == "run_python" for e in olaylar))
    grafik = [e for e in olaylar if e.type == "artifact"]
    kontrol("grafik artifact'ı yayınlandı", len(grafik) >= 1, f"{len(grafik)} adet")
    if grafik:
        kontrol("artifact URL'i doğru",
                grafik[0].data["url"] == f"/api/artifacts/{sid}/bolge_ciro.png",
                grafik[0].data["url"])
    kontrol("dataset_ready yayınlandı", "dataset_ready" in tipler)
    kontrol("kataloğa türetilmiş set eklendi",
            AgentSession(sid).catalog.get("bolge_ozet") is not None)
    kontrol("done ile bitti", tipler[-1] == "done")
    kontrol("final özet mesajı geldi", any(
        e.type == "message" and "Marmara" in e.data["text"] for e in olaylar))

    son = olaylar[-1].data
    kontrol("token sayacı işledi", son["usage"]["input"] > 0, str(son["usage"]))

    izler = oturum.read_trace()
    kod_izleri = [i for i in izler if i["type"] == "code"]
    kontrol("trace'e kod yazıldı", len(kod_izleri) == 3, f"{len(kod_izleri)} adet")
    kontrol("trace'te artifact kaydı var", any(i["type"] == "artifact" for i in izler))
    kontrol("trace'te finish var", any(i["type"] == "finish" for i in izler))

    # Grafik gerçekten diskte mi?
    png = config.session_dir(sid) / "artifacts" / "bolge_ciro.png"
    kontrol("grafik dosyası diskte", png.exists() and png.stat().st_size > 5000,
            f"{png.stat().st_size if png.exists() else 0} bayt")


async def senaryo_2():
    print("\n=== SENARYO 2: 3 ardışık hata → kernel restart + kurtarma notu ===")
    sid = "agent_s2"
    veri_hazirla(sid)
    oturum = AgentSession(sid)

    bozuk = ("Deniyorum.", [("run_python", {"code": "yok_boyle_degisken + 1"})])
    model = SahteModel([
        ("Değişken tanımlayayım.", [("run_python", {"code": "kalici = 42"})]),
        bozuk, bozuk, bozuk,
        ("Kernel sıfırlandı mı bakayım.", [("run_python", {"code": "kalici"})]),
        ("", [("finish", {"summary": "Test bitti."})]),
    ])

    olaylar = [ev async for ev in loop.run(oturum, "hata testi", model)]

    hatalar = [e for e in olaylar if e.type == "tool_error"]
    kontrol("3 hata da yayınlandı", len(hatalar) >= 3, f"{len(hatalar)} adet")
    kontrol("kernel sıfırlama durumu bildirildi", any(
        e.type == "status" and "sıfırlan" in e.data["text"] for e in olaylar))

    # Kurtarma notu modele gerçekten gitti mi?
    son_mesajlar = model.gorulen_mesajlar[-1]
    kontrol("kurtarma notu prompt'a eklendi", any(
        isinstance(m.get("content"), str) and "TÜM DEĞİŞKENLER" in m["content"]
        for m in son_mesajlar))

    # Restart sonrası 'kalici' silinmiş olmalı → NameError
    restart_sonrasi = [e for e in olaylar if e.type in ("tool_error", "tool_result")]
    kontrol("restart sonrası değişken silindi", any(
        e.type == "tool_error" and e.data["error_type"] == "NameError"
        for e in restart_sonrasi[-3:]))


async def senaryo_3():
    print("\n=== SENARYO 3: kredi bitti → net hata, döngü sonlanıyor ===")
    sid = "agent_s3"
    config.session_dir(sid)
    oturum = AgentSession(sid)
    olaylar = [ev async for ev in loop.run(oturum, "merhaba", KotaBiten())]
    kontrol("error event'i geldi", any(e.type == "error" for e in olaylar))
    kontrol("mesaj kredi diyor", any(
        e.type == "error" and "kredi" in e.data["text"].lower() for e in olaylar))
    kontrol("sonsuz döngüye girmedi", len(olaylar) <= 3, f"{len(olaylar)} event")


async def senaryo_4():
    print("\n=== SENARYO 4: SSE ucu gerçek HTTP üzerinden ===")
    from fastapi.testclient import TestClient

    import backend.api.chat as chat_modulu
    from backend.main import app

    sid = "agent_s4"
    veri_hazirla(sid)

    model = SahteModel([
        ("Şemaya bakayım.", [("get_schema", {"name": "satis"})]),
        ("", [("finish", {"summary": "Toplam ciro 30.484,81 TL."})]),
    ])
    chat_modulu.build_backend = lambda: model  # gerçek modeli devre dışı bırak

    with TestClient(app) as c:
        r = c.post("/api/chat", json={"session_id": sid, "message": "özet ver"})
        kontrol("HTTP 200", r.status_code == 200, str(r.status_code))
        kontrol("content-type event-stream",
                "text/event-stream" in r.headers.get("content-type", ""),
                r.headers.get("content-type", ""))

        olaylar = [json.loads(s[6:]) for s in r.text.splitlines() if s.startswith("data: ")]
        tipler = [o["type"] for o in olaylar]
        kontrol("SSE event'leri ayrıştırılabildi", len(olaylar) >= 4, str(tipler))
        kontrol("tool_call akışta var", "tool_call" in tipler, str(tipler))
        kontrol("done ile kapandı", tipler[-1] == "done", str(tipler))
        kontrol("Türkçe karakter bozulmadı", any(
            "ciro" in o.get("text", "") for o in olaylar if o["type"] == "message"))

        # Grafik servisi (senaryo 1'in ürettiği dosya)
        r2 = c.get("/api/artifacts/agent_s1/bolge_ciro.png")
        kontrol("artifact servisi çalışıyor", r2.status_code == 200, str(r2.status_code))
        kontrol("artifact image/png", r2.headers.get("content-type") == "image/png")
        r3 = c.get("/api/artifacts/agent_s1/../../../config.py")
        kontrol("yol geçişi engellendi", r3.status_code in (404, 415), str(r3.status_code))

        r4 = c.get(f"/api/sessions/{sid}/trace")
        kontrol("trace ucu çalışıyor", r4.status_code == 200 and r4.json()["steps"])


async def main():
    await senaryo_1()
    await senaryo_2()
    await senaryo_3()
    await senaryo_4()
    from backend.sandbox.manager import manager
    await manager.shutdown()
    print(f"\n{'=' * 62}\nGECTI: {gecti}   BASARISIZ: {basarisiz}")


asyncio.run(main())
