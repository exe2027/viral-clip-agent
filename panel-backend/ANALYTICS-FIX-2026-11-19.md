# Arreglo /resumen y analíticas GTA 6 — 2026-11-19

## Qué decía el usuario

El `/resumen` de Telegram del 2026-11-19 decía "Canales ES ·
@elclipdegta6" con YT 11 · medidos 1, TikTok 4 · 0, IG 2 · 1, FB
2 · 0, y "las vistas se pegan a mano en el panel". El usuario:
está mal/desactualizado, es falso, faltan cuentas, no todas se
llaman @elclipdegta6, y las vistas NO se pegan a mano.

## Cuentas reales por canal (verificado en vivo 2026-11-19)

Fuente: tabla `accounts` en la D1 remota `clips-avc` tras aplicar
la migración 0030 (ver abajo), contrastada con `jobs` posted
(`evidence_url`) y con las páginas públicas.

| Canal | Handle real | Cómo se verificó |
|---|---|---|
| YouTube ES | @elclipdegta6 | youtube.com/@elclipdegta6 ("El Clip de GTA 6", en vivo) |
| YouTube EN | @gta6clips | youtube.com/@gta6clips ("GTA6clips", en vivo) |
| TikTok ES | @elclipdegta6____bits | 3 `evidence_url` de jobs posted D1 (clips 7, 13, 23) |
| TikTok EN | @elclipdegta6en___ | `evidence_url` del job 19 (clip 12) |
| Instagram ES | @elmegaclipgta6 | instagram.com/elmegaclipgta6 (creador, en vivo) |
| Instagram EN | sin handle registrado | 0 publicados EN; no se inventa |
| Facebook ES | Elclipdegta6 (nombre de Página, no @) | Página vinculada a IG, confirmada por el usuario 2026-11-19 00:28 |
| Facebook EN | sin handle registrado | 0 publicados EN; no se inventa |

## Conteos reales (jobs `state='posted'`, tenant sebastian, 2026-11-19 ~20:55 UTC)

| Canal | Publicados | Medidos | Mediana vistas |
|---|---|---|---|
| YouTube ES | 7 | 1 | 65 (clip 13, 1 sola medición) |
| YouTube EN | 4 | 0 | sin medición |
| TikTok ES | 3 | 0 | sin medición |
| TikTok EN | 1 | 0 | sin medición |
| Instagram ES | 2 | 1 | 145, retención 48,8 % (clip 15, 1 sola medición) |
| Instagram EN | 0 | 0 | sin medición |
| Facebook ES | 2 | 0 | sin medición |
| Facebook EN | 0 | 0 | sin medición |
| **Total** | **19** | **2** | — |

Las 2 mediciones existentes son MANUALES (registradas 11:38 y
12:01 UTC, antes/después de que la automática fallara en sus dos
pasadas del día): la automática no midió ningún clip (ver abajo).

## Causa raíz de cada cifra falsa

1. **"Canales ES · @elclipdegta6"**: `telegram.ts`
   `buildAnalyticsMessage()` hardcodeaba esa cabecera para las 4
   plataformas y los 2 idiomas. La tabla `accounts` (diseñada para
   canal + handle) estaba VACÍA y `api_accounts` solo guarda una
   etiqueta genérica ("YouTube", "TikTok ES"). No había de dónde
   sacar ningún handle.
2. **11 / 4 / 2 / 2 como si fueran ES**: `computeAnalytics()`
   agrupaba solo por plataforma y contaba publicados desde
   `clips.publish_states` (espejo por clip, sin idioma por
   plataforma). Los totales eran correctos como TOTALES (YT 7 ES +
   4 EN = 11; TikTok 3 ES + 1 EN = 4; IG 2; FB 2 — coinciden con
   los 19 jobs posted), pero se presentaban como ES y faltaban los
   4 canales EN. El clip 23 sigue contando (sus 3 copias malas
   siguen publicadas en YT/TikTok/FB hasta que el usuario confirme
   borrarlas; el re-render corregido está en el panel, sin
   republicar).
3. **"Las vistas se pegan a mano"**: el mensaje lo decía como
   camino normal y OCULTABA el estado de la medición automática:
   `computeAnalytics()` ya calculaba `auto_status` pero
   `AnalyticsPlatformSummary` no lo incluía y el mensaje no lo
   enseñaba. La medición automática EXISTE y corre (ver abajo),
   pero falla en las 4 plataformas por permisos.
