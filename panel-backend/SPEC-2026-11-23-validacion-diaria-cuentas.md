# SPEC — Validación diaria de cuentas vinculadas del panel AVC

Fecha: 2026-11-23 ~22:47, CORREGIDA 22:54 tras revisión pez dorado (veredicto:
NO APRUEBA la v1; 4 bloqueadores + 12 hallazgos, todos aplicados abajo) y
CORREGIDA 23:01 tras segunda vuelta (veredicto: APRUEBA CON CORRECCIONES;
5 parciales + 8 hallazgos nuevos N1–N8, todos aplicados abajo).
Orden del usuario: "las cuentas que estan vinculadas en avc panel de clips
deben estar validadas, y validar que esten activas al menos 1 vez al día".
Método: EGM (esta spec → revisión pez dorado → aprobación del usuario →
implementar).
Estado: IMPLEMENTADO 2026-11-24 ~00:30 (usuario aprobó implementación
2026-11-24 00:04). Commit d6c2b2c (pusheado a origin main).
- Worker avc desplegado versión fafcb72e-8733-448b-877b-a9837c19b3bd,
  health {"ok":true,"tenant":"sebastian","worker_running":true}, crons de
  franja intactos (*/5 15-18, */5 21-23,0-3 UTC).
- Worker avc-kaez desplegado versión 33ad3126-dd2c-4cc2-9d00-874b4d1e44d6,
  DEPLOY OK por wr_v2.py (health OK tenant kaez worker_running true,
  crons intactos). Schedules vivos de kaez comprobados por API ANTES del
  deploy: los dos crons de franja (creados 2026-11-22T07:37:33Z).
- Panel (Pages avc-em9.pages.dev) redesplegado con los 2 archivos
  cambiados (app.js + index.html); verificado en vivo que el app.js
  servido es el nuevo (contiene dailyValidationHeader). Pre-deploy se
  comprobó que dist vivo era byte-idéntico al pre-cambio.
- Tests: 22 nuevos en worker/test/daily-validation.test.ts, todos en
  verde; suite completa 894 pass / 13 fail PRE-EXISTENTES (verificado
  con git stash: base 872 pass / 13 fail, los mismos 13); tsc limpio.
  Un test de ruta fallaba en suite completa por un mock de Date de otro
  archivo (carrera); corregido comparando el día con el checked_at del
  mismo resumen en vez de con el reloj real.
- Verificación en vivo §4 PARCIAL: ruta POST /api/accounts/validate-daily
  viva en ambos Workers y con sesión exigida (401 "Inicia sesión primero"
  sin sesión). La llamada forzada con sesión QUEDÓ PENDIENTE: como
  subagente no hay navegador y el login guardado en el vault fue
  rechazado dos veces el 2026-11-22; la única sesión viva está en el
  Chrome del usuario. No se inventó la verificación.
- Línea base D1 (2026-11-24 00:21 Chile): last_account_validation
  ausente en ambos tenants (ninguna corrida aún); seba 12 api_accounts
  (11 OK + 1 meta ya inválida, id 10, sin flip que alertar); kaez 5
  api_accounts, todas OK. Primera corrida automática: primera pasada del
  ciclo >=15:00 Chile hoy 2026-11-24 (cron 18:00 UTC).

## 1. Estado actual (verificado en código 2026-11-23 22:47 y 22:54)

La verificación real YA existe; lo que NO existe es que corra sola cada día:
- `verifyCredential(platform, credential, extra)` — `worker/src/verify.ts:84`.
  Verificación real por cuenta (mismo criterio que POST /verify). Para
  TikTok SÍ da veredicto real vía Zernio (`verifyZernioTiktok`,
  `verify.ts:38-82`: `ok:true` si la cuenta está sana). El `sin_veredicto`
  de la sonda es solo sobre scopes de engagement (`native-engagement.ts:
  178-187`) y NUNCA toca `verify_ok`.
