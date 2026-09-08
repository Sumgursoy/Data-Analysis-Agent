import { useState } from "react";

type Props = {
  name: string;
  code: string;
  output?: string;
  errorType?: string;
  durationMs?: number;
};

const SIMGE: Record<string, string> = {
  run_python: "🐍",
  run_sql: "🗄️",
  get_schema: "📋",
  list_datasets: "📚",
  add_dataset: "💾",
  finish: "✅",
};

/**
 * Agent'ın tek bir adımı: ne çağırdı, hangi kodu yazdı, ne aldı.
 *
 * Hatalar GİZLENMEZ — kırmızı çerçeveyle gösterilir. Jüri, agent'ın
 * hata yapıp bir sonraki adımda düzelttiğini görmeli; bu güven artırır.
 */
export function ToolCallCard({ name, code, output, errorType, durationMs }: Props) {
  const hatali = Boolean(errorType);
  const [acik, setAcik] = useState(true);

  return (
    <div className={`tool-kart${hatali ? " hatali" : ""}`}>
      <div className="tool-baslik" onClick={() => setAcik((a) => !a)}>
        <span>{SIMGE[name] ?? "🔧"}</span>
        <strong>{name}</strong>
        {hatali && <span>— {errorType}</span>}
        <span className="sure">
          {durationMs ? `${durationMs} ms · ` : ""}
          {acik ? "gizle" : "göster"}
        </span>
      </div>

      {acik && (
        <>
          {code && <pre className="kod">{code}</pre>}
          {output && (
            <pre className={`cikti${hatali ? " hata" : ""}`}>{output}</pre>
          )}
        </>
      )}
    </div>
  );
}
