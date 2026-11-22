# QA fase 2 — Panel AVC (Rol 9, juez de calidad)

- **Fecha:** 2026-11-17
- **Veredicto final: APROBADO** (sin defectos bloqueantes; 13 menores, ninguno bloquea)
- **Alcance auditado:** `frontend/dist/index.html` (587 líneas), `app.js` (1.531 líneas), `styles.css` (668 líneas), `tokens-gta6.css` (216 líneas) — leídos completos. `worker/src/index.ts` (476 líneas, router completo) — leído completo.
- **Documentos leídos antes:** `ui-ux-pro-max-skill/.../SKILL.md`, `references/pro-rules.md` (checklist pre-delivery, punto por punto, §7) y `references/quick-reference.md` §§1–3; `redesign/auditoria-ux.md`, `redesign/auditoria-visual.md`, `redesign/qa.md` (fase 1, APROBADO); `redesign-fase2/inventario.md` y los 4 `diseno-*.md`; `design-system/avc-panel/OVERRIDES.md` §8, `tokens-gta6.css`, `tokens-gta6.json`.
- **Método:** verificación estática de código + `node --check` + servidor local + `curl` real contra el Worker. **Sin navegador en vivo** (subagente genérico: no hay navegador; no se finge ninguna prueba visual en vivo; la prueba visual la hace el coordinador después).

## Limitaciones explícitas (no son defectos del front)

1. **Sin prueba visual en vivo:** 360/375 px, escritorio, horizontal, lector de pantalla y texto grande no se pudieron verificar en navegador. La verificación de layout es estática (media queries, `box-sizing`, anchos, tarjetas ≤ 640 px). Pendiente para el coordinador.
2. **Login 200 pendiente:** la contraseña del usuario `seba` no está en el contexto de este QA y no se escribe en ningún informe ni archivo. Se verificó el camino negativo (§6). El login 200 con cookie + `GET /api/auth/me` queda **pendiente de verificar por el coordinador** (igual que en la QA de fase 1).

## 1. Las 12 pestañas — APROBADO

Existen en el nav (`tabbtn-*`), en sus secciones (`tab-*`) y en `TAB_NAMES`, en el mismo orden en los tres sitios:

`worker · cola · cuentas · editor · canales · tracking · analiticas · usuarios · influencers · fuentes · avisos · ajustes`

- `TAB_NAMES` (app.js:176): `["worker","cola","cuentas","editor","canales","tracking","analiticas","usuarios","influencers","fuentes","avisos","ajustes"]` (12 valores).
- `loadTab()` tiene rama para las 12; `switchTab()`/`hashchange` las cubren todas (`#editor`, `#analiticas`, etc.).
- **Observación (menor M2):** el orden no coincide con el propuesto en ninguno de los 4 diseños (cada uno proponía un orden parcial distinto y ninguno define el orden completo de 12). El encargo pedía existencia, no un orden concreto; no bloquea.

## 2. Tema GTA 6 — APROBADO (con menores M1, M3, M4, M5, M12)

