# Endpoints nuevos — Backend Dev 1 (fase 2)

- **Fecha:** 2026-11-17
- **Alcance:** solo backend TypeScript del Worker. Sin tocar el front, sin tocar `publishGate`, fórmulas de viralidad/SEO ni reglas de publicación, sin tocar secretos.
- **Archivos tocados:**
  - `worker/src/index.ts` — 9 rutas nuevas + `PATCH` añadido a `Access-Control-Allow-Methods` (antes no estaba; sin él el navegador bloquearía el `PATCH /api/influencers/:id`).
  - `worker/src/db.ts` — funciones de acceso para `accounts`, `influencers`, `sources`, `vod_checks`, `panel_users` + regla de lanzamiento (`isLaunchInfluencer`).
  - `migrations/0013_accounts_tenant.sql` (nueva) + sección 0013 en `migrations/schema.sql` — `tenant_id` en `accounts`.
  - `worker/test/endpoints-nuevos.test.ts` (nuevo) — 21 tests con mock D1 en memoria.
- **Verificación:** `bun test` 38/38 (17 anteriores + 21 nuevos), `tsc --noEmit` OK. Migraciones 0001–0013 aplicadas en orden sobre SQLite local sin errores.
- **Sin desplegar:** el despliegue lo hace el coordinador. **Ojo:** la migración 0013 debe aplicarse en D1 antes (o junto) con este código: sin ella, `GET /api/social-accounts` falla (la columna `tenant_id` no existe) y responde 500, nunca con datos de otro tenant.

## Reglas comunes a todos los endpoints nuevos

- **Auth:** todos exigen sesión (`requireSession`: cookie firmada o `Authorization: Bearer`). Sin sesión: `401 {ok:false, error:"Inicia sesión primero."}`.
- **Tenant:** el tenant viene SIEMPRE de la sesión verificada, nunca del body. Cada consulta filtra por `tenant_id = <tenant de la sesión>`.
- **Excepción documentada:** `GET /api/panel-users` devuelve AMBOS usuarios del panel (la lista es de quién puede entrar, no datos de un tenant). Es la única ruta nueva sin filtro por tenant, y así lo pide el encargo.
- **Sin datos inventados:** sin filas, lista vacía (`[]`) o `null` donde aplica (`last_vod_check`). Nunca se devuelven `password_hash` ni credenciales: `GET /api/panel-users` ni siquiera selecciona `password_hash` en el SQL, y la tabla `accounts` no guarda credenciales.
- **Errores:** `{ok:false, error:"<mensaje>"}` con el código HTTP que corresponde (400 validación, 401 sin sesión, 404 no existe o es de otro tenant, 501 sin verificación real).

## 1. Cuentas sociales — tabla `accounts`

Tabla usada: **`accounts`** (migración 0002; `tenant_id` añadido en la 0013 — la 0012 no la cubrió y sin esa columna no había aislamiento por tenant). Columnas: `id, platform, label, handle, status, note, sort, tenant_id`. La tabla NO guarda credenciales.

### `GET /api/social-accounts`

- **Request:** sin body.
- **Response 200:** `{ok:true, accounts:[{id, platform, label, handle, status, note, sort}]}` ordenadas por `sort ASC, id ASC`. Sin filas: `{ok:true, accounts:[]}`.
- **Errores:** 401 sin sesión.

### `POST /api/social-accounts/:id/verify`

- **Request:** sin body.
- **Response:** **501** `{ok:false, error:"verificación no disponible: las cuentas sociales no guardan credencial en el Worker, así que no hay nada que verificar contra la plataforma. La verificación real vive en Cuentas API."}` — nunca un éxito fingido. No existe verificación real para cuentas sociales: no hay credencial que verificar (la verificación real de `verify.ts` trabaja sobre `api_accounts`).
- **Errores:** 404 si la cuenta no existe o es de otro tenant; 401 sin sesión.

## 2. Influencers — tabla `influencers`

Tabla usada: **`influencers`** (migración 0002; `tenant_id` en 0012). Columnas: `id, tenant_id, name, platform, profile_url, language, permission, active, notes, created_at`.

