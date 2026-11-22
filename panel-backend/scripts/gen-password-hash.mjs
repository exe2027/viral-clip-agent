#!/usr/bin/env node
// Genera la línea de hash de contraseña (PBKDF2) sin guardar la
// contraseña en ningún archivo y sin mostrarla al escribirla.
// Uso:
//   node scripts/gen-password-hash.mjs
// Pide la contraseña dos veces (sin eco) e imprime solo el hash.
// Después: pegar el hash en scripts/seed-users.sql
// (RELLENAR_HASH_SEBASTIAN o RELLENAR_HASH_COLEGA) y sembrar D1.
// El hash ya NO va a ningún secreto PANEL_PASSWORD_HASH: el login
// es por usuario contra panel_users (instalación única avc).
import { pbkdf2Sync, randomBytes } from "node:crypto";

function readSecret(prompt) {
  return new Promise((resolve) => {
    process.stdout.write(prompt);
    const stdin = process.stdin;
    stdin.setRawMode(true);
    stdin.resume();
    let buf = "";
    const onData = (chunk) => {
      const s = chunk.toString("utf8");
      if (s === "\r" || s === "\n") {
        stdin.setRawMode(false);
        stdin.pause();
        stdin.removeListener("data", onData);
        process.stdout.write("\n");
        resolve(buf);
      } else if (s === "\u0003") {
        process.exit(1);
      } else if (s === "\u007f") {
        buf = buf.slice(0, -1);
      } else {
        buf += s;
      }
    };
    stdin.on("data", onData);
  });
}

const pw = await readSecret("Contraseña del panel (no se guarda, no se ve): ");
const pw2 = await readSecret("Repetir: ");
if (!pw || pw !== pw2) {
  console.error("No coinciden o está vacía.");
  process.exit(1);
}
const salt = randomBytes(16);
const hash = pbkdf2Sync(pw, salt, 100000, 32, "sha256");
console.log(`pbkdf2$100000$${salt.toString("base64")}$${hash.toString("base64")}`);