- `VerifyOutcome` es solo `{ ok, message }`: NO distingue fallos
  transitorios de red ("No pudimos hablar con la plataforma…", "Zernio no
  respondió…", "Gemini no respondió…") de fallos de autenticación — los
  traga todos como `ok:false` (`verify.ts`).
- `probeNativeScopes(env, tenant, { force })` —
  `worker/src/publish/native-engagement.ts:163`. Sonda de scopes (YT =
  force-ssl, IG = scope de respuesta, Meta = debug_token, TikTok declara
  `sin_veredicto` explícito), caché 1 h (`PROBE_CACHE_MS = 3600_000`,
  `zernio-engagement.ts:72`). Rutas: GET /api/engagement/status (usa caché)
  y GET /api/engagement/scopes (force) (`index.ts:3651/3657`). El botón
  "Comprobar permisos" del panel es manual. La sonda es INFORMATIVA (vive
  en la sección Engagement) y no afecta `verify_ok`.
- `verifyApiAccount` (`index.ts:386`) es la verificación existente con
  aviso: en cada fallo llama `notify({ kind: "credential", ... })`
  (`index.ts:404-410`); `notify` (`jobs.ts:288`) solo deduplica mensajes
  idénticos dentro de 6 h (`EVENT_DEDUPE_WINDOW_MS = 6 * 3600 * 1000`,
  `db.ts:3661`). `notify` hace lookup del vínculo (`getTelegramLinkByTenant`,
  `db.ts:2133`), chequeo `botConfigured` (`telegram.ts:147`), dedupe de 6 h,
  fila en `notifications` y evento en el panel. El remitente establecido en
  todo el código es `notify()`; `sendTelegram` vive en `telegram.ts:558`.
  Una cuenta persistentemente inválida volvería a alertar cada día.
  Además `verifyApiAccount` NO captura errores de `decryptCredential` de
  la credencial principal (`index.ts:392`, sin try/catch): una clave de
  descifrado incorrecta lanza y aborta toda la corrida (la ruta del extra,
  `index.ts:395-396`, sí está protegida).
- `updateApiAccountVerification` (`db.ts:2209`) escribe `verify_ok`,
  `verified_at`, `verify_message`, `status` en `api_accounts`. Regla A2
  (`db.ts:2219-2236`): si `verify_ok`/`verify_message`/`status` no
  cambiaron, NO escribe nada — `verified_at` NO se refresca al re-verificar
  sin cambio (diseño confirmado por el comentario de
  `isAccountVerificationCurrent`).
- `isAccountVerificationCurrent` (`db.ts:~2255`): `verify_ok === 1` Y
  `verified_at` no null Y (`credential_changed_at` null O `verified_at >=
  credential_changed_at`). Una credencial cambiada invalida la verificación
  anterior (migración 0043).
- POST /api/accounts/:id/verify (`index.ts:3473`), POST
  /api/social-accounts/:id/verify (`index.ts:3507`) — este último responde
  501: "no hay verificación real posible… La verificación real vive en
  Cuentas API" (`index.ts:3489-3515`), porque la tabla `accounts` (las 6
  filas sociales de GET /api/social-accounts) no guarda credenciales.
  POST /api/onboarding/verify-all (`index.ts:2615`).
- GET /api/accounts (`index.ts:3370-3380`) YA devuelve por cuenta API
  `verify_ok`, `verify_message`, `verified_at` y `verification_current`.
  Las filas `api_accounts` con credencial: youtube×2, tiktok×2, meta×1
  (UNA credencial meta cubre IG ES y FB ES), twitch, gemini.
- Settings ES la tabla `settings` en D1 (JSON por tenant,
  `getSettings`/`saveSettings`, `db.ts:1987/1997`).
- Crons (avc, `wrangler.toml:33`): `*/5 15-18 * * *` y
  `*/5 21-23,0-3 * * *` — son UTC (Cloudflare no admite zona horaria en
  crons), equivalentes a 12:00-15:55 / 18:00-20:55 / 21:00-00:55 Chile en
  horario de verano (-03). La primera pasada ≥15:00 Chile existe (18:00
  UTC en verano; en invierno (-04) la primera es 21:00 UTC = 17:00 Chile).
  El mecanismo `>=` sigue robusto en ambos casos. La hora Chile se calcula
  con los helpers existentes (`santiagoDayKeyMs`, `db.ts:1011`;
  `santiagoParts`, `telegram.ts:159`), NUNCA con un offset fijo -03:00
  (`db.ts:1114` advierte que Chile cambia de horario).
- El patrón de puerta diaria YA existe: `isDailyDispatchDue`
  (`cycle-gate.ts:171`, 09:00 Santiago + day-key en settings). La cabecera
  de `cycle-gate.ts` dice: "Single-source: TODOS los intervalos del ciclo y
  los dispatches viven aquí". `scheduled()` (`index.ts:4245`) corre aunque
  el Worker esté parado (el cron dispara igual; `maybeAlertWorkerStopped`
  corre siempre).
