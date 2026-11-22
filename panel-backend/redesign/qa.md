# QA — Rediseño del panel AVC (Rol 10, juez de calidad)

- **Fecha:** 2026-11-17
- **Veredicto final: APROBADO**
- **Alcance auditado:** `frontend/dist/index.html` (221 líneas), `app.js` (979 líneas), `styles.css` (445+ líneas) — leídos completos.
- **Documentos leídos antes:** `design-system/avc-panel/OVERRIDES.md`, `redesign/auditoria-ux.md`, `redesign/auditoria-visual.md`, `redesign/diseno-login.md`, `redesign/diseno-worker.md`, `redesign/diseno-cola.md`, `redesign/diseno-cuentas-avisos-ajustes.md`, `redesign/implementacion.md`, `ui-ux-pro-max-skill/.claude/skills/ui-ux-pro-max/references/pro-rules.md` y `references/quick-reference.md` (§1–§3 releídos como paso final).
- **Método:** verificación estática de código + `node --check` + servidor local + llamadas reales por `curl` a la API del Worker. **Sin navegador en vivo** (subagente genérico: no hay navegador; no se finge ninguna prueba de navegador).

## Limitaciones explícitas (no son defectos del front)

1. **Sin prueba visual en vivo:** 375 px / 360 px / 640 px, horizontal, lector de pantalla y texto grande no se pudieron verificar en navegador. La verificación de layout es estática (media queries, `box-sizing`, anchos `min()`, tarjetas ≤ 640 px). Queda pendiente para el coordinador: abrir el panel, entrar y recorrer Worker / Cola / Cuentas / Avisos / Ajustes a 360 px y en escritorio (ya estaba así documentado en `implementacion.md` §3).
2. **Login 200 pendiente:** la contraseña del usuario `seba` está en el contexto del coordinador, no en el de este QA, y no se escribe en ningún informe ni archivo. Se verificó el camino negativo (ver §5). El login 200 con cookie + `GET /api/auth/me` queda **pendiente de verificar por el coordinador**.

## 1. Sintaxis y servicio local — APROBADO

- `node --check app.js` → OK (también tras las correcciones del §7).
- Servidor local (`python3 -m http.server` en `dist/`): `index.html` 200 (12.835 bytes), `app.js` 200 (47.640 bytes), `styles.css` 200 (21.549 bytes).
- `index.html` referencia `<link rel="stylesheet" href="styles.css">` y `<script src="app.js"></script>`; `<meta name="viewport" content="width=device-width, initial-scale=1">` sin bloqueo de zoom; `lang="es"`.

## 2. Regresiones — APROBADO

### Ids

- `app.js` usa 69 ids vía `$("…")`. 62 existen como `id="…"` estáticos en `index.html`. Los 7 restantes **se crean dinámicamente por innerHTML en la misma función antes de ser leídos** (verificado uno por uno): `msgClose` (en `showMsg`), `retryClips` / `emptyGoWorker` / `emptyRefreshClips` (en `loadClips`), `retryAccounts` (en `loadAccounts`), `retryNotifs` (en `loadNotifications`), `btnCopyUrl` (en `uploadSelected` → `xhr.onload`). Ningún `null` dereferenciado. Además existen los ids dinámicos por cuenta (`cred-<id>`, `extra-<id>`, `cred-<id>-error`, `extra-<id>-error`) y por desglose (`bd-<id>`), creados junto con su HTML.
- Los 35 ids del front anterior (login, header, Worker, Cola, Cuentas, Avisos, Ajustes) siguen todos presentes y sin renombrar: `loginBox, loginMsg, loginForm, username, password, loginBtn, loginHint, whoLine, healthLine, logoutBtn, msg, wRunning, wStarted, wNote, wActivity, btnStart, btnStop, btnRunOnce, btnRefreshW, clipList, clipDetailCard, clipTitle, clipDetail, clipTracking, videoFile, btnUpload, btnPublish, accountList, notifList, unreadBadge, settingsJson, btnSaveSettings` (+ `tabs` / `tab-*` / `tabbtn-*` nuevos).

