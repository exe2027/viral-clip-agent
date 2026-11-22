# Auditoría UX — Panel AVC (Clips GTA 6)

- **Fecha:** 2026-11-17
- **Alcance auditado:** `~/workspace/gta6-clips/independencia/panel-backend/frontend/dist/index.html` (150 líneas, leído completo) y `app.js` (258 líneas, leído completo). Login + 5 pestañas: Worker, Cola, Cuentas, Avisos, Ajustes.
- **Sitio / API:** https://avc-em9.pages.dev · https://avc.vikingosfoodspa.workers.dev
- **Rol:** solo auditoría. No se escribió ni editó código del front; no se tocó lógica de negocio ni API; no hay contraseñas ni secretos en este informe.
- **Método:** skill obligatoria `ui-ux-pro-max` — leídos `SKILL.md`, `references/quick-reference.md` (120 guías) y `references/pro-rules.md`; búsquedas con `search.py --domain ux` para: contraste/accesibilidad/foco, táctil 44px, errores/validación/labels, loading/botón deshabilitado, navegación/pestañas/atrás, scroll horizontal/viewport, autenticación accesible, empty states/toasts/aria-live. Contrastes calculados, no estimados; alturas táctiles calculadas desde el padding + línea de texto del CSS.

## Mediciones verificadas (no estimadas)

| Par | Contraste | Veredicto |
|---|---|---|
| Texto `#e8eaf0` sobre fondo `#0f1115` / tarjeta `#1a1d24` | 15,71:1 / 14,02:1 | Pasa 4.5:1 |
| Texto secundario `#9aa3b2` sobre `#0f1115` / `#1a1d24` | 7,43:1 / 6,63:1 | Pasa 4.5:1 (incluso a 12–13 px) |
| Blanco `#fff` sobre azul primario `#3b82f6` (botones y pestaña activa) | **3,68:1** | **Falla 4.5:1** (texto normal 14 px) |
| Blanco sobre `#7f1d1d` (error / peligro) | 10,02:1 | Pasa |
| Blanco sobre `#1e3a5f` (info) | 11,50:1 | Pasa |
| `#22c55e` ok / `#ef4444` bad / `#f59e0b` warn sobre `#0f1115` | 8,29 / 5,02 / 8,80:1 | Pasan |
| Píldora ok `#bbf7d0`/`#14532d`, píldora bad `#fecaca`/`#7f1d1d` | 7,52 / 6,93:1 | Pasan |

| Elemento | Altura real (padding + texto 14 px ≈ 19 px) | Mínimo |
|---|---|---|
| Botón de pestaña `nav button` (padding 8 px) | ≈ 35 px | 44 px — falla |
| Botón general `button` (padding 9 px) | ≈ 37 px | 44 px — falla |
| `input, textarea, select` (padding 9 px) | ≈ 37 px | 44 px — falla |
| Separación entre pestañas (`nav gap: 6px`) | 6 px | 8 px — falla |

- Viewport correcto: `width=device-width, initial-scale=1`, sin `maximum-scale` ni `user-scalable=no` (el zoom no está bloqueado — bien).
- Búsquedas `search.py --domain ux` ejecutadas y con resultados en los 7 temas pedidos; ninguna devolvió 0 resultados.

## Lo que está bien (no tocar)

- Labels visibles en Login, Cuentas y Ajustes (no es placeholder-only); `autocomplete="username"` y `autocomplete="current-password"` correctos en login; no hay bloqueo de pegado.
- Estado del Worker (`EN MARCHA` / `PARADO`) y de cuentas (`Conectada` / `Sin credencial`) se comunica con texto, no solo con color.
- Estados vacíos con mensaje explicativo en Cola, Cuentas y Avisos.
- Contraste del texto principal y secundario sobrado, también en modo oscuro (único tema del panel).
- Una sola CTA primaria por pantalla en la práctica (Entrar / Guardar ajustes); navegación de 5 pestañas, dentro del límite.

## Hallazgos

