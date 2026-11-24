// Verificación REAL de cuentas contra la API de cada plataforma
// (port de verifyApiCredential del panel v17). Solo un éxito real
// marca la cuenta «conectada». No se guarda ni se devuelve la
// credencial: solo ok / mensaje / fecha.
//
// TikTok vía Zernio: la credencial es la Zernio API key y se verifica
// contra Zernio, no contra open.tiktokapis.com:
//   GET /v1/accounts/{accountId}/health (docs.zernio.com; también en
//   los SDK oficiales: AccountsApi.getAccountHealth). Sin account_id
//   en extra, GET /v1/accounts?platform=tiktok y basta con que haya
//   una cuenta TikTok conectada. Una clave que empieza por "zk_"
//   también se trata como Zernio aunque falte el extra.

import { httpJson } from "./publish/http";

export interface VerifyOutcome {
  ok: boolean;
  message: string;
  /** Transitorio (red/timeout/HTTP 5xx): reintentable, NO invalida la
   *  cuenta. Ausente/false = fallo de autenticación (token inválido,
   *  revocado, permiso quitado). Clasificación POR SITIO (N6, spec
   *  2026-11-23): nunca por cadena de mensaje. */
  transient?: boolean;
}

/** HTTP 5xx o fallo de red (httpJson devuelve status 0 cuando el
 *  fetch no llega): transitorio. 4xx = la credencial no vale. */
