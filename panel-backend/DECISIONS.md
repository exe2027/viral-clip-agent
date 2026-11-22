# Decisiones del backend (con pros/contras)

> **NOTA 2026-11-19 — cerebro genérico.** Las §§1–5 describen
> diseños ya superseded (una instalación por persona, secretos por
> tenant en GitHub, workflows por tenant). La decisión vigente es
> la §6 (UNA sola instalación, `tenant_id`) **más** el cerebro
> genérico: `cerebro.yml` + `cerebro-live.yml` sin environments ni
> secretos por tenant en GitHub; el Worker emite un token de un solo
> uso por dispatch y el cerebro lo canjea en `POST /api/brain/session`
> por una sesión que le devuelve las credenciales descifradas del
> tenant (`GET /api/bridge/credentials`). El tenant "colega" se
> eliminó el 2026-11-19 ("colega" nunca fue un usuario).

## 1. Login: contraseña por despliegue (recomendada) vs Google/Firebase Auth

> NOTA 17 nov 2026: esta sección describía el diseño anterior (un
> despliegue por persona). La decisión vigente es la §6: UNA sola
> instalación avc con login por usuario en D1. La opción A sigue
> siendo la técnica implementada (PBKDF2 + cookie HMAC), pero la
> contraseña ya no vive en un secreto por despliegue sino en
> `panel_users` (un usuario por persona).

### Opción A — Contraseña por despliegue (IMPLEMENTADA)

Un panel = una persona = una contraseña. El hash (PBKDF2-SHA256,
100.000 iteraciones, salt aleatorio) vive como secreto de Cloudflare
(`PANEL_PASSWORD_HASH`, formato `pbkdf2$iter$salt_b64$hash_b64`).
Al hacer login, el Worker emite una cookie de sesión firmada con
HMAC-SHA256 (`SESSION_SECRET`, secreto por tenant): `base64(payload)
. base64(firma)`, httpOnly, Secure, SameSite=Lax, 30 días. Sin base
de datos de sesiones: la cookie se verifica con la firma; el logout
es borrar la cookie. La rotación es cambiar el secreto (invalida
todas las sesiones).

| Pros | Contras |
|---|---|
| $0, cero dependencias externas, funciona con Pages+Worker sin más piezas | Una sola credencial por panel (no hay "invitar a otro usuario con su Google") |
| Sin terceros con acceso a los datos del panel | Si la contraseña se filtra, hay que cambiarla a mano (`wrangler secret put`) |
| Sin cuentas de Google/Firebase que configurar por tenant | Sin recuperación de contraseña: si se olvida, se regenera el hash por CLI |
| La sesión no depende de que Google/Firebase esté arriba | El usuario debe recordar una contraseña más (guardarla en su gestor) |

Para este proyecto encaja: cada panel tiene **un** dueño, el colega
es el segundo panel (no un segundo usuario del mismo panel). La tabla
`panel_users` del panel v17 se conserva como lista informativa
(nombre/rol), pero **ya no es la sesión**: la sesión real es la
cookie firmada.

### Opción B — Google OAuth (NO implementada, esqueleto documentado)

Flujo: botón "Entrar con Google" → Google Cloud OAuth client por
tenant → `id_token` verificado en el Worker contra
`https://oauth2.googleapis.com/tokeninfo` (o JWKS) → sesión igual
que en A, con `email` en el payload y allowlist de 1 email por tenant
(`ALLOWED_EMAIL`, secreto).

| Pros | Contras |
|---|---|
| Sin contraseña que recordar; 2FA de Google | Un proyecto OAuth en Google Cloud **por tenant**, con pantalla de consentimiento y cliente verificado |
| Revocación desde la cuenta Google | Más piezas: si Google cambia algo, el login cae; hay que vigilar expiración de tokens |
| Identidad real (email) en la sesión | El Worker necesita llamar a Google en cada login (y cachear JWKS) |

**Cuándo cambiar a B:** si el panel pasa a tener más de una persona
con acceso real, o si Sebastián prefiere no gestionar contraseñas.
El cambio es solo `worker/src/auth.ts`: sustituir `loginPassword`
por `loginGoogle` (contrato abajo) y añadir `GOOGLE_CLIENT_ID` /
`ALLOWED_EMAIL` a los secretos. El resto (cookie de sesión, middleware)
no cambia.