### 1. Sin anillo de foco visible definido en ningún control
- **Pantalla:** Todas (Login, Worker, Cola, Cuentas, Avisos, Ajustes).
- **Elemento exacto:** todo el `<style>` de `index.html` — no existe ninguna regla `:focus`, `:focus-visible` ni `outline` (grep: 0 coincidencias); tampoco `aria-*`, `role` ni `tabindex` en todo el front.
- **Problema:** el foco de teclado depende del anillo por defecto del navegador, sin grosor, color ni contraste garantizados sobre fondo oscuro; un usuario de teclado no distingue dónde está.
- **Guía que viola:** `focus-states` (anillo visible 2–4 px en todo control interactivo) y `focus-appearance` (WCAG 2.2, contraste de estado 3:1).
- **Severidad:** Crítica.
- **Corrección:** añadir `:focus-visible { outline: 3px solid #93c5fd; outline-offset: 2px; }` para `button, input, textarea, select, a`.

### 2. Texto blanco sobre azul primario con contraste 3,68:1 (falla 4.5:1)
- **Pantalla:** Todas.
- **Elemento exacto:** `button { background: var(--acc) #3b82f6; color: #fff; font-size: 14px; }` y `nav button.active { background: var(--acc); }` (p. ej. "Entrar", "Iniciar", "Guardar credencial", pestaña activa "Worker").
- **Problema:** 3,68:1 < 4.5:1 exigido para texto normal; es el color de todas las acciones primarias y de la pestaña activa, no un caso aislado.
- **Guía que viola:** `color-contrast` (mínimo 4.5:1 texto normal) y `color-accessible-pairs`.
- **Severidad:** Crítica.
- **Corrección:** oscurecer el primario a `#2563eb` (blanco: ~5,2:1) o subir el texto a negrita 16 px+ manteniendo el azul solo para texto grande.

### 3. Ninguna acción asíncrona deshabilita el botón ni muestra carga (doble envío posible)
- **Pantalla:** Worker, Cola, Cuentas, Ajustes.
- **Elemento exacto:** `#btnStart`, `#btnStop`, `#btnRunOnce`, `#btnPublish`, `#btnUpload`, botones `data-save` / `data-verify` (generados en `loadAccounts()`), `#btnSaveSettings`; en `app.js` solo `doLogin()` hace `$("loginBtn").disabled = true` y sin cambiar su texto.
- **Problema:** pulsar dos veces "Publicar ahora", "Iniciar" o "Guardar credencial" dispara dos POST; durante la espera no hay spinner, texto de carga ni skeleton, y el contenido viejo queda como si nada pasara.
- **Guía que viola:** `loading-buttons` (deshabilitar botón en async + spinner/progreso) y `loading-states` / `submit-feedback`.
- **Severidad:** Crítica.
- **Corrección:** en cada handler, deshabilitar el botón y poner su texto en "Guardando… / Publicando… / Verificando…" hasta resolver la promesa, igual que ya hace `doLogin()`.

### 4. Errores solo en un banner superior, sin error junto al campo, sin anuncio ni foco
- **Pantalla:** Login, Cuentas, Ajustes, Cola (subida/publicación).
- **Elemento exacto:** `#loginMsg` / `#msg` (`showMsg()` y `showLoginMsg()` en `app.js`, `div.msg.err` en la parte superior) — ningún `aria-live`, `role="alert"`, `aria-describedby` ni error bajo el campo en todo el front.
- **Problema:** un error de credencial, de JSON inválido o de subida aparece lejos del campo que lo causó; el lector de pantalla no lo anuncia, el foco no se mueve al primer campo inválido y el usuario tiene que adivinar qué corregir.
- **Guía que viola:** `error-placement` (error específico bajo el campo + `aria-describedby`), `aria-live-errors`, `focus-management` y `error-summary`.
- **Severidad:** Crítica.
- **Corrección:** poner el error bajo su campo con `id` + `aria-describedby`, `role="alert"` en `#msg`, y mover el foco al primer campo inválido tras un envío fallido.

### 5. La tabla de la Cola provoca scroll horizontal en móvil (360 px)
- **Pantalla:** Cola.
- **Elemento exacto:** `#clipList` → `<table>` de 5 columnas (`Clip, Estado, Puntos, Plataformas, Creado`, generado en `loadClips()`) sin contenedor con `overflow-x: auto`, sin `<thead>`/`<caption>`/`scope` y sin reflow a tarjetas.
- **Problema:** 5 columnas con títulos, plataformas y fechas no caben en 360 px: la tabla desborda el viewport y obliga a scroll horizontal; además las filas no son semántica de tabla accesible.
- **Guía que viola:** `horizontal-scroll` (sin scroll horizontal en móvil) y `data-table` (alternativa accesible / estructura de tabla).
- **Severidad:** Crítica.
- **Corrección:** envolver la tabla en un `div` con `overflow-x: auto` y, en ≤640 px, renderizar cada clip como tarjeta apilada en vez de fila de tabla.