4. **Medición automática: ya desplegada antes de este arreglo,
   fallando en las 4 plataformas.** Migración 0028 aplicada
   2026-11-19 12:00 UTC por otra sesión; `collectAnalytics` corrió
   08:41 y 15:01 -03 (dos pasadas, `worker_events` 674-677 y
   5101-5106) y falló en todo:
   - YouTube: `sin_permiso` — HTTP 403, el token concedido no
     incluye `youtube.readonly`; reautorizar OAuth en Cuentas.
   - TikTok: `sin_permiso` — la cuenta guardada es vía Zernio (su
     clave no es un access token de TikTok); exige reautorizar con
     `video.list` (+ Login Kit + aprobación Display API).
   - Instagram: `error` — HTTP 400 sobre el media
     `18484769662119306` (clip 7): no existe / sin permisos para
     esa operación. El post `instagram.com/p/Df5iORvnD_x/` no se
     pudo verificar por web pública (Meta lo sirve solo por API).
   - Facebook: `sin_permiso` — código Meta 200, falta el scope
     `read_insights`; re-conectar Meta en Cuentas con los permisos
     nuevos.
   Hasta que el usuario re-conecte, `auto_status` muestra el
   motivo exacto por canal; no se finge medición.
5. **Hardcode extra del mismo origen**: `db.ts evidenceUrl()` y
   `publish/tiktok.ts` construían la evidencia de TikTok como
   `tiktok.com/@elclipdegta6/video/<id>` también para clips EN.
   Ahora el handle sale de `PublishContext` (tabla `accounts`) y
   sin handle la URL va sin `@usuario`.

## Cambios (sin tags de versión, orden del usuario 2026-11-19 08:53)

- Migración `0030_channel_handles.sql`: `language` en `accounts` +
  siembra de las 6 cuentas con handle verificado (idempotente).
  Instagram EN y Facebook EN no se siembran: sin handle verificado.
- `worker/src/db.ts`: `SocialAccountRow.language`,
  `listPostedJobs()` (jobs posted = fuente de verdad),
  `tiktokHandleFromEvidence()`, `evidenceUrl()` con handle
  opcional (sin handle, URL sin `@usuario`).
- `worker/src/index.ts`: `computeAnalytics()` reescrita — 8
  canales (plataforma+idioma), publicados desde jobs posted
  (DISTINCT por clip), medidos por idioma del clip, handle desde
  `accounts`, `auto_status` por canal; `platforms` se conserva
  como agregado ES+EN para compatibilidad; `unmeasured` ahora
  lleva `language`. `/resumen`, `/top`, `/api/telegram/test` y el
  resumen diario usan `channels`. `/api/social-accounts` expone
  `language`. El hito viral sigue disparándose solo con medición
  manual (hueco documentado en el código: la automática aún no lo
  dispara).
- `worker/src/telegram.ts`: `buildAnalyticsMessage()` por canal
  con handle real y línea de medición automática con el motivo
  exacto; sin "Canales ES · @elclipdegta6" ni "pegan a mano" en
  ningún texto del Worker ni del panel. `buildTopMessage()` igual.
- `worker/src/publish/index.ts` + `tiktok.ts`: la evidencia de
  TikTok usa el handle del canal, nunca el literal ES.
- `frontend/dist/app.js` + `index.html`: la pestaña Analíticas
  muestra ES/EN por tarjeta con handle y estado de medición
  automática; el formulario manual queda rotulado "Registrar una
  medición (respaldo)".
- Tests: `telegram.test.ts` (8 canales, endpoint
  `GET /api/analytics` por canal) y `endpoints-nuevos-2.test.ts`
  (publicados desde jobs posted) actualizados.
- **Coordinación (otro chat)**: el arreglo del preview de la Cola
  en móvil (`toggleQueuePreview` con búsqueda en el contenedor del
  botón, `app.js:583`, y `.queue-video[hidden]{display:none
  !important}`, `styles.css:370`) estaba intacto en disco antes y
  después de este trabajo y se desplegó junto con este arreglo. Las
  ediciones de `app.js` aquí fueron solo en la sección Analíticas.

## Commit

**Sin commit: `panel-backend` NO es un repo git** (no hay `.git`
en `~/workspace/gta6-clips/independencia/panel-backend` y el repo
`viral-clip-agent` no lo incluye; solo lo menciona en
`docs/independencia-cloudflare-plan.md`). Los cambios están en
disco y desplegados (abajo). Pendiente: crear el repo para el
panel o incluirlo en uno existente para poder versionarlo.

## Despliegue (2026-11-19 ~20:54 UTC)

`scripts/deploy_with_token.py` (token Cloudflare vía conector
`custom.cloudflare`, sin token en disco):

- Migración 0030 aplicada en D1 remota `clips-avc`: ✅ (1
  migración, 8 comandos, 20:54:49 UTC).
- Worker desplegado: ✅ versión
  `98131201-0275-4ab7-b14b-b1d59c428724`,
  https://avc.vikingosfoodspa.workers.dev