- `<html lang="es" data-theme="gta6">`; `<meta name="theme-color" content="#100a1a">`; `tokens-gta6.css` cargado después de `styles.css` y **idéntico byte a byte** al de `design-system/avc-panel/` (`diff` sin diferencias). Los 26 tokens de color verificados de `tokens-gta6.json` coinciden con el CSS (0 discrepancias; el degradado `sunset` es idéntico).
- Login con tema: `#loginBox` con borde degradado atardecer + sombra elevada (`tokens-gta6.css`), marca AVC con degradado, botón primario atardecer. Todas las pestañas heredan el tema (reglas `html[data-theme="gta6"]` en `styles.css` + `tokens-gta6.css`).
- **Contrastes recalculados con la fórmula WCAG (no estimados):** todos los pares de **texto** pasan AA ≥ 4,5:1 y coinciden con OVERRIDES §8.2 (texto/fondo 16,16:1; texto/tarjeta 13,56:1; dim/tarjeta 8,02:1; enlace/tarjeta 9,00:1; foco `#fbbf24`/tarjeta 9,77:1; blanco/peor parada del degradado `#c2410c` 5,18:1; pills ok 7,52 / bad 6,93 / warn 12,03 / sunset 10,88 / violet 9,23 / ember 8,18:1; badge blanco/`#be185d` 6,04:1; error texto 5,90:1; peligro blanco 10,02:1; info blanco 11,50:1). Tres valores de plataforma difieren levemente de lo tabulado en §8.2 por redondeo (TikTok 11,85 vs 12,07; IG 5,94 vs 6,05; FB 6,42 vs 6,54) — todos siguen pasando AA (menor M13, sin acción).
- Los dos pares por debajo de 4,5:1 son **no-texto** y están documentados y aceptados en OVERRIDES §8 ("Nota honesta sobre superficies"): tarjeta/fondo 1,19:1 y borde/tarjeta 3,44:1 (el borde sí pasa el 3:1 de no-texto). El punto `.dot.twitch` (`#a855f7`, 4,12:1) es decorativo, no texto.
- **Cero hex fuera de tokens:** `app.js` 0 hex; `styles.css` 0 hex fuera de `:root`; `index.html` solo `#100a1a` en `theme-color`. Neones crudos (`#ff2e88`, `#ff5e00`, `#ec4899`) solo en el degradado de la franja, en definiciones de variables `--color-neon-*-raw` y en un comentario — **nunca como `color` de texto** (grep: 0).
- **Táctil 44 px:** `min-height: var(--size-touch-target)` (44 px) en `button, nav button, input, textarea, select`; `#passwordToggle` 44×44; `.msg-close` 44×44; toggles y filtros 44 px. **Base 16 px:** `font-size: 16px` en `input, textarea, select` y cuerpo; `#settingsJson` a 13 px mono es vista de código, admitida por la spec. **Focos visibles:** `:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }` global; foco ámbar `#fbbf24` con el tema activo; inputs además con borde rosa + sombra inset.
- Menores de tema (ver §8): M1 (barra de prioridad sin degradado GTA 6), M3 (`tokens.css` no se carga como archivo aparte), M4 (header no sticky), M5 (bordes/hover de tabla sin los rgba de §8.3), M12 (CSS muerto en `tokens-gta6.css`).

## 3. Cero nombres personales — APROBADO

`grep -riE 'sebastián|sebastian|colega'` en `frontend/dist/` (html + js + css): **0 coincidencias**. `grep` de `Sin verificar` y de `seed-users.sql`: **0**. `#whoLine` pone solo `me.sub` (username de sesión, dinámico desde la API). `IlloJuan` aparece 5 veces (título, regla, notas, placeholder `Ej.: IlloJuan`) — handle público, permitido por el encargo y por los diseños. Ningún nombre real en ningún texto visible, `aria-label`, `title` ni comentario.

## 4. Cero datos inventados — APROBADO

- **Inventario de fetch (todos los `api()` / `fetch` / XHR de `app.js`):** `/api/health`, `/api/auth/login`, `/api/auth/me`, `/api/auth/logout`, `/api/worker/state|start|stop|run-once`, `/api/clips`, `/api/clips/:id`, `/api/clips/:id/tracking`, `/api/clips/:id/publish`, `/api/clips/:id/video` (PUT por XHR y GET por `fetch` en el Editor), `/api/accounts`, `/api/accounts/:id/credential`, `/api/accounts/:id/verify`, `/api/notifications`, `/api/notifications/:id/read`, `/api/settings` (GET/PUT). **Todos corresponden a una ruta real de `worker/src/index.ts`** (24 combinaciones verificadas). Ningún fetch a `/api/influencers`, `/api/sources`, `/api/users`, `/api/stats`, `/api/preview` ni a ninguna ruta inexistente (grep: 0; las únicas menciones son comentarios "Sin endpoint: ... sin fetch").
- **Pestañas sin endpoint, con vacío real + nota de pendiente (verificado uno por uno):**
  - Cuentas sociales: 4 tarjetas con pill "Sin conectar", Verificar deshabilitado, nota "Pendiente: conectar con el Worker…". Sin handles, fechas ni estados inventados.
  - Influencers: solo la tarjeta de configuración IlloJuan (con su nota "Dato de configuración del proyecto. No viene del Worker…") + "Sin más influencers" + formulario deshabilitado con nota pendiente. `loadInfluencers()` no hace fetch.
  - Fuentes: botón "Revisar VODs nuevos" deshabilitado, "Sin revisiones todavía.", "Sin fuentes todavía". `loadFuentes()` no hace fetch.
  - Editor: vacío "Elige un clip en la Cola…", "Sin MP4 todavía" sin MP4, "Marcar preview vista" siempre deshabilitado con su nota, "Publicar ahora" deshabilitado mientras `preview_seen = 0` con el motivo visible. El freno `preview_seen` no se oculta ni se marca desde el front. Duración con bandas 36–60 / 25–35 (incluye 30 s, "Prohibido") / 15–24 y 61–90 / fuera de 15–90, calculada de `end_sec - start_sec`. SEO por plataforma solo de la fila del clip; `null` = "SEO sin medir", nunca 0.
  - Analíticas: "Sin mediciones todavía" en vacío general, 4 tarjetas de resumen y 4 rankings; formulario "Pegar una medición" con "Guardar medición" deshabilitado y su nota pendiente. Cero cifras inventadas (medianas "—", nunca 0).
  - Canales: 4 tarjetas con pill "Sin datos" en salud, cuota "Usada hoy: sin datos · Restante: sin datos · Tope: 5/3 al día (fijo del Worker)", publicados "sin datos", ventana 18:00–24:00 fija. Cuenta API con los 4 estados reales. Nada derivado de la Cola.
  - Tracking: timeline de 11 pasos con los nombres exactos del diseño; paso sin evento = pill "Sin registro" + "Sin registro todavía." + "Sin hora"; "Otros eventos" con los eventos sin paso; progreso "Paso N de 11 con registro" / "Sin seguimiento todavía.". No se rellenan pasos.
  - Usuarios: vacío "Sin gestión de usuarios todavía" + nota; **no** se hardcodean las filas `seba`/`chacal` (el estado objetivo del diseño es para cuando exista endpoint). `loadPanelUsers()` no hace fetch.