### 6. Todos los objetivos táctiles miden 35–37 px, por debajo de 44 px
- **Pantalla:** Todas.
- **Elemento exacto:** `nav button` ≈ 35 px, `button` / `input` / `select` ≈ 37 px (padding 8–9 px + texto 14 px); "Marcar leída" (`data-read`, Avisos) y "Verificar ahora" (`data-verify`, Cuentas) incluidos.
- **Problema:** en teléfono se falla el toque y se pulsa el botón vecino; las pestañas además están separadas solo 6 px.
- **Guía que viola:** `touch-target-size` (mín. 44×44 px) y `touch-friendly-input` (input móvil ≥44 px).
- **Severidad:** Alta.
- **Corrección:** subir el padding vertical a 12–13 px y fijar `min-height: 44px` en `button`, `nav button`, `input`, `select` y `textarea`.

### 7. Inputs de 14 px: iOS hace zoom automático al enfocarlos
- **Pantalla:** Login (`#username`, `#password`), Cuentas (`#cred-<id>`, `#extra-<id>`), Ajustes (`#settingsJson`).
- **Elemento exacto:** `input, textarea, select { font-size: 14px; }` en `index.html`.
- **Problema:** por debajo de 16 px, Safari en iPhone amplía la página al enfocar el campo y no siempre vuelve atrás; el usuario pierde el contexto del formulario.
- **Guía que viola:** `readable-font-size` (mínimo 16 px de cuerpo en móvil, evita auto-zoom de iOS).
- **Severidad:** Alta.
- **Corrección:** poner `font-size: 16px` en `input, textarea, select` (el resto del texto puede seguir en 14 px).

### 8. Las pestañas no tienen URL, historial ni estado: Atrás saca de la app y se pierde el contexto
- **Pantalla:** Worker, Cola, Cuentas, Avisos, Ajustes.
- **Elemento exacto:** `nav#tabs` (5 `<button data-tab="…">`) y `switchTab()` en `app.js` — sin `location.hash`, `history.pushState`, `role="tablist"`/`role="tab"` ni `aria-selected`; `selectedClipId` y el scroll se pierden al cambiar de pestaña.
- **Problema:** no se puede compartir ni recargar en una pestaña, el botón Atrás del navegador no vuelve a la pestaña anterior (sale del panel o del login) y el detalle de un clip abierto se pierde al cambiar de pestaña.
- **Guía que viola:** `deep-linking` (toda pantalla clave alcanzable por URL), `back-behavior` (Atrás predecible, preserva estado) y `state-preservation`.
- **Severidad:** Alta.
- **Corrección:** sincronizar la pestaña con `#worker | #cola | #cuentas | #avisos | #ajustes` en la URL con `history.pushState` y leer el hash al cargar.

### 9. La pestaña activa solo se distingue por el color de fondo
- **Pantalla:** Todas (barra de pestañas).
- **Elemento exacto:** `nav button.active { background: var(--acc); border-color: var(--acc); }` — sin subrayado, negrita, icono, texto extra ni `aria-selected="true"`.
- **Problema:** quien no distingue el azul del gris (o usa lector de pantalla) no sabe en qué pestaña está; además el blanco sobre ese azul es el contraste fallido del hallazgo 2.
- **Guía que viola:** `color-not-only` (no comunicar solo con color), `nav-state-active` y `icon-context`/`aria-labels`.
- **Severidad:** Alta.
- **Corrección:** añadir a la pestaña activa `aria-selected="true"`, `font-weight: 700` y un borde inferior de 3 px además del fondo azul.

### 10. Ajustes es un JSON crudo de 14 filas: el usuario edita sintaxis, no ajustes
- **Pantalla:** Ajustes.
- **Elemento exacto:** `<label>Ajustes (JSON)</label>` + `<textarea id="settingsJson" rows="14">` y `saveSettings()` (`JSON.parse` + error genérico en `#msg`).
- **Problema:** subtítulos, límites diarios, franja horaria y Telegram se editan como texto JSON sin validar por campo; un carácter mal puesto invalida todo y el mensaje es el error crudo de JS ("Ajustes no válidos o error: …"), sin decir qué campo ni cómo arreglarlo.
- **Guía que viola:** `progressive-disclosure` (no abrumar de golpe), `input-helper-text`, `error-clarity` (causa + cómo arreglar) e `inline-validation`.
- **Severidad:** Alta.
- **Corrección:** sustituir el JSON por un formulario con un campo por ajuste (número, hora, texto) y dejar el JSON solo como vista avanzada de solo lectura.

