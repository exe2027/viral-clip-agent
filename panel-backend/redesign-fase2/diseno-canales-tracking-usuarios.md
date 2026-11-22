# Diseño — Canales/Salud, Tracking, Usuarios del panel (panel AVC)

- **Fecha:** 2026-11-17
- **Rol:** Rol 6 de 10 — Diseñador Canales/Salud + Tracking + Usuarios del panel (fase 2). Solo especificación; no se escribe código de implementación.
- **Alcance:** solo las tres pestañas nuevas `tab-canales`, `tab-tracking`, `tab-usuarios` de `frontend/dist/index.html` + `app.js`. Fuera de alcance: Login, Worker, Cola, Cuentas, Avisos (salvo el bug del punto rojo, §4), Ajustes, fórmulas de viralidad/SEO, puertas de publicación (`publishGate`), y cualquier cambio en el Worker o en las migraciones.
- **Fuentes leídas:** `redesign-fase2/inventario.md` (mapa endpoint-por-endpoint; manda en qué dato existe y qué dato no), `design-system/avc-panel/OVERRIDES.md` §8 (variante GTA 6 / Vice City; manda sobre `MASTER.md`), `design-system/avc-panel/tokens-gta6.css` y `tokens-gta6.json`, `frontend/dist/index.html` y `frontend/dist/app.js` (ids existentes, pestaña Avisos actual), `worker/src/index.ts` (endpoints reales), `worker/src/jobs.ts` (`publishGate`: topes y franja fijos), `trazabilidad-live-prioridad.md` §2 (los 11 pasos), migraciones `0006` (`panel_users`), `0009` (`clip_events`).
- **Regla de oro (del inventario, se respeta):** SIN ENDPOINT = estado vacío real + acción pendiente, NUNCA datos inventados. Un valor que no devuelve ningún endpoint real se muestra como «sin datos», no con un cero, un «OK» ni un ejemplo.
- **Sin nombres personales visibles.** En estas tres pestañas solo aparecen los usernames `seba` y `chacal` (§3). Ningún nombre real, ningún valor interno de tenant y ningún texto que identifique a una persona se renderiza en el panel.

## 0. Contrato global

### 0.1 Pestañas nuevas y orden

Orden del nav tras este diseño (móvil y escritorio, mismo orden):

`Worker · Cola · Cuentas · Canales · Tracking · Usuarios · Avisos · Ajustes`

- Las 5 pestañas existentes conservan su orden relativo, sus ids, sus hashes y su comportamiento. Las 3 nuevas se insertan entre Cuentas y Avisos.
- **Excepción explícita al inventario §3.3:** añadir pestañas exige ampliar `TAB_NAMES` con `"canales"`, `"tracking"`, `"usuarios"` (en ese orden, tras `"cuentas"`) y sus hashes `#canales`, `#tracking`, `#usuarios` en `switchTab` / `loadTab`. Es la única ampliación permitida de esa lista; los 5 valores existentes no se renombran, no se reordenan y no se eliminan. Sin esa ampliación las pestañas nuevas no existen.
- Activación del tema: `<html data-theme="gta6">` + `tokens-gta6.css` cargado DESPUÉS de `tokens.css` (OVERRIDES §8.4). Con el tema activo, `<meta name="theme-color" content="#100a1a">` (el `#0b0d12` actual es el de la base sin tema).
- Cada pestaña nueva es una `<section role="tabpanel" aria-labelledby="tabbtn-<nombre>">` con su botón `role="tab" aria-selected` en `#tabs`, igual que las existentes. Pestaña activa: fondo `--gradient-sunset`, texto blanco 700, `aria-selected="true"`, barra inferior 3 px `--color-neon-amber` (nunca solo color).

### 0.2 Tokens GTA 6 que usan las tres pestañas (todos de `tokens-gta6.css`; cero hex fuera de tokens)

| Uso | Token |
|---|---|
| Fondo de página | `--color-background: #100a1a` |
| Tarjeta | `--color-surface: #261a38`, borde 1 px `--color-border: #7c6aa0`, radio `--radius-card: 16px`, padding `--space-4`, `box-shadow: var(--shadow-card)`; hover `var(--shadow-elevated)` |
| Input / select | Fondo `--color-surface-input: #150d24`, borde `--color-border`, texto `--color-text`, `font-size: 16px`, `min-height: 44px`, radio `--radius-control: 10px` |
| Texto principal / secundario | `--color-text: #e8eaf0` / `--color-text-dim: #b8b3c9` |
| Enlace | `--color-link: #f9a8d4` (9.00:1 sobre tarjeta) |
| Foco visible | `:focus-visible { outline: 3px solid var(--color-focus: #fbbf24); outline-offset: 2px; }` en todo control |
| Botón primario | Fondo `--gradient-sunset` (`135deg #6d28d9 → #9d174d → #c2410c`), texto blanco, `min-height: 44px`, radio 10 px, `box-shadow: var(--shadow-neon-sunset)`; hover `--gradient-sunset-hover`; disabled opacidad .55 + texto de carga |
| Botón secundario (ghost) | Fondo transparente, borde `--color-border`, texto `--color-text`; hover borde `--color-neon-pink-text` + brillo 1.12 |
| Botón peligro | Fondo `--color-danger-bg: #7f1d1d`, texto blanco |
| Badge (Avisos, §4) | Fondo `--color-badge-bg: #be185d`, texto blanco (6.04:1), `min-width: 20px`, `height: 20px`, radio 999 px, tope «9+», oculto en 0 |
| Pills de estado | `.pill.ok` (`--color-ok-bg` / `--color-ok-fg`), `.pill.bad` (`--color-bad-bg` / `--color-bad-fg`), `.pill.warn` (`--color-warn-bg` / `--color-warn-fg`), `.pill.neutral` (fondo `--color-surface-input`, texto `--color-text-dim`); nuevas temáticas `.pill.sunset` (`#fbcfe8` / `#500724`), `.pill.violet` (`#ede9fe` / `#4c1d95`), `.pill.ember` (`#ffedd5` / `#7c2d12`); siempre con texto, nunca solo color; `padding: 2px 9px`, radio 999 px, `font-size: 12px`, `font-weight: 600` |
| Plataforma (punto / borde, nunca texto corrido con el hex crudo) | YouTube `--color-platform-youtube: #ff4e45` · TikTok `--color-platform-tiktok: #25f4ee` · Instagram `--color-platform-instagram: #f77737` (rosa `--color-platform-instagram-pink: #f472b6`) · Facebook `--color-platform-facebook: #60a5fa` |
| Tipografía | Títulos `var(--font-display)` ('Barlow Condensed', fallback 'Arial Narrow' / system-ui), 700, mayúsculas; h2 18 px, `line-height: 1.1`; cuerpo `var(--font-body)` ('Barlow', system-ui), 16 px, `line-height: 1.5`; fechas y números con `font-variant-numeric: tabular-nums`; mono `var(--font-mono)` solo para valores técnicos (claves, URLs) |
| Espaciado / táctil / motion | Escala `--space-1..6` (4 / 8 / 12 / 16 / 24 / 32 px); `min-height: 44px` en botones, pestañas, inputs, selects; gap entre controles mínimo `--space-2` (8 px); transiciones 150–250 ms `ease-out`; `button:hover { filter: brightness(1.12); }`, `button:active { transform: scale(.98); }`; `prefers-reduced-motion: reduce` anula transiciones y animaciones; safe-area con `env(safe-area-inset-*)` |
| Iconos | Solo SVG inline, `aria-hidden="true"`. Prohibidos emojis y glifos como iconos; el punto de estado es un `.dot` CSS de 8 px (10 px en el semáforo) |