function isTransientStatus(status: number): boolean {
  return status === 0 || status >= 500;
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

function joinIssues(issues: unknown): string {
  if (!Array.isArray(issues)) return "";
  return issues
    .map((i) => {
      if (typeof i === "string") return i;
      if (i && typeof i === "object") {
        const m = (i as Record<string, unknown>).message;
        if (typeof m === "string") return m;
      }
      return "";
    })
    .filter(Boolean)
    .join("; ");
}

async function verifyZernioTiktok(apiKey: string, accountId?: string): Promise<VerifyOutcome> {
  const auth = { Authorization: `Bearer ${apiKey}` };
  try {
    if (accountId) {
      const r = await httpJson("GET", `https://zernio.com/api/v1/accounts/${encodeURIComponent(accountId)}/health`, auth);
      if (r.status === 401 || r.status === 403) {
        return { ok: false, message: "Zernio rechazó la clave. Pégala de nuevo en Cuentas." };
      }
      if (r.status === 404) {
        return { ok: false, message: "Zernio no encuentra esa cuenta: revisa el account_id guardado en extra." };
      }
      if (r.status !== 200) {
        return { ok: false, message: "Zernio no respondió a la verificación. Espera un minuto y vuelve a intentarlo.", transient: isTransientStatus(r.status) };
      }
      const status = typeof r.json.status === "string" ? r.json.status : "";
      const issues = joinIssues(r.json.issues);
      if (status === "error") {
        return { ok: false, message: `Zernio dice que la cuenta TikTok necesita reconectarse${issues ? `: ${issues}` : "."}` };
      }
      // healthy / warning / sin campo: la clave vale y la cuenta existe.
      return { ok: true, message: `Zernio: cuenta TikTok conectada y sana${status === "warning" && issues ? ` (aviso: ${issues})` : "."}` };
    }
    // Sin account_id: basta con que la clave liste una cuenta TikTok conectada.
    const r = await httpJson("GET", "https://zernio.com/api/v1/accounts?platform=tiktok", auth);
    if (r.status === 401 || r.status === 403) {
      return { ok: false, message: "Zernio rechazó la clave. Pégala de nuevo en Cuentas." };
    }
    if (r.status !== 200) {
      return { ok: false, message: "Zernio no respondió a la verificación. Espera un minuto y vuelve a intentarlo.", transient: isTransientStatus(r.status) };
    }
    const accounts = Array.isArray(r.json.accounts) ? r.json.accounts : [];
    const connected = accounts.some((a) => {
      if (!a || typeof a !== "object") return false;
      const row = a as Record<string, unknown>;
      return row.platform === "tiktok" && (row.status === "connected" || row.status === undefined);
    });
    return connected
      ? { ok: true, message: "Zernio: hay una cuenta TikTok conectada." }
      : { ok: false, message: "La clave Zernio es válida, pero no hay ninguna cuenta TikTok conectada en Zernio. Conéctala en zernio.com y pega su account_id en extra." };
  } catch {
    return { ok: false, message: "No pudimos hablar con Zernio. Espera un minuto y vuelve a intentarlo.", transient: true };
  }
}

export async function verifyCredential(platform: string, credential: string, extraJson: Record<string, unknown> = {}): Promise<VerifyOutcome> {
  const full = credential.trim();
  if (!full) return { ok: false, message: "Falta la clave. Pégala y vuelve a intentarlo." };
  // YouTube OAuth: la credencial puede ser el JSON del token
  // (compacto de una línea: {token, refresh_token, client_id,
  // client_secret, scopes...}). Antes solo se tomaba la primera
  // línea y el JSON quedaba roto. Si es JSON con refresh_token, se
  // intercambia por un access token y se comprueba el scope
  // youtube.upload con tokeninfo. El resto de plataformas sigue
  // usando la primera línea (sus credenciales son de una línea).
  const cred = full.startsWith("{") ? full : (full.split("\n")[0] ?? "").trim();
  if (!cred) return { ok: false, message: "Falta la clave. Pégala y vuelve a intentarlo." };
  try {
    if (platform === "youtube") {
      if (cred.startsWith("{")) {
        let parsed: Record<string, unknown> = {};
        try {
          parsed = JSON.parse(cred) as Record<string, unknown>;
        } catch {
          return { ok: false, message: "El JSON de YouTube no es válido. Pega el archivo del token tal cual, en una sola línea." };
        }
        const refreshToken = typeof parsed.refresh_token === "string" ? parsed.refresh_token : "";
        const clientId = typeof parsed.client_id === "string" ? parsed.client_id : "";
        const clientSecret = typeof parsed.client_secret === "string" ? parsed.client_secret : "";
        const accessToken = typeof parsed.token === "string" ? parsed.token : "";
        // Con refresh_token se renueva: el access token guardado
        // puede estar caducado y eso no invalida la cuenta.
        if (refreshToken && clientId && clientSecret) {
          const body = new URLSearchParams({
            client_id: clientId,
            client_secret: clientSecret,
            refresh_token: refreshToken,
            grant_type: "refresh_token",
          });
          const ex = await httpJson("POST", "https://oauth2.googleapis.com/token", { "Content-Type": "application/x-www-form-urlencoded" }, body.toString());
          const fresh = typeof ex.json.access_token === "string" ? ex.json.access_token : "";
          if (ex.status !== 200 || !fresh) {
            return { ok: false, message: "YouTube no renovó el token con ese refresh_token: reautoriza OAuth y pega el JSON nuevo.", transient: isTransientStatus(ex.status) };
          }
          const ti = await httpJson("GET", `https://oauth2.googleapis.com/tokeninfo?access_token=${encodeURIComponent(fresh)}`);
          const scope = typeof ti.json.scope === "string" ? ti.json.scope.split(" ") : [];
          if (ti.status === 200 && scope.some((s) => s.includes("youtube.upload"))) {
            return { ok: true, message: "YouTube aceptó el token OAuth (scope youtube.upload, renovado con refresh_token)." };
          }
          if (ti.status === 200 && scope.some((s) => s.includes("youtube"))) {
            return { ok: false, message: "El token OAuth vale para YouTube pero NO tiene scope youtube.upload: reautoriza con ese permiso." };
          }
          return { ok: false, message: "YouTube renovó el token pero no se pudo comprobar su scope: vuelve a intentarlo.", transient: isTransientStatus(ti.status) };
        }
        // JSON sin refresh_token: se comprueba el access token que trae.
        if (accessToken) {
          const ti = await httpJson("GET", `https://oauth2.googleapis.com/tokeninfo?access_token=${encodeURIComponent(accessToken)}`);
          const scope = typeof ti.json.scope === "string" ? ti.json.scope.split(" ") : [];
          if (ti.status === 200 && scope.some((s) => s.includes("youtube.upload"))) {
            return { ok: true, message: "YouTube aceptó el token OAuth (scope youtube.upload). Sin refresh_token: cuando caduque habrá que pegar uno nuevo." };
          }
          if (ti.status !== 200) {
            return { ok: false, message: "YouTube no respondió a la comprobación del token: vuelve a intentarlo.", transient: isTransientStatus(ti.status) };
          }
          return { ok: false, message: "El token del JSON no tiene scope youtube.upload o caducó: pega el JSON con refresh_token." };
        }
        return { ok: false, message: "El JSON de YouTube no trae refresh_token ni token: pega el archivo del token completo." };
      }
      if (cred.startsWith("AIza")) {
        const r = await httpJson("GET", `https://www.googleapis.com/youtube/v3/i18nLanguages?part=snippet&key=${encodeURIComponent(cred)}`);
        return r.status === 200
          ? { ok: true, message: "YouTube aceptó la clave." }
          : { ok: false, message: "La clave no funcionó en YouTube. Revisa que sea la clave de tu proyecto y pégala de nuevo.", transient: isTransientStatus(r.status) };
      }
      // Token OAuth: primero tokeninfo. El token de subida del panel
      // solo tiene scope youtube.upload y channels?mine=true exige
      // readonly, así que daría 403 aunque el token sea válido.
      // tokeninfo dice el scope real: si incluye youtube, vale.
      const ti = await httpJson("GET", `https://oauth2.googleapis.com/tokeninfo?access_token=${encodeURIComponent(cred)}`);
      const scope = typeof ti.json.scope === "string" ? ti.json.scope.split(" ") : [];
      if (ti.status === 200 && scope.some((s) => s.includes("youtube"))) {
        const up = scope.some((s) => s.includes("youtube.upload"));
        return { ok: true, message: up ? "YouTube aceptó el token (scope youtube.upload)." : "YouTube aceptó el token." };
      }
      // Fallback: token con scope readonly (channels?mine=true).
      const r = await httpJson("GET", "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", { Authorization: `Bearer ${cred}` });
      return r.status === 200
        ? { ok: true, message: "YouTube aceptó la clave." }
        : { ok: false, message: "El acceso no funcionó en YouTube. Puede que haya vencido: crea uno nuevo y pégalo de nuevo.", transient: isTransientStatus(ti.status) || isTransientStatus(r.status) };
    }
    if (platform === "tiktok") {
      // Vía Zernio: extra {"via":"zernio"} o clave con prefijo zk_.
      if (extraJson.via === "zernio" || cred.startsWith("zk_")) {
        return verifyZernioTiktok(cred, str(extraJson.account_id));
      }
      const r = await httpJson("GET", "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name", { Authorization: `Bearer ${cred}` });
      const err = (r.json.error ?? {}) as Record<string, unknown>;
      const code = typeof err.code === "string" ? err.code : "";
      return r.status === 200 && (code === "" || code === "ok")
        ? { ok: true, message: "TikTok aceptó la clave." }
        : { ok: false, message: "La clave no funcionó en TikTok. Puede que haya vencido: crea una nueva y pégala de nuevo.", transient: isTransientStatus(r.status) };
    }
    if (platform === "twitch") {
      // Onboarding: credential = Client Secret, extra = {client_id}.
      // Se pide un token de app (client_credentials) y se comprueba
      // contra Helix: si el par ID/Secret no vale, Twitch dice 400.
      const clientId = str(extraJson.client_id);
      if (!clientId) return { ok: false, message: "Falta el Client ID de Twitch (va en Extra: {\"client_id\":\"…\"})." };
      const body = new URLSearchParams({ client_id: clientId, client_secret: cred, grant_type: "client_credentials" });
      const tok = await httpJson("POST", "https://id.twitch.tv/oauth2/token", { "Content-Type": "application/x-www-form-urlencoded" }, body.toString());
      const access = typeof tok.json.access_token === "string" ? tok.json.access_token : "";
      if (tok.status !== 200 || !access) {
        return { ok: false, message: "Twitch rechazó el Client ID/Secret. Revisa que los copiaste de dev.twitch.tv/console/apps.", transient: isTransientStatus(tok.status) };
      }
      // Validar el token (2026-11-19: /helix/users sin parámetros
      // devuelve 400 siempre, así que la verificación fallaba aunque
      // el par ID/Secret fuera válido). oauth2/validate dice si el
      // token vale y para qué client_id se emitió.
      const val = await httpJson("GET", "https://id.twitch.tv/oauth2/validate", { Authorization: `OAuth ${access}` });
      const valClientId = typeof val.json.client_id === "string" ? val.json.client_id : "";
      return val.status === 200 && valClientId === clientId
        ? { ok: true, message: "Twitch aceptó el Client ID/Secret." }
        : { ok: false, message: "Twitch dio el token pero no se pudo validar. Vuelve a intentarlo.", transient: isTransientStatus(val.status) };
    }
    if (platform === "gemini") {
      // Onboarding: credential = API key de Google AI Studio.
      // Listar modelos con la key: 200 = la key vale.
      const r = await httpJson("GET", `https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(cred)}`);
      if (r.status === 200) return { ok: true, message: "Gemini aceptó la API key." };
      if (r.status === 400 || r.status === 403) {
        return { ok: false, message: "Gemini rechazó la API key. Revisa que la copiaste de aistudio.google.com/apikey." };
      }
      return { ok: false, message: "Gemini no respondió a la verificación. Espera un minuto y vuelve a intentarlo.", transient: isTransientStatus(r.status) };
    }
    // Meta (instagram / facebook / meta): Graph API /me con el token.
    // OJO (2026-11-18): /me solo prueba el token de la Página, NO la
    // cuenta de Instagram. El clip 13 falló al publicar con el token
    // verificado en verde, porque el ig_user_id guardado ya no era
    // accesible. Si hay ig_user_id en extra, se comprueba de verdad.
    const r = await httpJson("GET", `https://graph.facebook.com/v26.0/me?access_token=${encodeURIComponent(cred)}`);
    if (r.status === 200 && typeof r.json.id === "string") {
      const igUserId = str(extraJson.ig_user_id);
      if (igUserId) {
        const ig = await httpJson("GET", `https://graph.facebook.com/v26.0/${encodeURIComponent(igUserId)}?fields=username&access_token=${encodeURIComponent(cred)}`);
        const username = typeof ig.json.username === "string" ? ig.json.username : "";
        if (ig.status === 200 && username) {
          return { ok: true, message: `Meta aceptó el token. Instagram @${username} accesible.` };
        }
        return {
          ok: false,
          message: "Meta aceptó el token, pero la cuenta de Instagram guardada no es accesible (puede que se haya desvinculado de la Página o esté restringida). Vuelve a conectar Meta en Cuentas para actualizar el vínculo.",
          transient: isTransientStatus(ig.status),
        };
      }
      return { ok: true, message: "Meta aceptó el token." };
    }
    return {
      ok: false,
      message: "Meta no aceptó la clave. Tiene que ser el token LARGO de la Página: en el Explorador, en «Usuario o página» elige tu Página y copia ese token (el corto vence en 1 hora).",
      transient: isTransientStatus(r.status),
    };
  } catch {
    return { ok: false, message: "No pudimos hablar con la plataforma. Espera un minuto y vuelve a intentarlo.", transient: true };
  }
}