Contrato del login Google (para implementarlo después):
```
POST /api/auth/google  { "id_token": "<token de Google>" }
  → verificar aud == GOOGLE_CLIENT_ID, iss == accounts.google.com,
    exp vigente, email == ALLOWED_EMAIL
  → 200 { ok: true } + Set-Cookie de sesión (igual que /api/auth/login)
  → 403 si el email no está en la allowlist
```

### Opción C — Firebase Auth (DESCARTADA por ahora)

Pros: SDK listo, login con Google/email, gestión de usuarios en
consola. Contras: un proyecto Firebase por tenant, SDK en el front
(otro origen de verdad), reglas de Firestore/RTDB que no usamos (los
datos viven en D1, no en Firebase), y el panel actual no tiene
Firebase en el código. Aporta complejidad sin aportar nada que B no
aporte con menos piezas.

## 2. Cifrado de credenciales: AES-256-GCM (WebCrypto)

- Clave: `CREDENTIAL_ENCRYPTION_KEY`, 32 bytes aleatorios en base64,
  secreto de Cloudflare **por tenant** (claves distintas → una fuga
  no compromete al otro panel).
- Formato guardado: `v1:<iv_b64>:<ciphertext_b64>` (GCM, tag de 128
  bits incluido en el ciphertext por WebCrypto). IV aleatorio de 12
  bytes por valor.
- Se cifra `api_accounts.credential` y `api_accounts.extra`
  (YouTube/TikTok guardan dos claves unidas por `\n` en `credential`).
- La API nunca devuelve el valor: `GET /api/accounts` devuelve
  `credential_masked` (`****abcd`, últimos 4) y `has_credential`.
- Rotación: cambiar la clave exige re-cifrar (script
  `scripts/rotate-encryption-key.py`, pendiente de escribir; hoy el
  procedimiento es: exportar, re-pegar las credenciales en el panel,
  que las vuelve a cifrar con la clave nueva).
- La migración desde `app.db` **no** migra los valores en texto
  plano: los redacta. Re-pegar es el precio de no arrastrar secretos
  en claro a D1.

## 3. Por qué el Worker no sube el video entero él solo

Límites de Workers (plan §3): ~10 ms de CPU por invocación, 128 MB,
sin ffmpeg, sin Python. Una subida real es: YouTube resumable
(varias peticiones con reintentos), TikTok 5 MB por chunk con PUTs
secuenciales, Facebook rupload por chunks de 8 MB, Instagram con
espera de procesado (polling 1/min, ~5 min). Ninguna cabe en una
invocación.

## 3. Por qué el Worker no sube el video entero él solo (y cuál es la vía principal)

Límites de Workers (plan §3): ~10 ms de CPU por invocación, 128 MB,
sin ffmpeg, sin Python. Una subida real es: YouTube resumable
(varias peticiones con reintentos), TikTok 5 MB por chunk con PUTs
secuenciales, Facebook rupload por chunks de 8 MB, Instagram con
espera de procesado (polling 1/min, ~5 min). Ninguna cabe en una
invocación.

**Vía principal (Cloudflare + GitHub Actions, decisión 17 nov 2026
"Todo automático"):** el cómputo pesado vive en GitHub Actions
(cadena del cerebro: detección, corte, subtítulos, publicación;
ver `COMPUTE.md`). El Worker sigue siendo el cerebro de estado y
además implementa los pasos de API por plataforma:
1. **Worker** (`worker/src/publish/`): cada paso de cada API como
   función pequeña y reanudable, con el estado en `jobs`
   (`resume_data`: `publish_id` de TikTok, `container_id` de IG,
   `video_id` de FB, URL de sesión resumable de YT). El cron del
   Worker (cada 5 min) avanza un paso por invocación. El botón
   Iniciar dispara además el workflow del cerebro con
   `repository_dispatch` (token `GITHUB_TOKEN`, secreto del Worker).
2. El corte y los subtítulos quemados son automáticos en GitHub
   Actions (ffmpeg + faster-whisper). Solo Cloudflare + manual
   queda como respaldo degradado (ver `COMPUTE.md`).