### Endpoints (19 rutas distintas, 20 combinaciones método+ruta; base sin cambio)

Base única en todo `app.js`: `https://avc.vikingosfoodspa.workers.dev`. Sin cambios de ruta, método ni cuerpo:

| Método | Ruta | Uso |
|---|---|---|
| POST | `/api/auth/login` | `{username, password}` |
| GET | `/api/auth/me` | sesión |
| POST | `/api/auth/logout` | salir |
| GET | `/api/health` | salud + `worker_running` |
| GET | `/api/worker/state` | estado + actividad |
| POST | `/api/worker/start`, `/stop`, `/run-once` | acciones Worker |
| GET | `/api/clips` | lista |
| GET | `/api/clips/:id` | detalle |
| GET | `/api/clips/:id/tracking` | seguimiento |
| POST | `/api/clips/:id/publish` | publicar (sin cuerpo) |
| PUT | `/api/clips/:id/video?filename=…` | subida MP4 (XHR) |
| GET | `/api/accounts` | cuentas |
| POST | `/api/accounts/:id/credential` | `{credential, extra}` |
| POST | `/api/accounts/:id/verify` | verificar |
| GET | `/api/notifications` | avisos |
| POST | `/api/notifications/:id/read` | marcar leída |
| GET | `/api/settings` | ajustes |
| PUT | `/api/settings` | guardar ajustes |

### Lógica / fórmulas — sin cambios

- `scoreClass`: `<50` bad, `50–89` warn, `≥90` ok (OVERRIDES §4.2). Barra etiquetada "Puntos" con `c.score` (solo "Prioridad" si la API devolviera `priority`, que hoy no existe); desglose solo si la API devuelve componentes numéricos (hoy no: sin botón ni ceros). `STATUS_MAP` y `PLAT_STATE_MAP` coinciden con las tablas de `diseno-cola.md` §4.4/§5.3. `fmtDate` sigue `es-CL` + `America/Santiago`. `parsePlatforms`, orden de lista (el de la API), frenos solo desde `blocked[]` y transitorios, tracking con exactamente los eventos de la API. Sin recálculo ni reordenación en el front.
- Escape XSS: todo contenido dinámico pasa por `esc()` (títulos, mensajes, estados, `verify_message`, `credential_masked`, `r2_key`, `public_url`, textos de `showMsg`); verificado por lectura.

## 3. Requisito nombre — APROBADO

`grep` en `frontend/dist/` (html + js + css):

| Cadena prohibida | Coincidencias |
|---|---|
| `Sebastián` | 0 |
| `sebastian o colega` | 0 |
| `seed-users.sql` | 0 |
| `Sin verificar` | 0 |
| `sebastian` / `colega` (cualquier caso) | 0 |

- Login: texto exacto `Entra con tu usuario del panel.`; placeholder `tu usuario`; hint tras fallo (en `app.js`, `doLogin`): `Si acabas de crear el panel y aún no tienes usuario, pide que te creen uno.` (solo tras fallo de credenciales; en fallo de red el hint queda vacío y el mensaje es el de red).
- `whoLine`: `showApp()` pone solo `me.sub`; en `showLogin()` pone `sin sesión`. Sin tenant.
- `healthLine`: `API conectada · Worker: en marcha/parado` o `API no responde`. No nombra tenants. (Nota: el propio `GET /api/health` del Worker devuelve `tenants: ["sebastian","colega"]` en su JSON; el front no lo renderiza: `checkHealth` solo usa `worker_running` con `Object.values(...).some(Boolean)`.)

## 4. Accesibilidad — APROBADO (tras las correcciones del §7)

