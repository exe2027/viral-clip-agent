# AVC — Content Rewards (puente local) — extensión Chrome v1.3.0

Extensión en **modo desarrollador (unpacked), gratis, sin Chrome Web Store y sin pagar $5**.
La nube sigue siendo el camino principal; esta extensión es el puente local para
lo que la nube no puede hacer sin la sesión de Chrome del usuario.

## v1.3.0 — qué cambió (2026-11-23)

### Login por tenant (decisión 2026-11-23 18:32: login por tenant, campañas separadas)
- **API key de Content Rewards POR TENANT** (`apiKey_seba` / `apiKey_kaez`) y
  **token de sesión del panel POR TENANT** (`panelToken_seba` / `panelToken_kaez`).
  Nada se comparte entre seba y kaez: ni claves, ni tokens, ni campañas, ni
  historial. El popup guarda/lee siempre para el tenant activo del
  desplegable y muestra el estado de ambos (`keysStatus`).
- Las claves viejas sin sufijo eran de seba y migran a `*_seba` al arrancar
  (`background.js`).
- La sesión del panel es **tenant-bound**: el token de seba da 401 en kaez y
  viceversa (TTL 30 días). Se respeta: cada tenant consulta solo su Worker.
- **Kaez todavía no tiene cuenta propia de Content Rewards.** Su login está
  preparado (claves por tenant, Worker, alarma), pero sin su API key sus
  envíos quedan en `needs_api_key` en espera. **Nadie inventa su cuenta.**
  Cuando él pegue su API key en el popup (tenant kaez), sus envíos arrancan.
- El permiso **`identity` se QUITÓ del manifest** (v1.3.0): no se usaba —
  no hay ninguna llamada a `chrome.identity` en el código (verificado por
  grep). El OAuth se hace abriendo una pestaña a
  `contentrewards.com/c/settings`, donde el usuario autoriza con su propia
  sesión de Chrome. Permisos ahora: solo `storage` y `alarms`.

### Puerta corregida: 'publicado en la red' ≠ 'enviado a Content Rewards'
- Bug de v1.2.0 (`lib.js:293-299`): un clip con `publish_breakdown` en
  `posted` bloqueaba el envío como "duplicado". Eso estaba al revés:
  **`posted` en D1 es el REQUISITO para enviar** (ahí nace el link real
  con su `evidence_url` y su `publishedAtMs` real), no un envío.
- Ahora el duplicado lo decide **solo una fila en `campaign_submissions`**
  (GET `/api/campaign-submissions` del Worker, para tenant+clip+plataforma+
  campaña, estado `pending` o `submitted`). Si la ruta aún no está
  desplegada en ese Worker, el chequeo cae al registro local y queda
  anotado como `local_fallback` (nunca se finge un chequeo que no se hizo).

### Ranking: tarifa NETA + solo FAMOSOS (reglas 2026-11-23 18:19 y 18:50)
- El ranking usa la tarifa **NETA** (bruta × 0,9, comisión del 10% de
  Content Rewards) con **piso de $1/1k neto**: BenDaDonnn ($1,00 bruto =
  $0,90 neto) queda fuera del ranking y del auto-envío.
- **Regla 2026-11-23 18:50: aunque sea campaña, solo influencers FAMOSOS,
  no cualquier persona.** Criterio (única fuente, igual en el Worker):
  **≥ 50.000 seguidores/suscriptores verificables en alguna plataforma**
  (`FAME_MIN_FOLLOWERS`). Una campaña que pague mucho pero de un streamer
  desconocido no entra al ranking, o queda última. Bobby Van Riper paga
  $1,50/1k pero tiene **734 seguidores en Twitch** (30 espectadores
  medios): **fuera del ranking, fuera de la prioridad y fuera de la
  activación** — en el popup sale al final marcada como no famosa. Su
  campaña tiene 22 clippers y 26 clips aprobados, pero eso no compensa el
  listón: sus clips promedian 4,1k vistas, así que el tope de $150 es
  inalcanzable en la práctica.
- Orden neto+fama del 23 nov (por score, tarifa neta × viralidad ×
  presupuesto restante): **Dasgasd3m ~4,55 > Dylan Page ~1,98 >
  ArthurTV ~1,35**. La tarifa y el presupuesto se re-verifican el día
  del envío (Discover o MCP, no solo `KNOWN_CAMPAIGNS`); si `verifiedAt`
  no es hoy, el selector marca `stale`.
- El selector matchea **streamer del clip ↔ streamer de la campaña**: si
  no coincide, NO auto-asigna (`selectBestCampaignForClip`:
  `sin_streamer` / `no_match`).

### Brief por campaña + material: las campañas NO entregan clips listos
- Cada campaña tiene su ficha de brief (`CAMPAIGN_BRIEFS` + `getCampaignBrief`):
  disclosure, hashtags/menciones, material permitido, expiración, mínimo y
  tope por clip. Lo que no se verificó queda `incomplete: true` y hay que
  leerlo en Discover el día del envío, **nunca inventarlo**.
- **Pregunta del usuario 2026-11-23 18:49: ¿las campañas entregan clips
  listos para subir? NO.** Todas entregan **brief + stream/VOD permitido**
  y hay que **cortar tu propio edit** del stream (Dasgasd3m: sus streams
  de Kick; ArthurTV: sus VODs/vídeos; Dylan Page: solo su maratón de 120 h
  en YouTube; BenDaDonnn: sus vídeos/broadcasts con Puffco). **Un clip ya
  hecho y subido tal cual lo rechazan por duplicado / no original**:
  Content Rewards rechaza el contenido reutilizado y acepta cada post una
  sola vez en una campaña. Nuestro cerebro ya corta solo de los streams en
  vivo, así que los 4 streamers famosos de campaña van al live scan (no se
  busca clip por clip a mano). No se descarga material sin verificar el
  brief. El popup muestra la ficha de brief al elegir campaña.