### 11. Parar el Worker y Publicar no piden confirmación (acciones destructivas / irreversibles)
- **Pantalla:** Worker, Cola (detalle del clip).
- **Elemento exacto:** `#btnStop` ("■ Parar", clase `.danger`) y `#btnPublish` ("Publicar ahora") — `workerAction()` y `publishSelected()` se ejecutan al primer toque, sin diálogo.
- **Problema:** un toque accidental para la cola (el usuario cree que nada se pierde, pero la publicación encola jobs reales) o publica un clip sin revisar; no hay deshacer.
- **Guía que viola:** `confirmation-dialogs` (confirmar antes de acciones destructivas) y `destructive-emphasis`.
- **Severidad:** Alta.
- **Corrección:** pedir confirmación con `confirm()` nativo (o diálogo propio) en "Parar" y "Publicar ahora" indicando qué se va a parar/publicar.

### 12. Login sin `<form>`: Enter solo funciona en la contraseña y no hay botón de mostrar contraseña
- **Pantalla:** Login.
- **Elemento exacto:** `#loginBox` es un `<div class="card">` (no `<form>`); solo `$("password").addEventListener("keydown", Enter → doLogin())`, `#username` no tiene handler; `#password` es `type="password"` sin toggle.
- **Problema:** pulsar Enter con el foco en Usuario no hace nada; en móvil no aparece el botón "Ir" del teclado; un error de tipeo en la contraseña no se puede revisar sin borrarla.
- **Guía que viola:** `password-toggle` (mostrar/ocultar contraseña), `accessible-authentication` (permitir gestores de contraseñas y envío estándar) y `submit-feedback`.
- **Severidad:** Alta.
- **Corrección:** envolver el login en `<form>` con submit real y añadir un botón "Mostrar" que cambie `#password` entre `password` y `text`.

### 13. Ningún `<label>` está asociado a su campo (`for` ausente en todos)
- **Pantalla:** Login, Cola, Cuentas, Ajustes.
- **Elemento exacto:** `<label>Usuario</label>` + `<input id="username">`, `<label>Contraseña</label>` + `<input id="password">`, `<label>Subir MP4…</label>` + `<input id="videoFile">`, `<label>Credencial…</label>` + `<input id="cred-${a.id}">`, `<label>Ajustes (JSON)</label>` + `<textarea id="settingsJson">` — ningún `for` en todo el front.
- **Problema:** tocar el texto del label no enfoca el campo (objetivo táctil perdido) y el lector de pantalla no anuncia el nombre del campo de forma fiable.
- **Guía que viola:** `form-labels` (`label` con atributo `for`) e `input-labels`.
- **Severidad:** Alta.
- **Corrección:** añadir `for="username"`, `for="password"`, `for="videoFile"`, `for="cred-<id>"`, `for="extra-<id>"` y `for="settingsJson"` a cada label.

### 14. El mensaje de error de login es jerga de desarrollo, sin vía de recuperación
- **Pantalla:** Login.
- **Elemento exacto:** `#loginHint` tras un fallo: "Si acabas de crear el panel y aún no tienes usuario, hay que crear los usuarios de login primero (seed-users.sql)."
- **Problema:** el usuario no puede crear usuarios ni sabe qué es `seed-users.sql`; el mensaje no dice si falló el usuario, la contraseña o la API, ni qué hacer (reintentar, pedir acceso).
- **Guía que viola:** `error-clarity` (causa + cómo arreglar), `error-recovery` (vía de recuperación: reintentar, editar, ayuda) y `error-feedback`.
- **Severidad:** Media.
- **Corrección:** cambiar el texto a "Usuario o contraseña incorrectos. Revisa ambos y vuelve a intentarlo; si acabas de crear el panel, pide que te creen el usuario." y distinguir el fallo de red ("API no responde, reintenta").

