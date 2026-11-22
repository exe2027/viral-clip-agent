-- seed-users.sql — usuarios de login de la instalación única avc.
-- Uso: rellenar los RELLENAR_HASH_* con la salida de
--   node scripts/gen-password-hash.mjs
-- (una contraseña distinta por persona, generada en local, nunca por
-- chat ni en el repo) y ejecutar:
--   wrangler d1 execute clips-avc --file scripts/seed-users.sql --remote
-- Los usernames son los que cada uno escribe en /login. El tenant es
-- lo que ve cada uno: sebastian solo ve lo de sebastian y kaez
-- solo lo de kaez (tenant_id en cada fila, migración 0012).
-- Un tenant nuevo necesita: su fila aquí (o un INSERT equivalente),
-- su ID en worker/src/auth.ts (TENANTS) y en el cerebro
-- (tenants.yaml). Su cerebro NO necesita nada en GitHub: las
-- credenciales viven en el panel (Cuentas) y el cerebro las pide
-- al Worker con un token de un solo uso por corrida.
-- Si hay que cambiar una contraseña: generar otro hash y hacer
-- UPDATE panel_users SET password_hash='<nuevo>' WHERE username='...';
-- las sesiones viejas se invalidan cambiando SESSION_SECRET.
-- NUNCA commitear este archivo con hashes reales: los hashes de
-- abajo son placeholders y el login falla hasta rellenarlos.

INSERT INTO panel_users (name, role, status, note, created_at, username, password_hash, tenant)
VALUES ('Sebastián Montes', 'dueno', 'activo', 'Propietario del canal @elclipdegta6', CAST(strftime('%s','now') AS INTEGER) * 1000, 'sebastian', 'RELLENAR_HASH_SEBASTIAN', 'sebastian');
