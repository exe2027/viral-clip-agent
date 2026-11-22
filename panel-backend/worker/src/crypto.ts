// Cifrado de credenciales: AES-256-GCM con WebCrypto.
// Clave por tenant en el secreto CREDENTIAL_ENCRYPTION_KEY (32 bytes
// en base64). Formato guardado: "v1:<iv_b64url>:<cipher_b64url>".
// Nunca se guarda ni se devuelve una credencial en texto plano.

const enc = new TextEncoder();
const dec = new TextDecoder();

export function b64urlEncode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

export function b64urlDecode(s: string): Uint8Array {
  const norm = s.replaceAll("-", "+").replaceAll("_", "/");
  const padded = norm + "=".repeat((4 - (norm.length % 4)) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function b64Encode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

export function b64Decode(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function importKey(keyB64: string): Promise<CryptoKey> {
  const raw = b64Decode(keyB64.trim());
  if (raw.length !== 32) {
    throw new Error("CREDENTIAL_ENCRYPTION_KEY debe ser 32 bytes en base64");
  }
  return crypto.subtle.importKey("raw", raw.buffer as ArrayBuffer, "AES-GCM", false, [
    "encrypt",
    "decrypt",
  ]);
}

/**
 * Clave por tenant (instalación única avc, decisión 17 nov 2026):
 * HKDF-SHA256 sobre la clave maestra CREDENTIAL_ENCRYPTION_KEY con
 * info = tenant ("sebastian" | "kaez"). La D1 es compartida, pero
 * un tenant no puede descifrar las credenciales del otro: su clave
 * derivada es distinta. Sin tenant (tests / compatibilidad), se usa
 * la clave maestra tal cual.
 */
export async function deriveTenantKey(masterB64: string, tenant: string): Promise<CryptoKey> {
  const raw = b64Decode(masterB64.trim());
  if (raw.length !== 32) {
    throw new Error("CREDENTIAL_ENCRYPTION_KEY debe ser 32 bytes en base64");
  }
  const base = await crypto.subtle.importKey("raw", raw.buffer as ArrayBuffer, "HKDF", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: new TextEncoder().encode("avc-tenant-key-v1"), info: new TextEncoder().encode(tenant) },
    base,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

async function keyFor(masterB64: string, tenant?: string): Promise<CryptoKey> {
  return tenant ? deriveTenantKey(masterB64, tenant) : importKey(masterB64);
}

export async function encryptCredential(plaintext: string, keyB64: string, tenant?: string): Promise<string> {
  const key = await keyFor(keyB64, tenant);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: iv.buffer as ArrayBuffer },
    key,
    enc.encode(plaintext),
  );
  return `v1:${b64urlEncode(iv)}:${b64urlEncode(new Uint8Array(ct))}`;
}

export async function decryptCredential(blob: string, keyB64: string, tenant?: string): Promise<string> {
  const parts = blob.split(":");
  if (parts.length !== 3 || parts[0] !== "v1") {
    throw new Error("formato de credencial cifrada no reconocido");
  }
  const key = await keyFor(keyB64, tenant);
  const iv = b64urlDecode(parts[1] as string);
  const ct = b64urlDecode(parts[2] as string);
  const pt = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: iv.buffer as ArrayBuffer },
    key,
    ct.buffer as ArrayBuffer,
  );
  return dec.decode(pt);
}

/** Lo único que el front ve de una credencial: últimos 4 caracteres. */
export function maskCredential(value: string): string {
  const t = value.trim();
  if (!t) return "";
  return `****${t.slice(-4)}`;
}

/** ¿El valor guardado parece texto plano legacy? (migración lo redacta). */
export function looksLikePlaintext(blob: string): boolean {
  return blob.length > 0 && !blob.startsWith("v1:");
}