- Telegram ya existe en el Worker (`telegram.ts`).
- Verificado en vivo hoy 22:46: panel seba con 6 cuentas sociales
  conectadas (YT ES @elclipdegta6, YT EN @gta6clips, TT ES
  @elclipdegta6____bits, TT EN @elclipdegta6en___, IG ES @elmegaclipgta6, FB
  ES Elclipdegta6). Kaez: 5 cuentas con verify_ok=1 (verificado 2026-11-23
  15:52). Los schedules vivos de kaez (puestos por API, sin línea de crons
  en `wrangler.kaez.toml`) NO están verificados en esta spec: hay que
  comprobarlos por API antes de asumir que la puerta de las 15:00 Chile
  dispara allí.

## 2. Decisión

**Validación diaria automática SIN cron nuevo: puerta "una vez al día"
dentro del ciclo existente, estilo `isDailyDispatchDue`.** Predicado exacto:
en `scheduled()`, tras `runWorkerCycle`, si el Worker está en marcha (vía
`isWorkerRunning(db, tenant)`, `jobs.ts:1383` — `r.ran` NO vale: es false
también cuando el worker corre pero el ciclo sale temprano por nada que
hacer, `jobs.ts:1489-1502`) Y la hora Chile es ≥15:00 (calculada con
`santiagoParts`, nunca offset fijo) Y `last_account_validation.day` ≠ hoy
Chile → ejecutar la validación. Si el Worker está apagado, no se valida y
el panel lo dice. El `>= 15:00` (en vez de una hora exacta) es robusto ante
una ventana perdida. Razón de no usar cron nuevo: añadirlo obliga a cirugía
de schedules en dos tenants y no sobrevive a un deploy nocron (que pone
`schedules: []`); la puerta vive en código + D1, no en schedules.

## 3. Cambios

### 3.1 Puerta diaria (Worker, `cycle-gate.ts` + `scheduled()`)
- Nueva puerta estilo `isDailyDispatchDue`: `isAccountValidationDue`
  (day-key en settings) en `cycle-gate.ts` (single source), y
  `maybeValidateAccountsDaily()` invocado en `scheduled()` (`index.ts:
  4245`) DESPUÉS de `runWorkerCycle`, en la cadena `waitUntil`, con su
  propio catch. NUNCA antes del ciclo publicador: la validación no bloquea
  el corte (si tarda, el ciclo sigue; son ~6 cuentas).
- Nueva clave en settings (tabla `settings`, vía `getSettings` /
  `saveSettings`): `last_account_validation` con forma EXACTA de objeto:
  `{ day: "YYYY-MM-DD" (Chile), n_ok, n_invalid, checked_at: ms }`. La
  comparación de la puerta es `settings.last_account_validation?.day !==
  day`. Se escribe con settings FRESCOS (read-modify-write) tras completar
  la validación, no con una instantánea anterior a la sonda force
  (`saveSettings` REEMPLAZA el JSON entero, así que una instantánea vieja
  pisaría `engagement_scopes`).
- En cada `scheduled()`, la puerta se comprueba por READ (no escribe para
  comprobar, disciplina D1). Si `getSettings` falla al comprobar la puerta,
  fail-open (patrón de `cycle-gate.ts`: `isDailyDispatchDue` devuelve true
  si no puede leer) — se ejecuta la validación.
- Si la validación es PARCIAL (alguna cuenta con credencial da transitorio,
  ver §3.2), NO se escribe el resumen y NO se marca el día como hecho: la
  cabecera sigue mostrando la última validación COMPLETA y se reintenta en
  la siguiente pasada. El día solo se marca cuando TODAS las cuentas con
  credencial tienen veredicto no-transitorio. Si todas dan transient, no
  se escribe nada.
- Si el Worker está apagado todo el día, la validación no corre: el panel
  muestra "sin validar hoy" (no se inventa un OK ni se dispara alerta por
  no haber corrido).

### 3.2 Qué se valida (sin reutilizar la rama de aviso)
- TODAS las cuentas de `api_accounts` del tenant que tengan credencial
  guardada (`credential_enc` o `credential` presente): YouTube ES/EN,
  TikTok ES/EN (vía Zernio), Meta (IG ES + FB ES, una sola credencial),
  Twitch, Gemini. Cuentas sin credencial no se validan: siguen en
  "pendiente", no en "inválida". Denominador del "n/n válidas": solo
  cuentas API con credencial.
