# Front (Pages) — qué cambia respecto al panel v17

El front actual (React, `client/src/`) se reutiliza casi tal cual; el
build se publica en Cloudflare Pages en el proyecto único `avc`
(https://avc.pages.dev). Decisión del usuario 17 nov 2026, 15:05 y
15:33: UNA sola página con login. Cambios:

> El build del front NO está en este repo: hay que exportarlo del
> artifact del panel y desplegarlo con
> `wrangler pages deploy <dist> --project-name avc` (ver
> `DEPLOY-avc.md` §5). Sin ese paso, avc.pages.dev no tiene front.

## 1. Login (nuevo, obligatorio)

- Ruta `/login`: formulario de usuario + contraseña →
  `POST /api/auth/login` `{username, password}`. El Worker devuelve
  `{ok, tenant}` + cookie de sesión.
- Sin sesión, cualquier llamada a `/api/*` devuelve 401 y el front
  redirige a `/login`. Se acabó `localStorage panel-session-user`
  con el id numérico: la sesión es la cookie httpOnly firmada del
  Worker. `listPanelUsers` queda como lista informativa (nombres),
  no como sesión.
- El front NO decide el tenant: lo dice `/api/auth/me`
  (`{tenant, sub}`). Cada usuario ve solo sus datos: el Worker
  filtra todo por el tenant de la sesión.
- El front debe enviar las llamadas con `credentials: "include"`
  (la cookie es la sesión).

## 2. Botón Iniciar (pestaña Worker, UNO por panel)

- Estado desde `GET /api/worker/state` (`running`, `startedAt`,
  `lastNote`, `activity`).
- Botón único: `POST /api/worker/start` ("Iniciar") /
  `POST /api/worker/stop` ("Parar"). Después de Iniciar, el Worker
  corre solo: el front no hace polling de trabajo, solo muestra
  actividad y avisos.
- Para probar sin esperar al cron: `POST /api/worker/run-once`.

## 3. Subir el MP4 (flujo principal solo-Cloudflare)

- En Editor/Cola: subir MP4 9:16 → `PUT /api/clips/:id/video?filename=clip.mp4`
  (`Content-Type: video/mp4`, máx. 128 MB). El Worker lo guarda en R2
  y devuelve `r2_key` + `public_url`.
- El clip sin MP4 no se puede publicar: el botón Publicar encola y
  el job queda en `draft` con el motivo si falta el archivo.
- La detección de VODs nuevos (Helix) es un paso futuro del Worker;
  hoy la entrada de material es el MP4 subido (o el clip ya existente
  migrado de `app.db`).

## 4. Avisos (campana en el panel)

- `GET /api/notifications` (últimas 50, con `read_at`).
- Campana con contador de no leídas; al abrir, `POST
  /api/notifications/:id/read`. Los avisos también quedan en
  Tracking (`GET /api/clips/:id/tracking`) con motivo y hora.
- Telegram NO aparece en el front hasta que el usuario lo configure
  en Ajustes (`telegram_bot_token` + `telegram_chat_id` en settings);
  por defecto no existe.

## 5. Cuentas (credenciales enmascaradas)

- `GET /api/accounts` ya no devuelve credenciales: solo
  `has_credential` + `credential_masked` + verificación.
- Pegar credencial: `POST /api/accounts/:id/credential`
  (`{credential, extra}`) → se cifra en el Worker.
- Verificar: `POST /api/accounts/:id/verify` (llamada real a la API).
- YouTube: campo 1 = API key; campo 2 (`extra`) = JSON
  `{"client_id","client_secret","refresh_token"}`.
- Meta: campo único = token largo de Página; `extra` = JSON
  `{"ig_user_id","page_id"}`.

## 6. Qué NO cambia

- 10 pestañas, Cola con prioridad, Tracking de 11 pasos, Analíticas,
  Ajustes (subtítulos, límites, franja). Los jobs, reintentos y
  anti-duplicado son internos: el front no los crea ni los edita
  (solo los muestra en Tracking).
- `publishClipNow` del panel v17 se sustituye por
  `POST /api/clips/:id/publish`: encola los jobs; el Worker (o el
  puente alternativo) los ejecuta. El front muestra el estado por
  plataforma desde `clips.publish_states` como hoy.