- Cuentas APIs: agrupación YouTube/TikTok/Meta + placeholders "Sin crear" solo con lista no vacía; con lista vacía, empty state "Sin cuentas todavía" + encabezados de grupo. Estados solo los 4 reales; "Añadir cuenta" y "Eliminar" deshabilitados con sus notas. Textos exactos del diseño (hints por plataforma, errores inline, aviso "Credencial guardada (****1234)…" con `r.masked` real).
- Ajustes: notas "Aún no lo aplica el Worker" intactas en Subtítulos, Límites y Franja.
- Desglose de prioridad: solo si la API devuelve componentes numéricos (`breakdownData`); hoy sin botón ni ceros.

## 5. Bug del punto rojo de Avisos — CORREGIDO (verificado en código; falta la prueba en vivo)

- `loadNotifications()` recalcula el badge **desde el `GET /api/notifications` fresco**: `unread = items.filter(n => !n.read_at).length`; 0 → `badge.hidden = true` + `badge.textContent = ""`; 1–9 → número; ≥10 → `"9+"`. `aria-label` de `#tabbtn-avisos` ("Avisos" / "Avisos, N sin leer") y `#notifCount` ("Todo al día. Sin avisos sin leer." / "1 aviso sin leer" / "N avisos sin leer") se recalculan en la misma función.
- **Regla CSS que hace efectivo el `hidden`:** `.badge[hidden] { display: none !important; }` en `styles.css` (además del global `.hidden { display: none !important; }`). Sin esta regla, el `display: inline-flex` de `.badge` habría anulado el atributo `hidden`: es justo el fallo que la spec prohibía y está cubierto.
- Al pulsar "Marcar leída": botón `disabled` + "Marcando…", `POST /api/notifications/:id/read`, y **solo si tiene éxito** se vuelve a llamar a `loadNotifications()` completa (nunca se decrementa un contador local). Si el POST falla: aviso "No se pudo marcar el aviso como leído. Reintenta." y el badge no cambia. Si el `GET` falla: el badge conserva su último valor (el `catch` no lo toca) y la lista muestra error + "Reintentar".
- Foco tras marcar la última: al siguiente botón "Marcar leída" si queda alguno, o al `h2 Avisos` si no queda ninguno.
- **Pendiente (coordinador, en vivo):** la prueba de aceptación §4.6 del diseño (1 aviso → badge desaparece y sigue sin aparecer tras recargar; 12 avisos → "9+" con aria-label "12 sin leer"; API caída → badge conserva valor).

## 6. Regresiones — APROBADO

### Sintaxis y servicio local
- `node --check frontend/dist/app.js` → OK.
- Servidor local (`python3 -m http.server` en `dist/`): `index.html` 200 (36.380 B), `app.js` 200 (78.362 B), `styles.css` 200 (33.376 B), `tokens-gta6.css` 200 (8.525 B).