### 0.3 Reglas globales para las tres pestañas (OVERRIDES §§1–4, 7)

- **Carga:** al entrar a cada pestaña, skeleton (bloques `--color-surface-input`, sin shimmer bajo `prefers-reduced-motion`) con `aria-busy="true"` hasta que llegue el `fetch`; nunca pantalla vacía ni salto de layout. Si el `fetch` falla: título de error + motivo + botón «Reintentar» (reintenta el `load*` de esa pestaña).
- **Botones asíncronos:** `disabled` + texto de carga («Actualizando…», «Cargando…») hasta resolver. Doble toque no dispara doble `fetch`.
- **Errores:** junto al campo o al bloque que falló, con `role="alert"`; `#msg` global con `role="status"` (éxito, auto-limpieza 5 s) / `role="alert"` (error, con ×). Fechas en es-CL, zona `America/Santiago`, con el `fmtDate` existente; si no hay fecha, se muestra «—», nunca una fecha inventada.
- **Estados siempre como pill con texto.** Nunca solo color, nunca texto plano.
- **Textos en español.** Todos los textos de interfaz, errores y vacíos están en español (ver §5, tabla consolidada para copiar).
- **Layout móvil-primero:** a ≤ 640 px todo apila en una columna a ancho completo; sin scroll horizontal a 360 px. En escritorio, contenido centrado de 980 px; las tarjetas de plataforma y de usuario pueden ir en rejilla de 2 columnas, nunca más estrechas que lo legible.

---

## 1. Canales / Salud — `#tab-canales` (PARCIAL: solo salud genérica real)

### 1.1 Qué dato es real y qué dato no existe (tabla de verdad)

| Dato que muestra la pestaña | Endpoint real | Estado |
|---|---|---|
| API conectada / no responde | `GET /api/health` (sin auth) → `{ok, app, worker_running, time}` | **Real.** Es lo que ya muestra `#healthLine` |
| Worker en marcha / parado, desde cuándo, última nota | `GET /api/worker/state` (sesión) → `{running, startedAt, lastNote, activity[]}` | **Real.** Es lo que ya muestra `#tab-worker` |
| Cuenta API por plataforma (Conectada / Sin credencial / No conectada) y última verificación | `GET /api/accounts` (sesión) → `verify_ok, has_credential, verify_message, verified_at, credential_masked` | **Real**, pero es la cuenta API, no el canal social. Se muestra como «Cuenta API», nunca como «canal conectado» |
| Tope diario fijo, plan del día, franja, espaciamiento | Ninguno (constantes verificadas en `worker/src/jobs.ts`, `publishGate`) | **Real como constante del Worker**, no como dato vivo. Se muestra etiquetado como valor fijo, nunca como medición |
| Salud por canal/plataforma (latencia, errores del adaptador, último intento por plataforma) | **Ninguno.** `worker/src/publish/` no expone ruta de salud | **Sin datos** |
| Cuota diaria usada hoy / restante hoy por plataforma | **Ninguno.** El conteo `publishedToday` solo existe dentro de `publishGate`; ningún endpoint lo devuelve | **Sin datos** |
| Clips publicados en total y en los últimos 7 días, por plataforma | **Ninguno.** Tabla `clip_stats` sin endpoint; `GET /api/clips` solo devuelve estados `en_carpeta / aprobado / programado`, nunca publicados | **Sin datos** |

### 1.2 Layout

```
<section id="tab-canales">
  <div class="card">                                    <!-- resumen general -->
    <h2>Canales y salud</h2>
    <p class="dim">Salud del panel y de cada plataforma. Lo que aún no mide el Worker se muestra como «sin datos», nunca como «todo bien».</p>
    <div id="channelSummary" aria-busy="true|false">   <!-- semáforo API + Worker + hora -->
    <p class="dim" id="channelUpdatedAt" role="status"></p>
    <button id="btnRefreshChannels" type="button" class="ghost">Actualizar</button>
  </div>
  <div id="channelList" aria-busy="true|false">        <!-- 4 tarjetas, una por plataforma -->
    <!-- por plataforma: <article class="card channel-card" data-channel="youtube|tiktok|instagram|facebook"> -->
  </div>
</section>
```

- **Móvil (≤ 640 px):** resumen arriba, luego las 4 tarjetas apiladas a ancho completo, en este orden fijo: YouTube, TikTok, Instagram, Facebook.
- **Escritorio:** el resumen a ancho completo; las 4 tarjetas en rejilla de 2 columnas.
- Cada tarjeta lleva su punto de plataforma (`.dot` con `--color-platform-*`), su nombre, su pill de salud y sus filas de dato. Nada de tabla en móvil.

### 1.3 Tarjeta resumen (datos reales, arriba de todo)

Contenido (todo sale de `GET /api/health` + `GET /api/worker/state`, los mismos datos del header y de `#tab-worker`; no se vuelve a pedir nada que no exista):

- Línea 1 — semáforo + texto, nunca solo color: punto 10 px + pill `.pill.ok` «EN MARCHA» / `.pill.warn` «PARADO» / `.pill.bad` «API CAÍDA», con el mismo criterio que `#workerLight` (`worker_running` con algún valor verdadero → en marcha; `GET /api/health` con error → API caída).
- Línea 2 — texto exacto: «API conectada · Worker: en marcha» o «API conectada · Worker: parado» o «API no responde» (los mismos textos de `#healthLine`).
- Línea 3 — «Última comprobación: <fecha y hora>» (`time` de `/api/health`, con `fmtDate`). Si el `fetch` falla, la línea dice «Última comprobación: sin datos» y aparece el botón «Reintentar».
- Botón ghost «Actualizar» (`#btnRefreshChannels`, 44 px): reintenta `loadChannels()` (salud + cuentas API). Durante la carga, `disabled` + texto «Actualizando…».
- **No se muestra** ningún contador de canales «sanos» (p. ej. «3 de 4 canales bien»): sin endpoint de salud por canal, ese resumen sería inventado.

### 1.4 Tarjeta por plataforma (las 4 iguales de estructura, distinto contenido real)

