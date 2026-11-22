# Telegram multi-tenant: vinculación por código (v2, 2026-11-18)

El bot es COMPARTIDO por todos los tenants, pero cada chat de
Telegram recibe SOLO las analíticas de SU cuenta. La v1 (un solo
`TELEGRAM_CHAT_ID` global, solo sebastian) queda descartada: si
otro usuario entraba al mismo bot, podía recibir datos ajenos.

## Regla de seguridad

El bot NUNCA pide la contraseña del panel dentro de Telegram. Un
chat de Telegram no es lugar para credenciales y un bot falso
podría robarlas. La vinculación se hace al revés: el código se
genera DENTRO del panel, con sesión ya iniciada, y se confirma en
el bot con `/link <código>`.

## Flujo que ve cada usuario

1. Entra a https://avc-em9.pages.dev e inicia sesión.
2. Va a **Ajustes → Telegram** y pulsa **«Generar código para
   vincular»**. El panel muestra un código de 6 dígitos (vale 10
   minutos, un solo uso).
3. En Telegram abre el bot y escribe `/link 123456` con su código
   (también vale pulsar el enlace `/start 123456` si el panel lo
   ofrece). El bot responde «Vinculado: a partir de ahora
   recibirás aquí el resumen diario de analíticas de tu cuenta
   (tu tenant), cada día a las 09:00 (Santiago)».
4. Cada día a las 09:00 (Santiago) el Worker le envía SOLO sus
   analíticas a SU chat. Otro usuario con el mismo bot recibe las
   suyas, nunca las de otro.
5. Para dejar de recibir: en el bot `/unlink`, o en el panel
   Ajustes → Telegram → «Desvincular». El botón «Enviar prueba»
   manda el resumen (o un mensaje de prueba) a su chat vinculado.

Un chat NO vinculado que escriba al bot (cualquier mensaje, o
`/start` sin código) solo recibe las instrucciones de vinculación.
Jamás recibe analíticas ni datos de nadie.

## Piezas

| Pieza | Dónde |
|---|---|
| Código de un solo uso (6 dígitos, 10 min) | `POST /api/telegram/link-code` (sesión). En D1 queda solo su hash SHA-256 (`telegram_link_codes.code_hash`), nunca en claro; se borra al usarse. Un segundo código invalida el primero. |
| Vínculo chat → tenant | `telegram_links` (migración `0023_telegram_links.sql`): `chat_id` PK, `tenant_id`, `linked_at`, `last_sent_day` (deduplica el resumen diario por tenant+día). Un chat pertenece a un solo tenant y un tenant a un solo chat; vincular mueve el chat. |
| Webhook | `POST /api/telegram/webhook` (sin sesión: Telegram no la tiene). Exige el header `X-Telegram-Bot-Api-Secret-Token` igual a `TELEGRAM_WEBHOOK_SECRET`; sin secreto configurado o con uno distinto, 403. Maneja `/link <código>`, `/start <código>`, `/unlink` y cualquier otro mensaje (instrucciones). |
| Estado y prueba | `GET /api/telegram/status` (sesión): `{configured, linked, chat_id_masked, last_sent_day}` — el chat solo enmascarado (últimos 4 dígitos). `POST /api/telegram/test` (sesión): envía el resumen real a SU chat vinculado. `POST /api/telegram/unlink` (sesión). |
| Envío diario | `scheduled()` del Worker, 09:00 America/Santiago, por cada tenant con vínculo y Worker en marcha. Usa `computeAnalytics()` por tenant. Si falla, queda en `worker_events` y reintenta dentro de las 09:00–09:59 sin parar la cola ni mezclar tenants. |
| Front | Ajustes → Telegram (`frontend/dist/index.html` + `app.js`: `loadTelegramLink`, `wireTelegramButtons`). Ya no hay campos de token/chat en Ajustes. |

## Secretos (wrangler secret put, nunca en el repo)

- `TELEGRAM_BOT_TOKEN`: el token del bot (compartido, igual para
  todos los tenants). Ya existía en v1.
- `TELEGRAM_WEBHOOK_SECRET`: secreto NUEVO de v2. Generarlo
  aleatorio (p. ej. 32 caracteres) y guardarlo con
  `wrangler secret put TELEGRAM_WEBHOOK_SECRET`.
- `TELEGRAM_CHAT_ID` (v1) ya NO se usa: puede borrarse con
  `wrangler secret delete TELEGRAM_CHAT_ID` una vez desplegada v2.

## Puesta en marcha (tras el código)

```bash
cd panel-backend
./worker/node_modules/.bin/wrangler login   # o CLOUDFLARE_API_TOKEN
(cd worker && npm run d1:migrate)           # aplica 0023_telegram_links
wrangler secret put TELEGRAM_BOT_TOKEN
wrangler secret put TELEGRAM_WEBHOOK_SECRET
./scripts/deploy.sh                          # Worker
./worker/node_modules/.bin/wrangler pages deploy frontend/dist --project-name avc  # front
```