### Ids
- 216 ids estáticos en `index.html`. Los 62 ids originales del inventario §3.2 siguen todos presentes (verificados 87 ids incluyendo los listados en §3.2 y extras como `clipScoreRing`, `publishLabel`, `btnOpenEditor`: **0 ausentes**).
- Todos los ids nuevos de los 4 diseños fase 2 están presentes (**0 ausentes** de la lista consolidada: subtabs, sociales, APIs, influencers, fuentes, editor, analíticas, canales, tracking, usuarios).
- Los 11 ids literales de `app.js` que no existen como estáticos **se crean dinámicamente por `innerHTML` en la misma función antes de leerse** (verificado uno por uno): `msgClose` (`showMsg`), `retryClips` / `emptyGoWorker` / `emptyRefreshClips` (`loadClips`), `retryAccounts` (`loadAccounts`), `retryNotifs` (`loadNotifications`), `btnCopyUrl` (`uploadSelected` → `xhr.onload`), `retryChannels` (`loadChannels`), `retryTracking` (`loadTrackingTimeline`), `retryPanelUsers` (`loadPanelUsers`), `trackingGoWorker` (`renderTrackingEmpty`). Más los dinámicos por cuenta (`cred-<id>`, `extra-<id>`, `cred-<id>-error`, `extra-<id>-error`), por desglose (`bd-<id>`) y por placeholder (`api-placeholder-<platform>-<language>`), creados junto con su HTML. Ningún `null` dereferenciado.
- `TAB_NAMES` ampliado solo con los 7 valores nuevos; los 5 existentes intactos; hash `#worker … #ajustes` y `switchTab`/`loadTab` sin cambios salvo las ramas nuevas.

### Endpoints y lógica — sin cambios
- Base única `https://avc.vikingosfoodspa.workers.dev`, `credentials: "include"` en `api()`/`apiRaw()` y en el XHR de subida. Sin cambios de método, ruta ni cuerpo en los 20 combos que usaba el front (login `{username, password}`; publish sin cuerpo; `PUT /api/clips/:id/video?filename=…` con `video/*`; credential `{credential, extra}`; settings PUT con JSON completo). Novedad fase 2: el Editor usa `GET /api/clips/:id/video`, endpoint real que el front no usaba antes (inventario §2.1 nº 16); ahora son 21 combos usados.
- `scoreClass` (<50 bad / 50–89 warn / ≥90 ok), `STATUS_MAP`, `PLAT_STATE_MAP`, orden de Cola (lo da la API), frenos solo desde `blocked[]`, tracking con exactamente los eventos de la API: intactos. Escape XSS con `esc()` en todo contenido dinámico (verificado por lectura).
- Sin emojis ni glifos como iconos (grep `▶ ■ ●` y emojis: 0); SVG inline play/stop con `aria-hidden="true"` y puntos `.dot` CSS.

### API real por curl
- `GET /api/health` → **200** `{"ok":true,"app":"avc","tenants":["sebastian","colega"],"worker_running":{"sebastian":false,"colega":false},"time":"2026-11-17T21:31:47.934Z"}`.
- `POST /api/auth/login` con `Origin: https://avc-em9.pages.dev`, usuario `seba`, contraseña incorrecta → **401** `{"ok":false,"error":"Usuario o contraseña incorrectos."}`, con `Access-Control-Allow-Origin: https://avc-em9.pages.dev` y `Access-Control-Allow-Credentials: true`. El camino de rechazo funciona y el CORS del front es correcto.
- **Login 200 + `GET /api/auth/me` con cookie: PENDIENTE de verificar por el coordinador** (la contraseña no está en el contexto de este QA; no se escribe aquí).

## 7. Checklist pre-delivery de `pro-rules.md` — punto por punto

Leyenda: APROBADO / NO VERIFICADO EN VIVO / N/A. Los ítems ya aprobados en fase 1 se re-verificaron en el código actual (incluidas las 7 pestañas nuevas).

### Proceso