```
<article class="card channel-card" data-channel="youtube" aria-labelledby="channel-title-youtube">
  <div class="channel-head">
    <span class="dot youtube" aria-hidden="true"></span>
    <h3 id="channel-title-youtube">YouTube</h3>
    <span class="pill neutral">Sin datos</span>              <!-- salud del canal: SIN endpoint -->
  </div>
  <dl class="channel-rows">
    <div><dt>Cuenta API</dt><dd><span class="pill ok|bad|warn">Conectada | Sin credencial | No conectada</span> · <span>verificado: <fecha> / motivo</span></dd></div>
    <div><dt>Cuota de hoy</dt><dd>Usada hoy: sin datos · Restante: sin datos · Tope: 5 al día (fijo del Worker)</dd></div>
    <div><dt>Publicados</dt><dd>Total: sin datos · Últimos 7 días: sin datos</dd></div>
    <div><dt>Ventana de publicación</dt><dd>18:00–24:00 (hora de Chile) · Espaciamiento mínimo: 2 h entre subidas</dd></div>
  </dl>
  <p class="dim channel-note">Sin datos de salud para este canal. …</p>
  <div class="row"><button type="button" class="ghost" data-goto-cuentas>Ir a Cuentas</button></div>
</article>
```

| Plataforma | Punto | Tope fijo del Worker (constante, no medición) | Plan del día (constante) | Cuenta API que la alimenta (`GET /api/accounts`) |
|---|---|---|---|---|
| YouTube | `--color-platform-youtube` | 5 al día | 3 | Cuenta API `platform: youtube` |
| TikTok | `--color-platform-tiktok` | 3 al día | 2 | Cuenta API `platform: tiktok` |
| Instagram | `--color-platform-instagram` | 3 al día | 2 | Cuenta API unificada `platform: meta` (la misma que Facebook) |
| Facebook | `--color-platform-facebook` | 3 al día | 2 | Cuenta API unificada `platform: meta` (la misma que Instagram) |

Reglas por campo (obligatorias):

- **Salud del canal — pill `.pill.neutral` «Sin datos».** Es el estado de las 4 tarjetas hoy. Nunca `.pill.ok` «Saludable» ni `.pill.bad` «Caído»: no hay endpoint que lo mida y un «OK» inventado escondería un canal roto. Texto bajo la pill, exacto: «Sin datos de salud para este canal. El Worker aún no expone la salud por plataforma.»
- **Cuenta API — pill real, solo 4 estados (v15).** Se deriva de `GET /api/accounts` con la misma tabla que Cuentas (`verify_ok === 1` → «Conectada» `.pill.ok`; `!has_credential` → «Sin credencial» `.pill.bad`; resto → «No conectada» `.pill.warn`). «Sin verificar» no se renderiza. Junto a la pill: `verify_message` si existe, y «verificado: <fecha>» solo si hay `verified_at`; si no hay cuenta API para esa plataforma en la respuesta, la fila dice «Sin cuenta API todavía. Se crea al migrar los datos del panel anterior o desde el Worker.» y el botón «Ir a Cuentas» lleva a `#tab-cuentas`.
- **Cuota de hoy — «sin datos» en usada y restante, tope fijo visible.** Texto exacto por tarjeta: «Usada hoy: sin datos · Restante: sin datos · Tope: 5 al día (fijo del Worker)» (YouTube) o «… Tope: 3 al día (fijo del Worker)» (resto). **Prohibido** calcular «usada» contando clips de `GET /api/clips`: esa lista excluye los publicados, así que el conteo sería falso. **Prohibido** mostrar el valor de Ajustes (`daily_limit_*`) como cuota: el Worker no lo lee (nota «Aún no lo aplica el Worker» de Ajustes, que no se quita).
- **Publicados — «sin datos» en total y en 7 días.** Texto exacto: «Total: sin datos · Últimos 7 días: sin datos». **Prohibido** derivarlo del score, de `publish_states` o de la Cola: un clip en Cola no es un clip publicado.
- **Ventana y espaciamiento — constantes del Worker, etiquetadas como tales.** Texto exacto (igual en las 4 tarjetas): «18:00–24:00 (hora de Chile) · Espaciamiento mínimo: 2 h entre subidas». Si el Worker cambia `publishGate`, cambia este texto; el front no lo calcula.
- **Nota al pie de cada tarjeta** (`.dim`, texto exacto): «Sin datos de salud para este canal. Cuando el Worker exponga la salud por plataforma, aparecerá aquí la última comprobación, los errores y la última publicación.»
- **Botón «Ir a Cuentas»** (ghost, 44 px, `data-goto-cuentas`): cambia a la pestaña Cuentas (`switchTab("cuentas")`). No es un botón de «conectar canal»: no existe endpoint de creación de cuentas sociales ni de cuentas API.

### 1.5 Estados de la pestaña

| Estado | Qué se muestra |
|---|---|
| Cargando | Skeleton: 1 bloque resumen + 4 tarjetas esqueleto, `aria-busy="true"` en `#channelSummary` y `#channelList` |
| Sin cuentas API (lista de `GET /api/accounts` vacía) | Las 4 tarjetas se muestran igual, con la fila «Sin cuenta API todavía…». No es un empty state de pestaña: la salud «sin datos» ya es el contenido |
| Error de red / 401 en salud o cuentas | Título «No se pudo cargar la salud de los canales», motivo del error, botón «Reintentar» (`#retryChannels`). El badge y el semáforo del header no se tocan: siguen mostrando su último estado conocido, nunca un 0 inventado |
| Éxito | Resumen + 4 tarjetas como en §1.3–1.4; `#channelUpdatedAt` con «Actualizado: <fecha y hora>» |

### 1.6 Acciones pendientes (no son trabajo de front; se listan para el backend)

1. Endpoint de salud por plataforma (latencia, último error del adaptador, última publicación por plataforma) en el Worker — sin él, la pill seguirá en «Sin datos».
2. Endpoint de cuota usada/restante hoy por plataforma (el conteo que hoy solo vive dentro de `publishGate`) — sin él, «Usada hoy» seguirá en «sin datos».
3. Endpoint de lectura de `clip_stats` (publicados total y últimos 7 días por plataforma) — sin él, «Publicados» seguirá en «sin datos».
4. `POST /api/accounts` (crear cuenta API) — sin él, «Ir a Cuentas» no puede ofrecer crear la cuenta que falta.

### 1.7 Ids nuevos que necesita el desarrollador (Canales)

| Id / atributo | Tipo | Uso |
|---|---|---|
| `tab-canales` | `<section>` | Panel de la pestaña (existe en `index.html`, nuevo) |
| `tabbtn-canales` | `<button data-tab="canales">` | Botón del nav (nuevo) |
| `channelSummary` | `<div>` | Resumen API + Worker (§1.3) |
| `channelUpdatedAt` | `<p role="status">` | «Actualizado: <fecha y hora>» |
| `btnRefreshChannels` | `<button>` | Reintenta `loadChannels()` |
| `channelList` | `<div>` | Contenedor de las 4 tarjetas |
| `retryChannels` | `<button>` (dinámico, creado en el error) | Reintenta `loadChannels()` tras fallo |
| `data-channel="youtube\|tiktok\|instagram\|facebook"` | atributo por tarjeta | Identifica la plataforma sin crear ids por tarjeta |
| `data-goto-cuentas` | atributo por botón | «Ir a Cuentas» → `switchTab("cuentas")` |
| `loadChannels()` | función nueva en `app.js` | `GET /api/health` + `GET /api/worker/state` + `GET /api/accounts`; pinta resumen y tarjetas. No crea endpoints nuevos |

---

## 2. Tracking — `#tab-tracking` (EXISTE como lectura; nueva pestaña propia)