- Por cuenta: llamar `verifyCredential` + `updateApiAccountVerification`
  DIRECTAMENTE. NO reutilizar `verifyApiAccount` (su rama de aviso
  alertaría cada día una cuenta persistentemente inválida, hallazgo 1).
- Fallos transitorios vs reales (hallazgo 2): se añade el flag `transient`
  a `VerifyOutcome`. Clasificación POR SITIO, no por cadena de mensaje
  (clasificar por cadena es frágil: un 500 de Google en tokeninfo se
  escribiría como inválido): excepción de red / timeout / HTTP 5xx →
  `transient: true`; HTTP 4xx → fallo de autenticación (`transient:
  false`). Por sitio: YouTube tokeninfo no-200 (5xx → transient, 4xx →
  auth); Zernio (red/timeout/5xx → transient; el catch interno "No
  pudimos hablar con Zernio" → transient); Twitch validate no-200 (5xx →
  transient, 4xx → auth); Meta debug_token (5xx → transient); Gemini
  (red/timeout/5xx → transient). Las cadenas de mensaje quedan solo para
  mostrar. Un resultado transitorio NO se escribe (`verify_ok` queda como
  estaba), NO marca el día como hecho y se reintenta en la siguiente
  pasada. Un fallo de autenticación (token inválido, revocado, permiso
  quitado) es `transient: false` y SÍ se escribe como inválido.
- Aislamiento de errores por cuenta: try/catch por cuenta (un error de
  `decryptCredential` u otro deja ESA cuenta como inválida con el motivo y
  se sigue con las demás) + catch de nivel superior que nunca rompe
  `scheduled()`. Excepción — fallo SISTÉMICO de descifrado: si el
  descifrado falla en TODAS las cuentas con credencial con el mismo error
  (p. ej. `CREDENTIAL_ENCRYPTION_KEY` incorrecta en el Worker), NO se
  escribe inválido en ninguna, NO se alerta, y se deja constancia en
  `worker_events` (una sola causa raíz falsa no puede generar N flips ni
  marcar todas las cuentas inválidas). Si solo UNA cuenta falla al
  descifrar, esa cuenta queda inválida con el motivo (clave por cuenta).
- Para las sociales, además `probeNativeScopes` con `force: true` (un force
  al día; la caché de 1 h existente absorbe cualquier otra llamada del
  día). La sonda es INFORMATIVA (sección Engagement) y NO afecta
  `verify_ok`. TikTok PUEDE ser OK vía Zernio (`verifyZernioTiktok`); el
  `sin_veredicto` de la sonda no impide el OK de credencial.

### 3.3 Dónde se ve (panel, sin sección nueva)
- El display va sobre la lista de Cuentas API (GET /api/accounts, que YA
  devuelve `verify_ok`, `verify_message`, `verified_at` y
  `verification_current`): junto a cada cuenta API, "válida" o "inválida:
  <motivo exacto>". El campo que manda "válida" es
  `verification_current`, NO `verify_ok` (un OK viejo con credencial
  cambiada es el falso positivo V2). NO se muestra "válida hace X h" por
  cuenta (la regla A2 no refresca `verified_at` al re-verificar sin
  cambio); el "hace X h" vive SOLO en la cabecera, calculado desde el
  `checked_at` del resumen de la puerta (un timestamp por corrida).
- Mapeo cuenta API → filas sociales (GET /api/social-accounts): cada fila
  social muestra la validez de su cuenta API subyacente (YouTube ES →
  fila youtube ES; TikTok ES → fila tiktok ES; Instagram ES y Facebook ES
  → la ÚNICA fila meta). El join ocurre EN EL SERVIDOR: GET
  /api/social-accounts enriquece cada fila con los campos de verificación
  de su cuenta API subyacente (el front no hace el join; hoy esa ruta no
  trae campos de verificación). Si la fila meta es inválida, IG ES y FB ES
  se muestran inválidas con el mismo motivo. (El mapeo asume una única
  fila meta por tenant; si existiera una segunda, haría falta el idioma
  para desambiguar — no contemplado.)
- Cabecera de Cuentas: "última validación diaria: <fecha/hora Chile> —
  n/n válidas" (desde `checked_at` del resumen) o "sin validar hoy". El
  resumen llega al panel vía GET /api/settings (endpoint que lo sirve).
