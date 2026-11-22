// Adaptadores de subida por plataforma (TypeScript, port fiel de
// clipagent/platforms.py + publish.py del repo viral-clip-agent).
// Cada paso es pequeño y reanudable: el cron del Worker ejecuta UN
// paso por invocación y guarda el progreso en jobs.resume_data.
// Ningún adaptador marca posted: eso lo hace jobs.markPosted solo
// con evidence_url. Sin credencial / sin auditoría / sin URL pública
// el resultado es draft con el motivo, nunca un posted fingido.
//
// Límites del Worker: un chunk de 5-8 MB por invocación cabe; el
// video entero NO se carga en memoria (se lee por rangos desde R2).

export interface HttpResult {
  status: number;
  json: Record<string, unknown>;
  headers: Headers;
  text: string;
}

export async function httpJson(
  method: string,
  url: string,
  headers: Record<string, string> = {},
  body?: unknown,
  timeoutMs = 30_000,
): Promise<HttpResult> {
  const init: RequestInit = {
    method,
    headers,
    signal: AbortSignal.timeout(timeoutMs),
  };
  if (body !== undefined) {
    // String bodies (p. ej. URLSearchParams.toString() para el refresh
    // OAuth de YouTube) van tal cual: JSON.stringify las envolvería
    // en comillas y Google rechazaría el grant_type.
    init.body = body instanceof Uint8Array ? (body as unknown as BodyInit) : typeof body === "string" ? (body as unknown as BodyInit) : JSON.stringify(body);
    if (!(body instanceof Uint8Array) && typeof body !== "string" && !headers["Content-Type"]) {
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    }
  }
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (e) {
    return { status: 0, json: { error: String(e) }, headers: new Headers(), text: String(e) };
  }
  // El cuerpo también puede romperse a mitad de lectura (la conexión
  // se cae tras recibir las cabeceras: el runtime del Worker lanza
  // "Network connection lost" en res.text()). Sin este catch, esa
  // excepción sube hasta runWorkerCycle y el job queda como "error
  // interno del Worker" sin motivo útil. Aquí se convierte en un
  // status 0 reintentable, igual que un fetch que falla de entrada.
  let text: string;
  try {
    text = await res.text();
  } catch (e) {
    const msg = String(e);
    return { status: 0, json: { error: msg }, headers: new Headers(), text: msg };
  }
  let json: Record<string, unknown> = {};
  try {
    json = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  } catch {
    json = { error: text };
  }
  return { status: res.status, json, headers: res.headers, text };
}

export function isRetryableStatus(status: number): boolean {
  return status === 0 || status === 429 || status === 500 || status === 502 || status === 503 || status === 504;
}

export function errorMessage(payload: Record<string, unknown>, fallback: string): string {
  const err = payload.error;
  if (err && typeof err === "object") {
    const m = (err as Record<string, unknown>).message;
    if (typeof m === "string") return m;
  }
  if (typeof err === "string") return err;
  return fallback;
}

export function draft(reason: string, extra: Record<string, unknown> = {}) {
  return { ok: false, mode: "draft" as const, reason, ...extra };
}