| Ítem | Resultado |
|---|---|
| Solo búsquedas relevantes para la interfaz | APROBADO — este QA no necesitó búsquedas; las auditorías previas las hicieron y este informe re-verifica su resultado en código |
| Revisado `quick-reference.md` §1–§3 (CRÍTICO + HIGH) como paso final | APROBADO — leído y contrastado: foco, contraste, táctil, errores, loading, skip link, safe-area |
| Probado a 375 px y en horizontal | NO VERIFICADO EN VIVO — sin navegador (limitación §0.1). Estático: media query ≤ 640 px (tarjetas en vez de tabla, 1 columna, botones a ancho completo), sin anchos fijos > viewport; nav con `flex-wrap` (12 pestañas envuelven, no se aprietan bajo 44 px) |
| Reduced-motion y Dynamic Type / texto más grande | APROBADO (reduced-motion: media query en `styles.css` y en `tokens-gta6.css`, `matchMedia` en `scrollIntoView`) / NO VERIFICADO EN VIVO (texto grande/Dynamic Type) |
| Contraste en modo oscuro verificado de forma independiente | APROBADO — calculado, ver §2 (el panel es solo oscuro; no existe tema claro) |
| Touch targets ≥ 44 pt y nada oculto tras safe areas | APROBADO — 44 px global; `env(safe-area-inset-*)` en `body`; `scroll-margin-top: 72px` en `#clipDetailCard` |

### Calidad visual

| Ítem | Resultado |
|---|---|
| Sin emojis como iconos (SVG en su lugar) | APROBADO — 0 glifos, SVG + `.dot` CSS |
| Iconos de una familia y estilo consistentes | APROBADO — 2 SVG inline (play/stop, 16×16); marcas de plataforma como punto + nombre (fallback admitido por la spec) |
| Recursos oficiales de marca con proporciones y espacio correctos | N/A con nota — no se dibujaron SVG de marca; la spec admite punto de color + nombre. No bloqueante |
| El estado de prensa no desplaza el layout ni causa jitter | APROBADO — `transform: scale(.98)` y `filter: brightness` no afectan al layout |
| Tokens semánticos consistentes, sin colores hardcodeados por pantalla | APROBADO — 0 hex fuera de `:root`/tokens; quedan solo estilos inline dinámicos de datos (alto de skeleton, ancho de barra, `flex-grow` de la escala de duración) |

### Interacción

| Ítem | Resultado |
|---|---|
| Feedback de prensa claro (ripple/opacidad/elevación) | APROBADO — hover brillo 1.12, active escala 0.98, 150–250 ms |
| Touch targets ≥ 44×44 pt | APROBADO |
| Micro-interacciones con tokens compartidos y responsivas | APROBADO — 150/200/250 ms, `ease-out` |
| Estados deshabilitados claros y no interactivos | APROBADO — `opacity: .55`, `cursor: not-allowed`, atributo `disabled` (toggles, botones pendientes, formularios sin endpoint); botones async deshabilitados + texto "…" |
| Orden de foco = orden visual, labels descriptivos | APROBADO con gap menor (M11) — labels con `for` en login, subida, ajustes y en los formularios nuevos (influencers, analíticas, tracking); el foco no se mueve al cambiar de pestaña (UX-17, preexistente). Tracking mueve el foco al título del clip y Cola al detalle |
| Sin conflictos de gestos (tap/drag/back-swipe) | APROBADO — sin gestos; solo botones; `<details>` nativo para los formularios plegados |

### Modo claro/oscuro

| Ítem | Resultado |
|---|---|
| Texto primario ≥ 4,5:1 en claro y oscuro | APROBADO (oscuro; panel solo oscuro por diseño) |
| Texto primario y secundario ≥ 4,5:1 en claro y oscuro | APROBADO (oscuro: 13,56:1 / 8,02:1 sobre tarjeta) |
| Divisores/bordes y estados distinguibles en ambos modos | APROBADO (oscuro: borde 3,44:1, estados con pill + texto) |
| Scrim de modal medido contra el fondo real | N/A — no hay modales propios; la confirmación es `confirm()` nativo |
| Ambos temas probados | N/A — tema único oscuro (variante GTA 6 activa) |

### Layout

| Ítem | Resultado |
|---|---|
| Safe areas respetadas (headers, tabs, CTA) | APROBADO — `env(safe-area-inset-*)` en `body` |
| Contenido con scroll no oculto tras barras fijas/sticky | APROBADO — nav `sticky`, `scroll-margin-top: 72px` en el detalle |
| Verificado en teléfono pequeño, grande y tablet (vertical + horizontal) | NO VERIFICADO EN VIVO — sin navegador |
| Gutters adaptativos por tamaño/orientación | APROBADO con gap menor (M11) — `main` 18 px → 12 px en móvil; header/nav siguen a 18 px fijos frente a `main` centrado a 980 px (VIS-16, preexistente) |
| Ritmo de espaciado 4/8 dp | APROBADO — escala 4/8/12/16/24/32 px |
| Medida de texto legible en pantallas grandes | APROBADO — `max-width: 980px` centrado; Editor 2 columnas ≥ 641 px (escenario máx. 400 px), Canales 2 columnas, Analíticas 2×2 |