### 2.1 Qué dato es real y qué dato no existe

| Dato | Endpoint real | Estado |
|---|---|---|
| Lista de clips para elegir cuál seguir | `GET /api/clips` → solo `archived=0` y `status IN ('en_carpeta','aprobado','programado')`, orden `created_at DESC` | **Real.** Misma fuente que la Cola; no se reordena por prioridad en el front (la API ordena por fecha) |
| Eventos de seguimiento de un clip | `GET /api/clips/:id/tracking` → `events[]` de `clip_events`, orden `at ASC`, con `step, actor, message, failed` | **Real.** Es lo que ya muestra `#clipTracking` en el detalle de Cola |
| Origen LIVE / VOD del clip | `GET /api/clips` / `GET /api/clips/:id` → campo `origin` (`'vod'` por defecto) | **Real** como campo de la fila; el segmento LIVE solo se llena cuando exista el cutter live |
| Paso actual, tiempo en el paso, barra «paso X de 11» | **Ninguno.** No hay endpoint de «paso actual» ni de «pasos completados» | **Derivado solo de los eventos**: el paso actual es el del último evento con datos; los pasos sin evento son «Sin registro» (§2.4) |
| Los 11 pasos como garantía | **Ninguno.** La API devuelve los eventos que existan, sin garantizar 11 | **No se rellenan.** Si la API devuelve 3 eventos, se muestran 3 eventos; los otros 8 pasos quedan en «Sin registro» |

### 2.2 Layout (móvil-primero, timeline vertical)

```
<section id="tab-tracking">
  <div class="card">
    <h2>Seguimiento</h2>
    <p class="dim">El recorrido de cada clip, desde que se detecta hasta que se publica. Solo se muestra lo que el Worker registró, con su hora y su motivo.</p>
    <label for="trackingClipSelect">Clip</label>
    <select id="trackingClipSelect">…</select>                 <!-- opciones desde GET /api/clips -->
    <div class="row" id="trackingFilters" role="group" aria-label="Filtrar por origen">
      <button type="button" data-tracking-filter="all" class="active" aria-pressed="true">Todos</button>
      <button type="button" data-tracking-filter="live" aria-pressed="false">LIVE</button>
      <button type="button" data-tracking-filter="vod" aria-pressed="false">VOD</button>
    </div>
    <button id="btnRefreshTracking" type="button" class="ghost">Actualizar</button>
  </div>
  <div class="card" id="trackingDetailCard">
    <div class="tracking-clip-head">
      <h3 id="trackingClipTitle" tabindex="-1">Clip</h3>
      <span id="trackingClipOrigin"></span>                     <!-- pill .pill.sunset LIVE / .pill.violet VOD -->
      <span id="trackingClipStatus"></span>                     <!-- pill de estado, misma STATUS_MAP que Cola -->
    </div>
    <p class="dim" id="trackingProgress" role="status"></p>     <!-- «Paso X de 11 con registro» -->
    <ol class="timeline" id="trackingTimeline" aria-busy="false"></ol>
    <div id="trackingOtherEvents" class="hidden">
      <h3>Otros eventos</h3>
      <ol class="timeline" id="trackingOtherList"></ol>
    </div>
  </div>
</section>
```

- **Móvil:** selector a ancho completo arriba; filtros como 3 botones en fila (44 px cada uno, `aria-pressed`); debajo, la tarjeta del clip y la timeline vertical a ancho completo.
- **Escritorio:** mismo orden, contenido centrado de 980 px; la timeline no se convierte en tabla ni en dos columnas: el seguimiento se lee de arriba abajo.
- **El detalle de Cola no se toca:** `#clipTracking` dentro de `#clipDetailCard` sigue mostrando los eventos tal cual (con su pill «Fallo» y su «Último paso»). Esta pestaña es otra vista de los mismos datos, no un reemplazo.

### 2.3 Selector de clip y filtros

- `#trackingClipSelect` (`<select>`, 16 px, 44 px, `label for` obligatorio): opciones desde `GET /api/clips`, texto de opción = título del clip (`title || seo_title || "Clip <id>"`), valor = `id`. Orden = el de la API (`created_at DESC`); no se reordena.
- Filtros `data-tracking-filter="all|live|vod"`: filtran las **opciones** del selector por `origin` (`'live'` → LIVE, resto → VOD). Son botones con `aria-pressed`, no un segundo select. El filtro activo lleva fondo `--gradient-sunset` + texto blanco + `aria-pressed="true"` (nunca solo color).
- Al cambiar el selector o el filtro: se carga `GET /api/clips/:id/tracking` del clip elegido y se pinta la timeline. Si el filtro deja el selector sin opciones, el selector muestra «Sin clips de este origen» y la tarjeta de detalle muestra su empty state (§2.6).
- `#btnRefreshTracking` (ghost, 44 px): recarga la lista de clips y la timeline del clip seleccionado, sin cambiar el clip elegido.
- **Clip sin elegir todavía:** al entrar a la pestaña se selecciona automáticamente el primer clip de la lista (el más reciente, como hace la Cola al no tener selección: ninguno). Texto en la tarjeta hasta elegir: «Elige un clip para ver su seguimiento.»

### 2.4 La timeline de 11 pasos (el corazón de la pestaña)

Los 11 pasos son los de `trazabilidad-live-prioridad.md` §2 (diseño aprobado). Sus **nombres** son texto fijo de diseño; sus **datos** (hora, actor, motivo, estado) salen solo de los eventos de la API. Un paso sin evento no tiene hora ni motivo: tiene «Sin registro».

| # | Paso (texto fijo) | Quién lo registra (diseño) | Estado del paso según los eventos |
|---|---|---|---|
| 1 | Detectado | watcher | Completado si hay evento de detección; si no, «Sin registro» |
| 2 | Pico detectado | watcher / scorer | Idem |
| 3 | Admisión a la cola | watcher | Idem; el motivo del evento es el motivo de admisión o de descarte |
| 4 | En cola de corte | worker | Idem |
| 5 | Cortando | worker | Idem |
| 6 | Subtítulos | worker | Idem; «sin voz» solo si el evento lo dice |
| 7 | Paquete viral y scores | worker | Idem |
| 8 | Listo para revisión | panel | Idem |
| 9 | Aprobado / rechazado | usuario | Idem; el motivo del rechazo es el `message` del evento, nunca un motivo inventado |
| 10 | En cola de publicación | publisher | Idem; un evento «Publicación frenada en <plataforma>: <motivo>» deja este paso en «Fallido» con ese motivo |
| 11 | Publicado / fallido | publisher | Idem; «Publicado en <plataforma>: <enlace>» → «Publicado»; un fallo → «Fallido» con su motivo |

Estructura de cada paso en la timeline (vertical, como `#clipTracking` actual):

```
<li class="tracking-step" data-step="1">
  <span class="dot ok|bad|neutral" aria-hidden="true"></span>
  <span class="tracking-step-num">1</span>
  <div>
    <p><b>Detectado</b> <span class="pill ok|bad|neutral">Completado | En curso | Fallido | Sin registro</span></p>
    <p class="dim"><time>17 nov 2026, 18:42</time> · watcher</p>     <!-- o «Sin hora» si no hay evento -->
    <p class="tl-msg">Mensaje del evento, tal cual lo devuelve la API.</p>
    <!-- si no hay evento: -->
    <p class="dim">Sin registro todavía.</p>
  </div>
</li>
```