### MCP: respuesta parseada, 200 con error = fallo
- `parseMcpResponse` / `parseMcpHttpResponse` (`lib.js`): JSON plano o
  SSE (`data:`), `result.error` y `result.content[0].text` JSON. Un **200
  con `error` es FALLO, no éxito**. El `submission_id` se guarda del
  resultado y se muestra en el historial del popup. **Verificación en
  vivo contra el MCP real queda PENDIENTE**: solo código + stubs, no se
  envió nada real.

### Asignación por clip+plataforma y reintentos de todos los bloqueos
- La asignación es por **tenant+clip+plataforma** (`assignmentKey`), no
  por clip solo: el mismo clip en TikTok y en YouTube son dos envíos
  distintos (dentro de la MISMA campaña, un link por plataforma).
- Se reintentan **TODOS los bloqueos reparables dentro de la ventana**
  (`worker_down`, `clip_check_failed`, `no_worker_base`, `mcp_error`,
  `worker_error`, `submissions_unavailable`), no solo `worker_down`. No
  se reintentan: duplicado, fuera de ventana, sin API key, sin sesión,
  streamer sin match, aún no publicado, sin `publishedAtMs`.
- **`publishedAtMs` nunca es `Date.now()` por defecto**: sin el instante
  REAL de publicación (el que marca `markPosted` en el Worker) el envío
  queda bloqueado (`missing_published_at`).

### Cola en nube (Chrome cerrado)
- La fila en `campaign_submissions` nace `pending` en D1 **antes** de
  enviar (registro en la cola en nube) y se marca `submitted` + `submission_id`
  después. Vive en D1, sobrevive a Chrome cerrado; al reabrir, la alarma
  de 5 min retoma las pendientes (`?pending=1`) dentro de los 30 min.
  Pasados 30 min el Worker las marca `expired`: nada se envía tarde.
  El Worker **no** envía al marketplace (no tiene la API key de Content
  Rewards, que vive solo en el Chrome del usuario): la extensión sigue
  siendo la única que envía, pero la cola en D1 es lo que sobrevive sin
  Chrome. Diseño completo en `../panel-backend/CAMPAIGN-SUBMISSIONS-0041.md`.

### Multi-marketplace + analíticas (2026-11-23 18:48)

**Tabla comparativa verificada en vivo el 23 nov 2026** (búsquedas y
lectura de páginas ese día; las tarifas cambian a diario: re-verificar
en la página de la campaña antes de entrar). Piso del usuario:
**$1/1k NETO** (tras comisión). Solo Content Rewards tiene API/MCP;
todo lo demás es **solo web, envío manual** (sin endpoint inventado).

| Marketplace | Tarifa bruta /1k | Comisión | Neta /1k | Mín. retiro | Mín. vistas/clip | Plataformas | API / MCP | GTA 6 hoy (23 nov) | Chile |
|---|---|---|---|---|---|---|---|---|---|
| Content Rewards (Whop) | por campaña ($0,35–$2,00) | 10% | bruta × 0,9 | $1 (Whop) | según campaña | TT/IG/YT/FB/X | **MCP oficial** | **21 abiertas**; top Dasgasd3m $2,00 ($1,80 neto) | Sí (Whop, 200+ países) |
| Vyro | por campaña ($1,00–$2,50) | 0% lista | = bruta | $10 (help) / $25 (términos), 1×/7 días a Whop | 5.000 | TT/IG/YT/X | Solo web | **0** (12 campañas 26 oct, ninguna GTA) | Sí (no está entre los 28 sin pago) |
| Clipster | por campaña ($0,08–$4, mediana $1,00) | 5% al retirar | bruta × 0,95 | $50 | mediana 2.000 | TT/IG/YT | Solo web | 20 abiertas, pero solo **edits de música** a $1,00 máx → **$0,95 neto < piso** | Sin verificar |
| CLIPPING (clipping.net) | por campaña ($0,10–$3,00+) | % no publicado | no verificable | por ciclo (sin botón) | 1.000 | TT/IG/YT/X | Solo web | Sin campaña GTA 6 verificada | Sin verificar |
| ClipBoom | por campaña (no publica tarifa) | 5% | bruta × 0,95 | €150, 2×/mes | según campaña | TT/IG/YT | Solo web | Ninguna GTA 6 | Sin verificar |
| Clipeamos | típico 0,30–0,60 €; altas $3–4 | 0% (+2,9% proveedor) | ≈ bruta × 0,971 | $50, automático 2×/mes | según campaña | TT/IG/YT | Solo web | Ninguna GTA 6 | Sin verificar |
| Lulosa | por campaña (media $1,10) | % no publicado | no verificable | $20 (o $1 a 90+ días) | según campaña | TT/IG/YT | Solo web | Ninguna (solo música latina) | Sin verificar |
| QuantyClips | **no publica tarifa** | no publicada | no verificable | $55 | sin publicar | TT (+IG/YT) | Solo web | Ninguna GTA 6 | Sin verificar |
| Ssemble Clip Rewards | — (tablero; cobra en CR) | — (10% en CR) | — | n/a | según campaña | TT/IG/YT | Solo web | iShowSpeed GTA 6 **CERRADA** 21 nov; Tomo $2/1k visible (campaña CR) | Sin verificar |
| Clipstake (nuevo) | $0,20 (DonBet) | no publicada | no verificable | sin verificar | sin verificar | IG | Solo web | DonBet Logo GTA6 $0,20/1k — bajo piso | Sin verificar |
| Clippers Media | $2–5 (música latina) | no publicada | no verificable | sin verificar | sin verificar | TT/IG/YT/X | Solo web | Ninguna GTA 6 (ejemplos ilustrativos, no reales) | Sin verificar |

