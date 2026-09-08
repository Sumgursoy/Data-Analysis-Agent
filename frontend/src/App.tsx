import { useCallback, useEffect, useRef, useState } from "react";
import {
  addDatabase, addSample, addUrl, createSession, getCatalog, getSchema,
  streamChat, uploadFile, type AgentEvent, type DatasetView,
} from "./api";
import { DatasetPanel } from "./components/DatasetPanel";
import { NotebookPanel } from "./components/NotebookPanel";
import { ToolCallCard } from "./components/ToolCallCard";

/** Akışta gösterilen tek bir öğe. */
type MetinItem = {
  kind: "user" | "status" | "thinking" | "message" | "error";
  text: string;
};
type ToolItem = {
  kind: "tool";
  id: string;
  name: string;
  code: string;
  output?: string;
  errorType?: string;
  durationMs?: number;
};
type ArtifactItem = {
  kind: "artifact";
  url: string;
  filename: string;
  caption: string;
};
type Item = MetinItem | ToolItem | ArtifactItem;

const ORNEKLER = [
  "Veriyi özetle, dikkat çeken ne var?",
  "Bölgelere göre ciroyu grafikle",
  "Aykırı değerleri bul ve göster",
];

export default function App() {
  const [sid, setSid] = useState<string | null>(null);
  const [datasets, setDatasets] = useState<DatasetView[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [girdi, setGirdi] = useState("");
  const [mesgul, setMesgul] = useState(false);
  const [yukleniyor, setYukleniyor] = useState(false);
  const [defter, setDefter] = useState<string[]>([]);
  const [sema, setSema] = useState<{ ad: string; kart: string } | null>(null);
  const [saglik, setSaglik] = useState<{ model: string; sandbox: string } | null>(null);

  const akisSonu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    createSession().then(setSid).catch((e) =>
      setItems([{ kind: "error", text: `Backend'e bağlanılamadı: ${e.message}` }]),
    );
    fetch("/api/health")
      .then((r) => r.json())
      .then((d) => setSaglik({ model: d.llm.model, sandbox: d.sandbox.backend }))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    akisSonu.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  const ekle = useCallback((it: Item) => setItems((o) => [...o, it]), []);

  /** tool_call ile gelen kartı, sonradan gelen sonucuyla birleştirir. */
  const toolGuncelle = useCallback((id: string, yama: Partial<ToolItem>) => {
    setItems((o) =>
      o.map((it) =>
        it.kind === "tool" && it.id === id ? { ...it, ...yama } : it,
      ),
    );
  }, []);

  async function yukle(files: FileList) {
    if (!sid) return;
    setYukleniyor(true);
    try {
      for (const f of Array.from(files)) {
        const yeni = await uploadFile(sid, f);
        ekle({
          kind: "status",
          text: `${f.name} işlendi — ${yeni.map((d) => d.name).join(", ")}`,
        });
      }
      setDatasets(await getCatalog(sid));
    } catch (e) {
      ekle({ kind: "error", text: `Yükleme başarısız: ${(e as Error).message}` });
    } finally {
      setYukleniyor(false);
    }
  }

  /** URL ve veritabanı kaynakları — ikisi de aynı kataloğa iniyor. */
  async function kaynakEkle(
    getir: (sid: string) => Promise<DatasetView[]>,
    etiket: string,
  ) {
    if (!sid) return;
    setYukleniyor(true);
    try {
      const yeni = await getir(sid);
      ekle({
        kind: "status",
        text: `${etiket} işlendi — ${yeni.map((d) => d.name).join(", ")}`,
      });
      setDatasets(await getCatalog(sid));
    } catch (e) {
      ekle({ kind: "error", text: `${etiket} eklenemedi: ${(e as Error).message}` });
    } finally {
      setYukleniyor(false);
    }
  }

  async function gonder(metin: string) {
    if (!sid || !metin.trim() || mesgul) return;

    setGirdi("");
    setMesgul(true);
    ekle({ kind: "user", text: metin });

    try {
      await streamChat(sid, metin, (e: AgentEvent) => isle(e));
      setDatasets(await getCatalog(sid));
    } catch (e) {
      ekle({ kind: "error", text: `Akış koptu: ${(e as Error).message}` });
    } finally {
      setMesgul(false);
    }
  }

  function isle(e: AgentEvent) {
    switch (e.type) {
      case "status":
      case "thinking":
      case "message":
        ekle({ kind: e.type, text: String(e.text ?? "") });
        break;

      case "error":
        ekle({ kind: "error", text: String(e.text ?? "Bilinmeyen hata") });
        break;

      case "tool_call": {
        const kod = String(e.code ?? "");
        ekle({
          kind: "tool",
          id: String(e.id),
          name: String(e.name),
          code: kod,
        });
        // Defter, backend'in trace.jsonl'dan üreteceği notebook'u yansıtır:
        // SQL adımları da hücre oluyor (export.py). Sadece run_python
        // sayınca yalnızca SQL kullanan bir analizde "0 hücre" yazıp
        // indirmeyi engelliyordu — oysa notebook üretilebiliyordu.
        if (e.executable) {
          setDefter((d) => [
            ...d,
            kod || `-- ${e.name} adımı (tam içerik indirilen .ipynb'de)`,
          ]);
        }
        break;
      }

      case "tool_result":
        toolGuncelle(String(e.id), {
          output: String(e.text ?? ""),
          durationMs: Number(e.duration_ms ?? 0),
        });
        break;

      case "tool_error":
        toolGuncelle(String(e.id), {
          output: String(e.text ?? ""),
          errorType: String(e.error_type ?? "Error"),
        });
        break;

      case "artifact":
        ekle({
          kind: "artifact",
          url: String(e.url),
          filename: String(e.filename),
          caption: String(e.caption ?? ""),
        });
        break;

      case "dataset_ready":
      case "done":
        break;
    }
  }

  async function semaGoster(ad: string) {
    if (!sid) return;
    try {
      setSema({ ad, kart: await getSchema(sid, ad) });
    } catch (e) {
      ekle({ kind: "error", text: (e as Error).message });
    }
  }

  return (
    <div className="uygulama">
      <header className="baslik">
        <h1>Veri Analisti Agent</h1>
        {saglik && (
          <>
            <span className="rozet">{saglik.model}</span>
            <span className={`rozet${saglik.sandbox === "local" ? " uyari" : ""}`}>
              sandbox: {saglik.sandbox}
            </span>
          </>
        )}
        {mesgul && <span className="rozet">çalışıyor…</span>}
      </header>

      <DatasetPanel
        datasets={datasets}
        yukleniyor={yukleniyor}
        onUpload={yukle}
        onSchema={semaGoster}
        onUrl={(u) => kaynakEkle((s) => addUrl(s, u), u)}
        onDsn={(d) => kaynakEkle((s) => addDatabase(s, d), "Veritabanı")}
        onSample={() => kaynakEkle(addSample, "Örnek veri")}
      />

      <main className="akis-sarmal">
        <div className="akis">
          {items.length === 0 && (
            <div className="bos-durum">
              Soldan bir dosya yükle, sonra soru sor.
              <br />
              <br />
              {ORNEKLER.map((o) => (
                <span key={o} className="ornek" onClick={() => gonder(o)}>
                  {o}
                </span>
              ))}
            </div>
          )}

          {items.map((it, i) => {
            switch (it.kind) {
              case "user":
                return <div key={i} className="kullanici-mesaj">{it.text}</div>;
              case "status":
                return <div key={i} className="durum">{it.text}</div>;
              case "thinking":
                return <div key={i} className="dusunce">{it.text}</div>;
              case "message":
                return <div key={i} className="agent-mesaj">{it.text}</div>;
              case "error":
                return <div key={i} className="hata-kutu">⚠ {it.text}</div>;
              case "tool":
                return (
                  <ToolCallCard
                    key={i}
                    name={it.name}
                    code={it.code}
                    output={it.output}
                    errorType={it.errorType}
                    durationMs={it.durationMs}
                  />
                );
              case "artifact":
                return (
                  <figure key={i} className="grafik">
                    <img src={it.url} alt={it.caption || it.filename} />
                    <figcaption className="ad">{it.caption || it.filename}</figcaption>
                  </figure>
                );
            }
          })}
          <div ref={akisSonu} />
        </div>

        <div className="giris">
          <textarea
            value={girdi}
            placeholder={
              datasets.length ? "Verine bir soru sor…" : "Önce soldan veri yükle…"
            }
            onChange={(e) => setGirdi(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                gonder(girdi);
              }
            }}
          />
          <button
            className="gonder"
            disabled={mesgul || !girdi.trim() || !sid}
            onClick={() => gonder(girdi)}
          >
            Gönder
          </button>
        </div>
      </main>

      <NotebookPanel hucreler={defter} sessionId={sid} />

      {sema && (
        <div className="ortu" onClick={() => setSema(null)}>
          <div className="pencere" onClick={(e) => e.stopPropagation()}>
            <button className="kapat" onClick={() => setSema(null)}>×</button>
            <h2 style={{ marginTop: 0 }}>{sema.ad}</h2>
            <pre>{sema.kart}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
