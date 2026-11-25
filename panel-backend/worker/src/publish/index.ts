// Despachador de publicación: resuelve las credenciales cifradas del
// panel y llama al adaptador de la plataforma. Las credenciales se
// descifran SOLO aquí, en memoria, y nunca se registran ni se
// devuelven.
//
// Formato guardado (api_accounts, columnas *_enc, AES-GCM):
// - youtube: credential_enc = API key (verificación); extra_enc = JSON
//   {"client_id","client_secret","refresh_token"} (OAuth para subir).
// - tiktok (Direct Post propio): credential_enc = access token
//   (scope video.publish). Sin auditoría solo permite privado.
// - tiktok vía Zernio (público): credential_enc = Zernio API key,
//   extra_enc = {"via":"zernio","account_id":"<id cuenta Zernio>"}.
//   La app TikTok for Business de Zernio ya está auditada, así que
//   el vídeo directo es SOLO PÚBLICO (ver publish/zernio-tiktok.ts).
//   Si via no es "zernio", se usa el Direct Post propio.
// - meta:   credential_enc = Page access token; extra_enc = JSON
//   {"ig_user_id","page_id"}.
// Si solo hay texto plano legacy en `credential`, NO se usa: el
// resultado es draft pidiendo volver a pegar la credencial (así el
// texto plano viejo nunca viaja ni se usa).

import type { Env } from "../cloudflare";
import type { Tenant } from "../auth";
import { decryptCredential } from "../crypto";
import { clipLanguage, evidenceUrl, getApiAccount, getClip, languageName, listApiAccounts, listSocialAccounts, panelPlatformLabel, type ClipLanguage, type PanelPlatform } from "../db";
import { classifyGateBlock, recheckPublishGate, reservePublishSlot, retryAtForGateBlock } from "../gate";
import type { JobRow } from "../jobs";
import { facebookStep } from "./facebook";
import { instagramStep } from "./instagram";
import { tiktokStep } from "./tiktok";
import { youtubeStep, type YoutubeCreds } from "./youtube";
import { zernioTiktokStep } from "./zernio-tiktok";
import { captionMarkerReason, checkDuplicateBeforeUpload, hasOwnClipMarker } from "./idempotency";
import { appendWalEntry } from "../wal";
import { santiagoDayKey } from "../gate";

export interface ResumeData {
  step?: string;
  [key: string]: unknown;
}

export interface PublishCreds {
  youtube?: YoutubeCreds;
  tiktok?: string;
  /** "zernio" = publicar por Zernio (público); ausente = Direct Post propio. */
  tiktokVia?: "zernio" | "direct";
  /** Id de la cuenta TikTok en Zernio (extra.account_id). */
  tiktokAccountId?: string;
  instagram?: string;
  facebook?: string;
  igUserId?: string;
  pageId?: string;
}

export interface PublishContext {
  job: JobRow & { resumeData: ResumeData | null };
  creds: PublishCreds;
  r2Size: number | null;
  /** Handle real del canal TikTok (tabla accounts, 0030) para la
   *  evidence_url. Sin handle, la URL va sin @usuario. Antes se
   *  hardcodeaba @elclipdegta6 también para los clips EN. */
  tiktokHandle?: string | null;
}

export type PublishResult =
  | { mode: "posted"; platformId: string; evidenceUrl: string; alreadyPublished?: boolean }
  | { mode: "processing"; resumeData: ResumeData; reason?: string }
  | { mode: "draft"; reason: string }
  | { mode: "awaiting_captcha"; reason: string }
  | { mode: "error"; reason: string; retryable: boolean }
  /** Freno TRANSITORIO en ejecución (F2, spec 2026-11-24): el job
   *  no va a draft, se reprograma a `retryAtMs` (ver
   *  applyPublishResult, caso "retry"). */
  | { mode: "retry"; reason: string; retryAtMs: number };