### 15. Sin feedback de prensa ni `touch-action`: los botones no responden al toque
- **Pantalla:** Todas.
- **Elemento exacto:** `button` y `nav button` en el CSS — solo `cursor: pointer`, sin `:hover`, `:active`, `transition` ni `touch-action: manipulation`.
- **Problema:** al tocar no hay cambio visual en <100 ms; el usuario no sabe si el toque entró y vuelve a pulsar (agrava el hallazgo 3).
- **Guía que viola:** `press-feedback` (feedback visual al pulsar), `tap-feedback-speed` (feedback en <100 ms) y `tap-delay` (`touch-action: manipulation`).
- **Severidad:** Media.
- **Corrección:** añadir `button:active { filter: brightness(1.15); transform: scale(0.98); }` y `touch-action: manipulation` en `button, a`.

### 16. El mensaje `#msg` es persistente, sin cierre ni auto-descarte, y no se anuncia
- **Pantalla:** Todas (tras cada acción).
- **Elemento exacto:** `<div id="msg">` + `showMsg()` — el `.msg` queda fijo hasta la siguiente acción, sin botón de cerrar, sin auto-descarte y sin `aria-live="polite"`.
- **Problema:** un "Ajustes guardados" de hace 10 minutos sigue visible y se confunde con el estado actual; un error puede pasar desapercibido si el usuario estaba mirando otra zona.
- **Guía que viola:** `toast-dismiss` (auto-descarte 3–5 s), `toast-accessibility` (no robar foco, `aria-live="polite"`) y `success-feedback`.
- **Severidad:** Media.
- **Corrección:** dar a `#msg` `role="status" aria-live="polite"` y auto-limpiar los mensajes de éxito a los 5 s (los errores quedan hasta cerrar con una ×).

### 17. Al cambiar de pestaña el foco no se mueve ni se anuncia el cambio
- **Pantalla:** Todas.
- **Elemento exacto:** `switchTab()` — solo alterna `.hidden` y `.active`; sin mover el foco a la sección ni `aria-live` ni título de sección enfocable.
- **Problema:** el lector de pantalla sigue en el botón de la pestaña y no anuncia el contenido nuevo; el usuario de teclado tiene que tabular desde arriba para llegar al contenido.
- **Guía que viola:** `focus-on-route-change` (mover el foco al contenido principal tras el cambio) y `keyboard-nav`.
- **Severidad:** Media.
- **Corrección:** tras `switchTab()`, hacer `document.querySelector('#tab-' + name + ' h2').setAttribute('tabindex','-1')` y `.focus()` en él.

### 18. Sin skip link ni landmarks completos para teclado
- **Pantalla:** Todas.
- **Elemento exacto:** `<body>` — no hay enlace "Saltar al contenido", y `<main>` existe pero `nav#tabs` no tiene `aria-label` y el header no es `<header>` con landmark claro para el lector.
- **Problema:** un usuario de teclado tabula por el header, la salud de la API, "Salir" y las 5 pestañas antes de llegar al contenido en cada carga.
- **Guía que viola:** `skip-links` (saltar al contenido principal) y `consistent-help`.
- **Severidad:** Media.
- **Corrección:** añadir como primer elemento `<a href="#main" class="skip-link">Saltar al contenido</a>` con CSS que lo muestre al enfocarlo.

### 19. La insignia de Avisos es un número pelado, sin frase contextual ni región live
- **Pantalla:** Avisos (pestaña) y barra de pestañas.
- **Elemento exacto:** `<span id="unreadBadge">` dentro del botón "Avisos" — `loadNotifications()` escribe solo `(3)`; sin `role="status"`, `aria-live` ni texto.
- **Problema:** si cambia de 0 a 3 no se anuncia nada, y si se anunciara sería solo "3", sin contexto; el `●` de cada aviso no leído depende de un glifo.
- **Guía que viola:** `contextual-live-badge-updates` (anunciar frase completa, una sola región live atómica) y `tab-badge`.
- **Severidad:** Media.
- **Corrección:** poner `<span id="unreadBadge" role="status" aria-live="polite">3 avisos sin leer</span>` y actualizar el texto completo, no solo el número.

### 20. El campo de credencial bloquea el gestor de contraseñas y no se puede revisar
- **Pantalla:** Cuentas.
- **Elemento exacto:** `<input id="cred-${a.id}" type="password" autocomplete="off" placeholder="Pega aquí el token / API key">` (generado en `loadAccounts()`), sin toggle mostrar/ocultar.
- **Problema:** `autocomplete="off"` impide que el gestor guarde/rellene el token, el usuario pega a ciegas un token largo sin poder verificarlo, y un error de pegado solo se descubre al pulsar "Verificar ahora".
- **Guía que viola:** `accessible-authentication` (permitir gestores de contraseñas y pegado) y `password-toggle`.
- **Severidad:** Media.
- **Corrección:** quitar `autocomplete="off"` (usar `autocomplete="current-password"` o dejar el valor por defecto) y añadir un toggle mostrar/ocultar por cuenta.