**Regla de lanzamiento (dura):** solo IlloJuan puede quedar activo con permiso. Si se intenta crear o activar a otro influencer (`active=true` con handle distinto de IlloJuan, sin `@`, sin distinguir mayúsculas), la respuesta es **400** con el motivo y NO se crea/activa nada. No se activa en silencio. Cambiar el handle a IlloJuan y activar en el mismo `PATCH` sí pasa (se evalúa el estado resultante). Un influencer distinto de IlloJuan sí se puede crear desactivado, y se le pueden editar notas/permiso/url mientras siga desactivado.

Serialización: la API usa `handle` (= columna `name`) y `url` (= columna `profile_url`); ambos nombres se devuelven (alias). `permission` y `active` salen como booleanos JSON.

### `GET /api/influencers`

- **Request:** sin body.
- **Response 200:** `{ok:true, influencers:[{id, handle, name, platform, url, profile_url, language, permission, active, notes, created_at}]}` ordenados por `id ASC`. Sin filas: `{ok:true, influencers:[]}`.
- **Errores:** 401 sin sesión.

### `POST /api/influencers`

- **Request (JSON):** `{platform, handle, url?, language?, permission?, active?, notes?}`. Se aceptan también los alias `name` (por `handle`) y `profile_url` (por `url`).
  - `platform` (obligatorio): `twitch` | `youtube` | `kick` (sin distinguir mayúsculas).
  - `handle` (obligatorio): texto no vacío, máx. 100 caracteres.
  - `url` (opcional): vacío o con `http://` / `https://`.
  - `language` (opcional, por defecto `es`): `es` | `en`.
  - `permission` (opcional, por defecto `false`), `active` (opcional, por defecto `true`): booleanos JSON (o 0/1).
  - `notes` (opcional): texto, máx. 2000 caracteres.
- **Response 201:** `{ok:true, influencer:{...}}` con la fila creada.
- **Errores:** 400 validación (plataforma/handle/url/idioma/booleanos/notas) o regla de lanzamiento (`Solo IlloJuan puede estar activo con permiso en el lanzamiento: "<handle>" no se puede activar. Se puede crear desactivado.`); 401 sin sesión.

### `PATCH /api/influencers/:id`

- **Request (JSON):** parcial; solo los campos presentes se actualizan: `platform, handle, url, language, permission, active, notes` (mismas validaciones que en POST; un campo presente pero inválido es 400). La regla de lanzamiento se evalúa sobre el estado resultante (handle + active tras aplicar el patch).
- **Response 200:** `{ok:true, influencer:{...}}` con la fila actualizada.
- **Errores:** 404 si no existe o es de otro tenant; 400 validación o regla de lanzamiento; 401 sin sesión.

### `DELETE /api/influencers/:id`

- **Request:** sin body.
- **Response 200:** `{ok:true}`.
- **Errores:** 404 si no existe o es de otro tenant; 401 sin sesión.
- **Nota:** borrar a IlloJuan no está bloqueado por el encargo (la regla solo gobierna la activación). Si el coordinador quiere protegerlo, es una decisión aparte.

## 3. Fuentes / VODs — tablas `sources` y `vod_checks`

Tablas usadas: **`sources`** (migración 0002; `game` en 0006; `tenant_id` en 0012) y **`vod_checks`** (migración 0002; `tenant_id` en 0012). **Solo lectura.**

### `GET /api/sources`

- **Request:** sin body.
- **Response 200:** `{ok:true, sources:[{id, url, platform, title, streamer, status, progress, status_note, influencer_id, game, created_at, updated_at}], last_vod_check:{id, ran_at, summary, findings} | null}`. Fuentes ordenadas por `updated_at DESC, id DESC`. `last_vod_check` es la última revisión registrada por el cerebro (por `ran_at DESC`); `null` si el cerebro aún no escribió ninguna — nunca se inventa una revisión.
- **Errores:** 401 sin sesión.

### `GET /api/sources/:id`

- **Request:** sin body.
- **Response 200:** `{ok:true, source:{...}}` con la fila completa.
- **Errores:** 404 si no existe o es de otro tenant; 401 sin sesión.

## 4. Usuarios del panel — tabla `panel_users`

Tabla usada: **`panel_users`** (migración 0006; `username/password_hash/tenant` en 0012). Solo lectura.

### `GET /api/panel-users`