Reglas por paso (obligatorias):

- **Pill de estado del paso:** «Completado» (`.pill.ok`) si hay al menos un evento no fallido que mapea a ese paso; «Fallido» (`.pill.bad`) si el último evento de ese paso tiene `failed === 1`; «En curso» (`.pill.warn`) solo para el paso del último evento con datos, cuando el clip aún no está en estado terminal; «Sin registro» (`.pill.neutral`) si ningún evento mapea a ese paso. El estado nunca es solo color: siempre lleva texto.
- **Hora:** `fmtDate(event.at)` (es-CL, `America/Santiago`) del evento que mapea al paso; si hay varios, la del último. Sin evento: el texto es «Sin hora», nunca la hora de otro paso ni la hora de carga.
- **Actor y motivo:** `event.actor` y `event.message` tal cual los devuelve la API (con `esc()`), sin reescribirlos ni resumirlos. Si el evento no trae actor, no se escribe actor.
- **«Último paso»:** la marca que ya usa `#clipTracking` («Último paso» bajo el último evento en orden de la API) se conserva aquí también: va bajo el paso que contiene el último evento con datos, no bajo el paso 11 salvo que el último evento sea del paso 11.
- **`#trackingProgress`:** texto exacto «Paso <n> de 11 con registro» donde <n> es el número de pasos con al menos un evento; si no hay ningún evento: «Sin seguimiento todavía.» (mismo texto que el detalle de Cola). **Prohibido** decir «Paso X de 11 completado» cuando X cuenta pasos sin evento: solo cuentan los pasos con registro real.
- **Pill de origen del clip:** LIVE → `.pill.sunset` «LIVE»; VOD → `.pill.violet` «VOD». Es el `origin` de la fila del clip; si el campo viene vacío, no se muestra pill de origen y no se asume VOD en el texto (el filtro lo trata como VOD solo para listar, sin afirmarlo en la cabecera).
- **Pill de estado del clip:** la misma `STATUS_MAP` de Cola (Pendiente / En cola / Publicado / Fallido / Frenado). No se crea un mapa nuevo.

### 2.5 Mapeo evento → paso (cómo se decide, sin inventar)

- El Worker hoy solo escribe dos valores de `step`: `«publicado»` y `«aviso»` (`jobs.ts`, `index.ts`). `«publicado»` mapea al paso 11 si el mensaje empieza por «Publicado en», y al paso 10 con estado «Fallido» si el mensaje empieza por «Publicación frenada». `«aviso»` no mapea a ningún paso del pipeline: va a «Otros eventos» (§2.6).
- **Claves canónicas propuestas (decisión pendiente con el backend, no un hecho):** `detectado`, `pico`, `admision`, `cola_corte`, `cortando`, `subtitulos`, `paquete`, `revision`, `decision`, `cola_publicacion`, `publicado`. El front solo mapea por igualdad exacta con esa lista; un `step` que no esté en la lista ni sea `«publicado»` / `«aviso»` va a «Otros eventos» con su texto tal cual. El front no adivina el paso por el contenido del mensaje.
- **Consecuencia honesta, visible en el diseño:** hasta que el cerebro / el puente escriban eventos con esas claves, la mayoría de los pasos mostrarán «Sin registro» aunque el clip exista. Eso es correcto: es el estado real de los datos, no un fallo del diseño. La pestaña no finge un pipeline completo.

### 2.6 Otros eventos, vacíos y errores

- **Otros eventos (`#trackingOtherEvents`, oculto si no hay):** lista con el mismo formato de fila que `#clipTracking` (punto, hora, `step` tal cual, pill «Fallo» si `failed`, actor + mensaje). Título (h3): «Otros eventos». Texto introductorio (`.dim`): «Eventos registrados que no corresponden a un paso del pipeline. Se muestran tal cual los devuelve el Worker.»
- **Clip sin eventos:** los 11 pasos en «Sin registro» con «Sin registro todavía.» cada uno, `#trackingProgress` con «Sin seguimiento todavía.», y la sección de otros eventos oculta. No se muestra un error: que un clip no tenga eventos es un estado válido.
- **Sin clips (lista de `GET /api/clips` vacía):** la tarjeta de detalle muestra título «Sin clips para seguir» y texto «Los clips aparecen aquí cuando el cerebro los detecta o cuando se migran del panel anterior.» + botón ghost «Ir al Worker». El selector queda deshabilitado con la opción «Sin clips».
- **Error de carga:** título «No se pudo cargar el seguimiento», motivo, botón «Reintentar» (`#retryTracking`). Si falla solo la timeline pero la lista cargó, el error va dentro de `#trackingDetailCard` y el selector sigue funcionando.
- **Cargando:** skeleton de 3 líneas en el selector y de 11 filas cortas en la timeline, `aria-busy="true"`.

### 2.7 Ids nuevos que necesita el desarrollador (Tracking)

| Id / atributo | Tipo | Uso |
|---|---|---|
| `tab-tracking` | `<section>` | Panel de la pestaña (nuevo) |
| `tabbtn-tracking` | `<button data-tab="tracking">` | Botón del nav (nuevo) |
| `trackingClipSelect` | `<select>` | Selector de clip (opciones desde `GET /api/clips`) |
| `trackingFilters` | `<div role="group">` | Grupo de los 3 filtros por origen |
| `data-tracking-filter="all\|live\|vod"` | atributo por botón | Filtro de opciones del selector |
| `btnRefreshTracking` | `<button>` | Recarga lista + timeline del clip elegido |
| `trackingDetailCard` | `<div class="card">` | Tarjeta del clip + timeline |
| `trackingClipTitle` | `<h3 tabindex="-1">` | Título del clip elegido (foco al cambiar de clip, como `#clipTitle`) |
| `trackingClipOrigin` | `<span>` | Pill LIVE (`.pill.sunset`) / VOD (`.pill.violet`) |
| `trackingClipStatus` | `<span>` | Pill de estado (`STATUS_MAP` de Cola) |
| `trackingProgress` | `<p role="status">` | «Paso <n> de 11 con registro» / «Sin seguimiento todavía.» |
| `trackingTimeline` | `<ol class="timeline">` | Los 11 pasos, en orden fijo 1–11 |
| `trackingOtherEvents` | `<div>` (oculto si no hay) | Sección «Otros eventos» |
| `trackingOtherList` | `<ol class="timeline">` | Eventos sin paso asignado, en orden de la API |
| `retryTracking` | `<button>` (dinámico) | Reintenta la carga tras fallo |
| `loadTracking()` / `loadTrackingTimeline(clipId)` | funciones nuevas en `app.js` | `GET /api/clips` + `GET /api/clips/:id/tracking`; no tocan `#clipTracking` ni `selectClip()` |

---

## 3. Usuarios del panel — `#tab-usuarios` (SIN ENDPOINT salvo login)

### 3.1 Qué dato es real y qué dato no existe