function parseJson(text: string): Record<string, unknown> {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

/** Descifra las credenciales de una plataforma desde api_accounts.
 *  El tenant es el de la sesión / del job: nunca se descifra ni se
 *  lee una cuenta de otro tenant.
 *
 *  DOS COLAS (0019): la credencial se resuelve POR PLATAFORMA +
 *  IDIOMA. `language` es el idioma del clip; `accountId` (cuando
 *  viene, es la cuenta que POST /publish ya resolvió para ese
 *  idioma) manda y se comprueba que su idioma coincide: si la
 *  cuenta es del otro idioma, NO se devuelve credencial (el job
 *  queda en draft con el motivo, nunca se publica en el canal del
 *  otro idioma). Sin accountId, se busca la cuenta de ese idioma.
 *  El fallback a la cuenta del otro idioma está PROHIBIDO aquí. */
export async function resolveCreds(
  env: Env,
  platform: PanelPlatform,
  tenant: Tenant,
  language: ClipLanguage = "es",
  accountId?: number,
): Promise<PublishCreds> {
  const out: PublishCreds = {};
  const accounts = await listApiAccounts(env.DB, tenant);
  // api_accounts.platform: "youtube" | "tiktok" | "meta" (panel v17).
  const want = platform === "youtube_shorts" ? "youtube" : platform === "tiktok" ? "tiktok" : "meta";
  const row = accountId !== undefined
    ? accounts.find((a) => a.id === accountId)
    : accounts.find((a) => a.platform === want && a.language === language && (a.credential_enc || a.credential));
  if (!row) return out;
  // La cuenta elegida es de otra plataforma o de otro idioma: sin
  // credencial. El job queda en draft (ver dispatchPublishStep),
  // nunca publica aquí.
  if (row.platform !== want || row.language !== language) return out;
  if (!row.credential_enc) {
    // Texto plano legacy: no se usa (ver cabecera).
    return out;
  }
  const key = env.CREDENTIAL_ENCRYPTION_KEY;
  // Clave derivada por tenant (HKDF, crypto.ts): la D1 es compartida
  // pero cada tenant solo descifra lo suyo.
  const credential = await decryptCredential(row.credential_enc, key, tenant).catch(() => "");
  const extra = row.extra_enc ? await decryptCredential(row.extra_enc, key, tenant).catch(() => "") : "";
  const extraJson = parseJson(extra);
  if (platform === "youtube_shorts") {
    // OAuth en extra (vía clásica) o en la credencial misma (JSON
    // del token pegado en Cuentas, como lo verifica verify.ts).
    let clientId = str(extraJson.client_id);
    let clientSecret = str(extraJson.client_secret);
    let refreshToken = str(extraJson.refresh_token);
    if ((!clientId || !clientSecret || !refreshToken) && credential.trim().startsWith("{")) {
      const fromCred = parseJson(credential.trim());
      clientId = clientId ?? str(fromCred.client_id);
      clientSecret = clientSecret ?? str(fromCred.client_secret);
      refreshToken = refreshToken ?? str(fromCred.refresh_token);
    }
    if (clientId && clientSecret && refreshToken) {
      out.youtube = { accessToken: "", refreshToken, clientId, clientSecret };
    }
  } else if (platform === "tiktok") {
    if (credential) out.tiktok = credential.split("\n")[0]?.trim();
    // Vía Zernio: la credencial ES la Zernio API key y el account_id
    // va en extra. Sin via=zernio, Direct Post propio (privado).
    if (extraJson.via === "zernio") {
      out.tiktokVia = "zernio";
      out.tiktokAccountId = str(extraJson.account_id);
    }
  } else {
    if (credential) {
      out.instagram = credential;
      out.facebook = credential;
    }
    out.igUserId = str(extraJson.ig_user_id);
    out.pageId = str(extraJson.page_id);
  }
  return out;
}

/** Un paso de publicación de un job. Jobs que no son `publish`
 *  (vod_check, cut, subtitle, package) NO los ejecuta el Worker:
 *  el corte y los subtítulos son manuales en el plan principal
 *  (solo Cloudflare, ver COMPUTE.md); el puente de GitHub Actions
 *  es solo una alternativa. Aquí quedan en processing con el motivo,
 *  sin fingir avance. */
export async function dispatchPublishStep(env: Env, job: JobRow): Promise<PublishResult> {
  if (job.kind !== "publish" || !job.platform) {
    return {
      mode: "processing",
      resumeData: {},
      reason: `paso ${job.kind}: no lo ejecuta el Worker (corte/subtítulos son manuales en el plan solo-Cloudflare; ver COMPUTE.md)`,
    };
  }
  if (!job.r2_key) {
    return { mode: "draft", reason: "el clip no tiene MP4 en R2: súbelo desde el Editor antes de publicar." };
  }
  // PUERTA DE IDIOMA en el Worker (0019, segunda línea de defensa;
  // la primera es publishGate en POST /publish): el job solo se
  // ejecuta con la cuenta de (plataforma, idioma del clip). Si el
  // job trae otra cuenta, o no hay cuenta en ese idioma, queda en
  // draft con el motivo — nunca se publica en el canal del otro
  // idioma, aunque la cuenta ES esté conectada y la EN no.
  const clip = job.clip_id ? await getClip(env.DB, job.clip_id, job.tenant_id) : null;
  const lang = clipLanguage(clip ?? {});
  if (job.api_account_id) {
    const acc = await getApiAccount(env.DB, job.api_account_id, job.tenant_id);
    if (!acc) {
      return { mode: "draft", reason: "el job apunta a una cuenta que ya no existe: vuelve a encolar el clip desde Publicar." };
    }
    const want = job.platform === "youtube_shorts" ? "youtube" : job.platform === "tiktok" ? "tiktok" : "meta";
    if (acc.platform !== want || acc.language !== lang) {
      return {
        mode: "draft",
        reason: `idioma: el clip es en ${languageName(lang)} pero la cuenta ${panelPlatformLabel(job.platform)} del job es en ${languageName(acc.language)}: nunca se publica en el canal del otro idioma`,
      };
    }
  }
  const head = await env.CLIPS_BUCKET.head(job.r2_key).catch(() => null);
  // RE-CHEQUEO DEL GATE EN EJECUCIÓN (M1, 2026-11-18): el gate de
  // enqueue ya no decide solo. Con el clip actual y los contadores
  // frescos, si frena por un motivo TRANSITORIO (F2, spec
  // 2026-11-24: espaciamiento, 15 min, 1-por-franja, rolling,
  // tope) el job se REPROGRAMA a retryAtMs, no va a draft. Si
  // frena por un motivo DURO (calidad, sin cuenta, streamer
  // desactivado), va a draft como hoy. Los retornos directos que
  // no pasan por el gate (sin MP4, cuenta inexistente o en otro
  // idioma) siguen devolviendo "draft" sin cambio.
  const gateReason = await recheckPublishGate(env, job);
  if (gateReason) {
    if (classifyGateBlock(gateReason).transient) {
      const retryAtMs = await retryAtForGateBlock(env, job, gateReason, Date.now());
      return { mode: "retry", reason: gateReason, retryAtMs };
    }
    return { mode: "draft", reason: `gate de publicación: ${gateReason}` };
  }
  // RESERVA ATÓMICA DEL CUPO (0022, NEEDS-VALIDATION §4): entre
  // el re-chequeo y el adaptador, otro job del mismo grupo (o de
  // otro ciclo) podría publicar y rebasar el tope. La reserva en
  // D1 hace visible este cupo al siguiente job antes de subir.
  const reserve = await reservePublishSlot(env.DB, job);
  if (!reserve.ok) {
    if (classifyGateBlock(reserve.reason).transient) {
      const retryAtMs = await retryAtForGateBlock(env, job, reserve.reason, Date.now());
      return { mode: "retry", reason: reserve.reason, retryAtMs };
    }
    return { mode: "draft", reason: `gate de publicación: ${reserve.reason}` };
  }
  const creds = await resolveCreds(env, job.platform, job.tenant_id, lang, job.api_account_id ?? undefined);
  // B2 — COMPROBACIÓN PREVIA A SUBIR (solo en el primer paso de
  // un job fresco, sin resume_data): diario WAL + plataforma. Si
  // ya existe, NO se sube: se devuelve posted con la evidencia
  // encontrada y el wrapper (jobs.ts) lo registra como
  // already_published en el diario; el volcado lo marca en D1.
  // Una subida multi-step en curso no se re-comprueba en cada chunk.
  const isFresh = !job.resume_data;
  if (isFresh && job.clip_id) {
    const dup = await checkDuplicateBeforeUpload(env, job, creds).catch(() => null);
    if (dup?.duplicate && dup.platformId && dup.evidenceUrl) {
      return { mode: "posted", platformId: dup.platformId, evidenceUrl: dup.evidenceUrl, alreadyPublished: true };
    }
    // B1 — INTENCIÓN en el diario ANTES de subir (solo-agregar,
    // nunca tumba la subida si R2 falla). Sin intención no hay
    // resultado que volcar: el registro en D1 puede esperar a que
    // la cuota vuelva, pero la subida no espera a D1.
    await appendWalEntry(env, job.tenant_id, {
      type: "intent",
      clip_id: job.clip_id,
      platform: job.platform,
      account_id: job.api_account_id ?? null,
      job_id: job.id,
      idempotency_key: job.idempotency_key,
      day: santiagoDayKey(),
    });
  }
  // Handle del canal (tabla accounts) para la evidence_url de
  // TikTok: sin fila, null y la URL va sin @usuario (nunca un
  // handle hardcodeado del otro idioma).
  const tiktokHandle =
    job.platform === "tiktok"
      ? ((await listSocialAccounts(env.DB, job.tenant_id).catch(() => []))
          .find((a) => a.platform === "tiktok" && a.language === lang)?.handle ?? null)
      : null;
  const ctx: PublishContext = {
    // Sin marca [clip:<id>] en el caption que viaja a la plataforma
    // (2026-11-23): era visible en público (reel del clip 168 en
    // Instagram). El caption guardado en el job viaja tal cual a
    // todas las plataformas; la protección contra duplicados es
    // WAL + idempotency_key, y la marca solo se LEE para posts
    // viejos que ya la llevan. El chequeo V4 está justo debajo,
    // antes del switch: un caption que YA lleve su marca propia
    // (job viejo) se rechaza aquí, no se envía.
    job: { ...job, resumeData: job.resume_data ? parseJson(job.resume_data) : null, caption: job.caption },
    creds,
    r2Size: head?.size ?? null,
    tiktokHandle,
  };
  // V4 — VALIDADOR DE CAPTION (SPEC validators, Goldfish 8):
  // el caption con la marca PROPIA ([clip:<id-del-clip>]) se
  // rechaza ANTES de despachar a cualquier adaptador. Sin
  // cambiar límites de caracteres: el corte a 150 (TikTok
  // Direct) / 2200 (Zernio) lo hace cada adaptador. Error no
  // reintentable: es un defecto del dato guardado en el job,
  // no un fallo temporal. La intención B1 ya quedó en el
  // diario; el respaldo la ve y la rechaza igual con su propio
  // chequeo V4 (sin subir).
  if (hasOwnClipMarker(ctx.job.caption, ctx.job.clip_id)) {
    return { mode: "error", reason: captionMarkerReason(ctx.job.clip_id as number), retryable: false };
  }
  switch (job.platform) {
    case "youtube_shorts":
      return youtubeStep(ctx, env);
    case "tiktok":
      return creds.tiktokVia === "zernio" ? zernioTiktokStep(ctx, env) : tiktokStep(ctx, env);
    case "instagram_reels":
      return instagramStep(ctx, env);
    case "facebook_reels":
      return facebookStep(ctx, env);
  }
}

export async function getAccountForVerify(env: Env, id: number, tenant: Tenant) {
  return getApiAccount(env.DB, id, tenant);
}