- **Foco visible:** `:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }` global; foco en input además con borde + `box-shadow inset`. Contraste del anillo `#93c5fd` sobre fondo 10,78:1 (WCAG 2.2 exige ≥ 3:1).
- **Táctil 44 px:** `min-height: var(--size-touch-target)` (44 px) en `button, nav button, input, textarea, select`; gap entre pestañas 8 px (`--space-2`); `#passwordToggle` 44×44. Tras la corrección del §7, también el botón × de cierre de mensaje.
- **Inputs 16 px:** `font-size: var(--font-size-input)` (16 px) en `input, textarea, select` (iOS sin auto-zoom). `#settingsJson` a 13 px mono es la vista avanzada de código, admitida por la spec §4.4.
- **Contrastes calculados con la fórmula WCAG (no estimados):** texto/fondo 16,16:1; texto/tarjeta 13,22:1; dim/fondo 7,64:1; dim/tarjeta 6,25:1; blanco/primario `#1d4ed8` 6,70:1; texto `#e8eaf0`/primario 5,57:1; blanco/hover `#2563eb` 5,17:1; enlace/fondo 10,78:1; enlace/tarjeta 8,82:1; ok texto 6,98:1; error texto 5,75:1; aviso texto 7,41:1; pill ok 7,52:1; pill error 6,93:1; pill aviso 12,03:1; blanco/peligro 10,02:1; blanco/info 11,50:1; blanco/badge `#dc2626` 4,83:1; plataformas como texto 4,87:1–11,55:1. **Todos los pares de texto pasan AA 4,5:1.** Las dos únicas parejas por debajo son no-texto y están documentadas y aceptadas en OVERRIDES §2: tarjeta/fondo 1,22:1 y borde/tarjeta 2,10:1 (la separación la dan borde + sombra). El caso `#e8eaf0` sobre `#2563eb` (4,30:1) **no ocurre**: la pestaña activa y los botones usan texto blanco (`--color-on-primary`) sobre el primario/hover.
- **`role="alert"` / `aria-live`:** `#msg` con `role="status"` (éxito, auto-limpieza 5 s) o `role="alert"` (error, con ×); `#loginMsg` con `role="alert"`; 8 errores de campo con `role="alert"` + `aria-describedby` + `aria-invalid` + foco al primer inválido; badge con `role="status" aria-live="polite"` y texto contextual (`3 avisos sin leer`, tope `9+`, oculto en 0, `aria-label` del botón de pestaña); tabs con `role="tablist"` / `role="tab"` / `aria-selected` / `aria-controls` y `role="tabpanel"`; desglose con `aria-expanded`; toggle de contraseña con `aria-pressed` / `aria-controls`; semáforo del Worker con `aria-live="polite"` y `aria-label` completo.
- **Sin glifos como iconos:** 0 ocurrencias de `▶ ■ ●` en html/js/css; SVG inline (play/stop, `aria-hidden="true"`) y puntos `.dot` CSS.
- **Sin hex crudo:** `app.js` 0 hex; `styles.css` 0 hex fuera de `:root`; `index.html` solo `#0b0d12` en `<meta name="theme-color">` (valor del token `--color-background`, exigido por VIS-14). `#3b82f6`, `#ef4444`, `#0000EE`: 0 en los tres archivos.
- **Otros:** skip link `Saltar al contenido`; `color-scheme: dark`; safe-area con `env(safe-area-inset-*)` en `body`; `prefers-reduced-motion` en CSS (anula transiciones/animaciones/scroll) y en JS (`scrollIntoView` con `behavior: "auto"`); `min-height: 100dvh`; tabla con `<thead>`/`<tbody>`/`scope="col"`/caption oculta y `overflow-x: auto`; tarjetas 9:16 (72×128, `aspect-ratio: 9/16`) a ≤ 640 px; `labels` con `for` en login, subida y ajustes; `<form>` de login con submit por Enter desde cualquier campo; `autocomplete="username"` / `"current-password"` sin bloqueo de pegado; credencial con `autocomplete="current-password"` (sin `off`).

## 5. Login real por API — PARCIALMENTE VERIFICADO