- **Request:** sin body.
- **Response 200:** `{ok:true, users:[{username, tenant, status, created_at}]}` ordenados por `username ASC`. Devuelve ambos usuarios del panel (seba y chacal) sin filtrar por el tenant de la sesión — la lista es de quién puede entrar al panel. Solo esos 4 campos: el SQL selecciona únicamente `username, tenant, status, created_at`; `password_hash` (y `name`, `role`, `note`) nunca salen del Worker.
- **Errores:** 401 sin sesión.
- **Nota:** no hay crear/editar/desactivar usuarios desde el panel: eso sigue siendo por SQL (`scripts/seed-users.sql` + `gen-password-hash.mjs`).

## Tests

`worker/test/endpoints-nuevos.test.ts` — 21 tests, mock D1 en memoria (intérprete SQL mínimo para el subconjunto que usa el Worker: `SELECT` con `WHERE`/`ORDER BY`/`LIMIT`, `INSERT`, `UPDATE`, `DELETE` con prepared statements). La sesión se crea de verdad con `createSessionToken`. Cubren:

- Social-accounts: 401 sin sesión; lista vacía; aislamiento por tenant (cada uno ve solo sus cuentas); la respuesta no contiene `credential`/`password`; verify 501 con "verificación no disponible"; verify 404 (inexistente o de otro tenant).
- Influencers: lista vacía; crear IlloJuan activo 201; crear otro activo 400 con el motivo y sin crear fila; crear otro desactivado 201; validación 400 (plataforma, handle, URL); PATCH activar a otro 400 / editar notas y permiso 200; PATCH renombrar a IlloJuan + activar 200; PATCH/DELETE inexistente 404; DELETE borra; aislamiento por tenant (GET vacío, PATCH/DELETE 404).
- Sources: vacío (`sources:[]`, `last_vod_check:null`); fuentes del tenant + última revisión del cerebro (la más reciente por `ran_at`); detalle 200 / 404 (inexistente o de otro tenant).
- Panel-users: 401 sin sesión; ambos usuarios con exactamente los 4 campos públicos; el JSON no contiene `password_hash` ni el valor del hash.

## Qué quedó SIN endpoint real (con el motivo)

| Pendiente | Motivo |
|---|---|
| Verificación real de cuentas sociales (`POST /api/social-accounts/:id/verify` devuelve 501) | La tabla `accounts` no guarda credencial: no hay nada que verificar contra la plataforma. La verificación real existe solo para cuentas API (`POST /api/accounts/:id/verify`, `verify.ts`). Para verificar lo social haría falta guardar una credencial por cuenta social (o verificar existencia pública del canal con la API key de YouTube) — decisión de backend aparte, no inventada aquí. |
| Revisión de VODs nuevos (sin `POST /api/sources/check` ni similar) | La detección de VODs nuevos la hace GitHub Actions (cerebro, cron 09:00), no el Worker. El front seguirá mostrando la acción "Revisar VODs nuevos" como pendiente; este Worker solo lee `sources` y `vod_checks`. |
| Crear/editar/eliminar cuentas sociales (`POST/DELETE /api/social-accounts`) | Fuera del encargo (solo lectura + verify). Las filas solo existen si ya están en D1 (migración del panel anterior). |
| Crear/editar/desactivar usuarios del panel | Sigue siendo por SQL fuera del panel (`scripts/seed-users.sql`). El endpoint nuevo es solo lectura. |
| Crear cuenta API (`POST /api/accounts`) y eliminar cuenta API (`DELETE /api/accounts/:id`) | Fuera de este encargo (pertenecen al Dev de Cuentas/API). Siguen sin endpoint. |
| Editor / `preview_seen`, Analíticas (`clip_stats`) | Fuera de este encargo (otros devs de fase 2). Sin endpoint: el freno de publicación por preview seguirá activo. |

## Para el coordinador (despliegue)

1. Aplicar la migración **0013** en D1 (`wrangler d1 migrations apply clips-avc`) antes o junto con este código; sin ella `GET /api/social-accounts` responde 500.
2. Desplegar el Worker como siempre; no hay secretos nuevos ni cambios en `wrangler.toml`.
3. El front puede empezar a llamar a estos 9 endpoints; las pestañas Influencers, Fuentes y Usuarios dejan de estar en vacío pendiente (salvo la acción "Revisar VODs nuevos" y el verify social, ver tabla anterior).