Fuentes (23 nov 2026): OpenClip GTA 6
(https://openclip.app/clipping/gta-6-clipping — 41 abiertas, 21 CR +
20 Clipster, hasta $2,00/1k, 21:00 UTC); ClipCPM GTA 6
(https://clipcpm.com/gta-6 — 58 campañas listadas con tarifa y
presupuesto); Vyro (https://vyro.com/campaigns, tablero 26 oct vía
Clyper https://www.clyper.io/guides/vyro-clipping; países sin pago
https://vyro.com/help/earnings-and-payments/eligibility); Clipster
(https://www.clyper.io/guides/clipster-clipping — 29 oct: 114
campañas, mediana $1/1k, mín. $50, 5%); CLIPPING
(https://clipping.net/docs/reference/faq + https://findclout.com/blog/clipping-net-payouts);
ClipBoom (https://clipboom.io/en + https://clippingdaily.com/clipboom-review/);
Clipeamos (https://www.clipeamos.com/guias/cuanto-se-gana-con-clipping y
https://www.clipeamos.com/comparativas/clipeamos-vs-clipboom);
Lulosa (https://lulosa.com + https://clippingcalc.com/alternatives/lulosa);
QuantyClips (https://www.quantyclips.com/en); Ssemble
(https://app.ssemble.com/clip-rewards); Whop payouts Chile
(https://docs.whop.com/manage-your-business/manage-payouts/set-up-payouts).
Duplicados/rechazos por subir un clip tal cual:
https://openclip.app/clipping/avoiding-duplicate-content-flags y
https://clippingdaily.com/clipping-rejected-for-reused-content/.

**Qué se añadió al ranking (por tenant y por marketplace):**
- `MARKETPLACES` (`lib.js`): registro de los 11 marketplaces con
  comisión, mínimo de retiro, plataformas, API y material. Solo
  Content Rewards tiene `api: "mcp"`; el resto es `manual_web`.
- `rankMarketplaceCampaigns` / `selectMarketplaceForClip` (`lib.js`):
  mismo piso neto ($1/1k) y mismo filtro de fama (regla 18:50) por
  marketplace. **UN clip = UN marketplace**: la asignación
  (`marketplaceAssignmentKey`: tenant+clip, sin plataforma) elige uno
  y no se re-asigna a otro; dentro del mismo marketplace sí se cobra
  en varias plataformas. `background.js` guarda `marketplace` en cada
  asignación (Content Rewards en el auto-envío).
- **Al ranking automático NO entró nada nuevo hoy**: las 7 campañas
  GTA 6 de Clipster (`CLIPSTER_GTA6_CAMPAIGNS`) son edits de música a
  $1,00 bruto = **$0,95 neto, bajo el piso**; Vyro/ClipBoom/Clipeamos/
  Lulosa/QuantyClips no tienen ninguna campaña GTA 6 hoy; CLIPPING y
  Clipstake no publican comisión (neto no verificable = fuera).
- **Candidatas CR sin UUID** (`CANDIDATE_CAMPAIGNS_NO_ID`, NO en el
  ranking automático): Lauren Hamden $1,50/1k solo IG ($4,2k/$5k) y
  GTA VI Lucia vs Fanvue AI Lucia $1,50/1k IG+TT ($5k intactos) —
  pasan el piso neto pero **sin UUID público y sin seguidores
  verificados** (fama desconocida = no famoso); Tomo GTA VI ($2,00 en
  Ssemble vs $1,50 en OpenClip 19 nov: **tarifa en conflicto**).
  El UUID se toma de Discover/MCP el día del envío, nunca se inventa.
  Los pools (Lacy $12.500, Kai Cenat $10.000, Clix $5.000, YourRAGE
  $5.000) no son tarifa por 1k y no entran al ranking.
- **Ficha de material por marketplace** (`MARKETPLACES[].material`):
  ninguna entrega clips listos en GTA 6; todas dan brief + fuente
  autorizada (carpeta/VOD/stream nombrado) y hay que **cortar tu
  propio edit**. Excepción documentada: algunas campañas de marca en
  CR comparten clips verticales listos (no la norma en GTA 6).
  **Un clip ya hecho y subido tal cual lo rechazan por duplicado /
  no original** (misma huella visual/audio que otros envíos, o
  re-subida de otro clipper): hay que cortar otro momento u otro
  corte (inicio/fin, hook y texto distintos), no solo cambiar un
  efecto o los subtítulos.
- **Streamers nuevos → live scan, no a mano:** el cerebro ya corta
  solo de los streams en vivo (`clipagent/live_scan.py` lee
  `streamers.yaml`, la única lista). Dasgasd3m, ArthurTV, Dylan Page
  y Bobby Van Riper **ya están** en `streamers.yaml`; Lauren Hamden,
  Lucia y Tomo **no están**: se añaden solo cuando tengan UUID +
  fama verificada (≥ 50.000 seguidores). No se busca clip por clip a
  mano ni se descarga material sin verificar el brief.

**Analíticas en tiempo real (popup, sección "Analíticas"):**
- Por tenant (seba / kaez) y en total: **dinero total ganado**
  (suma `amount_usd` de enviados en D1), **dinero sin retirar** y
  **dinero retirado**: **no disponible por API** en ningún
  marketplace verificado (el MCP de Content Rewards no expone saldo;
  el resto no tiene API) — el popup lo dice así y remite a la web
  (Whop / Content Rewards), nunca inventa un número. **Vistas
  aprobadas** (suma `views` de enviados), **enviados / pendientes /
  fallidos / caducados / con vistas**, y **por campaña**
  (marketplace + campaña + vistas + importe).
- Fuente: `GET /api/campaign-submissions` del Worker de cada tenant
  (sin `clip_id` = todas las filas), vía `listAllSubmissions`
  (`worker.js`) + `AVC_GET_ANALYTICS` (`background.js`) +
  `summarizeAnalytics` / `summarizeAllAnalytics` (`lib.js`).
- **Si la tabla/ruta aún no responde** (404 = aún no desplegada en
  ese Worker, o sin token del panel), el popup muestra **"sin datos
  todavía" con el motivo, no ceros** como si fueran reales. Una tabla
  vacía real (0 filas) sí muestra ceros reales.
- Honestidad sobre aceptados/rechazados: D1 **no** registra
  aceptado/rechazado del marketplace; "con vistas" (enviado con
  `views > 0`) es el proxy y así se etiqueta; `failed`/`expired` son
  los que D1 sí conoce. `views`/`amount_usd` en `null` suman como 0
  hasta que el Worker los actualice por PATCH.
- Tests en `validate.sh`: totales por estado, por campaña, total
  ganado, saldos siempre `null`, "sin datos" con `null`, tabla vacía
  con ceros reales, `listAllSubmissions` con stub (200 y 404).

## Decisiones del usuario (2026-11-23 18:19) — aplicadas en la v1.2.0

1. **Instagram: @elmegaclipgta6.** Es la cuenta a conectar en Content Rewards,
   por OAuth en el Chrome del usuario, sin login con contraseña por Muse
   (regla 2026-11-23 17:41, sigue vigente) y sin códigos.
2. **Campañas: las más virales y las que más paguen — selector dinámico, no
   una campaña fija.** El ranking (`lib.js: rankCampaigns`) puntúa
   `tarifa NETA × viralidad (1 + log10(1 + creadores)) × presupuesto
   restante/total` (v1.3.0: neta, no bruta; ver § v1.3.0) y elige la mejor
   del día entre las 5 del radar del 23 nov y cualquier otra que se añada
   después, **solo de streamers famosos** (regla 18:50, ≥ 50.000
   seguidores). A una campaña agotada no se envía. La tarifa/presupuesto
   se re-verifica el día del envío (el selector marca `stale` si
   `verifiedAt` no es hoy).
3. **Influencers virales:** el cerebro debe cortar clips de los streamers
   virales de esas campañas (ver "Plan: influencers virales" abajo). La lista
   de influencers del Worker **NO se toca en esta tarea**, solo queda el plan.
4. **ES y EN a la vez:** la extensión sirve para ambos idiomas, sin preferir
   uno; ninguna campaña se filtra por idioma.
5. **Envío automático:** cuando llega `AVC_CLIP_PUBLISHED` con campaña
   asignada —o sin ella, en cuyo caso el selector le asigna la mejor del
   día— se envía solo por MCP oficial dentro de 30 min, sin esperar click.
   El popup solo muestra lo enviado y el historial; no es el flujo normal.
6. **Worker activo 24/7:** la extensión NUNCA apaga el worker (lista de
   bloqueo intacta). Si `/api/health` dice `worker_running=false` o no
   responde, no envía, lo marca como bloqueado y lo reintenta en la alarma
   de 5 min; el popup lo muestra en rojo como "worker parado — avisar".
   La extensión no intenta prenderlo.
7. **Para kaez también:** ambos tenants activos por defecto (seba y kaez),
   con la base de cada uno saliendo de `workers.json`, nunca hardcodeada, y
   el chequeo de duplicados **por tenant** (un clip de seba no bloquea uno
   de kaez y viceversa).
8. **Cobro: PayPal o cripto, o si se puede a cuenta en Chile como
   BancoEstado.** Esto **NO se programa en la extensión**: es configuración
   de pago en Whop / Content Rewards, del usuario. La extensión no toca
   pagos, bancos ni billeteras; solo se anota aquí como método elegido.

## Plan: influencers virales (decisión 3 — EJECUTADO 2026-11-23, ver abajo)

Para cobrar en las campañas del ranking hay que cortar clips nuevos de
sus streamers. Alta 2026-11-23 (streamers.yaml + puerta del Worker +
`panel-backend/scripts/seed_campaign_influencers.py` para el alta en D1
por tenant, que corre el coordinador con su sesión del panel):
- **Dasgasd3m** ($2,00/1k bruto = $1,80 neto, 65 creadores, ~62.500
  seguidores en Kick) — prioridad 1 por tarifa + viralidad. En el live
  scan (Kick).
- **ArthurTV** ($1,50/1k = $1,35 neto, $5.000 intactos, >500.000 subs en
  YouTube + 428.108 en IG) — presupuesto intacto. En el live scan (Twitch).
- **Dylan Page** ($1,20/1k = $1,08 neto, 1,17M subs en YouTube) — clip
  DEBE mostrarlo + #DylanPage; solo su maratón de 120 h. YouTube-only:
  NO entra al live scan (solo twitch/kick); solo VOD-fallback.
- **BenDaDonnn** ($1,00/1k = $0,90 neto, 645.690 seguidores en Twitch,
  único con Facebook) — famoso, pero BAJO el piso neto: sin prioridad y
  sin auto-asignación. En el live scan (Twitch).
- **Bobby Van Riper EXCLUIDO** ($1,50/1k, pero 734 seguidores en Twitch):
  regla 2026-11-23 18:50 (solo famosos). No entra al ranking, no da
  prioridad, no se activa, no se vigila (desactivado en streamers.yaml).
- Del resto del radar del 23 nov, también virales: Lucia, Lauren Hamden,
  BrettLiftsXP ($2,00/1k solo TikTok), AsianJeff, DJ Tunez, Boeshi.
Los clips 174/175/176 actuales (IShowSpeed / Kai Cenat) **no** califican
para ninguna campaña ≥ $1/1k de este ranking: hace falta cortar de estos
streamers nuevos. Cada clip nuevo va a UNA sola campaña que paga, en
3-4 redes dentro de esa misma campaña (regla de stacking vigente).
**Material:** ninguna campaña entrega clips listos (ver § v1.3.0 arriba):
hay que cortar tu propio edit del stream/VOD permitido; un clip ya hecho
subido tal cual lo rechazan por duplicado / no original.

## Cobro (decisión 8 — solo anotado, no programado)

Content Rewards paga al saldo de Whop desde $1, y de ahí el usuario
elige el retiro: **PayPal, cripto, o cuenta en Chile como BancoEstado
si Whop lo permite**. Eso se configura en Whop / Content Rewards por el
usuario, no en la extensión ni en el panel. La extensión no guarda
ningún dato de pago.

## Qué hace (v1.2.0) — conexión a D1 vía Worker

La D1 **no** se accede directa desde el navegador: solo el Worker la lee.
La extensión habla con el Worker del tenant activo y, **antes de
cualquier envío** a Content Rewards, consulta en vivo:

1. **Salud del worker** — `GET {base}/api/health` (sin auth). Si
   `worker_running` no es `true` o el Worker no responde, **no se envía**,
   el popup marca "worker parado — avisar" (en rojo) y la alarma de 5 min
   lo reintenta cuando el worker vuelve (decisión 6, worker 24/7). Es solo
   lectura: un chequeo de salud no apaga nada, y la extensión jamás llama a
   ninguna ruta que pare, encienda o reinicie el worker (ver lista de
   bloqueo abajo).
2. **Estado del clip en D1** — `GET {base}/api/clips/:id` (con el token
   de sesión del panel). Lee `clip.status` y `publish_breakdown` por
   plataforma. **`posted` en esa plataforma es el REQUISITO para
   enviar** (ahí está el link real con su `evidence_url` y su
   `publishedAtMs` real): si D1 dice que aún no está publicado, no hay
   link que enviar y se bloquea como `not_published_yet`.
3. **Duplicado en `campaign_submissions`** — `GET
   {base}/api/campaign-submissions?clip_id=<n>&platform=<p>&campaign_id=<c>`
   (con sesión). Si en D1 ya hay una fila `pending` o `submitted` para
   ese tenant+clip+plataforma+campaña, **no se reenvía**:
   `duplicate_submission`. Si la ruta aún no está desplegada, cae al
   registro local (`local_fallback`).
4. **Registro local** — si ese post ya se envió a esa campaña desde
   esta extensión, tampoco se reenvía (v1, intacto).

La puerta falla **cerrada**: sin salud, sin token de sesión del panel
o sin clip en D1, no se envía. Sin consultar D1 no se puede garantizar
que no se duplica.

La base del Worker **nunca está hardcodeada**: sale de
`https://avc-em9.pages.dev/workers.json` (el mismo archivo público que
lee el panel) en tiempo de ejecución, según el tenant elegido en el
popup (seba / kaez). Los hosts de los Workers en `manifest.json` son
solo la lista de permisos de Chrome, no la base elegida.

El token de sesión del panel lo pegas una vez en el popup (como la API
key) y vive solo en `chrome.storage.local`. La extensión no lee el
localStorage del panel (otro origen) y no tiene content scripts.
Sin token, el chequeo de D1 no se puede hacer y los envíos se bloquean.

## Qué hace (v1)

- **Conectar cuentas con tu propia sesión:** el botón del popup abre
  `contentrewards.com/c/settings` en una pestaña nueva y autorizas el OAuth
  oficial ahí mismo, con tu sesión de Chrome (ya estás logueado). Esto es lo
  que desbloquea Instagram, que Muse no puede conectar con login de contraseña
  (orden del usuario, 2026-11-23 17:41, sigue vigente).
- **Envío automático (v1.2.0, decisión 5):** al publicarse un clip, el
  selector dinámico le asigna la mejor campaña del día y se envía solo por
  MCP, dentro de 30 min. El formulario manual del popup queda solo como
  respaldo, no como flujo normal.
- **Idempotencia + ventana de 30 min:** un post = una campaña, una sola vez.
  Si ese post ya se envió a esa campaña, se bloquea. Si pasaron más de
  30 minutos desde la publicación, no se envía tarde.
- **Registro local:** cada intento queda en `chrome.storage.local` y se ve en
  el popup (últimos envíos, estado).

## Qué NO hace

- No pide ni guarda contraseñas de Instagram, TikTok, YouTube ni Google.
- No hace login con contraseña a ninguna red. No pide códigos de 6 dígitos.
- No publica clips. No hace clicks en páginas, no tiene content scripts, no scrapea.
- No envía el mismo post a dos campañas ni a dos marketplaces.
- No corre sin PC: si Chrome/PC se cierra, la extensión se pausa. Los
  envíos pendientes quedan como filas `pending` en D1 (cola en nube) y
  se retoman al reabrir Chrome, dentro de los 30 min; sin API key quedan
  en `needs_api_key` hasta que el usuario la pegue.

## Instalación desde la web (recomendado, 2026-11-23)

Todo desde Chrome, sin pasar archivos por el chat:

- **Página de instalación:** https://avc-em9.pages.dev/extension/
- **ZIP de la extensión:** https://avc-em9.pages.dev/extension/avc-extension-1.3.0.zip
  (también https://avc-em9.pages.dev/extension/avc-extension.zip, siempre la última versión publicada)
- **Auto-configuración:** https://avc-em9.pages.dev/extension/auto-setup.json
  (tenants seba/kaez desde `workers.json`, campañas del ranking y reglas; los valores por defecto de `lib.js` ya son idénticos a estos. Desde v1.3.0 `background.js` la descarga sola al instalar y cada hora — el popup muestra "Auto-configuración web v1.3.0 cargada". Si la web no responde, se usan los valores del ZIP.)
- También enlazada desde el panel: sidebar → **Extensión Chrome ↗**.

## Auto-configuración: qué configura sola (sin que toques nada)

El archivo `extension/auto-setup.json` (servido en la web de arriba) configura TODO automáticamente al cargar la extensión en Chrome:

1. **Tenants:** seba y kaez, cada uno con su base de Worker tomada de `workers.json` (nunca hardcodeada), su API key, su token de panel, sus campañas y su historial — nada compartido entre ellos.
2. **Campañas del ranking:** las 5 del radar del 23 nov (Dasgasd3m $2,00, ArthurTV $1,50, Dylan Page $1,20, más Bobby y BenDaDonnn marcadas como EXCLUIDAS: Bobby por no famoso, BenDaDonnn por neta $0,90 bajo el piso) + los 11 marketplaces comparados, con la regla de un clip = un marketplace.
3. **Reglas:** ventana de 30 min, 1 post = 1 campaña una sola vez, tarifa neta (bruta × 0,9, piso $1/1k neto), solo influencers famosos (≥ 50.000 seguidores), worker 24/7 (nunca se apaga), ES y EN a la vez, envío automático.
4. **Prioridad en cola:** 60% de los slots de cada cuenta van primero a clips con campaña (valor documentado aquí; vive en el Worker en `DEFAULT_PANEL_SETTINGS`, por tenant, cambiable en Ajustes sin tocar código).
5. **Analíticas:** de dónde salen los números (tabla `campaign_submissions` en D1 + API oficial de cada marketplace; saldo sin retirar = "no disponible por API, ver en la web", nunca inventado).

**Lo único que queda manual, solo para el usuario (checklist):**
- [ ] Conectar Instagram (@elmegaclipgta6), TikTok y YouTube por OAuth en su propio Chrome (contentrewards.com/c/settings).
- [ ] Pegar su API key de Content Rewards en el popup (una por tenant; kaez pega la suya cuando tenga cuenta propia).
- [ ] Pegar su token de sesión del panel por tenant en el popup.
- [ ] Configurar el cobro en Whop (PayPal, cripto o BancoEstado si lo permite) + KYC/RUT.
- [ ] Decidir la campaña de los clips 174/175/176 antes de sus slots (hoy no califican para ninguna ≥$1/1k neto).

Aclaración honesta: Chrome no permite instalar directo desde la web sin Web Store; la descarga es desde esa página y la carga es en modo desarrollador, en 2 clics.

## Instalación en modo desarrollador (5 pasos)

1. Descarga el ZIP desde https://avc-em9.pages.dev/extension/ y descomprímelo en una carpeta que no vayas a borrar (Chrome necesita que siga existiendo).
2. Abre Chrome en tu PC y ve a `chrome://extensions`.
3. Activa **Modo desarrollador** (arriba a la derecha).
4. Fija la extensión en la barra (icono de puzzle → pin en “AVC — Content Rewards”).
5. Abre el popup, pega tu API key de Content Rewards (la obtienes tú en
   `contentrewards.com`, tu cuenta) y pulsa **Guardar API key**.
   Sin API key no hay envío automático: el envío queda en pendiente.
6. **Nuevo en v2:** en el popup, elige el tenant (seba / kaez) y pega
   tu token de sesión del panel (localStorage `avc-session`, ver
   "Token de sesión del panel" en el popup). Sin ese token, el chequeo
   de D1 no se puede hacer y los envíos se bloquean, así no se duplica.

## Cómo conectar Instagram con tu propia sesión

1. En el popup, pulsa **Conectar Instagram / TikTok / YouTube**.
2. Se abre `contentrewards.com/c/settings` → **Cuentas**.
3. Elige Instagram y autoriza. Cuenta a conectar, por decisión del usuario
   (23 nov 2026): **@elmegaclipgta6** (cuenta Profesional/Creador).
   Sin login con contraseña por Muse y sin códigos: autorizas tú en tu
   propio Chrome, con tu sesión.
4. Vuelve al popup: el estado se verifica en la página de Content Rewards
   (la extensión no lee tus contraseñas ni tu sesión).

TikTok y YouTube se conectan igual, con su OAuth oficial, cuando el usuario
lo autorice en su Chrome. Hoy (23 nov 2026) ninguna red está conectada en
Content Rewards; las contraseñas guardadas de TikTok y YouTube fueron
rechazadas, por eso la vía es el OAuth en su propio navegador.

## Cómo se envía un clip (flujo v1.3.0 — automático)

1. El panel AVC publica el clip por API (como siempre, en la nube), en ES
   o en EN, para seba o para kaez (decisiones 4 y 7).
2. El panel (o Muse) le manda a la extensión un mensaje
   `AVC_CLIP_PUBLISHED` con `clipId, platform, postUrl, publishedAtMs`
   (y `tenant` si es kaez). El `publishedAtMs` es el REAL que marca
   `markPosted` en el Worker; nunca se inventa con `Date.now()`.
3. Si el clip no tiene campaña asignada, el **selector dinámico** le asigna
   la mejor del día para esa plataforma y ese streamer (`rankCampaigns`:
   tarifa NETA × viralidad × presupuesto restante; agotadas, bajo piso
   neto y no famosas fuera). Si ya tiene una asignada por
   `AVC_ASSIGN_CAMPAIGN`, se respeta esa.
4. Valida (v1): link https, plataforma aceptada por la campaña, campaña no
   agotada, tarifa neta ≥ $1/1k, streamer famoso, no duplicado local
   **de ese tenant**, dentro de 30 min.
5. **Puerta del Worker (v2, corregida en v1.3.0):** resuelve la base desde
   `workers.json`, consulta `GET /api/health` (worker corriendo),
   `GET /api/clips/:id` (clip en D1 y **publicado** en esa plataforma:
   `posted` es el requisito, no un duplicado) y
   `GET /api/campaign-submissions` (sin fila `pending`/`submitted` para
   ese clip+plataforma+campaña). Si algo falla, no envía, lo registra
   como `blocked` con el motivo (`worker_down`, `no_panel_session`,
   `clip_not_in_d1`, `not_published_yet`, `duplicate_submission`,
   `duplicate_local`) y el popup muestra el motivo. Si el bloqueo es
   reparable, la alarma de 5 min lo reintenta sola cuando se arregle,
   dentro de la ventana de 30 min.
6. Antes de enviar, registra la fila `pending` en D1 (cola en nube); envía
   por MCP con tu API key (por tenant) y la marca `submitted` con su
   `submission_id`. El popup muestra el estado del worker de ambos
   tenants (corriendo/parado, última consulta) y el estado del clip en D1.
   El formulario manual del popup queda solo como respaldo.

## Qué falta en el Worker (ya existe en código, PENDIENTE DE DESPLEGAR)

La tabla **`campaign_submissions`** y sus rutas **ya existen en el repo**
(migración `panel-backend/migrations/0041_campaign_submissions.sql` +
rutas `GET/POST/PATCH /api/campaign-submissions` en el Worker +
`markPosted` la marca al publicar), pero **aún no están desplegadas en
los Workers vivos**: el coordinador las despliega con `wr_v2.py` (ver
`../panel-backend/CAMPAIGN-SUBMISSIONS-0041.md` para los pasos y la
verificación con `/api/health`). Hasta que devuelva 200, la extensión
informa `submissionsAvailable: false` y usa el fallback a registro local
(anotado como `local_fallback`, nunca fingido).

## Rutas reales del Worker que usa la v2

Verificadas en el código, no inventadas:
- `GET /api/health` — `panel-backend/worker/src/index.ts:1921-1924`,
  sin auth, `{ ok, app: "avc", tenant, worker_running: <boolean>, time }`.
- `GET /api/clips/:id` — `index.ts:2732-2740`, con sesión,
  `{ ok, clip, publish_breakdown, retention }`;
  `publish_breakdown` en `src/r2-retention.ts:77-110`
  (jobs `publish` por plataforma, `state: posted|pending|failed|
  not_enqueued`, `evidence_url`); `retention.state` en
  `r2-retention.ts:114-123` (`subidos|en_cola|sin_mp4|borrado`).
- `GET /api/worker/state` — `index.ts:2402`, con sesión, solo lectura
  (la extensión lo tiene permitido, pero la v2 usa `/api/health`).
- `GET /api/campaign-submissions` — `index.ts` (v1.3.0, migración 0041),
  con sesión, `{ ok, submissions: [...] }`; `POST` registra la fila
  `pending` antes de enviar y `PATCH /api/campaign-submissions/:id` la
  marca `submitted` con su `submission_id`. Es la ÚNICA escritura que la
  extensión hace al Worker (mediada por él; el navegador nunca toca D1
  directo). Ver `../panel-backend/CAMPAIGN-SUBMISSIONS-0041.md`.
- Sesión: `index.ts:221-243` (`requireSession`): cookie `panel-session`
  o `Authorization: Bearer <token>`; el token vive en el panel en
  localStorage (`frontend/dist/app.js:118-120`, clave `avc-session`;
  base del Worker en clave `avc-worker-base`, `app.js:11-13`).
- Mapa usuario → base: `frontend/dist/workers.json`
  (seba → `https://avc.vikingosfoodspa.workers.dev`,
  kaez → `https://avc-kaez.clipsultravirales768.workers.dev`),
  leído por el panel en `app.js:30-43`.

## Lista de bloqueo (no negociable)

- **NUNCA llamar a una ruta que pare, encienda, reinicie o cambie el
  estado del worker:** `POST /api/worker/start` (`index.ts:2458`),
  `POST /api/worker/stop` (`index.ts:2465`),
  `POST /api/worker/run-once` (`index.ts:2475`),
  `POST /api/worker/phase` (`index.ts:2445`),
  `POST /api/worker/event` (`index.ts:2453`). La lista está codificada
  en `lib.js` (`FORBIDDEN_WORKER_PATHS`) y `worker.js` (`workerFetch`)
  la hace cumplir antes de tocar la red; `validate.sh` falla si alguna
  de esas rutas aparece fuera de `lib.js`. La extensión solo hace
  GETs de lectura al Worker, con UNA excepción: POST/PATCH a
  `/api/campaign-submissions` (registro de envío mediado por el Worker,
  ver arriba).
- No contraseñas, no códigos de 6 dígitos, no login con contraseña a Instagram.
- No doble envío: un post, una campaña, una vez.
- No publicar por clicks ni por automatización de navegador.
- No scrapear Content Rewards ni las redes.
- No subir a la Chrome Web Store desde esta v2; modo desarrollador, gratis.
- No prometer que corre sin PC: con Chrome cerrado, se pausa.

## Archivos (v1.3.0)

- `manifest.json` — Manifest V3 v1.3.0, permisos mínimos (storage, alarms;
  **`identity` quitado**: no se usaba, el OAuth va por pestaña).
  Hosts: contentrewards.com, mcp.contentrewards.com, api.whop.com,
  avc-em9.pages.dev (workers.json) y los dos Workers de tenant
  (`*.workers.dev` de seba y kaez, solo como permisos; la base se
  elige en tiempo de ejecución desde workers.json). Sin content scripts.
- `lib.js` — reglas puras: ventana 30 min, idempotencia **por tenant**,
  payload MCP, **ranking dinámico NETO + FAMA** (`rankCampaigns`,
  `selectBestCampaign`, `selectBestCampaignForClip`, `campaignScore`,
  `isFamousCampaign`, `FAME_MIN_FOLLOWERS = 50000`), fichas de brief
  (`CAMPAIGN_BRIEFS`, `getCampaignBrief`, `material: "brief_only"`),
  parseo MCP (`parseMcpResponse`, `parseMcpHttpResponse`,
  `extractSubmissionId`) + puerta del Worker **corregida**
  (`decideWorkerGate`, `parseHealth`, `parseClipState`,
  `FORBIDDEN_WORKER_PATHS`, `CAMPAIGN_SUBMISSIONS_PENDING`,
  `isRetryableBlocked`, `assignmentKey`) + **multi-marketplace**
  (`MARKETPLACES`, `CLIPSTER_GTA6_CAMPAIGNS`,
  `CANDIDATE_CAMPAIGNS_NO_ID`, `netRateForMarketplace`,
  `rankMarketplaceCampaigns`, `selectMarketplaceForClip`,
  `marketplaceAssignmentKey`, `alreadyAssignedMarketplace`) +
  **analíticas** (`summarizeAnalytics`, `summarizeAllAnalytics`,
  `formatUsd`). Probado con Node (ver `validate.sh`).
- `worker.js` — habla con el Worker: `workers.json` → base,
  `GET /api/health`, `GET /api/clips/:id`,
  `GET/POST/PATCH /api/campaign-submissions` (cola en nube) +
  `listAllSubmissions` (analíticas: todas las filas del tenant),
  `workerFetch` con lista de bloqueo. Sin `chrome.*`: testeable en Node
  con fetch stub.
- `background.js` — service worker: **envío automático** (auto-asigna la
  mejor campaña si el clip no tiene), puerta del Worker antes de cada
  envío, claves y tokens **por tenant** (`apiKey_seba/kaez`,
  `panelToken_seba/kaez`, migración de las viejas a seba), mensajes
  (`AVC_CHECK_WORKER`, `AVC_GET_ANALYTICS`), asignación con
  `marketplace` (un clip = un marketplace), registro con motivo de
  bloqueo, alarma cada 5 min: salud de **ambos tenants** + reintento de
  **todos** los bloqueos reparables dentro de la ventana + retomar la
  cola en nube (`?pending=1`).
- `popup.html` / `popup.css` / `popup.js` — popup en español: estado del
  worker de ambos tenants, estado del clip en D1 (con chequeo de
  submissions disponible/fallback), tenant, token de sesión del panel
  **por tenant**, API key **por tenant**, selector de campañas ordenado
  por ranking **neto** (con la no famosa al final, marcada), ficha de
  brief al elegir campaña, historial con `submissionId`, Instagram
  @elmegaclipgta6, **sección Analíticas** (por tenant + total, desde D1;
  saldos "no disponible por API"; "sin datos todavía" si la tabla no
  responde).
- `validate.sh` — valida JSON y JS, la lista de bloqueo y las reglas
  v1 + v2 + v1.2.0 + v1.3.0 con Node (puerta corregida, ranking neto+fama,
  brief, MCP, asignación por plataforma, reintentos, **multi-marketplace
  y analíticas**).

## Pendiente (fuera de v1.3.0)

- Que el usuario conecte TikTok/YouTube/Instagram (@elmegaclipgta6) en su
  Chrome con su OAuth.
- Que el panel AVC emita `AVC_CLIP_PUBLISHED` / `AVC_ASSIGN_CAMPAIGN`
  (hoy el panel no envía esos mensajes; el envío manual desde el popup
  ya funciona y el auto-envío arranca en cuanto el panel los emita).
- **Kaez:** que pegue su propia API key de Content Rewards en el popup
  (tenant kaez). Sin ella sus envíos quedan en `needs_api_key`.
- **Cerebro (decisión 3):** los 4 streamers famosos de campaña ya están
  en el live scan (Dasgasd3m, ArthurTV, BenDaDonnn por twitch/kick;
  Dylan Page solo por VOD-fallback, youtube-only). Bobby Van Riper queda
  fuera por fama. Falta el alta en D1 por tenant con
  `panel-backend/scripts/seed_campaign_influencers.py --apply` (la corre
  el coordinador con su sesión del panel) y que el cerebro corte sus
  primeros clips.
- Re-verificar tarifas y presupuestos el día del envío (el radar del
  23 nov: 28 campañas GTA 6, 18 ≥ $1/1k; el selector marca `stale` si no
  es de hoy).
- **Cobro (decisión 8):** el usuario configura PayPal / cripto / BancoEstado
  en Whop / Content Rewards. No es trabajo de la extensión.
- **En el Worker:** desplegar la migración 0041 + rutas de
  `campaign_submissions` (ya existen en el repo; ver "Qué falta en el
  Worker" arriba y `../panel-backend/CAMPAIGN-SUBMISSIONS-0041.md`).
  Hasta entonces, el chequeo de submissions en D1 usa el fallback local.
- Lectura del estado de cuentas conectadas desde la API oficial (hoy el
  popup lo remite a la página de Content Rewards; no inventa un estado).
- **Verificación en vivo del MCP real queda pendiente**: solo código +
  stubs; no se envió nada real.
- **Candidatas sin UUID (Lauren Hamden, Lucia, Tomo):** verificar en
  Discover/MCP el UUID, la tarifa real (Tomo en conflicto $2,00 vs
  $1,50) y los seguidores (≥ 50.000) antes de entrar; si pasan, añadir
  el streamer a `streamers.yaml` (live scan) y la campaña al ranking.
- **Analíticas en vivo:** verificar que `GET /api/campaign-submissions`
  responde en los Workers desplegados (hoy la ruta existe en código,
  pendiente de desplegar): hasta entonces el popup muestra "sin datos
  todavía". El saldo sin retirar / retirado seguirá siendo "no
  disponible por API" (ningún marketplace verificado lo expone).