### 21. "Sin verificar" reaparece como estado aunque la auditoría v15 lo eliminó
- **Pantalla:** Cuentas.
- **Elemento exacto:** en `loadAccounts()`: `a.has_credential ? '<span class="pill">Sin verificar</span>' : …` — la píldora gris "Sin verificar" cuando hay credencial sin verificar.
- **Problema:** contradice la decisión del panel v15 (solo 4 estados: Conectada, Sin credencial, No conectada, Sin crear); el usuario ve un quinto estado que creía eliminado y no sabe si la cuenta está pendiente, rota o sin probar.
- **Guía que viola:** `consistency` (mismo estilo/estados en todas las pantallas) y `state-clarity`.
- **Severidad:** Media.
- **Corrección:** mapear `has_credential && !verify_ok` a "No conectada" con el motivo en `verify_message`, como define v15.

### 22. Sin skeleton ni reserva de espacio: las listas aparecen de golpe y el layout salta
- **Pantalla:** Worker (`#wActivity`), Cola (`#clipList`), Cuentas (`#accountList`), Avisos (`#notifList`).
- **Elemento exacto:** `loadWorker()`, `loadClips()`, `loadAccounts()`, `loadNotifications()` — sustituyen `innerHTML` de vacío a contenido sin skeleton, `aria-busy` ni altura reservada.
- **Problema:** al entrar a cada pestaña hay un vacío y luego un salto de layout (CLS); el usuario no sabe si está cargando o vacío.
- **Guía que viola:** `progressive-loading` (skeleton/shimmer en esperas >1 s), `content-jumping` (reservar espacio al contenido asíncrono) y `loading-states`.
- **Severidad:** Media.
- **Corrección:** mostrar 3 líneas skeleton con `aria-busy="true"` en cada lista mientras carga y sustituirlas por el contenido real.

### 23. Sin confirmación ni aviso al perder cambios sin guardar
- **Pantalla:** Ajustes (y Cola, al cambiar de clip con el detalle abierto).
- **Elemento exacto:** `#settingsJson` + `switchTab()` — cambiar de pestaña descarta el JSON editado sin guardar, sin aviso; no hay auto-guardado ni indicador "sin guardar".
- **Problema:** el usuario edita ajustes, toca otra pestaña por error y pierde el trabajo sin saberlo.
- **Guía que viola:** `form-autosave` (auto-guardar borradores en formularios largos) y `sheet-dismiss-confirm` (confirmar antes de descartar cambios sin guardar).
- **Severidad:** Media.
- **Corrección:** marcar el textarea como sucio al editar y preguntar "¿Salir sin guardar los ajustes?" al cambiar de pestaña con cambios pendientes.

### 24. La salud de la API falla sin reintento ni explicación
- **Pantalla:** Todas (header).
- **Elemento exacto:** `#healthLine` ("Comprobando API…" → "API no responde") en `checkHealth()` — sin botón Reintentar, sin hora del último intento y sin `aria-live`.
- **Problema:** si la API cae, el usuario solo ve "API no responde" en texto pequeño del header y no sabe si es su red, el Worker o algo temporal, ni qué hacer.
- **Guía que viola:** `error-recovery` (vía de recuperación: reintentar), `timeout-feedback` (feedback claro + reintento) y `offline-support`.
- **Severidad:** Media.
- **Corrección:** convertir `#healthLine` en "API no responde — Reintentar" con un botón que vuelva a llamar a `checkHealth()` y muestre la hora del intento.

### 25. Los estados vacíos explican pero no ofrecen acción
- **Pantalla:** Cola, Cuentas, Avisos.
- **Elemento exacto:** textos "La cola está vacía…", "No hay cuentas todavía…" y "Sin avisos." (en `loadClips()`, `loadAccounts()`, `loadNotifications()`) — sin botón de acción en ninguno.
- **Problema:** el usuario que ve la cola vacía no sabe si esperar al Worker, ir a Worker a iniciarlo o recargar; el que no tiene cuentas no tiene por dónde empezar.
- **Guía que viola:** `empty-states` (mensaje útil + acción).
- **Severidad:** Baja.
- **Corrección:** añadir a cada vacío un botón: "Ir al Worker" en Cola, "Actualizar" en Cuentas, "Actualizar" en Avisos.

