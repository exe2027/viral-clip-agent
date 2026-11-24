// Validación diaria de cuentas vinculadas (spec 2026-11-23,
// SPEC-2026-11-23-validacion-diaria-cuentas.md).
//
// Cada día, en la primera pasada del ciclo a partir de las 15:00
// hora de Chile, se verifican de verdad todas las cuentas de
// api_accounts del tenant que tengan credencial guardada y se
// guarda el resultado en D1. El panel Cuentas muestra cuáles están
// activas; un aviso por Telegram solo cuando una cuenta pasa de
// válida a inválida (flip 1→0).
//
// Reglas que este módulo respeta (la spec las detalla):
// - Puerta por READ (isAccountValidationDue, cycle-gate.ts):
//   no revalida dos veces el mismo día; fail-open si no se puede
//   leer settings; no corre con el Worker parado.
// - NO reutiliza verifyApiAccount (su rama de aviso alertaría cada
//   día una cuenta persistentemente inválida): llama a
//   verifyCredential + updateApiAccountVerification directamente.
// - Transitorios (red/timeout/HTTP 5xx, flag transient en
//   VerifyOutcome) NO se escriben, NO marcan el día y se reintentan
//   en la siguiente pasada; una corrida parcial no escribe resumen.
// - Aislamiento por cuenta (try/catch por cuenta + catch superior
//   que nunca rompe scheduled()); fallo SISTÉMICO de descifrado
//   (todas las cuentas) no escribe inválido, no alerta y deja
//   constancia en worker_events.
// - La sonda de scopes (probeNativeScopes force) es informativa y
//   no afecta verify_ok. El resumen se escribe con settings FRESCOS
//   DESPUÉS de la sonda (saveSettings reemplaza el JSON entero y
//   una instantánea anterior pisaría engagement_scopes).
// - La validación corre DESPUÉS de runWorkerCycle (la invoca
//   scheduled() en index.ts): nunca bloquea el corte.

import type { D1Database, Env } from "./cloudflare";
import type { Tenant } from "./auth";
import { decryptCredential } from "./crypto";
import {
  getApiAccount,
  getSettings,
  isAccountVerificationCurrent,
  listApiAccounts,
  logWorkerEvent,
  saveSettings,
  updateApiAccountVerification,
  type ApiAccountRow,
} from "./db";
import { isWorkerRunning, notify } from "./jobs";
import { probeNativeScopes } from "./publish/native-engagement";
import { santiagoParts } from "./telegram";
import { ACCOUNT_VALIDATION_DAY_KEY, isAccountValidationDue } from "./cycle-gate";
import { verifyCredential, type VerifyOutcome } from "./verify";

export interface AccountValidationSummary {
  day: string;
  n_ok: number;
  n_invalid: number;
  checked_at: number;
}

export type DailyValidationRunReason =
  | "not_due"
  | "worker_stopped"
  | "systemic_decrypt"
  | "partial"
  | "done";

export interface DailyValidationResult {
  ran: boolean;
  reason: DailyValidationRunReason;
  summary: AccountValidationSummary | null;
  partial: boolean;
  /** Cuentas con credencial (denominador del n/n válidas). */
  checked: number;
  valid: number;
  invalid: number;
  transient: number;
}

function emptyResult(reason: DailyValidationRunReason): DailyValidationResult {
  return { ran: false, reason, summary: null, partial: false, checked: 0, valid: 0, invalid: 0, transient: 0 };
}

/** Vigencia actual de una cuenta, releyendo la fila (la escritura
 *  puede saltarse por A2 si nada cambió: verified_at no se
 *  refresca). null = no se pudo leer: no se puede afirmar un flip,
 *  así que no se alerta. */
async function currentAfter(db: D1Database, id: number, tenant: Tenant): Promise<boolean | null> {
  try {
    const row = await getApiAccount(db, id, tenant);
    return row ? isAccountVerificationCurrent(row) : null;
  } catch {
    return null;
  }
}

/** Aviso de flip 1→0 por notify() (el remitente establecido: lookup
 *  del vínculo, chequeo botConfigured, dedupe de 6 h, fila en
 *  notifications y evento en el panel). El texto lleva la fecha y
 *  hora de Chile, así que cada flip es único y el dedupe de 6 h
 *  nunca suprime un flip genuino. Nunca levanta. */
