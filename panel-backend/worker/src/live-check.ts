// Comprobación de directos AHORA (Twitch Helix + Kick API pública).
//
// Antes vivía inline en el handler GET /api/influencers/live de
// index.ts. Se extrae aquí porque el validador de suministro
// (worker-validate.ts) también necesita saber quién está en directo
// en GTA VI: sin directos ES en vivo no hay material que vigilar y
// el estado es "Sin directos ES", no ATASCADO.
//
// Hallazgos que corrige esta extracción (2026-11-19):
// - La cuenta Twitch NO existía en api_accounts (el paso 3 del
//   onboarding nunca se completó en el panel): todos los Twitch
//   salían live=null con "Falta el Client ID/Secret". Sin ella no
//   hay forma de saber si un ES está en directo.
// - Helix devolvía HTTP 400 para TODO el lote: normalizeHandle no
//   quita espacios y "Kai Cenat"/"Will Neff" iban como user_login
//   con espacio ("kai cenat"), que Helix rechaza. twitchLogin deja
//   el login en [a-z0-9_] tanto al preguntar como al buscar el
//   resultado.
// - El endpoint no devolvía el juego: Helix trae game_name y Kick
//   trae categories[0].name. Sin el juego no se puede aplicar la
//   puerta de categoría (solo GTA VI vale, clipagent/category.py).
import type { D1Database } from "./cloudflare";
import type { Tenant } from "./auth";
import { listApiAccounts, listInfluencers, normalizeHandle } from "./db";
import { decryptCredential } from "./crypto";
import { httpJson } from "./publish/http";

/** Solo lo que necesita la comprobación de directos (nunca el Env
 *  entero: la clave de cifrado es lo único que se toca). */
export interface LiveCheckEnv {
  CREDENTIAL_ENCRYPTION_KEY: string;
}

export interface LiveCheckEntry {
  id: number;
  handle: string;
  platform: string;
  language: string;
  live: boolean | null;
  /** Juego/categoría del directo (Helix game_name, Kick categories[0]). */
  game: string | null;
  title: string | null;
  viewers: number | null;
  reason: string;
}

// Nombres canónicos de la categoría (mismo conjunto que
// clipagent/category.py GTA6_NAMES, normalizado igual: minúsculas
// y espacios simples).
const GTA6_NAMES = new Set([
  "gta vi",
  "gta 6",
  "gta6",
  "grand theft auto vi",
  "grand theft auto 6",
  "grand theft auto vi (gta 6)",
]);

export function normalizeCategory(name: string | null | undefined): string {
  return (name ?? "").trim().toLowerCase().replace(/\s+/g, " ");
}

/** ¿La categoría del directo es GTA VI? Solo GTA VI vale para un
 *  clip (puerta de categoría del usuario, 2026-11-18). Espejo exacto
 *  de clipagent/category.py is_gta6: conjunto canónico + prefijo
 *  ("grand theft auto vi ..." con sufijo, como la categoría de Kick
 *  "Grand Theft Auto VI (GTA)"), pero "gta v" / "grand theft auto v"
 *  NO valen. */
export function isGta6Category(game: string | null | undefined): boolean {
  const norm = normalizeCategory(game);
  if (GTA6_NAMES.has(norm)) return true;
  return norm.startsWith("grand theft auto vi") || norm.startsWith("gta vi ");
}

/** Login de Twitch a partir del nombre del panel: Helix solo acepta
 *  [a-z0-9_] en user_login. "Kai Cenat" es kaicenat; sin quitar el
 *  espacio Helix devuelve 400 para TODO el lote (2026-11-19). */
export function twitchLogin(handle: string): string {
  return normalizeHandle(handle).replace(/[^a-z0-9_]/g, "");
}

/** ¿Quién está en directo AHORA? Twitch Helix (GET /helix/streams)
 *  necesita el Client ID/Secret de Twitch guardados en Cuentas
 *  (onboarding, paso 3): sin ellos el estado es "sin verificar"
 *  (live: null) con el motivo exacto, nunca un vivo/offline
 *  inventado. Kick se consulta por su API pública (sin credencial).
 *  Solo lectura: no publica nada. */