**Mecanismo alternativo dirigido por el Worker:**
`publisher-bridge/bridge.py` corre en GitHub Actions ($0) y hace el
trabajo pesado con el código ya probado de `viral-clip-agent`,
devolviendo la evidencia al Worker. Es la vía para que el Worker
dispare subidas fuera del cron del cerebro (cada 30 min en franja
18:00–24:00 Chile), no la vía principal de cómputo. No hay VPS ni
PC prendida 24/7 en ningún caso (salvo el runner autoalojado solo
para el agente navegador TikTok).

En ambos casos `posted` exige `evidence_url`.

## 4. D1: qué cambió respecto a app.db

- Mismo esquema y mismos datos (migraciones 0001–0010 adaptadas).
- Adaptaciones D1: `__drizzle_migrations.id SERIAL` → `INTEGER
  PRIMARY KEY AUTOINCREMENT` (D1 no tiene `SERIAL`); resto del SQL
  es SQLite estándar y D1 lo acepta tal cual.
- Migración 0011 (nueva): `jobs`, `sessions` (reserva para
  invalidación por servidor; hoy la sesión es cookie firmada sin
  estado), `notifications` (avisos visibles en el panel; Telegram
  solo si se configura en Ajustes), `api_accounts.credential_enc` /
  `extra_enc` (columnas cifradas; `credential`/`extra` quedan como
  columnas legacy que la migración deja vacías y el código ya no lee).
- Índices nuevos: `jobs(clip_id, platform)`,
  `jobs(idempotency_key)` UNIQUE, `clip_events(clip_id, at)`,
  `clip_stats(clip_id, platform)`.

## 6. Una sola instalación multi-tenant (avc.pages.dev) — DECISIÓN 17 nov 2026

El usuario corrigió el diseño el 17 nov 2026 a las 15:05 y lo
reafirmó a las 15:33: **UNA sola página, https://avc.pages.dev, con
login al entrar**. Cada persona entra con su login y ve solo su
panel, sus cuentas, su cola y sus credenciales. NO un
avc-colega.pages.dev separado. AVC = Agent Viral Clip. Primero
desplegar avc.pages.dev, para probar lo demás (IG/FB por API, etc.).