- `GET /api/health` → **200** `{"ok":true,"app":"avc","tenants":["sebastian","colega"],"worker_running":{"sebastian":false,"colega":false},"time":"2026-11-17T20:54:14.633Z"}`.
- `POST /api/auth/login` con `Origin: https://avc-em9.pages.dev`, usuario `seba`, contraseña incorrecta → **401** `{"ok":false,"error":"Usuario o contraseña incorrectos."}`, con `Access-Control-Allow-Origin: https://avc-em9.pages.dev` y `Access-Control-Allow-Credentials: true`. El camino de rechazo funciona y el CORS del front es correcto.
- **Login 200 + `GET /api/auth/me` con cookie: PENDIENTE de verificar por el coordinador** (la contraseña no está en el contexto de este QA; no se escribe aquí).

## 6. Checklist pre-delivery de `pro-rules.md` — punto por punto

Leyenda: APROBADO / RECHAZADO (bloqueante o menor, indicado) / NO VERIFICADO EN VIVO / N/A.

### Proceso

| Ítem | Resultado |
|---|---|
| Solo búsquedas relevantes para la interfaz | APROBADO — este QA no necesitó búsquedas; las auditorías previas las hicieron (`search.py --domain ux`, 7 temas + estilo/color/tipografía) y este informe re-verifica su resultado en código |
| Revisado `quick-reference.md` §1–§3 (CRÍTICO + HIGH) como paso final | APROBADO — leído completo (257 líneas) y contrastado: foco, contraste, táctil, errores, loading, skip link, safe-area |
| Probado a 375 px y en horizontal | NO VERIFICADO EN VIVO — sin navegador (limitación del §0, punto 1). Estático: media query ≤ 640 px, tarjetas 9:16, sin anchos fijos > viewport |
| Reduced-motion y Dynamic Type / texto más grande | APROBADO (reduced-motion, estático: media query + `matchMedia` en `scrollIntoView`) / NO VERIFICADO EN VIVO (texto grande/Dynamic Type) |
| Contraste en modo oscuro verificado de forma independiente | APROBADO — calculado, ver §4 (el panel es solo oscuro; no existe tema claro) |
| Touch targets ≥ 44 pt y nada oculto tras safe areas | APROBADO (tras §7) |

### Calidad visual

| Ítem | Resultado |
|---|---|
| Sin emojis como iconos (SVG en su lugar) | APROBADO — 0 glifos, SVG + `.dot` CSS |
| Iconos de una familia y estilo consistentes | APROBADO — 2 SVG inline (play/stop, 16×16, rellenos); marcas de plataforma como punto + nombre (ver limitación documentada abajo) |
| Recursos oficiales de marca con proporciones y espacio correctos | N/A con nota — no se dibujaron SVG de marca (YouTube/TikTok/Instagram/Facebook); la spec §2.2 de Cuentas admite el punto de color + nombre como fallback y eso es lo implementado. No bloqueante |
| El estado de prensa no desplaza el layout ni causa jitter | APROBADO — `transform: scale(.98)` no afecta al layout; `filter: brightness` tampoco |
| Tokens semánticos consistentes, sin colores hardcodeados por pantalla | APROBADO (tras §7; quedan solo estilos inline dinámicos de datos: alto de skeleton y ancho de barra de progreso) |

### Interacción

| Ítem | Resultado |
|---|---|
| Feedback de prensa claro (ripple/opacidad/elevación) | APROBADO — hover brillo 1.12, active escala 0.98, < 250 ms |
| Touch targets ≥ 44×44 pt | APROBADO (tras §7) |
| Micro-interacciones con tokens compartidos y responsivas | APROBADO — 150/200/250 ms, `ease-out` |
| Estados deshabilitados claros y no interactivos | APROBADO — `opacity: .55`, `cursor: not-allowed`, atributo `disabled`; botones async deshabilitados + texto `…`; deshabilitado por estado (Iniciar en marcha, Parar parado) |
| Orden de foco = orden visual, labels descriptivos | APROBADO con gap menor — labels con `for`, orden DOM = visual; **el foco no se mueve al cambiar de pestaña** (UX-17, media; ninguna spec de diseño lo incluyó en su checklist de aceptación). Menor, no bloqueante |
| Sin conflictos de gestos (tap/drag/back-swipe) | APROBADO — sin gestos; solo botones; la tarjeta no es clicable, solo "Ver detalle" |