export async function checkInfluencersLive(
  db: D1Database,
  env: LiveCheckEnv,
  tenant: Tenant,
): Promise<LiveCheckEntry[]> {
  const rows = await listInfluencers(db, tenant);
  const accounts = await listApiAccounts(db, tenant);
  const tw = accounts.find((a) => a.platform === "twitch" && a.credential_enc);
  const twLive = new Map<string, { title: string; viewers: number; game: string }>();
  let twReason = "";
  if (!tw) {
    twReason =
      "Falta el Client ID/Secret de Twitch en Cuentas (asistente de onboarding, paso 3): sin ellos el Worker no puede preguntar a Twitch quién está en directo.";
  } else {
    try {
      const secret = await decryptCredential(tw.credential_enc, env.CREDENTIAL_ENCRYPTION_KEY, tenant);
      let clientId = "";
      if (tw.extra_enc) {
        try {
          const extra = JSON.parse(await decryptCredential(tw.extra_enc, env.CREDENTIAL_ENCRYPTION_KEY, tenant)) as Record<
            string,
            unknown
          >;
          if (typeof extra.client_id === "string") clientId = extra.client_id;
        } catch {
          clientId = "";
        }
      }
      if (!clientId) {
        twReason = "La cuenta Twitch no tiene Client ID en Extra: pégalo en el onboarding (paso 3).";
      } else {
        const tokBody = new URLSearchParams({ client_id: clientId, client_secret: secret, grant_type: "client_credentials" });
        const tok = await httpJson("POST", "https://id.twitch.tv/oauth2/token", { "Content-Type": "application/x-www-form-urlencoded" }, tokBody.toString());
        const access = typeof tok.json.access_token === "string" ? tok.json.access_token : "";
        if (tok.status !== 200 || !access) {
          twReason = "Twitch rechazó el Client ID/Secret: revísalos en Cuentas (onboarding, paso 3).";
        } else {
          const logins = rows.filter((r) => r.platform === "twitch").map((r) => twitchLogin(r.name));
          if (logins.length > 0) {
            const qs = logins.map((l) => `user_login=${encodeURIComponent(l)}`).join("&");
            const streams = await httpJson("GET", `https://api.twitch.tv/helix/streams?${qs}`, {
              Authorization: `Bearer ${access}`,
              "Client-Id": clientId,
            });
            if (streams.status !== 200) {
              twReason = `Twitch Helix devolvió HTTP ${streams.status}: no se pudo comprobar el directo.`;
            } else {
              const data = Array.isArray(streams.json.data) ? (streams.json.data as Array<Record<string, unknown>>) : [];
              for (const s of data) {
                const login = typeof s.user_login === "string" ? s.user_login.toLowerCase() : "";
                if (login) {
                  twLive.set(login, {
                    title: typeof s.title === "string" ? s.title : "",
                    viewers: typeof s.viewer_count === "number" ? s.viewer_count : 0,
                    game: typeof s.game_name === "string" ? s.game_name : "",
                  });
                }
              }
            }
          }
        }
      }
    } catch {
      twReason = "No se pudo hablar con Twitch: revisa la conexión y vuelve a intentarlo.";
    }
  }
  const live: LiveCheckEntry[] = [];
  for (const r of rows) {
    if (r.platform === "twitch") {
      const hit = twLive.get(twitchLogin(r.name));
      live.push({
        id: r.id,
        handle: r.name,
        platform: "twitch",
        language: r.language ?? "es",
        live: hit ? true : twReason ? null : false,
        game: hit?.game ?? null,
        title: hit?.title ?? null,
        viewers: hit?.viewers ?? null,
        reason: twReason,
      });
    } else if (r.platform === "kick") {
      try {
        const k = await httpJson("GET", `https://kick.com/api/v2/channels/${encodeURIComponent(normalizeHandle(r.name))}`);
        const ls = k.json.livestream as Record<string, unknown> | null | undefined;
        const cats = Array.isArray(ls?.categories) ? (ls.categories as Array<Record<string, unknown>>) : [];
        const firstCat = cats.length > 0 ? cats[0] : undefined;
        const game = firstCat && typeof firstCat.name === "string" ? (firstCat.name as string) : "";
        live.push({
          id: r.id,
          handle: r.name,
          platform: "kick",
          language: r.language ?? "es",
          live: k.status === 200 ? !!ls : null,
          game: ls ? game : null,
          title: ls && typeof ls.session_title === "string" ? (ls.session_title as string) : null,
          viewers: ls && typeof ls.viewer_count === "number" ? (ls.viewer_count as number) : null,
          reason: k.status === 200 ? "" : `Kick devolvió HTTP ${k.status}: no se pudo comprobar el directo.`,
        });
      } catch {
        live.push({
          id: r.id,
          handle: r.name,
          platform: "kick",
          language: r.language ?? "es",
          live: null,
          game: null,
          title: null,
          viewers: null,
          reason: "No se pudo hablar con Kick.",
        });
      }
    } else {
      live.push({
        id: r.id,
        handle: r.name,
        platform: r.platform,
        language: r.language ?? "es",
        live: null,
        game: null,
        title: null,
        viewers: null,
        reason: `La plataforma ${r.platform} no tiene comprobación de directo.`,
      });
    }
  }
  return live;
}

/** ¿Cuántos streamers en español están en directo EN GTA VI ahora
 *  mismo? Devuelve null si NO se pudo comprobar alguno (live null:
 *  sin credencial de Twitch, plataforma sin comprobación o API
 *  caída): con un solo ES sin verificar no se puede afirmar que
 *  no hay directos ES. Nunca inventa un 0. */
export function countLiveEsGta6(entries: LiveCheckEntry[]): number | null {
  let count = 0;
  for (const e of entries) {
    if ((e.language ?? "es") !== "es") continue;
    if (e.live === null) return null;
    if (e.live && isGta6Category(e.game)) count++;
  }
  return count;
}

/** Comprueba los directos y devuelve el conteo ES en GTA VI (o null
 *  si no se pudo comprobar). Nunca lanza: si algo falla, null y el
 *  validador mantiene el veredicto anterior. */
export async function countEsLiveGta6(db: D1Database, env: LiveCheckEnv, tenant: Tenant): Promise<number | null> {
  try {
    return countLiveEsGta6(await checkInfluencersLive(db, env, tenant));
  } catch {
    return null;
  }
}