Configurar el webhook de Telegram (una vez, con el token del bot
y el secreto elegidos; sin poner valores reales aquí):

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://avc.<cuenta>.workers.dev/api/telegram/webhook&secret_token=<SECRETO>
```

Verificar: `GET /api/telegram/status` con sesión → `configured:
true, linked: false` hasta vincular. Después, cada usuario genera
su código y lo confirma con `/link <código>`.

## OJO: migración de v1 a v2

El chat que ya recibía en v1 NO queda vinculado automáticamente:
tras desplegar v2 hay que volver a vincularlo (Ajustes →
Telegram → generar código → `/link <código>` en el bot). Hasta
entonces, ese tenant no recibe el resumen diario.

## Tests

`worker/test/telegram-link.test.ts` (18 tests): código válido
vincula, expirado/incorrecto no vincula, código de un solo uso,
chat no vinculado no recibe datos, `/unlink` desvincula, webhook
sin secreto da 403, envío diario por tenant no mezcla datos y
deduplica por día. Suite completa: `bun test test/` en `worker/`.

## v3 (2026-11-18, código listo, SIN desplegar): comandos + avisos

El bot ya no es solo el resumen de las 09:00. Comandos para el
chat vinculado (el tenant sale del vínculo por chat_id, nunca del
texto; un chat no vinculado recibe solo instrucciones):

| Comando | Qué hace |
|---|---|
| `/resumen` | Las analíticas de ahora mismo (mismo texto que el diario) |
| `/cola` | Cuántos vídeos hay en cola y los 5 primeros (score, viralidad, SEO, LIVE/VOD, ⚠️ falta preview / sin subtítulos, 📌 orden manual) |
| `/top [n]` | A cuáles les ha ido mejor (solo medidos; sin medición lo dice) |
| `/estado` | Worker en marcha o parado, fallidos pendientes, último evento |
| `/ayuda` | Lista de comandos |
| `/primero <id>`, `/subir <id>`, `/bajar <id>` | Ordenar la Cola (idea 49, migración 0024 `clips.manual_rank`, NULL = automático; nunca toca `score` ni la fórmula del cerebro; un VOD manual nunca pasa por encima de un LIVE) |
| `/auto <id>` | Ese clip vuelve al orden automático |

Avisos automáticos (re-cableado de `notify()` en `jobs.ts`, H1
resuelto: ya no lee `settings.telegram_bot_token/chat_id`, sino
`telegram_links` + `TELEGRAM_BOT_TOKEN` del entorno; una fila en
`notifications` = un envío, nunca se reenvía):

- **Job fallido final (B2):** al marcarse `failed` definitivo.
  Si `last_error` habla de límite de frecuencia / rate limit /
  429, el aviso añade «No reintentes hasta que pase el
  enfriamiento».
- **Job atascado (B3):** `processing` más de 30 min; al
  detectarlo vuelve a `scheduled`, así que no se re-avisa cada
  minuto.
- **Cuenta desconectada / credencial fallida (B7):** cuando
  `verifyApiAccount` deja de verificar una cuenta (al guardar,
  al verificar o en `verify-all`); dice plataforma y etiqueta,
  nunca el token.
- **Hito viral (B6, versión honesta):** al pegar una medición
  manual (`POST /api/clips/:id/stats`) que supere 1.000 y luego
  10.000 vistas en esa plataforma, el bot felicita con la
  medición real pegada. Dedup por (clip, plataforma, umbral) en
  `notifications` kind `milestone`: no se repite.
- `info` (borradores) nunca se envía por Telegram; sin env, sin
  token o sin vínculo, el aviso vive solo en el panel.

El front (Ajustes → Telegram) menciona los comandos nuevos. No
hace falta ningún secreto nuevo ni re-vincular: el vínculo v2
sigue valiendo.

### Verificación en vivo (tras desplegar)

1. Desplegar: aplicar la migración 0024 en remoto
   (`wrangler d1 migrations apply clips-avc`), desplegar Worker
   y Pages. Sin secretos nuevos.
2. En Telegram, con el chat ya vinculado: `/ayuda` → lista de
   comandos. `/cola` → la Cola real con scores. `/resumen` →
   mismo texto que el diario de las 09:00. `/top` → ranking
   medido o «Sin medición todavía». `/estado` → Worker en marcha
   y fallidos pendientes.
3. Ordenar: `/primero <id>` → «ahora es el primero de VOD/LIVE
   (orden manual)» y `/cola` lo muestra con 📌. `/auto <id>` →
   vuelve al orden automático.
4. Fallo: provocar un fallo final (o esperar uno real) → llega
   un solo mensaje «Falló la subida…» a SU chat. Si es de
   Facebook con límite, añade «No reintentes…».
5. Cuenta: en Cuentas, verificar una cuenta con credencial mala
   → llega «La cuenta … dejó de verificar».
6. Hito: pegar una medición ≥1.000 vistas en un clip →
   «¡Hito viral! … llegó a 1.500 vistas en YouTube.»; pegar otra
   mayor sin cruzar 10.000 no repite; al cruzar 10.000 llega el
   segundo hito.
7. Chat no vinculado que escriba `/resumen`: solo instrucciones,
   nunca datos.

## v3.1 (2026-11-18, código listo, SIN desplegar): control del Worker + alerta de desactivación + preview opcional

### Control del Worker desde Telegram

| Comando | Qué hace |
|---|---|
| `/iniciar` | Enciende el Worker (mismo camino que el botón Iniciar del panel: `setWorkerRunning(true)` + dispatch `panel-start-<tenant>` al cerebro en GitHub). Si ya estaba en marcha, no hace nada. No pide confirmación. |
| `/parar` | Para el Worker, pero NUNCA en un solo mensaje: el bot pregunta «¿Seguro? Esto detiene TODA la publicación… Responde SÍ para confirmar (vale 5 minutos)». Solo un SÍ posterior (o `/parar sí`) en el mismo chat ejecuta la parada (mismo camino que el botón Parar del panel: `setWorkerRunning(false)` + dispatch `panel-stop-<tenant>`). Cualquier otro texto cancela la confirmación; caducada (5 min), no para. Si ya estaba parado, lo dice sin preguntar. |
| `/estado` | Ya existía en v3: dice si el Worker está en marcha o parado. |

La confirmación pendiente vive en D1 (migración 0026, tabla
`telegram_stop_confirmations`: `chat_id` PK, `tenant_id`,
`expires_at`), de un solo uso y por chat+tenant: un chat
re-vinculado a otro tenant no puede confirmar una parada ajena.
Al desvincular (`/unlink`) se borra. Seguridad multi-tenant
igual que el resto: el tenant sale del vínculo por chat_id y un
tenant nunca para el Worker de otro.

### Alerta si el Worker se desactiva de la nada

El cron (`scheduled()`, cada minuto) compara por tenant el
estado real de `worker_state` con el último estado conocido
(clave `telegram_worker_last_running` en settings, 1/0; el panel
no la borra al guardar Ajustes porque `effectiveSettings`
conserva las claves desconocidas):

- Primera vez (sin marca): inicializa en silencio, sin avisar
  de un estado que ya era así.
- Transición 1→0: UN solo aviso a su chat vinculado — «⚠️ El
  Worker se desactivó a las HH:MM (Santiago)… enciéndelo con
  /iniciar» — sin inventar la causa, y actualiza la marca: el
  minuto siguiente ya no es transición y no repite.
- Transición 0→1 o sin cambio: solo actualiza la marca, sin
  avisar.
- La parada desde Telegram NO genera la alerta (al parar ya pone
  la marca a 0). La parada desde el panel SÍ avisa (puede ser
  otro usuario o un error). Sin vínculo, el aviso vive solo en
  el panel (`worker_events`).

### Preview opcional (mismo deploy, no es del bot)

El preview ya NO es obligatorio para publicar. Ajuste por
tenant `require_preview`, por defecto `false`:

- `publishGate` (y el re-chequeo en ejecución) solo frena con
  «falta ver el preview en el Editor» cuando el ajuste está
  activado. El resto de puertas no cambia (subtítulos, SEO,
  duración, GTA VI, topes).
- Ajustes → sección «Preview en el Editor»: casilla «Exigir ver
  el preview antes de publicar», desmarcada por defecto. Si se
  activa, vuelve el comportamiento viejo.
- En el Editor el preview sigue disponible como casilla
  («Marcar preview como visto») y el botón Publicar solo se
  bloquea por preview no visto cuando la exigencia está activa;
  si no, la nota dice que verlo es opcional. Al cambiar la
  duración, el preview guardado se sigue invalidando, pero la
  nota ya no exige volver a verlo salvo que la exigencia esté
  activa.

### Verificación en vivo (tras desplegar)

1. Desplegar: aplicar la migración 0026 en remoto
   (`wrangler d1 migrations apply clips-avc`), desplegar Worker
   y Pages. Sin secretos nuevos ni re-vincular.
2. `/iniciar` con el Worker parado → «🟢 Worker en marcha» y el
   panel lo muestra en marcha. `/iniciar` otra vez → «ya estaba
   en marcha».
3. `/parar` → pregunta «¿Seguro?» y el Worker SIGUE en marcha.
   Responder «no» → se cancela (llega la ayuda) y el Worker
   sigue en marcha. `/parar` otra vez y responder «SÍ» → «🔴
   Worker parado» y el panel lo muestra parado. Y NO llega la
   alerta de «se desactivó» (era voluntaria).
4. Con el Worker parado desde Telegram, `/iniciar` → en marcha.
5. Parar desde el PANEL (botón Parar) → en el minuto siguiente
   llega UN solo mensaje «⚠️ El Worker se desactivó a las
   HH:MM…» a su chat. No se repite en los minutos siguientes.
   `/iniciar` → en marcha, sin aviso.
6. Preview: con un clip sin preview visto, Publicar desde el
   Editor funciona (botón habilitado). En Ajustes, activar
   «Exigir ver el preview…» → el mismo clip vuelve a bloquearse
   («falta ver el preview en el Editor») hasta marcarlo visto.