- Nada que configure el usuario: la validación corre sola; el panel solo
  muestra. No se crea sección ni pestaña nueva (regla "no más
  configuraciones").

### 3.4 Alertas (solo flips)
- Telegram SOLO cuando una cuenta pasa de válida a inválida: se compara
  `isAccountVerificationCurrent` ANTES vs DESPUÉS por cuenta y se avisa
  solo en flip 1→0 (plataforma, idioma, motivo). Una cuenta ya inválida
  (antes 0, después 0) NO re-alerta al día siguiente. La alerta usa
  `notify()` (`jobs.ts:288`, el remitente establecido: lookup del vínculo,
  chequeo `botConfigured`, dedupe de 6 h, fila en `notifications` y evento
  en el panel) con `kind: "credential"` y texto EXACTO:
  `Cuenta inválida: <plataforma> <idioma> (<label>) — <motivo> —
  <fecha/hora Chile>`. La fecha/hora en el texto hace que cada flip sea
  único, así que el dedupe de 6 h nunca suprime un flip genuino (ni uno
  que coincida con una verificación manual vía `verifyApiAccount`). La
  comparación antes/después se lee ANTES de validar cada cuenta (el orden
  es: leer vigencia actual → validar → comparar → alertar si flip).
- Sin aviso si la validación no corrió porque el Worker estaba apagado.
- Sin aviso si una cuenta sin credencial sigue pendiente.
- Sin aviso por fallos transitorios (no se escriben).

### 3.5 Límites y multi-tenant
- Disciplina D1: ≤1 verificación por cuenta por día + 1 probe force al
  día + ~2 escrituras de settings. La puerta se comprueba por READ.
  Dentro del presupuesto (≤3.500 escrituras/día por tenant).
- Mismo código en avc y avc-kaez (cada Worker valida SUS cuentas). Los
  schedules vivos de kaez se comprueban por API ANTES del deploy (si sus
  ventanas difieren, la hora real de validación difiere y se anota).
  avc-sebastian SIN tocar (parado).
- No se tocan los crons de slots. No se re-verifica en cada ciclo.
- Forzado en vivo: nueva ruta POST /api/accounts/validate-daily que
  ejecuta el mismo camino diario (puerta ignorada, mismos flips y
  transitorios) y devuelve el resumen. Exige sesión de panel
  (`requireSession`, patrón por ruta en `index.ts:257`). Una corrida
  forzada SÍ marca el día (la automática posterior salta) y corre aunque
  el Worker esté parado (es una acción manual, no va por la puerta del
  ciclo). (NO reutilizar POST /api/onboarding/verify-all para forzar:
  itera con `verifyApiAccount` y su rama de aviso alertaría de más.)

## 4. Verificación (antes de darlo por hecho)
- Tests nuevos (worker): la puerta no revalida dos veces el mismo día
  (Chile, con `santiagoDayKeyMs`); valida tras cambio de credencial
  (`credential_changed_at` > `verified_at` invalida el OK anterior); flip
  válida→inválida dispara EXACTAMENTE 1 alerta; ya-inválida no re-alerta
  al día siguiente; fallo transitorio (red) no escribe inválido, no marca
  el día como hecho y reintenta en la siguiente pasada; corrida parcial
  (una cuenta transient) no escribe resumen ni marca el día; fallo
  sistémico de descifrado (todas las cuentas) no escribe inválido, no
  alerta y deja constancia en `worker_events`; una cuenta con error de
  descifrado no aborta la corrida (las demás se validan);
  `scheduled()` con Worker parado no valida ("sin validar hoy", sin
  alertas); fallo de lectura de `getSettings` en la puerta → fail-open
  (valida); la cabecera "hace X h" sale del `checked_at` del resumen, no
  de `verified_at`; el display usa `verification_current` (OK viejo con
  credencial cambiada se muestra inválido); TikTok OK vía Zernio aunque la
  sonda diga `sin_veredicto`; la ruta forzada exige sesión, marca el día
  y corre con el Worker parado.
- Suite del Worker en verde; `tsc` limpio.
- Deploy avc + avc-kaez con health + schedules (wr_v2.py; verificar que los
  crons de slots siguen intactos tras el deploy). Schedules de kaez
  comprobados por API antes del deploy.
- En vivo: forzar con POST /api/accounts/validate-daily y leer Cuentas en
  el panel + `verify_ok` / `verified_at` / `verify_message` en D1 de ambos
  tenants. (2026-11-24 ~00:30: ruta viva y con sesión exigida en ambos
  Workers — 401 sin sesión —; la llamada forzada con sesión PENDIENTE por
  falta de sesión de panel en el subagente, ver Estado. Línea base D1
  leída: seba 12 cuentas / kaez 5, `last_account_validation` ausente en
  ambos. Primera corrida automática hoy 15:00 Chile.)

## 5. Riesgos y no-gos
- No inventar un OK: lo no verificable queda `sin_veredicto` (sonda) o
  "sin validar hoy" (puerta sin correr); lo transitorio no se escribe.
- No alertar por lo que ya se sabía (solo flips 1→0).
- No crear sección ni pestaña nueva en el panel.
- No tocar crons ni el ciclo de corte (la validación va DESPUÉS de
  `runWorkerCycle` en `waitUntil`, con su propio catch).
- No tocar kaez salvo desplegarle el mismo código (tras comprobar sus
  schedules por API). No tocar avc-sebastian.

## 6. Qué cambia para el usuario
- Nada que configurar: cada día a partir de las 15:00 (en horario de
  verano; en invierno a partir de las 17:00) la primera pasada del ciclo
  valida todas sus cuentas vinculadas y el panel Cuentas muestra cuáles
  están activas y cuáles no, con el motivo y la hora de la última
  validación.
- Si una cuenta se cae (token revocado, permiso quitado, cuenta
  desconectada), le llega UN aviso por Telegram en vez de enterarse cuando
  un clip no se publica.

## 7. Historial de revisión (EGM)
- Primera vuelta (22:54): NO APRUEBA — 4 bloqueadores + 12 hallazgos.
  Esta versión aplica los 16: (1) flips sin reutilizar la rama de aviso de
  `verifyApiAccount` (verifyCredential + updateApiAccountVerification
  directos, comparación antes/después); (2) flag `transient` en
  `VerifyOutcome` (red no escribe, no marca el día); (3) display sobre
  GET /api/accounts + mapeo API→social (meta alimenta IG y FB); (4)
  "hace X h" solo en cabecera desde `checked_at` del resumen; (5)
  identificadores corregidos (`updateApiAccountVerification`,
  `isAccountVerificationCurrent` con fórmula completa); (6) crons UTC +
  helpers `santiagoDay`/`santiagoParts`, nunca offset fijo; (7) TikTok OK
  vía Zernio, sonda solo informativa; (8) puerta en `cycle-gate.ts` +
  `scheduled()` estilo `isDailyDispatchDue`; (9) predicado exacto con
  Worker en marcha; (10) try/catch por cuenta + catch superior; (11)
  validación DESPUÉS de `runWorkerCycle` en `waitUntil`; (12) forzado vía
  POST /api/accounts/validate-daily; (13) schedules de kaez por API antes
  del deploy; (14) meta una sola credencial, denominador definido; (15)
  sonda informativa; (16) settings es la tabla `settings`, resumen servido
  por GET /api/settings, escritura con settings frescos.
- Segunda vuelta (23:01): APRUEBA CON CORRECCIONES — 11/16 resueltos, 5
  parciales (2, 3, 6, 9, 12) + 8 nuevos N1–N8. Esta versión aplica los 8:
  N1 ruta de alerta: `notify()` con texto exacto (fecha/hora incluida, el
  dedupe de 6 h no suprime un flip genuino; deja fila en `notifications` y
  evento en el panel; la comparación se lee ANTES de validar); N2 forma
  exacta de `last_account_validation` = objeto `{day, n_ok, n_invalid,
  checked_at}` y comparación `.day !== day`; N3 corrida parcial no escribe
  resumen ni marca el día (el día solo se marca con veredicto
  no-transitorio en todas las cuentas con credencial); N4 fallo sistémico
  de descifrado (todas las cuentas) no escribe ni alerta, constancia en
  `worker_events`; N5 ruta forzada exige sesión, marca el día y corre con
  el Worker parado; N6 transitorios por sitio (red/timeout/5xx →
  transient; 4xx → auth), incluidos tokeninfo, catch de Zernio y validate
  de Twitch; N7 puerta fail-open si `getSettings` falla; N8 citas
  corregidas (`decryptCredential` sin try/catch es `index.ts:392`,
  `santiagoDay` → `santiagoDayKeyMs`, predicado con
  `isWorkerRunning(db, tenant)` en vez de `r.ran`, "válida" manda
  `verification_current`, join en el servidor en GET
  /api/social-accounts, frases de hora matizadas con DST). Con esto la
  spec aprueba sin reservas según la segunda vuelta.