Esto SUSTITUYE el §1 opción A tal como estaba ("un panel = un
despliegue = una contraseña") y el §5 ("cuenta Cloudflare por
persona"):

| Antes (una instalación por persona) | Ahora (una instalación, avc) |
|---|---|
| Pages `panel-sebastian` + `panel-colega` | Pages `avc` → https://avc.pages.dev |
| Worker `clips-worker-<tenant>`, D1 `clips-<tenant>`, R2 `clips-<tenant>` | Worker `avc`, D1 `clips-avc`, R2 `clips-avc` |
| `PANEL_PASSWORD_HASH` por despliegue | Login por usuario en D1 (`panel_users`: username + hash PBKDF2 + tenant) |
| Aislamiento por binding (un bug no podía tocar al otro) | Aislamiento por `tenant_id` en cada fila + prefijo R2 `clips/<tenant>/` + cifrado HKDF por tenant |
| Cada persona con su cuenta Cloudflare y su cuota gratis | UNA cuenta (la de Sebastián) y UNA cuota gratis para los dos |
| El admin de cada cuenta solo veía sus secretos | Quien administra Cloudflare ve los secretos de ambos (son secretos del mismo Worker) |

Cambios de código (migración 0012):
- `panel_users` gana `username`, `password_hash`, `tenant`.
- `tenant_id` en clips, api_accounts, jobs, notifications,
  clip_events, clip_stats, clip_previews, sources, influencers,
  vod_checks, worker_events, settings y worker_state (estas dos
  últimas con índice UNIQUE por tenant: una fila por tenant).
- `auth.ts`: la sesión lleva `{sub: username, tenant}`; ya no se
  compara contra `env.TENANT_NAME` (eliminado).
- `db.ts` / `jobs.ts`: toda consulta de usuario filtra por el
  tenant de la sesión; el cron corre un ciclo por tenant.
- `crypto.ts`: la clave de cifrado se deriva por tenant (HKDF-SHA256
  sobre `CREDENTIAL_ENCRYPTION_KEY`); sin tenant (tests), se usa la
  clave maestra como antes.
- `r2.ts`: keys `clips/<tenant>/<clip_id>/<archivo>`.
- `index.ts`: login `{username, password}`; el puente exige
  `X-Bridge-Tenant`; `/api/health` devuelve `app: "avc"`.
- `wrangler.toml` único en la raíz; `tenants/*` queda como LEGADO.

Riesgo que se asume a conciencia: el aislamiento ahora depende de
que TODAS las consultas filtren por tenant. Si una consulta nueva
olvida el filtro, un usuario vería datos del otro. Por eso el
convenio es: ninguna consulta de datos de usuario sin `tenant_id`,
y los tests cubren el cifrado por tenant y la sesión con tenant.
La D1 compartida no es el problema: el filtro + HKDF es lo que
separa. Lo que NO se comparte nunca: las credenciales de plataforma
(cada tenant pega las suyas en Cuentas) ni las sesiones.

## 7. Qué falta y qué necesita el usuario

| Falta | Lo aporta |
|---|---|
| Cuenta Cloudflare de Sebastián + `wrangler login` | Sebastián (una sola cuenta; ver §6) |
| `wrangler d1 create clips-avc` / `r2 bucket create clips-avc` / Pages `avc` | Se crean con los nombres de `wrangler.toml` (raíz) |
| Usuarios de login sembrados en D1 (`seed-users.sql`) | Sebastián y su colega, cada uno con su contraseña (hashes con `gen-password-hash.mjs`, nunca por chat) |
| `SESSION_SECRET`, `CREDENTIAL_ENCRYPTION_KEY`, `BRIDGE_TOKEN`, `GITHUB_TOKEN` | Secretos únicos del Worker avc, con `wrangler secret put` (nunca por chat) |
| OAuth YouTube por tenant (client + refresh token) | Sebastián (canal @elclipdegta6); colega (su canal) |
| TikTok: app auditada o agente navegador | Decisión del usuario (plan §5); sin auditoría, Direct Post solo publica en privado |
| Meta: token largo de Página, App Review | Sebastián (su app es solo suya); colega usa su propia app. `ig_user_id` y `page_id` los descubre el OAuth solo (ver §8) |
| Dominio `avc.pages.dev` + dominio público R2 para IG | `avc.pages.dev` es el link; IG exige URL HTTPS pública del video (dominio R2) |
| Acceso al repo + GitHub Environments si el puente corre en Actions | Quien despliegue |
| 1 clip de prueba por panel, verificado en vivo | Después del despliegue, con credenciales reales |

## 8. Meta por defecto: una cuenta → Página + Instagram vinculado — DECISIÓN 18 nov 2026

Cada clip se publica **en la Página de Facebook Y en la cuenta de
Instagram vinculada a esa Página, desde la misma cuenta Meta**.
No es una opción por clip: `instagram_reels` y `facebook_reels`
resuelven la misma cuenta `meta` del tenant en
`POST /api/clips/:id/publish`, y el front crea los clips con las 4
plataformas por defecto. Si una de las dos frena (gate, error),
la otra sigue su curso; el clip solo se cierra cuando todas están
en estado terminal.

Por qué así: la Página y su Instagram son el mismo canal (mismo
nombre, mismo contenido, misma audiencia). Publicar en uno solo
sería perder la mitad del alcance por defecto, y pedirlo clip a
clip es fricción sin beneficio. El que quiera una sola plataforma
puede quitarla de `platforms` al crear el clip; el default no se
toca.

Cómo se configura (sin pegado manual): el OAuth de Meta
(`GET /api/oauth/meta/callback`) toma la primera Página de
`/me/accounts` y lee su `instagram_business_account`; guarda
`page_id` e `ig_user_id` en Extra de la cuenta Meta. Re-conectar
Meta vuelve a descubrir ambos valores: es el arreglo cuando el
vínculo cambia.

Lección del 18 nov 2026 (clip 13, IlloJuan borra GTA 6): el job de
Instagram falló con `Object with ID '<ig_user_id>' does not
exist, cannot be loaded due to missing permissions` mientras la
cuenta Meta verificaba en verde. Causa raíz: la verificación solo
comprobaba `/me` con el token de Página, nunca la cuenta de
Instagram; y la Página había perdido su
`instagram_business_account` porque @elclipdegta6 estaba
suspendida (apelación enviada ese día). Desde ese día `verify.ts`
también comprueba la cuenta de Instagram
(`GET /{ig_user_id}?fields=username`): si no es accesible, la
cuenta queda No conectada con el motivo. Regla: un "Conectada"
nunca más puede ser falso para Instagram.

No confundir: el perfil personal en modo profesional también se
llama igual que el canal y NO es la Página. Los reels van a la
Página, nunca al perfil. (Los ids reales viven solo en Extra de
la cuenta Meta, cifrados en D1; nunca en este repo.)

Procedimiento verificado 2026-11-19 (token largo + vínculo, la
noche del 19 nov): el vínculo se confirma en 3 lugares (Business
Suite → Cuentas de Instagram, 1 activo conectado: la Página; la
Página → Configuración → Cuentas vinculadas → Instagram,
Conectada; Centro de cuentas). Si Graph no devuelve
`instagram_business_account` aunque en pantalla diga conectado,
el token se generó ANTES de la (re)vinculación y sus ámbitos de
Instagram no tienen ningún objeto asociado (vacío en el Depurador
de token): re-vincular Instagram a la Página y DESPUÉS generar el
token nuevo. En el Explorador de Graph: elegir la app, marcar los
8 permisos (instagram_basic, pages_show_list,
pages_read_engagement, instagram_content_publish,
pages_manage_posts, instagram_manage_comments,
instagram_manage_insights, business_management), confirmar la
Página, el negocio y "Activar todos los Cuentas de Instagram
actuales y futuros". Ampliar a larga duración en el Depurador
("Ampliar token de acceso", debe decir que no caduca nunca) y
después pedir el token de la Página con `me/accounts`; el corto
vence en 1 hora. Guardar en el panel el token + el Extra JUNTOS
en una sola operación (guardar solo el token borra el Extra con
`ig_user_id` y `page_id`). Verificación:
`me/accounts?fields=id,name,instagram_business_account` devuelve
el campo; el panel muestra "Meta aceptó el token. Instagram @...
accesible" y la tarjeta en verde. Errores: "Meta no aceptó la
clave" = token corto o vencido → sacar el LARGO; "aceptó el token
pero la cuenta de Instagram no es accesible" = falta el Extra o el
vínculo no está en Graph → revisar el Extra y re-vincular +
regenerar el token.

## 9. Marca de agua: logo por tenant, quemado en el render — DECISIÓN 18 nov 2026

El usuario pidió marca de agua en el panel: subir sus logos, elegir
el activo, y que el render lo queme semitransparente. Defaults de la
prueba del clip de referencia: centrada, ~320 px de diámetro, 20% de
opacidad.

### Dónde vive cada cosa

| Pieza | Dónde |
|---|---|
| Logos (PNG/JPG, ≤5 MB) | Bucket R2 que ya existe, keys `watermarks/<tenant>/<azar>_<nombre>` (mismo patrón con componente aleatorio que los MP4) |
| Filas de logos | D1 `watermark_logos` (migración 0025): id, tenant, r2_key, nombre, tipo, tamaño, creado |
| Configuración | El JSON de Ajustes por tenant: `watermark_enabled`, `watermark_logo_id`, `watermark_opacity` (0–100), `watermark_size_px` (50–1000), `watermark_position` (`center` \| `top-left` \| `top-right` \| `bottom-left` \| `bottom-right`) |
| UI | Pestaña Ajustes, sección "Marca de agua": interruptor, opacidad, tamaño, posición, subida múltiple con vista previa, elegir activo, eliminar |

### Contrato hacia el render (el cerebro lo lee por el puente)

`GET /api/bridge/settings?tenant=...` (con `BRIDGE_TOKEN`) devuelve,
además de los Ajustes efectivos, **`watermark_url`**: una URL firmada
HMAC (60 min, `GET /api/public-asset?key=&exp=&sig=`) del logo ACTIVO
del tenant, solo cuando la marca está activada y el logo existe en R2.
Si está activada pero no hay logo resoluble, el puente la apaga SOLO
en esa respuesta (sin tocar D1) y `watermark_url` es `null`, para que
el render no falle por un logo que no existe. Campos que el render
debe aceptar (mismos nombres): `watermark_enabled` (bool),
`watermark_url` (string|null), `watermark_opacity` (0–100, %),
`watermark_size_px` (px de diámetro), `watermark_position`.

### Por qué no va el id ni la key en el puente

El cerebro (GitHub Actions) no puede leer R2 ni D1: necesita la URL
firmada. El TTL es 60 min (no 15 como el vídeo) porque el cerebro
lee los Ajustes al arrancar la corrida y descarga el logo al
renderizar, y una corrida puede durar más de 15 min.

### Aislamiento (decisión explícita)

- Todas las consultas de logos filtran por el tenant de la sesión;
  la lista, la vista previa y el borrado de un tenant nunca tocan
  los del otro.
- El `watermark_logo_id` NUNCA se copia con la configuración base
  del onboarding (no está en `BASE_SETTINGS_KEYS`): ese id apunta a
  filas de otro tenant. Sí se copian interruptor, opacidad, tamaño
  y posición.
- Al borrar el logo activo, el Worker limpia `watermark_logo_id`
  en los Ajustes del mismo tenant.

### Qué falta (pieza del renderer, fuera de este repo)

El renderer aún no acepta la marca: falta que lea
`watermark_enabled`/`watermark_url`/`watermark_opacity`/
`watermark_size_px`/`watermark_position` del puente (añadirlos a sus
campos conocidos y aplicarlos) y un `--watermark` (o equivalente) en
el render que descargue la URL, la componga con la opacidad, el
tamaño y la posición indicados, y la queme en el vídeo. Sin esa
pieza, el panel guarda y expone la configuración, pero los clips
salen sin marca. No se crea bucket nuevo: se reutiliza el de clips.

## Validador de suministro del Worker (2026-11-18 22:49, pedido del usuario)

"En marcha" no basta: con 18 streamers activos con permiso en D1
(10 ES + 8 EN) es casi imposible que no haya un live o VOD, así que
Cola vacía + sin clips en 6 h = atascado. `worker/src/worker-validate.ts`
(`validateWorkerSupply` pura + `collectWorkerSupply` desde D1):
ok si hay Cola o llegó un clip en 6 h; stuck si Cola vacía y sin
clips en 6 h, o sin dispatch en vivo en 2 h (toca cada 15 min);
blocked si 0 streamers activos con permiso; stopped si está parado.
Vive en GET /api/worker/validate, en /api/worker/state (campo
`validation`) y en /estado de Telegram; el cron llama a
`maybeAlertWorkerStuck` y avisa por Telegram 1 vez cada 6 h mientras
siga atascado (marca en settings, se resetea al volver a ok). El
front muestra ATASCADO en vez del piloto verde cuando está stuck o
blocked. Sin migración nueva: usa worker_state, influencers, clips
y jobs, que ya existen. Desplegado Worker fc44f72e + Pages bd8f4cbd;
verificado en vivo 22:49: running=true, 18 influencers, Cola 3,
último clip 22:05, último dispatch 22:43 => ok.

## Validador endurecido + rechazos visibles (2026-11-18 23:04)

Corrección del usuario ("esos 3 clips los sacó hace rato"): umbral
de 6 h a 60 min y la Cola vieja ya no tapa el estancamiento (Cola>0
con último clip viejo = stuck). POST /api/clips registra cada
rechazo en worker_events (kind "rechazo") con el motivo exacto.
Hallazgo en la corrida 44439014558: 5 en vivo, 4 picos, 2 clips
renderizados, 0 encolados — el cerebro rescata picos 45-49 por
gritos y los renderiza, pero el panel los tira con 400 porque el
`score` que manda el cerebro es la prioridad de YouTube (45/46),
no la viralidad (48,3/49,0), y el POST exige score >= 50. Pendiente
decisión del usuario: ¿rescatados 45-49 a la Cola como bloqueados
para revisión (sin auto-publicar) o Cola >= 50 estricta?

## Rescatados a la Cola como bloqueados (2026-11-18 23:32)

Decisión del usuario 23:13: los rescatados 45-49 por gritos SÍ entran
a la Cola, bloqueados para revisión humana, sin auto-publicar.
Panel (Worker ee2dd9ed, Pages 532b83ee): POST /api/clips acepta
rescue=true solo si viralidad revisada 45-49 + 2+ s de gritos + SEO
máx. >= 90 (re-verificado en el panel); nace publish_blocked=1 con
rescue en viral JSON; publishGate nombra el freno como rescate;
Editor: chip "Rescatado" en Cola + botón "Revisado: desbloquear"
(solo con subtítulos completos). Cerebro (commits 1f366b9 + 0414de3):
_enqueue_panel manda rescue/viral_score/seo_scores solo en rescate.
La puerta 50 de publicación no se toca; sin rescate, el 400 sigue.