### Accesibilidad

| Ítem | Resultado |
|---|---|
| Iconos decorativos junto a texto ocultos del árbol de accesibilidad | APROBADO — `aria-hidden="true"` en SVG y `.dot` |
| Imágenes/iconos significativos sin texto equivalente tienen alternativa | APROBADO — barra de prioridad `role="img"` + `aria-label`; anillo de score con "Puntos: X" en texto |
| Controles de icono con nombre accesible y estado (selected/pressed/expanded) | APROBADO — tabs `aria-selected`, toggles `role="switch"` + `aria-checked` + `aria-disabled`, desglose `aria-expanded`, filtros Tracking `aria-pressed`, zonas seguras `aria-pressed`, toggle contraseña `aria-pressed` |
| Campos con labels, hints y errores claros | APROBADO — labels con `for`, hints, errores bajo el campo con `role="alert"` + `aria-invalid` + foco al primer inválido (login, cuentas, ajustes, analíticas, influencers) |
| Color no es el único indicador | APROBADO — pills con texto, punto + texto en semáforo y matriz, pestaña activa con fondo + negrita + barra + `aria-selected` |
| Reduced motion y texto grande sin roturas | APROBADO (reduced-motion) / NO VERIFICADO EN VIVO (texto grande) |
| UI fija y overlays no tapan el foco de teclado | APROBADO |
| Alternativas de botón/teclado para arrastrar/deslizar | APROBADO — no hay interacciones de arrastre |
| Autenticación permite gestores de contraseñas y pegado, con alternativa no cognitiva | APROBADO — `autocomplete` correcto, sin bloqueo de pegado, toggle Mostrar/Ocultar en login y en credenciales |
| Contenido auto-rotativo con pausa/stop | N/A — no hay contenido auto-rotativo |
| Formularios fallidos conservan errores inline; multi-error enfoca el resumen | APROBADO — errores por campo conservados, foco al primer inválido, `#loginMsg`/`#msg` con `role="alert"` como resumen; Ajustes con confirmación al salir sin guardar |

## 8. Defectos encontrados

### Bloqueantes: ninguno

### Mayores: ninguno

### Menores (13, no bloquean)