| Dato | Endpoint real | Estado |
|---|---|---|
| Usuario con sesión actual | `GET /api/auth/me` (sesión) → `{tenant, sub}` | **Real.** `sub` es el username de la sesión; es lo que ya muestra `#whoLine` |
| Lista de usuarios del panel | **Ninguno.** `POST /api/auth/login`, `GET /api/auth/me` y `POST /api/auth/logout` leen `panel_users` pero no hay listar / crear / editar / desactivar | **Sin endpoint** |
| Crear, editar o desactivar un usuario desde el panel | **Ninguno.** Hoy es por SQL (`scripts/seed-users.sql` + `gen-password-hash.mjs`), fuera del panel | **Sin endpoint** |

### 3.2 La lista (estado objetivo, cuando exista el endpoint)

La lista tiene **dos filas**, una por cada cuenta de login sembrada en la instalación. Cada fila muestra **solo** tres cosas: el username, la marca de sesión actual (si es la sesión en curso) y el estado activo. Nada más.

```
<section id="tab-usuarios">
  <div class="card">
    <h2>Usuarios del panel</h2>
    <p class="dim">Quién puede entrar a este panel. Cada usuario ve solo su propio contenido.</p>
    <div id="panelUserList" aria-busy="true|false">
      <!-- por usuario: -->
      <div class="user-row">
        <span class="user-avatar" aria-hidden="true">SE</span>   <!-- iniciales del username, ver §3.3 -->
        <div class="user-text">
          <p class="user-name">seba</p>
          <p class="dim user-meta">Sesión actual</p>              <!-- solo en la fila de me.sub -->
        </div>
        <span class="pill ok">Activo</span>
      </div>
      <div class="user-row">
        <span class="user-avatar" aria-hidden="true">CH</span>
        <div class="user-text">
          <p class="user-name">chacal</p>
        </div>
        <span class="pill ok">Activo</span>
      </div>
    </div>
    <p class="dim" id="panelUserNote">Los usuarios se crean fuera del panel. …</p>
  </div>
</section>
```

- **Fila 1 — username `seba`, pill `.pill.ok` «Activo».** Si `GET /api/auth/me` devuelve ese username como `sub`, su fila añade la línea `.dim` «Sesión actual» bajo el username. Si la sesión actual es la otra fila, la marca va en la otra fila. La marca nunca se escribe a mano: sale de comparar cada username con `me.sub`.
- **Fila 2 — username `chacal`, pill `.pill.ok` «Activo».** Sin línea de sesión salvo que sea la sesión actual.
- **Orden fijo:** `seba` arriba, `chacal` debajo. No se ordena por fecha ni por actividad: no hay endpoint que devuelva esos campos para ordenar.
- **Avatar:** iniciales del username en mayúsculas (`SE`, `CH`) dentro de un círculo con fondo `--gradient-sunset` y texto blanco. Son las iniciales del username visible, no de un nombre real: no se inventa ninguna inicial que no salga del username.
- **Texto al pie de la lista** (`#panelUserNote`, `.dim`, texto exacto): «Los usuarios se crean fuera del panel. Si necesitas un usuario nuevo o desactivar uno, pide que lo hagan en el Worker.»
- **Móvil:** filas a ancho completo, avatar a la izquierda, username en el centro, pill a la derecha; `min-height: 44px` por fila. **Escritorio:** mismo diseño, contenido centrado de 980 px; no se convierte en tabla (son dos filas, no hacen falta columnas).

### 3.3 Prohibiciones (lo que la fila NO muestra, nunca)

- **Ningún nombre real.** Ni en la fila, ni en un subtítulo, ni en un `title`, ni en un `aria-label`, ni en un comentario visible. Solo el username.
- **El tenant interno no se muestra.** El valor de tenant de la sesión es un dato interno de aislamiento (cada usuario ve solo su contenido) y no se renderiza en ningún texto, pill, tooltip ni atributo visible. Tampoco se muestra como parte del username ni como sufijo.
- **Sin rol, sin fecha de creación, sin nota, sin correo.** La tabla `panel_users` guarda más campos, pero la fila muestra solo username + estado. Mostrar el rol o la fecha sería exponer datos internos que esta pestaña no necesita.
- **Sin credenciales.** Nunca se muestra ni se sugiere ninguna contraseña, hash ni longitud de contraseña.
- **Sin botones de acción.** No hay botón «Crear usuario», «Editar», «Desactivar» ni «Eliminar»: no existe endpoint que los respalde y un botón sin backend es un botón que miente. Cuando el Worker añada el endpoint, la spec se amplía con sus confirmaciones y sus estados de carga; hasta entonces, la pestaña es de solo lectura.
- **Sin contador inventado.** No se muestra «2 usuarios» como titular: el número sale de las filas que devuelva el endpoint cuando exista; en el estado interino (§3.4) no hay número.

### 3.4 Estado interino: sin endpoint, la pestaña nace vacía (regla del inventario)

Hoy no hay endpoint de listado, así que la pestaña **no puede renderizar las dos filas como datos vivos**. Su estado real al implementarse es el vacío + acción pendiente, y la lista del §3.2 es el estado objetivo al que se conecta el endpoint cuando exista:

- Título (h3): «Sin gestión de usuarios todavía»
- Texto (`.dim`, exacto): «Este panel aún no puede listar los usuarios. La creación y la desactivación se hacen fuera del panel, en el Worker.»
- Acción: botón ghost «Actualizar» (`#retryPanelUsers`, 44 px) que reintenta la carga. **No** se ofrece «Crear usuario».
- **Prohibido** escribir las dos filas a mano en `app.js` o en `index.html` como datos fijos: el estado «Activo» de cada cuenta solo lo sabe la base de datos, y una fila fija quedaría obsoleta o falsa en cuanto cambie algo fuera del panel. Las filas del §3.2 se pintan solo desde la respuesta del endpoint, cuando exista. La única fila que el front puede afirmar hoy sin endpoint es la de la sesión actual (`me.sub`), y aun así se pinta dentro de la lista del endpoint, no como sustituto de ella.

### 3.5 Ids nuevos que necesita el desarrollador (Usuarios)

| Id / atributo | Tipo | Uso |
|---|---|---|
| `tab-usuarios` | `<section>` | Panel de la pestaña (nuevo) |
| `tabbtn-usuarios` | `<button data-tab="usuarios">` | Botón del nav (nuevo) |
| `panelUserList` | `<div>` | Contenedor de las filas (o del empty state interino) |
| `panelUserNote` | `<p class="dim">` | Nota al pie (§3.2) |
| `retryPanelUsers` | `<button>` (dinámico, en el empty state) | Reintenta la carga |
| `loadPanelUsers()` | función nueva en `app.js` | Hoy: intenta la carga y muestra el empty state del §3.4 (no hay endpoint al que llamar). Cuando exista el endpoint de listado, esta función lo consume y pinta las filas del §3.2; el cambio es solo dentro de esta función |

---

## 4. Corrección del bug del punto rojo de Avisos

### 4.1 Dónde está el punto rojo

Hay dos puntos rojos en Avisos y el bug es del de la **pestaña**, no del de la fila:

