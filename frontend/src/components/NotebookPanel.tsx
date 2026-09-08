type Props = { hucreler: string[]; sessionId: string | null };

/**
 * Analiz defteri — agent'ın çalıştırdığı kodun birikimi.
 *
 * Yarışma açısından değeri: sonuçlar "doğrulanabilir" oluyor. Jüri
 * notebook'u indirip Jupyter'da baştan koşturabilir.
 */
export function NotebookPanel({ hucreler, sessionId }: Props) {
  // Notebook backend'de trace.jsonl'dan üretiliyor: çıktılar ve SQL adımları
  // da giriyor, sayfa yenilense bile kayıp olmuyor.
  const indirmeUrl = sessionId ? `/api/export/${sessionId}.ipynb` : "#";

  return (
    <aside className="panel sag">
      <h2>Analiz Defteri</h2>

      <a
        className={`indir${hucreler.length === 0 ? " pasif" : ""}`}
        href={indirmeUrl}
        download
        onClick={(e) => hucreler.length === 0 && e.preventDefault()}
      >
        ⬇ .ipynb indir ({hucreler.length} hücre)
      </a>

      {hucreler.length === 0 && (
        <p style={{ color: "var(--soluk)", fontSize: 12.5 }}>
          Agent kod çalıştırdıkça burada birikecek.
        </p>
      )}

      {hucreler.map((kod, i) => (
        <div key={i} className="defter-hucre">
          <div className="no">[{i + 1}]</div>
          <pre>{kod}</pre>
        </div>
      ))}
    </aside>
  );
}
