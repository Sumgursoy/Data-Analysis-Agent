/** Backend ile konuşma katmanı. */

export type AgentEvent = {
  type:
    | "status" | "thinking" | "message" | "tool_call" | "tool_result"
    | "tool_error" | "artifact" | "dataset_ready" | "error" | "done";
  [key: string]: unknown;
};

export type DatasetView = {
  name: string;
  kind: string;
  origin: string;
  row_count: number | null;
  col_count: number | null;
  summary: string;
  sampled: boolean;
  notes: string[];
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detay = res.statusText;
    try {
      const govde = await res.json();
      detay = govde.detail ?? detay;
    } catch {
      /* gövde JSON değilse durum metniyle yetin */
    }
    throw new Error(detay);
  }
  return res.json() as Promise<T>;
}

export async function createSession(): Promise<string> {
  const r = await fetch("/api/sessions", { method: "POST" });
  const d = await json<{ session_id: string }>(r);
  return d.session_id;
}

export async function getCatalog(sid: string): Promise<DatasetView[]> {
  const r = await fetch(`/api/sessions/${sid}/catalog`);
  const d = await json<{ datasets: DatasetView[] }>(r);
  return d.datasets;
}

export async function getSchema(sid: string, name: string): Promise<string> {
  const r = await fetch(`/api/sessions/${sid}/schema/${encodeURIComponent(name)}`);
  const d = await json<{ schema_card: string }>(r);
  return d.schema_card;
}

export async function uploadFile(sid: string, file: File): Promise<DatasetView[]> {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch(`/api/sessions/${sid}/sources/file`, {
    method: "POST",
    body: form,
  });
  const d = await json<{ datasets: DatasetView[] }>(r);
  return d.datasets;
}

export async function addSample(sid: string): Promise<DatasetView[]> {
  const r = await fetch(`/api/sessions/${sid}/sources/sample`, { method: "POST" });
  const d = await json<{ datasets: DatasetView[] }>(r);
  return d.datasets;
}

export async function addUrl(sid: string, url: string): Promise<DatasetView[]> {
  const r = await fetch(`/api/sessions/${sid}/sources/url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const d = await json<{ datasets: DatasetView[] }>(r);
  return d.datasets;
}

export async function addDatabase(sid: string, dsn: string): Promise<DatasetView[]> {
  const r = await fetch(`/api/sessions/${sid}/sources/sql`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dsn }),
  });
  const d = await json<{ datasets: DatasetView[] }>(r);
  return d.datasets;
}

export async function getTrace(sid: string): Promise<Record<string, unknown>[]> {
  const r = await fetch(`/api/sessions/${sid}/trace`);
  const d = await json<{ steps: Record<string, unknown>[] }>(r);
  return d.steps;
}

/**
 * Agent akışını tüketir.
 *
 * EventSource kullanılamıyor: o sadece GET yapabiliyor, bizim mesajı
 * gövdede POST etmemiz gerekiyor. Bu yüzden fetch + ReadableStream ile
 * SSE çerçevelerini elle ayrıştırıyoruz.
 */
export async function streamChat(
  sid: string,
  message: string,
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sid, message }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`Akış başlatılamadı (HTTP ${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let tampon = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    tampon += decoder.decode(value, { stream: true });

    // SSE çerçeveleri boş satırla ayrılır. Son parça yarım olabilir,
    // onu tamponda bırakıp bir sonraki okumayı bekliyoruz.
    const parcalar = tampon.split("\n\n");
    tampon = parcalar.pop() ?? "";

    for (const parca of parcalar) {
      for (const satir of parca.split("\n")) {
        if (!satir.startsWith("data: ")) continue;
        try {
          onEvent(JSON.parse(satir.slice(6)) as AgentEvent);
        } catch {
          /* bozuk çerçeveyi atla, akışı kesme */
        }
      }
    }
  }
}