- **Badge de la pestaña** (`#unreadBadge`, dentro de `#tabbtn-avisos`): fondo `--color-badge-bg: #be185d` (tema GTA 6), texto blanco, tope «9+». Es el punto rojo que debe desaparecer cuando no hay avisos sin leer.
- **Punto de la fila** (`.dot` CSS de 8 px en `.notif-row.notif-unread`): marca visual de cada aviso no leído dentro de la lista. Desaparece con la fila al re-renderizarla como leída; no es el bug.

### 4.2 Comportamiento correcto esperado (invariante)

El badge **no es un contador propio**: es una vista derivada de `GET /api/notifications`. No existe endpoint de «contador de no leídas». La única fuente de verdad es `read_at` en la respuesta.

```
no_leídas = notificaciones.filter(n => n.read_at == null).length
```

| Situación | Badge (`#unreadBadge`) | `aria-label` de `#tabbtn-avisos` | `#notifCount` |
|---|---|---|---|
| 0 sin leer | Oculto (`hidden = true`, `textContent = ""`). Sin punto rojo, sin «(0)», sin «0» | «Avisos» | «Todo al día. Sin avisos sin leer.» |
| 1–9 sin leer | Visible, texto = el número («3») | «Avisos, 3 sin leer» | «3 avisos sin leer» |
| 10 o más sin leer | Visible, texto «9+» (tope visual) | «Avisos, 12 sin leer» (el número real, sin tope, en el texto accesible) | «12 avisos sin leer» (el número real) |

### 4.3 Secuencia correcta al marcar un aviso como leído

1. El usuario pulsa «Marcar leída» (`data-read="<id>"`): el botón queda `disabled` + texto «Marcando…» hasta resolver.
2. `POST /api/notifications/<id>/read`. Si falla: `#msg role="alert"` con «No se pudo marcar el aviso como leído. Reintenta.», el botón vuelve a «Marcar leída» habilitado y **el badge no cambia** (el aviso sigue sin leer).
3. Si el POST tiene éxito: se vuelve a llamar a `loadNotifications()` **completa** (nuevo `GET /api/notifications`) y el badge, el `aria-label` y `#notifCount` se recalculan desde esa respuesta fresca. **Nunca** se decrementa un contador local ni se oculta el badge a mano: si el `read_at` no quedó persistido en el Worker, el `GET` lo devuelve sin leer y el badge debe seguir visible. Esa es la prueba de que el dato manda y no la animación.
4. Al marcar el **último** aviso sin leer: tras el `GET` fresco, `no_leídas === 0` → `badge.hidden = true`, `badge.textContent = ""`, `aria-label` vuelve a «Avisos», `#notifCount` dice «Todo al día. Sin avisos sin leer.», la fila pasa a `.notif-read` (sin punto, sin botón) y el foco va al siguiente aviso no leído o al `h2 Avisos` si no queda ninguno.
5. Al recargar la página: el badge sigue oculto, porque `read_at` quedó persistido en el Worker. Si reaparece tras recargar, el problema es de persistencia en el Worker, no del front: no se «arregla» ocultándolo en el front.

### 4.4 Cuándo se recalcula el badge

- Al entrar con sesión (`doLogin` → `loadNotifications()`), al entrar a la pestaña Avisos (`switchTab("avisos")` → `loadTab` → `loadNotifications()`), y tras cada «Marcar leída» con éxito (§4.3). Esas tres llamadas ya existen en `app.js`; la corrección es **garantizarlas**, no añadir un contador nuevo.
- Si el `GET /api/notifications` falla: el badge **conserva su último valor conocido** (no se oculta ni se pone a 0), la lista muestra «No se pudieron cargar los avisos» + motivo + botón «Reintentar». Un error de red nunca se presenta como «todo leído».

### 4.5 Qué NO se hace (causas del bug, prohibidas)

- No poner un número a mano en el badge ni fijar `badge.textContent = "0"`.
- No ocultar el badge con CSS (`display: none` en una clase) sin poner `hidden = true` y vaciar el texto: el lector de pantalla seguiría anunciando el número viejo.
- No decrementar un contador local (`unread--`) tras el POST: si el POST falló o el `read_at` no persistió, el badge mentiría.
- No usar una variable global de «no leídas» que sobreviva entre pestañas: cada cálculo sale del `GET` más reciente.
- No confundir el punto de la fila con el badge: quitar el `.dot` de una fila no quita el badge, y viceversa.

### 4.6 Prueba de aceptación del bug (para el coordinador, en vivo)

1. Entrar con 1 aviso sin leer: el badge muestra «1» y `#notifCount` dice «1 aviso sin leer».
2. Pulsar «Marcar leída»: el badge **desaparece** (sin punto rojo en la pestaña), `#notifCount` dice «Todo al día. Sin avisos sin leer.» y la fila queda sin punto ni botón.
3. Recargar la página: el badge sigue sin aparecer.
4. Con 12 avisos sin leer: el badge muestra «9+» y el `aria-label` de la pestaña dice «Avisos, 12 sin leer».
5. Con la API caída: el badge conserva su valor anterior y la lista muestra el error con «Reintentar».

---

## 5. Textos exactos (español, para copiar)

| Dónde | Texto |
|---|---|
| Canales — cabecera | «Salud del panel y de cada plataforma. Lo que aún no mide el Worker se muestra como «sin datos», nunca como «todo bien».» |
| Canales — resumen en marcha / parado / caída | «API conectada · Worker: en marcha» / «API conectada · Worker: parado» / «API no responde» |
| Canales — última comprobación | «Última comprobación: <fecha y hora>» / sin dato: «Última comprobación: sin datos» |
| Canales — salud por canal | Pill «Sin datos» + «Sin datos de salud para este canal. El Worker aún no expone la salud por plataforma.» |
| Canales — sin cuenta API | «Sin cuenta API todavía. Se crea al migrar los datos del panel anterior o desde el Worker.» |
| Canales — cuota YouTube | «Usada hoy: sin datos · Restante: sin datos · Tope: 5 al día (fijo del Worker)» |
| Canales — cuota resto | «Usada hoy: sin datos · Restante: sin datos · Tope: 3 al día (fijo del Worker)» |
| Canales — publicados | «Total: sin datos · Últimos 7 días: sin datos» |
| Canales — ventana | «18:00–24:00 (hora de Chile) · Espaciamiento mínimo: 2 h entre subidas» |
| Canales — nota al pie | «Sin datos de salud para este canal. Cuando el Worker exponga la salud por plataforma, aparecerá aquí la última comprobación, los errores y la última publicación.» |
| Canales — error | «No se pudo cargar la salud de los canales» + botón «Reintentar» |
| Canales — actualizado | «Actualizado: <fecha y hora>» |
| Tracking — cabecera | «El recorrido de cada clip, desde que se detecta hasta que se publica. Solo se muestra lo que el Worker registró, con su hora y su motivo.» |
| Tracking — progreso | «Paso <n> de 11 con registro» / «Sin seguimiento todavía.» |
| Tracking — paso sin evento | Pill «Sin registro» + «Sin registro todavía.» + «Sin hora» |
| Tracking — otros eventos | Título «Otros eventos» + «Eventos registrados que no corresponden a un paso del pipeline. Se muestran tal cual los devuelve el Worker.» |
| Tracking — sin clips | Título «Sin clips para seguir» + «Los clips aparecen aquí cuando el cerebro los detecta o cuando se migran del panel anterior.» + botón «Ir al Worker» |
| Tracking — selector vacío por filtro | «Sin clips de este origen» |
| Tracking — sin elegir clip | «Elige un clip para ver su seguimiento.» |
| Tracking — error | «No se pudo cargar el seguimiento» + botón «Reintentar» |
| Usuarios — cabecera | «Quién puede entrar a este panel. Cada usuario ve solo su propio contenido.» |
| Usuarios — sesión actual | «Sesión actual» (bajo el username de `me.sub`) |
| Usuarios — nota al pie | «Los usuarios se crean fuera del panel. Si necesitas un usuario nuevo o desactivar uno, pide que lo hagan en el Worker.» |
| Usuarios — vacío interino | Título «Sin gestión de usuarios todavía» + «Este panel aún no puede listar los usuarios. La creación y la desactivación se hacen fuera del panel, en el Worker.» + botón «Actualizar» |
| Avisos — error al marcar | «No se pudo marcar el aviso como leído. Reintenta.» |
| Avisos — contador | «3 avisos sin leer» / «1 aviso sin leer» / «Todo al día. Sin avisos sin leer.» |
| Global — botones | «Actualizar» / «Reintentar» / «Ir a Cuentas» / «Ir al Worker» / «Marcar leída» / «Marcando…» / «Actualizando…» / «Cargando…» |

