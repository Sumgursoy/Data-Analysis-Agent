import { useRef, useState } from "react";
import type { DatasetView } from "../api";

type Props = {
  datasets: DatasetView[];
  yukleniyor: boolean;
  onUpload: (files: FileList) => void;
  onSchema: (name: string) => void;
  onUrl: (url: string) => void;
  onDsn: (dsn: string) => void;
  onSample: () => void;
};

const KIND_ETIKET: Record<string, string> = {
  file: "dosya",
  sql_table: "SQL",
  derived: "türetilmiş",
  raw: "ham",
  web: "web",
};

function olcu(d: DatasetView): string {
  if (d.row_count == null || d.col_count == null) return KIND_ETIKET[d.kind] ?? d.kind;
  return `${d.row_count.toLocaleString("tr-TR")} satır × ${d.col_count} kolon`;
}

export function DatasetPanel({
  datasets, yukleniyor, onUpload, onSchema, onUrl, onDsn, onSample,
}: Props) {
  const [suruklu, setSuruklu] = useState(false);
  const [sekme, setSekme] = useState<"url" | "sql" | null>(null);
  const [deger, setDeger] = useState("");
  const girdi = useRef<HTMLInputElement>(null);

  function gonder() {
    const v = deger.trim();
    if (!v) return;
    if (sekme === "url") onUrl(v);
    else onDsn(v);
    setDeger("");
    setSekme(null);
  }

  return (
    <aside className="panel sol">
      <h2>Veri Setleri</h2>

      <div
        className={`birakma-alani${suruklu ? " aktif" : ""}`}
        onClick={() => girdi.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setSuruklu(true);
        }}
        onDragLeave={() => setSuruklu(false)}
        onDrop={(e) => {
          e.preventDefault();
          setSuruklu(false);
          if (e.dataTransfer.files.length) onUpload(e.dataTransfer.files);
        }}
      >
        {yukleniyor ? "İşleniyor…" : "Dosya sürükle veya seç"}
        <br />
        <span style={{ fontSize: 11 }}>csv · xlsx · json · parquet · pdf</span>
      </div>

      <input
        ref={girdi}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) onUpload(e.target.files);
          e.target.value = "";
        }}
      />

      <div className="kaynak-secim">
        <button
          className={sekme === "url" ? "etkin" : ""}
          onClick={() => setSekme(sekme === "url" ? null : "url")}
        >
          🌐 URL
        </button>
        <button
          className={sekme === "sql" ? "etkin" : ""}
          onClick={() => setSekme(sekme === "sql" ? null : "sql")}
        >
          🗄️ Veritabanı
        </button>
      </div>

      {sekme && (
        <div className="kaynak-girdi">
          <input
            autoFocus
            value={deger}
            placeholder={
              sekme === "url"
                ? "https://site.com/tablo"
                : "postgresql://kullanici:parola@sunucu/veritabani"
            }
            onChange={(e) => setDeger(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && gonder()}
          />
          <button onClick={gonder} disabled={!deger.trim()}>Ekle</button>
        </div>
      )}

      <div style={{ marginTop: 14 }}>
        {datasets.length === 0 && (
          <>
            <p style={{ color: "var(--soluk)", fontSize: 12.5 }}>
              Henüz veri yok. Bir dosya yükle, agent şemasını otomatik çıkarsın.
            </p>
            <button className="ornek-veri" onClick={onSample} disabled={yukleniyor}>
              ✨ Örnek veriyle dene
            </button>
          </>
        )}

        {datasets.map((d) => (
          <div key={d.name} className="veriseti">
            <div className="ad">
              {d.name}
              <span className="etiket">{KIND_ETIKET[d.kind] ?? d.kind}</span>
            </div>
            <div className="olcu">
              {olcu(d)}
              {d.sampled && " · örneklem"}
            </div>
            {d.summary && <div className="ozet" title={d.summary}>{d.summary}</div>}
            <button onClick={() => onSchema(d.name)}>şema göster</button>
          </div>
        ))}
      </div>
    </aside>
  );
}