async function maybeAlertFlip(
  db: D1Database,
  tenant: Tenant,
  env: Env,
  acc: ApiAccountRow,
  before: boolean,
  after: boolean | null,
  message: string,
  nowMsValue: number,
): Promise<void> {
  if (!before || after !== false) return;
  const { day, hour, minute } = santiagoParts(new Date(nowMsValue));
  const when = `${day} ${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  const text = `Cuenta inválida: ${acc.platform} ${acc.language} (${acc.label}) — ${message} — ${when}`;
  try {
    await notify(db, { kind: "credential", message: text }, tenant, env);
  } catch {
    // Que el aviso falle no rompe la corrida.
  }
}

/** La corrida diaria completa (puerta + verificación + sonda +
 *  resumen). Con force: true ignora la puerta y el estado del
 *  Worker (ruta manual POST /api/accounts/validate-daily): una
 *  corrida forzada SÍ marca el día. Nunca levanta por un fallo de
 *  red o de una cuenta; solo un fallo de D1 al leer las cuentas
 *  puede propagarse (el llamador lo captura). */
export async function validateAccountsDaily(
  env: Env,
  tenant: Tenant,
  nowMsValue: number,
  opts: { force?: boolean } = {},
): Promise<DailyValidationResult> {
  const db = env.DB;
  const { day } = santiagoParts(new Date(nowMsValue));
  if (!opts.force) {
    if (!(await isAccountValidationDue(db, tenant, nowMsValue))) return emptyResult("not_due");
    if (!(await isWorkerRunning(db, tenant))) return emptyResult("worker_stopped");
  }
  const accounts = await listApiAccounts(db, tenant);
  const withCred = accounts.filter((a) => a.credential_enc || a.credential);

  // Paso 1: descifrar todas las credenciales. Si TODAS fallan, es
  // un fallo sistémico (p. ej. CREDENTIAL_ENCRYPTION_KEY
  // incorrecta en el Worker): no se escribe inválido en ninguna,
  // no se alerta, constancia en worker_events. Sin esta guarda, una
  // sola causa raíz marcaría todas las cuentas inválidas y
  // dispararía N flips. El extra se descifra con la misma
  // tolerancia que verifyApiAccount (si falla, extra = {}).
  type Decrypted = {
    acc: ApiAccountRow;
    credential: string;
    extraJson: Record<string, unknown>;
    decryptError: string | null;
  };
  const decrypted: Decrypted[] = [];
  for (const acc of withCred) {
    let credential = "";
    let decryptError: string | null = null;
    try {
      credential = acc.credential_enc
        ? await decryptCredential(acc.credential_enc, env.CREDENTIAL_ENCRYPTION_KEY, tenant)
        : acc.credential;
    } catch (e) {
      decryptError = e instanceof Error ? e.message : String(e);
    }
    let extraJson: Record<string, unknown> = {};
    try {
      const extraRaw = acc.extra_enc
        ? await decryptCredential(acc.extra_enc, env.CREDENTIAL_ENCRYPTION_KEY, tenant)
        : acc.extra || "";
      if (extraRaw) extraJson = JSON.parse(extraRaw) as Record<string, unknown>;
    } catch {
      extraJson = {};
    }
    decrypted.push({ acc, credential, extraJson, decryptError });
  }
  const decryptFailed = decrypted.filter((d) => d.decryptError !== null);
  if (withCred.length > 0 && decryptFailed.length === withCred.length) {
    await logWorkerEvent(
      db,
      "validacion_cuentas",
      `Validación diaria omitida: el descifrado falló en las ${withCred.length} cuentas con credencial (fallo sistémico, probablemente CREDENTIAL_ENCRYPTION_KEY). No se marcó nada como inválido ni se alertó.`,
      tenant,
    ).catch(() => undefined);
    return { ran: true, reason: "systemic_decrypt", summary: null, partial: true, checked: withCred.length, valid: 0, invalid: 0, transient: 0 };
  }

  // Paso 2: verificar cada cuenta, con aislamiento por cuenta.
  let valid = 0;
  let invalid = 0;
  let transientCount = 0;
  for (const d of decrypted) {
    const before = isAccountVerificationCurrent(d.acc);
    try {
      if (d.decryptError !== null) {
        // Solo esta cuenta falla al descifrar: inválida con el
        // motivo (clave por cuenta, no sistémico).
        const msg = `No se pudo descifrar la credencial guardada (${d.decryptError}). Vuelve a pegarla en Cuentas.`;
        await updateApiAccountVerification(db, d.acc.id, tenant, false, "error", msg);
        const after = await currentAfter(db, d.acc.id, tenant);
        invalid++;
        await maybeAlertFlip(db, tenant, env, d.acc, before, after, msg, nowMsValue);
        continue;
      }
      let outcome: VerifyOutcome;
      try {
        outcome = await verifyCredential(d.acc.platform, d.credential, d.extraJson);
      } catch {
        outcome = { ok: false, message: "No pudimos hablar con la plataforma. Espera un minuto y vuelve a intentarlo.", transient: true };
      }
      if (outcome.transient) {
        // Transitorio: no se escribe, no marca el día, se reintenta
        // en la siguiente pasada. No cuenta como válida ni inválida.
        transientCount++;
        continue;
      }
      await updateApiAccountVerification(db, d.acc.id, tenant, outcome.ok, outcome.ok ? "conectada" : "error", outcome.message);
      const after = await currentAfter(db, d.acc.id, tenant);
      if (outcome.ok) valid++;
      else invalid++;
      await maybeAlertFlip(db, tenant, env, d.acc, before, after, outcome.message, nowMsValue);
    } catch (e) {
      // Aislamiento por cuenta: un error inesperado en una cuenta
      // no aborta la corrida; esa cuenta queda inválida con el
      // motivo y se sigue con las demás.
      try {
        await updateApiAccountVerification(
          db,
          d.acc.id,
          tenant,
          false,
          "error",
          `La verificación falló: ${e instanceof Error ? e.message : String(e)}`,
        );
        invalid++;
      } catch {
        // Sin D1 no hay nada que hacer.
      }
    }
  }

  // Paso 3: sonda de scopes (informativa, no afecta verify_ok).
  // Nunca rompe la validación.
  try {
    await probeNativeScopes(env, tenant, { force: true });
  } catch {
    // La sonda es informativa: su fallo no invalida la corrida.
  }

  // Paso 4: resumen, SOLO en corrida completa (sin transitorios).
  // Con settings FRESCOS (read-modify-write): la sonda acaba de
  // escribir engagement_scopes y saveSettings reemplaza el JSON
  // entero, así que una instantánea anterior lo pisaría.
  if (transientCount > 0) {
    return { ran: true, reason: "partial", summary: null, partial: true, checked: withCred.length, valid, invalid, transient: transientCount };
  }
  const summary: AccountValidationSummary = { day, n_ok: valid, n_invalid: invalid, checked_at: nowMsValue };
  try {
    const settings = await getSettings(db, tenant);
    await saveSettings(db, tenant, { ...settings, [ACCOUNT_VALIDATION_DAY_KEY]: summary });
  } catch {
    // Si no se pudo guardar el resumen, el día no queda marcado y
    // se reintenta en la siguiente pasada.
    return { ran: true, reason: "partial", summary: null, partial: true, checked: withCred.length, valid, invalid, transient: 0 };
  }
  return { ran: true, reason: "done", summary, partial: false, checked: withCred.length, valid, invalid, transient: 0 };
}

/** Puerta + corrida diaria, llamada desde scheduled() DESPUÉS de
 *  runWorkerCycle. Nunca levanta: un fallo no rompe el ciclo. */
export async function maybeValidateAccountsDaily(env: Env, tenant: Tenant, nowMsValue: number): Promise<DailyValidationResult | null> {
  try {
    const r = await validateAccountsDaily(env, tenant, nowMsValue);
    if (r.ran && r.summary) {
      await logWorkerEvent(
        env.DB,
        "validacion_cuentas",
        `Validación diaria de cuentas: ${r.summary.n_ok}/${r.summary.n_ok + r.summary.n_invalid} válidas (${r.summary.day}).`,
        tenant,
      ).catch(() => undefined);
    }
    return r;
  } catch (e) {
    try {
      await logWorkerEvent(
        env.DB,
        "validacion_cuentas",
        `Validación diaria falló: ${e instanceof Error ? e.message : String(e)}`,
        tenant,
      );
    } catch {
      // Sin D1 no hay nada que hacer.
    }
    return null;
  }
}