### 26. El clip se abre con un enlace `<a href="#">` que salta arriba y no es un botón
- **Pantalla:** Cola.
- **Elemento exacto:** `<a href="#" data-clip="${c.id}">` en `loadClips()` (con `preventDefault()` en el handler) — toda la fila no es clicable, solo el título.
- **Problema:** `href="#"` es un anti-patrón: si el JS falla, el clic salta al inicio de la página; además el enlace no anuncia que abre un detalle y la fila no es operable por teclado como tal.
- **Guía que viola:** `keyboard-nav` (todo operable por teclado con semántica correcta) y `gesture-alternative` (controles visibles para acciones críticas).
- **Severidad:** Baja.
- **Corrección:** sustituir el `<a href="#">` por un `<button class="linklike">Ver detalle</button>` dentro de la celda del título.

### 27. `scrollIntoView({ behavior: "smooth" })` ignora `prefers-reduced-motion`
- **Pantalla:** Cola (detalle del clip).
- **Elemento exacto:** `$("clipDetailCard").scrollIntoView({ behavior: "smooth" })` en `selectClip()`; no hay ninguna regla `@media (prefers-reduced-motion)` en el CSS.
- **Problema:** el desplazamiento animado se fuerza incluso a quien pidió reducir el movimiento en el sistema.
- **Guía que viola:** `reduced-motion` (respetar `prefers-reduced-motion`) y `motion-meaning`.
- **Severidad:** Baja.
- **Corrección:** usar `behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'` y añadir la media query para desactivar transiciones.

### 28. Sin área segura: el contenido puede quedar bajo el notch / barra de gestos
- **Pantalla:** Todas (móvil).
- **Elemento exacto:** `body`, `header` y `main` — ningún `env(safe-area-inset-*)`, `padding-bottom` extra ni `min-h-dvh`; `#loginBox { margin: 60px auto; }` fijo.
- **Problema:** en iPhone con Dynamic Island / barra de gestos, el header o el último botón pueden quedar pegados al borde o bajo el chrome del sistema.
- **Guía que viola:** `safe-area-awareness` y `viewport-units` (preferir `dvh` a `vh` en móvil).
- **Severidad:** Baja.
- **Corrección:** añadir `padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)` al `body` o al contenedor principal.

### 29. La URL pública del MP4 subido se muestra como texto largo sin ajuste garantizado
- **Pantalla:** Cola (detalle del clip, tras subir).
- **Elemento exacto:** `showMsg("MP4 guardado en R2: " + d.r2_key + " · URL pública: " + d.public_url, …)` en `uploadSelected()`; `.msg` no tiene `overflow-wrap`.
- **Problema:** una URL larga de R2 puede desbordar la tarjeta en 360 px (el `pre` sí tiene `word-break`, el `.msg` no) y no es copiable con un toque ni es un enlace.
- **Guía que viola:** `long-token-wrapping` (URLs/IDs deben refluir con `overflow-wrap: anywhere`) y `truncation-strategy`.
- **Severidad:** Baja.
- **Corrección:** añadir `.msg { overflow-wrap: anywhere; }` y mostrar la URL como `<a href>` con un botón "Copiar".

### 30. "Salir" está junto a la salud de la API, sin separación ni confirmación
- **Pantalla:** Todas (header).
- **Elemento exacto:** `<button class="ghost hidden" id="logoutBtn">Salir</button>` junto a `#healthLine`, sin separación visual ni confirmación al cerrar sesión.
- **Problema:** cerrar sesión es una acción que saca de la app y está al mismo nivel visual que la información de estado; un toque accidental obliga a volver a loguearse.
- **Guía que viola:** `destructive-nav-separation` (acciones peligrosas separadas de la navegación normal) y `escape-routes`.
- **Severidad:** Baja.
- **Corrección:** separar "Salir" con margen y pedir confirmación, o moverlo a Ajustes al final con estilo peligro.