## 6. Ids nuevos consolidados (lo que necesita el desarrollador)

| Pestaña | Ids nuevos en `index.html` | Funciones nuevas en `app.js` |
|---|---|---|
| Canales | `tab-canales`, `tabbtn-canales`, `channelSummary`, `channelUpdatedAt`, `btnRefreshChannels`, `channelList`, `retryChannels` (dinámico), `data-channel`, `data-goto-cuentas` | `loadChannels()` |
| Tracking | `tab-tracking`, `tabbtn-tracking`, `trackingClipSelect`, `trackingFilters`, `data-tracking-filter`, `btnRefreshTracking`, `trackingDetailCard`, `trackingClipTitle`, `trackingClipOrigin`, `trackingClipStatus`, `trackingProgress`, `trackingTimeline`, `trackingOtherEvents`, `trackingOtherList`, `retryTracking` (dinámico) | `loadTracking()`, `loadTrackingTimeline(clipId)` |
| Usuarios | `tab-usuarios`, `tabbtn-usuarios`, `panelUserList`, `panelUserNote`, `retryPanelUsers` (dinámico) | `loadPanelUsers()` |
| Avisos (bug, §4) | Ningún id nuevo | Ninguna función nueva: garantizar `loadNotifications()` tras login, al entrar a Avisos y tras cada «Marcar leída» con éxito |

**Ids existentes que NO se tocan:** `loginBox`, `loginForm`, `username`, `password`, `whoLine`, `healthLine`, `logoutBtn`, `workerLight`, `workerLightText`, `msg`, `app`, `main`, `tabs`, `tab-worker`, `tab-cola`, `tab-cuentas`, `tab-avisos`, `tab-ajustes` y sus `tabbtn-*`, `unreadBadge`, `clipList`, `clipDetailCard`, `clipTitle`, `clipScoreRing`, `clipDetail`, `clipBrakes`, `brakeList`, `brakeNote`, `clipTracking`, `accountList`, `notifHeading`, `notifCount`, `notifList`, `settingsForm` y todos los `set-*`. Tampoco se tocan la base `API`, `credentials: "include"`, los métodos/rutas/cuerpos de los 20 endpoints que usa el front, `STATUS_MAP`, `PLAT_STATE_MAP`, `scoreClass`, `publishGate` ni `seoForPlatform`. La única ampliación permitida es la de `TAB_NAMES` (§0.1).

## 7. Decisiones pendientes (no inventar; confirmar con el Worker / el usuario)

1. **Salud por plataforma.** Sin endpoint, las 4 pills de Canales quedan en «Sin datos». Definir la ruta y qué devuelve (última comprobación, último error, última publicación).
2. **Cuota usada/restante hoy.** El conteo vive dentro de `publishGate` y ningún endpoint lo expone. Definir la ruta que lo devuelva por plataforma.
3. **Publicados total / 7 días.** Sin endpoint de `clip_stats`. Definir la ruta de lectura antes de mostrar ninguna cifra.
4. **Claves canónicas de `step` para el tracking (§2.5).** El cerebro / el puente deben confirmar (o crear) los valores que escriben en `clip_events.step` para los pasos 1–9; hasta entonces esos pasos muestran «Sin registro».
5. **Listado de usuarios.** Sin endpoint, la pestaña Usuarios nace con el vacío del §3.4 y la lista del §3.2 es su estado objetivo. Definir la ruta (listar) y, después, crear / desactivar.
6. **Crear cuenta API.** Sin `POST /api/accounts`, el botón «Ir a Cuentas» no puede ofrecer crear la cuenta que falta.
7. **Franja y límites de Ajustes.** Siguen sin aplicar en el Worker (notas «Aún no lo aplica el Worker» intactas); la cuota que muestra Canales es el valor fijo de `publishGate`, no el de Ajustes.

## 8. Checklist de aceptación (estas tres pestañas + bug)

- [ ] Las 3 pestañas existen en el nav en el orden del §0.1, con `TAB_NAMES` ampliado solo con los 3 valores nuevos; las 5 pestañas viejas intactas.
- [ ] Ningún hex fuera de `tokens-gta6.css` / `tokens.css`; ningún estilo inline nuevo en `app.js`; `theme-color #100a1a` con el tema activo.
- [ ] Canales: 4 tarjetas con pill «Sin datos» en salud, cuota «sin datos» en usada/restante con tope fijo visible, publicados «sin datos», cuenta API con los 4 estados reales y «Sin verificar» sin aparecer; ningún número derivado de la Cola.
- [ ] Tracking: selector + filtros LIVE/VOD, timeline de 11 pasos en orden fijo con «Sin registro» donde no hay evento, «Otros eventos» con los eventos sin paso, «Último paso» en el último evento con datos, `#clipTracking` de Cola sin tocar.
- [ ] Usuarios: filas solo con username + pill «Activo» (+ «Sesión actual» en la de `me.sub`); sin nombres reales, sin tenant visible, sin rol, sin fechas, sin botones de crear/editar/desactivar; estado interino vacío (§3.4) hasta que exista endpoint.
- [ ] Avisos: badge oculto en 0 y visible con «9+» a partir de 10, recalculado desde el `GET` fresco tras cada «Marcar leída»; prueba del §4.6 superada en vivo.
- [ ] Táctil 44 px, inputs a 16 px, foco ámbar 3 px visible, estados como pills con texto, skeletons al cargar, errores con «Reintentar», sin scroll horizontal a 360 px, `prefers-reduced-motion` respetado.
- [ ] `node --check frontend/dist/app.js` OK; grep de nombres personales en `dist/` = 0; cada `fetch` nuevo apunta a una ruta real del inventario (§2.1).