### Modo claro/oscuro

| Ítem | Resultado |
|---|---|
| Texto primario ≥ 4,5:1 en claro y oscuro | APROBADO (oscuro; el panel es solo oscuro por diseño, no hay tema claro) |
| Texto primario y secundario ≥ 4,5:1 en claro y oscuro | APROBADO (oscuro: 13,22:1 / 6,25:1 sobre tarjeta) |
| Divisores/bordes y estados distinguibles en ambos modos | APROBADO (oscuro: borde 2,10:1, estados con pill + texto) |
| Scrim de modal medido contra el fondo real | N/A — no hay modales propios; la confirmación es `confirm()` nativo |
| Ambos temas probados | N/A — tema único oscuro |

### Layout

| Ítem | Resultado |
|---|---|
| Safe areas respetadas (headers, tabs, CTA) | APROBADO — `env(safe-area-inset-*)` en `body` |
| Contenido con scroll no oculto tras barras fijas/sticky | APROBADO (tras §7: `scroll-margin-top: 72px` en `#clipDetailCard`; la nav es `sticky`) |
| Verificado en teléfono pequeño, grande y tablet (vertical + horizontal) | NO VERIFICADO EN VIVO — sin navegador |
| Gutters adaptativos por tamaño/orientación | APROBADO con gap menor — `main` 18 px → 12 px en móvil; **header/nav siguen a 18 px fijos mientras `main` centra a 980 px (VIS-16, baja, no incluida en el checklist de OVERRIDES §7)**. Menor, no bloqueante |
| Ritmo de espaciado 4/8 dp | APROBADO — escala 4/8/12/16/24/32 px |
| Medida de texto legible en pantallas grandes | APROBADO — `max-width: 980px` centrado |

### Accesibilidad

| Ítem | Resultado |
|---|---|
| Iconos decorativos junto a texto ocultos del árbol de accesibilidad | APROBADO — `aria-hidden="true"` en SVG y `.dot` |
| Imágenes/iconos significativos sin texto equivalente tienen alternativa | APROBADO — barra de prioridad `role="img"` + `aria-label` con el valor; anillo de score con "Puntos: X" en texto al lado |
| Controles de icono con nombre accesible y estado (selected/pressed/expanded) | APROBADO — toggle `aria-pressed`, desglose `aria-expanded`, tabs `aria-selected` |
| Campos con labels, hints y errores claros | APROBADO — labels con `for`, hints, errores bajo el campo con `aria-describedby` |
| Color no es el único indicador | APROBADO — pills con texto, punto + texto en semáforo y matriz |
| Reduced motion y texto grande sin roturas | APROBADO (reduced-motion) / NO VERIFICADO EN VIVO (texto grande) |
| UI fija y overlays no tapan el foco de teclado | APROBADO (tras §7) |
| Alternativas de botón/teclado para arrastrar/deslizar | APROBADO — no hay interacciones de arrastre |
| Autenticación permite gestores de contraseñas y pegado, con alternativa no cognitiva | APROBADO — `autocomplete` correcto, sin bloqueo de pegado, toggle Mostrar/Ocultar |
| Contenido auto-rotativo con pausa/stop | N/A — no hay contenido auto-rotativo |
| Formularios fallidos conservan errores inline; multi-error enfoca el resumen | APROBADO — errores por campo conservados al corregir, foco al primer inválido, `#loginMsg`/`#msg` con `role="alert"` como resumen |

## 7. Defectos encontrados y corregidos por este QA (menores, ninguno bloqueante)

Se corrigieron directamente en `frontend/dist/` (autorizado por el encargo para defectos pequeños y claros) y se re-verificó después (`node --check` OK, 200 en los 3 archivos, hex 0, ids intactos):