1. **M1 — La barra de prioridad no usa el degradado GTA 6 de OVERRIDES §8.3.** `tokens-gta6.css` define `.priority-bar .fill-low/.fill-mid/.fill-high` (degradados atardecer), pero `app.js` emite `.priority-fill.ok/warn/bad` y `styles.css` los pinta sólidos (`--color-ok/warn/bad`). Las clases `fill-*` no se usan en ningún sitio (grep: 0 en html/js). La barra sigue comunicando por color semántico + número + etiqueta, pero el degradado rosa→violeta en ≥90 y el brillo neón no se ven. Arreglo: emitir `fill-low/mid/high` en `scoreBar()` o añadir reglas de degradado para `.priority-fill.ok/warn/bad` bajo `[data-theme="gta6"]`.
2. **M2 — Orden del nav distinto del propuesto en los diseños.** Implementado: Worker, Cola, Cuentas, Editor, Canales, Tracking, Analíticas, Usuarios, Influencers, Fuentes, Avisos, Ajustes. Cada diseño proponía un orden parcial distinto (Influencers/Fuentes tras Cuentas; Canales/Tracking/Usuarios entre Cuentas y Avisos) y ninguno define el orden completo de 12. Decidir un orden canónico o aceptar el actual.
3. **M3 — `tokens.css` no se carga como archivo aparte.** `index.html` carga `styles.css` y luego `tokens-gta6.css`; los tokens base viven duplicados en el `:root` de `styles.css`. Funcionalmente correcto (las overrides de `[data-theme="gta6"]` ganan por orden y especificidad), pero diverge de OVERRIDES §8.4 ("`tokens-gta6.css` cargado después de `tokens.css`").
4. **M4 — El header no es sticky.** Solo `nav#tabs` es sticky (`top: 0`); OVERRIDES §8.3 dice del header "sticky, `z-index: 10`". La navegación (lo que importa) sí queda fija al bajar.
5. **M5 — Tabla en escritorio sin los valores rgba de §8.3.** Filas con `border-bottom: 1px solid var(--color-border)` y hover `var(--color-surface-input)`, en vez de `rgba(124,106,160,.4)` y `rgba(236,72,153,.08)`. Visual menor.
6. **M6 — Formulario "Añadir influencer" cerrado en escritorio.** El diseño pedía el `<details>` cerrado por defecto en móvil y abierto por defecto en escritorio; está cerrado en todos los anchos (sin atributo `open` ni lógica por breakpoint).
7. **M7 — Tracking: estado vacío por filtro demasiado escueto.** Con filtro LIVE/VOD sin clips, el detalle muestra solo el título "Sin clips de este origen" y "Elige un clip para ver su seguimiento.", sin el texto explicativo ni el botón "Ir al Worker" del empty state de §2.6.
8. **M8 — Tracking: "Último paso" cuando el último evento no mapea a un paso.** Si el último evento es un `aviso` (va a "Otros eventos"), ningún paso de la timeline lleva la marca "Último paso" (la lleva el último evento de "Otros eventos"). La letra de §2.4 dice que la marca va bajo el paso que contiene el último evento con datos. Comportamiento razonable y coherente con `#clipTracking`, pero distinto de la letra de la spec.
9. **M9 — Escala de duración del Editor: marcas no alineadas con los segmentos.** Los segmentos son proporcionales (`flex-grow` 15/10/10/1/24/30 sobre 0–90), pero la fila de marcas usa `justify-content: space-between`, así que las 8 etiquetas (15, 25, 30, 35, 36, 40, 60, 90) quedan repartidas uniformemente y no caen sobre sus límites reales (p. ej. "25" cae ~14% en vez de ~28%). Guía estática, sin interacción: solo precisión visual.
10. **M10 — Analíticas: "Sin clips medidos" fijo en las tarjetas de resumen.** El diseño pedía "0 de N clips medidos" con N real de `GET /api/clips` cuando hubiera clips. El texto está hardcodeado en el HTML y `loadAnaliticas()` no lo actualiza. Cosmético: `GET /api/clips` excluye los publicados, así que el N de publicados no se puede derivar de ahí de todos modos.
11. **M11 — Menores preexistentes de fase 1, no regresiones:** foco que no se mueve al cambiar de pestaña (UX-17); hash con `replaceState`, no `pushState`, así que Atrás no vuelve a la pestaña anterior (UX-8); `#healthLine` sin botón "Reintentar" ni hora del último intento (UX-24, y sin el botón que pide §8.3 para el semáforo); "Salir" sin confirmación (UX-30); header/nav a 18 px fijos frente a `main` centrado a 980 px (VIS-16).
12. **M12 — CSS muerto en `tokens-gta6.css`.** `.btn-primary`, `.logo-avc` y `.worker-dot` no se usan en html/js (grep: 0); sus efectos están cubiertos por las reglas equivalentes de `styles.css` (botones primarios con degradado, `.brand-mark`, `.worker-light .dot`). Solo limpieza.
13. **M13 — Tres ratios de plataforma en OVERRIDES §8.2 difieren levemente del recálculo** (TikTok 11,85 vs 12,07; IG 5,94 vs 6,05; FB 6,42 vs 6,54). Todos pasan AA; probable redondeo en la tabla. Sin acción en el front.

## Veredicto

**APROBADO.** Sin defectos bloqueantes ni mayores. Las 12 pestañas existen y navegan por hash; el tema GTA 6 está aplicado en login y en todas las pestañas con contrastes AA verificados y cero hex fuera de tokens; cero nombres personales; cero datos inventados (cada pestaña sin endpoint muestra vacío real + nota de pendiente y ningún fetch apunta a una ruta inexistente); el bug del punto rojo de Avisos está corregido en código (badge derivado del `GET` fresco, `hidden` efectivo por CSS, recálculo tras cada "Marcar leída"); y no hay regresiones (ids, endpoints, fórmulas, `node --check` OK, health 200, login incorrecto 401 con CORS correcto). Quedan 13 menores documentados arriba y dos verificaciones en vivo para el coordinador: **prueba visual a 360 px y en escritorio** (recorrido de las 12 pestañas, con foco en Editor 9:16, Tracking y Canales) y **login 200 con cookie + `GET /api/auth/me`** (la contraseña no está en el contexto de este QA).