### 31. El input de archivo no valida tamaño/tipo antes de subir y no enlaza su label
- **Pantalla:** Cola (detalle del clip).
- **Elemento exacto:** `<input type="file" id="videoFile" accept="video/mp4,video/*">` + `#btnUpload`; `uploadSelected()` solo comprueba que haya archivo, no tamaño (máx. 128 MB, solo en el label) ni tipo.
- **Problema:** un archivo de 200 MB o no-MP4 empieza a subirse y falla tarde con un error genérico en `#msg`; el label tampoco tiene `for`.
- **Guía que viola:** `inline-validation` (validar al elegir, no solo al enviar), `input-helper-text` y `error-placement`.
- **Severidad:** Baja.
- **Corrección:** al cambiar el archivo, validar `f.size <= 128 MB` y `f.type` y mostrar el error bajo el campo antes de permitir subir.

### 32. El textarea JSON usa fuente proporcional (legibilidad del JSON)
- **Pantalla:** Ajustes.
- **Elemento exacto:** `#settingsJson` hereda `font-family: system-ui` (no monoespaciada), a 14 px.
- **Problema:** llaves, comillas y comas del JSON se distinguen peor en fuente proporcional; con el hallazgo 10, el JSON crudo es doblemente difícil de revisar.
- **Guía que viola:** `font-pairing` / legibilidad de datos y `number-tabular` (figuras y datos con ancho fijo).
- **Severidad:** Baja.
- **Corrección:** poner `#settingsJson { font-family: ui-monospace, monospace; font-size: 13px; }` mientras exista el editor JSON.

## Resumen por severidad

| Severidad | Hallazgos | IDs |
|---|---|---|
| Crítica | 5 | 1, 2, 3, 4, 5 |
| Alta | 8 | 6, 7, 8, 9, 10, 11, 12, 13 |
| Media | 11 | 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24 |
| Baja | 8 | 25, 26, 27, 28, 29, 30, 31, 32 |
| **Total** | **32** | |

## Los 10 más graves (orden de arreglo sugerido)

1. **#1 — Sin foco visible** (Crítica, todas las pantallas): añadir `:focus-visible` con outline 3 px.
2. **#2 — Blanco sobre azul 3,68:1** (Crítica): oscurecer el primario a `#2563eb`.
3. **#3 — Sin loading/deshabilitado en acciones async** (Crítica): deshabilitar + texto "…" en cada POST.
4. **#4 — Errores solo arriba, sin anuncio ni foco** (Crítica): error bajo el campo + `role="alert"` + foco al primer inválido.
5. **#5 — Tabla de Cola con scroll horizontal** (Crítica): envolver en `overflow-x: auto` y tarjetas en ≤640 px.
6. **#6 — Objetivos táctiles 35–37 px** (Alta): `min-height: 44px` en botones e inputs.
7. **#7 — Inputs 14 px, zoom iOS** (Alta): `font-size: 16px` en inputs.
8. **#8 — Pestañas sin URL ni Atrás** (Alta): hash `#worker/#cola/…` + `pushState`.
9. **#10 — Ajustes como JSON crudo** (Alta): formulario por campo, JSON solo como vista avanzada.
10. **#11 — Parar/Publicar sin confirmación** (Alta): diálogo de confirmación antes de ejecutar.

## Checklist pre-entrega (pro-rules.md) — estado actual

- [ ] Todos los objetivos táctiles ≥44 pt — **no** (35–37 px, hallazgo 6).
- [ ] Contenido no oculto tras áreas seguras — **no** (hallazgo 28).
- [ ] Contraste de texto ≥4.5:1 en el tema oscuro — **no** (botón primario 3,68:1, hallazgo 2).
- [ ] Sin emojis como iconos estructurales — parcial (`▶` / `■` son glifos de texto con etiqueta, aceptable; no hay SVG).
- [ ] Feedback de prensa claro — **no** (hallazgo 15).
- [ ] Estados deshabilitados claros y no interactivos — parcial (solo login deshabilita, hallazgo 3).
- [ ] Orden de foco = orden visual, labels descriptivos — **no** (labels sin `for`, sin skip link, hallazgos 13, 18).
- [ ] Color no es el único indicador — parcial (estados con texto, bien; pestaña activa solo color, hallazgo 9).
- [ ] Reduced motion y texto grande sin roturas — **no verificado** (sin media query, hallazgo 27).
- [ ] Autenticación permite gestores de contraseñas y pegado — parcial (login sí; credencial con `autocomplete="off"`, hallazgo 20).
- [ ] Formularios fallidos conservan errores inline y enfocan resumen — **no** (hallazgo 4).
- [ ] Probado a 375 px y en horizontal — **no verificado en vivo** (esta auditoría es de código; la tabla desborda a 360 px por inspección, hallazgo 5).