- Pages desplegado: ✅ https://57a3ccc6.avc-em9.pages.dev
  (proyecto `avc`, dominio en uso https://avc-em9.pages.dev).
  Este despliegue incluye también el trabajo pendiente del otro
  chat (reglas de publicación 2026-11-19 + preview móvil), que
  esperaba a que la suite volviera a verde.
- `tsc` limpio; `bun test test/` **447 pass, 0 fail** (28
  archivos; baseline 445, +2 tests nuevos de este arreglo).

## Verificación en vivo (después de desplegar)

- `GET /api/health` en workers.dev: 200
  `{"ok":true,"app":"avc","tenants":["sebastian","colega","kaez"],
  "worker_running":{"sebastian":true,"colega":false,"kaez":true}}`.
- `GET /api/analytics` y `/api/social-accounts` sin sesión: 401
  (auth intacta).
- D1 remota: 6 filas en `accounts` con los handles de la tabla de
  arriba; migración 0030 en `d1_migrations`; `analytics_status`
  sin cambios (sigue `sin_permiso`/`error` en las 4: esperado
  hasta re-conectar).
- Pages desplegado: `app.js` servido contiene el
  `toggleQueuePreview` con scope y el nuevo
  `renderAnalyticsSummary(platforms, channels)`; `styles.css`
  contiene `.queue-video[hidden]`; `index.html` contiene
  "Registrar una medición (respaldo)".
- `/resumen` reconstruido con datos vivos de D1 y la función
  `buildAnalyticsMessage` desplegada (mismo código, mismos datos
  que usaría el Worker): 8 canales con handles y conteos de la
  tabla de arriba, cada uno con su línea de medición automática.
  Ver el texto exacto en el reporte al chat principal.
- **NO verificado en vivo con sesión**: el contenido de
  `GET /api/analytics` y el `/resumen` enviado por Telegram
  requieren sesión del panel / chat vinculado. Para cerrarlo: el
  usuario (o el chat principal con navegador) abre
  https://avc-em9.pages.dev → Analíticas (debe mostrar 8
  canales con handles) y escribe `/resumen` al bot.
- `https://avc.pages.dev` (raíz y `/api/health`) sigue sin
  responder desde aquí (empty reply, HTTP 000), igual que antes
  del despliegue: el panel en uso es `avc-em9.pages.dev` y la API
  es `avc.vikingosfoodspa.workers.dev`. Preexistente, no causado
  por este arreglo.

## Pendiente del usuario (para que la medición automática mida)

1. Reautorizar YouTube OAuth en Cuentas (falta el scope
   `youtube.readonly` en el token concedido).
2. Re-conectar Meta en Cuentas (faltan `instagram_manage_insights`
   y `read_insights`; además el media 18484769662119306 del clip 7
   da 400: revisar si ese reel sigue existiendo).
3. Reautorizar TikTok con `video.list` (+ Login Kit + aprobación
   Display API); hoy la cuenta es vía Zernio y su clave no sirve
   para medir.
4. Confirmar los handles de Instagram EN y Facebook EN cuando
   existan (hoy "sin handle registrado", sin inventar).
5. Confirmar borrar las 3 copias malas del clip 23 (YT
   `e4FOx9M_Lso`, TikTok `7709836660304776500`, FB
   `4706559349610438`): siguen contando como publicadas hasta que
   se borren. TikTok se borra desde la app (sin API de borrado).

## Addendum 20:57 UTC — el preview de la Cola cambió en disco DESPUÉS del despliegue

- El despliegue de Pages de las ~20:55 UTC subió el `app.js` tal
  como estaba entonces: `toggleQueuePreview` (línea 583) con
  búsqueda en el contenedor del botón + `.queue-video[hidden]` en
  `styles.css:370`. Verificado antes de desplegar.
- A las 20:56:18 UTC (`app.js`) y 20:56:33 UTC (`styles.css`) otro
  proceso/chat reescribió la sección del preview en disco:
  `toggleQueuePreview` ahora usa `data-queue-frame`, `qpCloseFrame`,
  `qpFrameHtml` y pestañas, y está en la línea ~706. Sigue buscando
  en el contenedor del botón (`btn.closest("article, tr")` con
  fallback global), así que el principio del arreglo móvil se
  mantiene, y la regla `.queue-video[hidden]` sigue en
  `styles.css:370`. El código de Analíticas de este arreglo sigue
  intacto en disco (`renderAnalyticsSummary(platforms, channels)`,
  línea ~1713).
- Consecuencia: **el Pages desplegado (57a3ccc6) lleva la versión
  del preview de las 20:55; la variante nueva de disco NO está
  desplegada**. No se re-desplegó unilateralmente: ese trabajo es
  de otro chat y podía seguir a medias. Re-desplegar cuando ese
  trabajo esté cerrado (suite verde + preview verificado).