1. **`.msg-close` (botón × del mensaje de error) medía 32×32 px** — por debajo del mínimo de 44 px (`styles.css`, la regla de clase pisaba la regla global de `button`). Corregido a `min-height/min-width: var(--size-touch-target)` (44 px). Además su `:hover` heredaba el fondo azul del primario (`button:hover`); añadido `.msg-close:hover { background: transparent; }`.
2. **Estilos inline estáticos restantes (VIS-8):** `style="flex:1; min-width:240px"` (×2) y `style="margin-top:16px"` en `index.html`; `style="margin-top:12px"` y `style="text-align:left"` en `app.js`. Movidos a clases nuevas en `styles.css`: `.detail-col`, `.mt-3`, `.mt-4`, `.clip-title-btn`. Quedan solo los inline **dinámicos** (valores de datos, no estilo estático): alto del skeleton (`style="height:…px"`) y ancho del relleno de la barra de prioridad (`style="width:…%"`), más `style.width` por JS en la subida — correctos así.
3. **Posible desborde de chips de plataforma a 360 px:** en la rejilla de 2 columnas de la tarjeta (`.platform-matrix.cols-2`), un chip `YouTube/Instagram + pill` con `white-space: nowrap` podía superar su columna (~148 px a 360 px). Añadido `min-width: 0` a `.platform-chip` y `white-space: normal` en la variante de 2 columnas. No verificable visualmente sin navegador; el cambio solo permite el ajuste de línea del chip si hace falta.
4. **La nav fija podía tapar el título al abrir el detalle** (`scrollIntoView` con `block: "start"` + `nav sticky`, foco a `#clipTitle` con `preventScroll`): añadido `#clipDetailCard { scroll-margin-top: 72px; }` (WCAG `focus-not-obscured`).

## 8. Observaciones menores restantes (no bloqueantes, no corregidas)

- **UX-17 (media):** al cambiar de pestaña el foco no se mueve al contenido (ninguna de las 4 specs de diseño lo incluyó en su checklist; solo el detalle de clip mueve el foco, y eso sí está implementado).
- **UX-8 (alta, parcial):** las pestañas sincronizan el hash (`#worker`, …) y se lee al cargar, pero el cambio usa `history.replaceState`, no `pushState`: Atrás no vuelve a la pestaña anterior. La spec de Worker lo dejaba fuera de alcance.
- **UX-24 (media):** `#healthLine` no tiene botón "Reintentar" ni hora del último intento (la spec de Worker §2.1.1 lo asigna al diseño global del header, que no existe en este encargo).
- **UX-30 (baja):** "Salir" sin confirmación ni separación especial.
- **VIS-16 (baja):** header/nav a 18 px fijos frente a `main` centrado a 980 px en escritorio (desalineación de bordes izquierdos).
- **Logos de marca:** punto de color + nombre en vez de SVG oficiales (limitación ya documentada en `implementacion.md` §1; la spec lo admite como fallback).
- **Franja 24:00:** `input type="time"` no admite `24:00`; si el ajuste guardado trae `24:00`, el campo queda vacío y el hint dice "hoy la franja es 18:00–24:00" (limitación ya documentada; `formToSettings` conserva la clave por merge, sin pérdida de datos).
- **Sin verificación visual en vivo ni login 200:** ver §0, puntos 1–2 (pendientes del coordinador, no del front).

## Veredicto

**APROBADO.** Sin defectos bloqueantes. Sintaxis, servicio local, ids (62 estáticos + 7 dinámicos verificados), endpoints (19 rutas, base y cuerpos sin cambio), fórmulas, requisito de nombre (0 coincidencias), accesibilidad (foco, 44 px, inputs 16 px, contrastes calculados, roles/aria) y API (health 200, login incorrecto 401 con CORS correcto) verificados. Los 4 defectos menores hallados fueron corregidos en `frontend/dist/` y re-verificados. Pendientes fuera del front: prueba visual en vivo a 360/375 px y login 200 con cookie (coordinador).
