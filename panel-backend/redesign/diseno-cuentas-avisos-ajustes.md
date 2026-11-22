# Diseño — Cuentas / Avisos / Ajustes (panel AVC)

- **Fecha:** 2026-11-17
- **Rol:** Rol 8 — Diseñador de Cuentas / Avisos / Ajustes. Solo especificación; no se edita el front.
- **Alcance:** solo las 3 pestañas `tab-cuentas`, `tab-avisos`, `tab-ajustes` de `frontend/dist/index.html` + `app.js`. Fuera de alcance: Login, Worker, Cola, Worker/API, fórmulas de viralidad/SEO, puertas de publicación.
- **Autoritativo:** `design-system/avc-panel/OVERRIDES.md` manda sobre `MASTER.md` (modo claro de lujo, descartado) y sobre `tokens.css` / `tokens.json`. Todo color, tipo, espaciado, radio, táctil y motion de esta spec sale de OVERRIDES §§2–5. No se introduce ningún token, hex, fuente ni componente nuevo.
- **Fuentes leídas:** `OVERRIDES.md`, `MASTER.md`, `tokens.css`, `redesign/auditoria-ux.md` (hallazgos UX-3, 4, 6, 7, 9, 10, 13, 15, 16, 19, 20, 21, 22, 23, 25, 32), `redesign/auditoria-visual.md` (VIS-5, 7, 8, 9, 11, 13, 14, 15, 18), `redesign/ideas.md` (#15 tarjetas de Cuentas, #16 Ajustes por secciones, #19 Avisos agrupados, #11 skeletons, #12 empty states), `frontend/dist/index.html`, `frontend/dist/app.js` (`loadAccounts`, `loadNotifications`, `loadSettings`/`saveSettings`), y el Worker (`worker/src/index.ts`, `worker/src/db.ts`, `worker/src/jobs.ts`) solo para verificar qué campos y endpoints existen de verdad.
- **Regla de oro:** los ids de DOM y los endpoints se conservan tal cual. Ningún cambio de esta spec exige cambiar la API. Donde el diseño propone algo que la API hoy no hace, se marca como **decisión pendiente** y no se inventa el endpoint ni el comportamiento.

## 0. Lo que se conserva (contrato con el implementador)

| Elemento | Valor actual (no tocar) |
|---|---|
| Pestañas / secciones | `#tab-cuentas`, `#tab-avisos`, `#tab-ajustes`; botones `nav button[data-tab="cuentas"\|"avisos"\|"ajustes"]` |
| Contenedores | `#accountList`, `#notifList`, `#settingsJson`, `#btnSaveSettings`, `#unreadBadge` (dentro del botón de la pestaña Avisos), `#msg` global |
| Cuentas — por cuenta | `input#cred-<id>` (`type="password"`), `input#extra-<id>`, `button[data-save="<id>"]`, `button[data-verify="<id>"]` |
| Avisos — por aviso | `button[data-read="<id>"]` |
| Endpoints | `GET /api/accounts` · `POST /api/accounts/<id>/credential` `{credential, extra}` · `POST /api/accounts/<id>/verify` · `GET /api/notifications` (máx. 50, orden `created_at DESC`) · `POST /api/notifications/<id>/read` · `GET /api/settings` · `PUT /api/settings` (el Worker hace `{...current, ...body}`: un PUT parcial conserva las claves no enviadas) |
| Datos que la API devuelve | Cuenta: `id, platform, language, label, status, note, verified_at, verify_ok, verify_message, has_credential, credential_masked`. Aviso: `id, message, created_at, read_at` (además `kind`, no usado hoy por el front). Ajustes: `settings` es un `Record<string, unknown>` libre, sin esquema tipado en el Worker |
| Texto de cabecera | Cuentas: «Aquí conectas YouTube, TikTok y Meta. Si dice Conectada, está lista. Las credenciales nunca se muestran: solo se pegan y se cifran en el Worker.» — se conserva, con el ajuste de estados del §1.3. Ajustes: «Configuración del panel (subtítulos, límites diarios, franja horaria, Telegram). Se guarda como JSON en el Worker.» — se conserva y se corrige en el §3.1 (ya no se guarda «como JSON» desde el punto de vista del usuario) |

## 1. Reglas globales para las 3 pestañas

Aplican OVERRIDES §§1–3 y 7; se repiten aquí solo las que el implementador debe verificar en estas pestañas.

- **Tokens obligatorios, sin hex crudo.** Todo color es un `var(--color-*)` de `tokens.css`. Prohibido `#3b82f6` (blanco 3,68:1, falla AA), `#ef4444` como texto, `#0000EE` como enlace. Primario `--color-primary: #1d4ed8` con texto blanco (6,70:1); hover `--color-primary-hover: #2563eb` solo con texto blanco.
- **Táctil 44 px.** `min-height: 44px` en todos los botones, inputs, selects y textareas de las 3 pestañas; `font-size: 16px` en todos los campos (evita el auto-zoom de iOS, UX-7/VIS-6). Gap entre controles `var(--space-2)` (8 px) como mínimo.
- **Foco visible.** `:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }` en todo control. Sin excepciones.
- **Feedback de prensa.** `button:hover { filter: brightness(1.12); }`, `button:active { transform: scale(.98); }`, `touch-action: manipulation`; transiciones 150–250 ms `ease-out`; `prefers-reduced-motion: reduce` anula transiciones y animaciones.
- **Labels asociados.** Todo `label` lleva `for="<id del campo>"` (UX-13). Tocar el texto del label enfoca el campo. Nada de placeholder como único nombre del campo.
- **Botones asíncronos (UX-3).** En cada acción async el botón queda `disabled` (opacidad .55, `cursor: not-allowed`) y cambia su texto hasta resolver: «Guardando…», «Verificando…», «Guardando ajustes…». Sin spinner decorativo sin texto: el texto es el estado. Doble toque no dispara doble POST.
- **Errores junto al campo (UX-4).** El error vive bajo su campo, con `aria-describedby="<id>-error"`, `aria-invalid="true"` en el campo, y `#msg` con `role="alert"` solo como resumen. Tras un envío fallido, el foco va al primer campo inválido. Éxito en `#msg` con `role="status" aria-live="polite"`, auto-limpieza a los 5 s; error queda hasta cerrarse con ×. `#msg { overflow-wrap: anywhere; }`.
- **Carga (UX-22, idea #11).** Al entrar a cada pestaña, 3 líneas skeleton con `aria-busy="true"` (bloques `--color-surface-input`, sin shimmer bajo `prefers-reduced-motion`) hasta que llega el `fetch`; nunca pantalla vacía ni salto de layout. Si el `fetch` falla, skeleton → mensaje de error con botón «Reintentar» (reintenta el `load*` correspondiente).
- **Estados siempre como pill con texto (OVERRIDES §4, VIS-10/11).** Nunca solo color, nunca texto plano. Componente `.pill` existente: `padding: 2px 9px`, radio 999 px, `font-size: 12px`, `font-weight: 600`; variantes `.pill.ok` (`--color-ok-bg`/`--color-ok-fg`, 7,52:1), `.pill.bad` (`--color-bad-bg`/`--color-bad-fg`, 6,93:1), `.pill.warn` (`--color-warn-bg`/`--color-warn-fg`, 12,03:1), `.pill.neutral` (fondo `--color-surface-input`, texto `--color-text-dim`).
- **Iconos.** Solo SVG inline (Lucide/Heroicons), `aria-hidden="true"`. Prohibidos emojis y glifos `▶ ■ ●` como iconos (OVERRIDES §4; el `●` de aviso no leído pasa a ser un `.dot` CSS de 8 px, ver §2.3).
- **Layout.** Tarjetas `.card` (`--color-surface`, borde 1 px `--color-border`, radio 12 px, padding `--space-4`, `box-shadow: var(--shadow-card)`). Ancho de contenido 980 px centrado. A ≤ 640 px todo apila en una columna; sin scroll horizontal a 360 px. Sin estilos inline en `app.js`: las tarjetas de cuenta, filas de aviso y botones pasan a clases (`.account-card`, `.notif-row`, `.btn-block`).
- **Textos en español.** Todos los textos de interfaz, errores y vacíos están en español (es-CL para fechas: `fmtDate` actual, zona `America/Santiago`, se conserva).

---

## 2. Cuentas (`#tab-cuentas`)

Corrige UX-3, 4, 6, 7, 13, 20, 21, 22, 25; VIS-5, 7, 8, 9, 11; ideas #15 y #12.

### 2.1 Estructura — una tarjeta por cuenta

```
<section id="tab-cuentas">
  <div class="card">
    <h2>Cuentas</h2>
    <p class="dim">Aquí conectas YouTube, TikTok y Meta. Si dice Conectada, está lista.
       Las credenciales nunca se muestran: solo se pegan y se cifran en el Worker.</p>
    <div id="accountList" aria-busy="true|false">
      <!-- por cuenta: -->
      <article class="card account-card" aria-labelledby="acc-title-<id>">
        <header class="account-head">
          <span class="platform-mark platform-<slug>" aria-hidden="true">[SVG logo]</span>
          <div>
            <h3 id="acc-title-<id>"><label-ish…> Nombre</h3>
            <p class="dim account-sub">Plataforma · Idioma</p>
          </div>
          <span class="pill ok|bad|warn|neutral">Estado</span>
        </header>
        <p class="dim account-status">Mensaje de verificación · verificado: fecha · credencial: …</p>
        <p class="field-error" id="cred-<id>-error" role="alert" hidden></p>  <!-- ver §2.4 -->
        <form class="account-form" data-account="<id>">   <!-- ver nota §2.5 -->
          <label for="cred-<id>">Credencial (se cifra al guardar, nunca se muestra)</label>
          <div class="input-with-toggle">
            <input id="cred-<id>" type="password" autocomplete="current-password"
                   placeholder="Pega aquí el token / API key"
                   aria-describedby="cred-<id>-hint cred-<id>-error">
            <button type="button" class="ghost input-toggle" aria-label="Mostrar credencial"
                    aria-pressed="false">Mostrar</button>
          </div>
          <p class="dim" id="cred-<id>-hint">Se cifra en el Worker al guardar. Pegar no muestra el valor guardado.</p>
          <label for="extra-<id>">Extra (opcional)</label>
          <input id="extra-<id>" placeholder='{"ig_user_id":"…","page_id":"…"}'
                 aria-describedby="extra-<id>-hint extra-<id>-error" inputmode="text" spellcheck="false">
          <p class="dim" id="extra-<id>-hint">Solo para Meta/Instagram: IDs de Instagram y de Página, en JSON.</p>
          <p class="field-error" id="extra-<id>-error" role="alert" hidden></p>
          <div class="row account-actions">
            <button type="submit" data-save="<id>">Guardar credencial</button>
            <button type="button" class="ghost" data-verify="<id>">Verificar ahora</button>
          </div>
        </form>
      </article>
    </div>
  </div>
</section>
```

- **Orden dentro de la tarjeta:** cabecera (logo + nombre + pill) → línea de estado → formulario (credencial, extra, acciones). El estado se ve antes que el formulario: es la pregunta que la tarjeta responde.
- **Una tarjeta por cuenta, no una tabla.** En móvil apilan a ancho completo; en escritorio, rejilla de 1 columna (las tarjetas llevan formulario; 2 columnas comprimirían los inputs por debajo de lo legible). Separación entre tarjetas `var(--space-4)`.
- **Cabecera de la pestaña** (`h2 Cuentas` + párrafo `.dim`) se conserva. El párrafo actual dice «Si dice Conectada, está lista» — sigue siendo cierto con los 4 estados del §2.3.

### 2.2 Marca por plataforma — logo y color, sin inventar texto

| Plataforma (`a.platform`) | Marca visual | Texto (color de texto, nunca marca cruda) |
|---|---|---|
| YouTube | SVG logo YouTube; punto/borde `--color-platform-youtube: #ff4e45` (4,87:1) | Nombre en `--color-text`; el acento de marca solo en el logo/punto, no en texto corrido |
| TikTok | SVG logo TikTok; punto/borde `--color-platform-tiktok: #25f4ee` (11,55:1) | Idem |
| Instagram | SVG logo Instagram; punto/borde `--color-platform-instagram: #f77737` (5,79:1) o `--color-platform-instagram-pink: #f472b6` (6,01:1) para el degradado | Idem |
| Facebook | SVG logo Facebook; punto/borde `--color-platform-facebook: #60a5fa` (6,26:1) | Idem |
| Meta (cuenta unificada ES, `label` «Meta · Español») | Dos puntos juntos (Instagram + Facebook) o logo Meta en neutro; **no** se inventa un color propio de Meta | El título es el `label` («Meta · Español»), no el slug |

- **Regla de marca (OVERRIDES §2):** los hex de marca crudos (`#ff0000`, `#fe2c55`, `#e1306c`, `#1877f2`) son decorativos (logo, punto, borde) y nunca texto. Solo se usan las variantes claras de la tabla, que pasan AA sobre tarjeta.
- **Título de la tarjeta:** `a.label || a.platform` (como hoy). Subtítulo: `plataforma · idioma` (p. ej. «Meta · Español», «YouTube · Español»). Si no hay `label` ni `language`, el subtítulo muestra solo la plataforma; nunca se inventa un nombre.
- **Logo:** SVG inline de una sola familia, 24×24 px, `aria-hidden="true"`. Sin emoji, sin glifo. Si una plataforma no tiene SVG mapeado, se muestra el punto de color (`.dot`, 8 px, `border-radius: 50%`) con el nombre en texto — nunca se deja la tarjeta sin identificador visual.

### 2.3 Estados — solo 4, con texto, y el caso «Sin verificar»

| Estado canónico (v15) | Condición en los datos | Pill | Texto en la línea de estado |
|---|---|---|---|
| **Conectada** | `verify_ok === 1` | `.pill.ok` | `verify_message` (o «conectada») + ` · verificado: <fecha>` si hay `verified_at` |
| **Sin credencial** | `verify_ok !== 1` y `!has_credential` | `.pill.bad` | «Sin credencial guardada. Pega el token o la API key abajo y pulsa Guardar credencial.» |
| **No conectada** | `verify_ok !== 1` y `has_credential` (incluye `verify_ok === 0` y `verify_ok === null` con credencial) | `.pill.warn` | `verify_message` si existe (motivo del Worker), si no: «Credencial guardada, pero la última verificación falló o aún no se ha verificado. Pulsa Verificar ahora.» + ` · verificado: <fecha>` si hay `verified_at` |
| **Sin crear** | No hay fila de cuenta para esa plataforma/idioma (no llega en `GET /api/accounts`) | No se renderiza como tarjeta; ver §2.6 (empty state) | — |

- **⚠️ Decisión pendiente con el Worker — «Sin verificar» (UX-21 / VIS-11).** `app.js` hoy renderiza `has_credential && !verify_ok` como `<span class="pill">Sin verificar</span>` (pill neutra, indistinguible de «sin estado»). Eso contradice la decisión del panel v15 registrada en memoria: solo 4 estados (Conectada / Sin credencial / No conectada / Sin crear) y «Sin verificar» eliminado. **Esta spec NO inventa un quinto estado:** mapea ese caso a **No conectada** (pill ámbar `.pill.warn`, con el motivo en `verify_message`), que es lo que v15 define. El implementador debe cambiar esa rama de `loadAccounts()`; si el Worker necesita distinguir «credencial guardada, nunca verificada» de «verificación fallida», eso es una decisión del Worker (nuevo valor de `verify_ok`/`status`), no de esta spec. Hasta que el Worker lo decida, ambos casos se muestran como «No conectada» con su motivo.
- **Línea de estado:** `verify_message || note || ""` + fecha de última verificación (`fmtDate(verified_at)`) + estado de la credencial: `credential_masked` tal cual lo devuelve la API (`«cifrada en D1»`, o `«texto plano legacy: volver a pegar»`, o nada si no hay credencial). **Nunca se muestra el valor de la credencial ni su longitud.** Si `credential_masked` dice «texto plano legacy», la línea añade: «Vuelve a pegarla para cifrarla.» (el POST de credencial ya borra el texto plano legacy al cifrar).
- **`status` de la BD** (`a.status`: p. ej. `pendiente`, `conectada`, `error`) no se renderiza como texto: es un valor interno del Worker. Solo se usa la tabla de arriba, derivada de `verify_ok` + `has_credential`.
- **Fecha:** `fmtDate(verified_at)` existente (es-CL, `America/Santiago`). Si no hay `verified_at`, no se muestra la fecha; no se escribe «nunca» como fecha.

### 2.4 Credencial — cifrada, revisable, con error junto al campo

- **Input:** `type="password"`, `autocomplete="current-password"` (UX-20: se quita `autocomplete="off"` para no bloquear el gestor de contraseñas), `placeholder="Pega aquí el token / API key"`, `font-size: 16px`, `min-height: 44px`, fondo `--color-surface-input`, borde `--color-border`, radio 8 px. Pegar no está bloqueado. El campo **siempre aparece vacío** al cargar: la credencial guardada nunca se rellena ni se muestra (la API solo devuelve `credential_masked`).
- **Mostrar/ocultar (UX-20, UX-12).** Botón «Mostrar» / «Ocultar» junto al input (dentro de `.input-with-toggle`, a la derecha, `flex: 0 0 auto`, min-height 44 px, sin estirar el input). Alterna `type="password"` ↔ `type="text"`, con `aria-pressed` y `aria-label` actualizados («Mostrar credencial» / «Ocultar credencial»). Al guardar, el campo vuelve a `type="password"` y se vacía. No hay botón de mostrar en el campo Extra (no es secreto de la misma clase; es JSON de IDs).
- **Guardar credencial (`data-save`).** Primario (`--color-primary`, texto blanco). Al pulsar: valida en cliente que el campo no esté vacío → si está vacío, error inline «Pega la credencial primero.» bajo el campo + foco al campo, sin POST. Si hay valor: botón `disabled` + texto «Guardando…» hasta resolver; `POST /api/accounts/<id>/credential` con `{credential, extra}` (el `extra` se envía tal cual esté en su campo, vacío si no se tocó). Éxito: `#msg` (`role="status"`) «Credencial guardada (<masked>). Ahora pulsa Verificar.» — `<masked>` es `r.masked` de la respuesta; el campo de credencial se vacía; se recarga la lista (`loadAccounts()`). Error (API o red): error inline bajo el campo de credencial con el mensaje de la API, más `#msg role="alert"`; el valor pegado **se conserva** en el campo para reintentarlo (no se borra un token largo por un error de red).
- **Verificar ahora (`data-verify`).** Secundario ghost. Al pulsar: `disabled` + texto «Verificando…» hasta resolver; `POST /api/accounts/<id>/verify`. **Sin credencial guardada:** el Worker responde 400 («Sin credencial cifrada: pégala primero en Cuentas.») → error inline bajo la credencial: «Sin credencial guardada. Pégala primero y pulsa Guardar credencial.», foco al campo, sin recargar la lista. **Con respuesta:** éxito → `#msg` «Verificación: conectada — <message>»; fallo → `#msg role="alert"` «Verificación: error — <message>» **y** error inline bajo la credencial con el mismo `<message>` (el motivo debe verse junto a la tarjeta, no solo arriba). En ambos casos se recarga la lista para que la pill y la fecha se actualicen.
- **Campo Extra (`extra-<id>`).** Label con `for`, texto exacto: «Extra (opcional)». Hint bajo el campo: «Solo para Meta/Instagram: IDs de Instagram y de Página, en JSON.» Placeholder: `'{"ig_user_id":"…","page_id":"…"}'`. Se envía solo dentro del POST de credencial (como hoy: no hay guardado independiente). Si el contenido no es JSON válido al guardar, error inline bajo Extra: «El campo Extra no es JSON válido. Revisa las comillas y las llaves.», con el detalle del parser si lo aporta el navegador; no se envía el POST. Si está vacío, se envía `""` (el Worker lo trata como sin extra). El campo Extra **no** se vacía al recargar si el usuario lo escribió y el guardado falló; tras un guardado correcto se recarga la lista (el Worker no devuelve el extra, así que el campo vuelve vacío — comportamiento actual, se conserva y se documenta en el hint solo si el implementador lo ve necesario; no se inventa un GET del extra).
- **Errores inline (clase nueva).** `.field-error { color: var(--color-bad); font-size: 13px; line-height: 1.5; margin: var(--space-1) 0 var(--space-2); }` con `role="alert"` y `aria-describedby` en el campo. El campo inválido lleva `aria-invalid="true"` y borde `--color-bad` (el borde solo no basta: siempre hay texto).
- **Jerarquía de botones (VIS-9).** `.row > button { flex: 0 0 auto; }` — «Guardar credencial» y «Verificar ahora» no se estiran por igual. «Guardar» es primario y va primero; «Verificar» es ghost. En móvil, ambos a ancho completo apilados (`.account-actions { flex-direction: column; }` a ≤ 640 px) con `min-height: 44px` cada uno; en escritorio, en fila con gap 8 px.
- **Sin confirmación** para guardar/verificar (no son destructivos; el guardado sobrescribe la credencial anterior, y eso se comunica en el hint, no con un diálogo).

### 2.5 Notas de implementación (sin cambiar la lógica)

- Los botones `data-save` / `data-verify` conservan sus `data-*` y sus handlers; solo cambia su render (clases en vez de estilos inline) y los estados de loading/error. Si el implementador envuelve cada tarjeta en un `<form>`, el «Guardar» pasa a `type="submit"` con `preventDefault()` y «Verificar» a `type="button"` (para que Enter en la credencial guarde, no verifique). Si no se usa `<form>`, Enter en `#cred-<id>` dispara Guardar (equivalente al login, UX-12).
- La recarga tras guardar/verificar conserva el scroll de la pestaña (no se hace `scrollIntoView`).
- **Skeleton:** 2 tarjetas esqueleto (cabecera + 2 líneas + 2 botones) con `aria-busy="true"` en `#accountList` mientras carga `GET /api/accounts`.

### 2.6 Empty state — sin cuentas

- Si `GET /api/accounts` devuelve lista vacía, `#accountList` muestra (idea #12, UX-25):
  - Título (h3): «Sin cuentas todavía»
  - Texto: «Las cuentas se crean al migrar los datos del panel anterior o desde el Worker. Cuando haya una, aparecerá aquí para pegar su credencial y verificarla.»
  - Acción: botón ghost «Actualizar» (reintenta `loadAccounts()`), 44 px. No se inventa un botón «Crear cuenta»: no existe endpoint de creación en el front.
- Si el `fetch` falla: título «No se pudieron cargar las cuentas», texto con el motivo, botón «Reintentar».

### 2.7 Checklist de aceptación — Cuentas

- [ ] 4 estados y solo 4; «Sin verificar» no aparece en ningún render.
- [ ] Cada tarjeta: logo SVG + nombre + pill con texto + línea de estado con fecha y `credential_masked`.
- [ ] Credencial nunca visible al cargar; toggle Mostrar/Ocultar funciona y vuelve a ocultar al guardar.
- [ ] Guardar y Verificar con loading + disabled; errores bajo su campo con foco al primer inválido.
- [ ] Botones no estirados por `.row`; táctil 44 px; labels con `for`; sin hex fuera de tokens; a 360 px sin scroll horizontal.

---

## 3. Avisos (`#tab-avisos`)

Corrige UX-6, 9 (badge), 15, 16, 19, 22, 25; VIS-9, 15; ideas #12, #19 (parcial, ver §3.5).

### 3.1 Estructura — lista legible, una fila por aviso

```
<section id="tab-avisos">
  <div class="card">
    <h2>Avisos</h2>
    <p class="dim" id="notifCount" role="status" aria-live="polite">…</p>   <!-- ver §3.2 -->
    <div id="notifList" aria-busy="true|false">
      <!-- por aviso: -->
      <div class="notif-row notif-unread|notif-read">
        <span class="dot" aria-hidden="true"></span>          <!-- solo si no leído -->
        <p class="notif-message">Mensaje</p>
        <p class="dim notif-meta">fecha · tipo</p>
        <button class="ghost notif-read-btn" data-read="<id>">Marcar leída</button>
      </div>
    </div>
  </div>
</section>
```

- **Fila:** grid de 2 columnas en escritorio — texto (mensaje + fecha) a la izquierda, botón a la derecha, alineado verticalmente al centro. En móvil (≤ 640 px): mensaje arriba, fecha debajo, botón debajo a ancho completo (ver §3.3). Separador entre filas: `border-bottom: 1px solid var(--color-border)`; última fila sin borde. Padding vertical `var(--space-3)` (12 px).
- **Mensaje:** texto en `--color-text`, 16 px, `line-height: 1.5`, `overflow-wrap: anywhere` (los mensajes pueden llevar URLs o IDs largos). Sin truncado: el aviso se lee entero.
- **Fecha:** `.dim`, 13 px, `fmtDate(created_at)` (es-CL, `America/Santiago`). Formato de fecha legible, no timestamp crudo.
- **No leído vs leído:** el aviso no leído lleva `.dot` (8 px, `--color-badge-bg` o `--color-primary` — ver §3.2) + mensaje en peso 600; el leído, sin punto y mensaje en peso 400 con color `--color-text-dim` en la fecha. El estado nunca es solo el punto: el botón «Marcar leída» solo existe en los no leídos, y el contador del §3.2 lo dice en texto. El `●` con `<b>` actual se elimina (glifo sin `aria-hidden`, VIS-20).
- **Orden:** el que devuelve la API (`created_at DESC`, máx. 50). No se reordena en el front.
- **Tipo (`n.kind`):** la API lo devuelve pero el front hoy no lo usa. Si el implementador lo muestra, es solo como texto atenuado junto a la fecha («publicado», «fallo», «credencial»… traducido a español; si el `kind` llega en inglés o vacío, no se muestra — no se inventa un catálogo de tipos). No es obligatorio para esta spec.

### 3.2 Badge de no leídos — con texto contextual y región live

- **En la pestaña (nav):** el botón «Avisos» lleva `<span id="unreadBadge" class="badge" role="status" aria-live="polite">` (UX-19). Contenido:
  - `unread === 0`: elemento oculto (`hidden`, sin texto). Nunca «(0)».
  - `1–9`: solo el número («3»).
  - `≥ 10`: «9+» (tope visual, VIS-15).
  - El `aria-label` del botón de la pestaña es «Avisos, 3 sin leer» (o «Avisos» si es 0): el número solo no se anuncia sin contexto.
- **Estilo `.badge` (OVERRIDES §4):** fondo `--color-badge-bg: #dc2626`, texto blanco (4,83:1), `min-width: 20px`, `height: 20px`, `line-height: 20px`, `padding: 0 6px`, radio 999 px, `font-size: 11px`, `font-weight: 700`, centrado. Se actualiza en cada `loadNotifications()`; el cambio de texto completo es lo que anuncia el live region, no un número suelto.
- **Contador en la pestaña:** bajo el `h2 Avisos`, línea `#notifCount` con `role="status"`: «3 avisos sin leer» / «1 aviso sin leer» / «Todo al día. Sin avisos sin leer.» Se actualiza con el mismo `loadNotifications()`. Es el texto contextual que el badge por sí solo no da.
- **Al marcar como leída:** el badge y el contador se actualizan tras el POST (vía `loadNotifications()` como hoy). No hay optimismo inventado: si el POST falla, el aviso sigue como no leído y `#msg role="alert"` dice «No se pudo marcar el aviso como leído. Reintenta.»

### 3.3 «Marcar leída» — 44 px sin estirar el botón (VIS-9)

- **El defecto actual:** `.row > * { flex: 1; min-width: 140px; }` estira el botón fantasma hasta ocupar la misma fracción que el texto del aviso; el botón compite con el mensaje.
- **La corrección:** la fila NO usa `.row`. El botón es `flex: 0 0 auto; align-self: center;` en escritorio (ancho según su texto, nunca estirado) y a ≤ 640 px pasa a ancho completo (`width: 100%`) apilado bajo el texto — ancho completo en móvil es apilado, no estirado en fila, y es el comportamiento correcto para un objetivo táctil.
- **Tamaño:** `min-height: 44px`, `padding: 10px 16px`, `font-size: 14px` (texto del botón; el cuerpo es 16 px, el botón secundario puede ser 14 px sin romper AA sobre ghost: texto `--color-text` sobre transparente, 13,22:1 sobre tarjeta). Texto exacto: «Marcar leída».
- **Comportamiento:** `POST /api/notifications/<id>/read`; durante el POST el botón queda `disabled` + texto «Marcando…»; tras el éxito, `loadNotifications()` recarga la lista (el aviso pasa a leído y su botón desaparece). Error: `#msg role="alert"` + el botón vuelve a «Marcar leída» habilitado.
- **Foco tras marcar:** el foco va al siguiente aviso no leído (o al `h2 Avisos` si no queda ninguno), no se pierde al recargar la lista.

### 3.4 Empty state — sin avisos

- Si la lista está vacía (idea #12, UX-25):
  - Título (h3): «Sin avisos»
  - Texto: «Todo al día. Los avisos de publicaciones, fallos y credenciales aparecerán aquí.»
  - Acción: botón ghost «Actualizar» (reintenta `loadNotifications()`), 44 px.
- Si el `fetch` falla: «No se pudieron cargar los avisos», motivo, botón «Reintentar».
- **Skeleton:** 3 filas esqueleto (punto + 2 líneas + botón) con `aria-busy="true"` mientras carga.

### 3.5 Fuera de esta spec (idea #19, parcial)

- **Agrupar por día (Hoy / Ayer / fecha) y «Marcar todas como leídas»** (idea #19) quedan como **segunda oleada, no en esta entrega**: agrupar por día es solo presentación y podría hacerse en el front, pero «Marcar todas» no tiene endpoint (solo existe `POST /api/notifications/<id>/read` por aviso). No se diseña el botón «Marcar todas» ni se inventa su endpoint aquí; si el Worker lo añade, la spec se amplía con su loading y su confirmación. El filtro por tipo tampoco se incluye: `kind` no tiene catálogo acordado con el Worker.

### 3.6 Checklist de aceptación — Avisos

- [ ] Badge `.badge` con tope «9+», oculto en 0, `role="status"` y texto contextual («3 avisos sin leer») en la pestaña y bajo el `h2`.
- [ ] Fila legible: mensaje 16 px entero, fecha atenuada, punto CSS (no `●`) solo en no leídos.
- [ ] «Marcar leída» 44 px, `flex: 0 0 auto` en escritorio, ancho completo apilado en móvil; con loading «Marcando…».
- [ ] Empty state con acción «Actualizar»; skeleton al cargar; sin scroll horizontal a 360 px.

---

## 4. Ajustes (`#tab-ajustes`)

Corrige UX-3, 4, 6, 7, 10, 13, 16, 23, 32; VIS-6, 7, 8, 18; idea #16.

### 4.1 Principio — adiós JSON crudo como única vía (UX-10)

- **El formulario por secciones es la vía principal.** El JSON deja de ser el editor: pasa a ser una **vista avanzada plegable, de solo lectura en reposo y editable solo dentro de ese pliegue** (ver §4.4). El texto de cabecera actual («Se guarda como JSON en el Worker») se cambia por: «Configuración del panel (subtítulos, límites diarios, franja horaria, Telegram). Los cambios se guardan al pulsar Guardar ajustes.»
- **Un campo por ajuste, con su label, su hint y su validación.** Nada de claves JSON visibles en el formulario. El formulario construye el objeto que se envía por `PUT /api/settings`; el `GET /api/settings` rellena cada campo; las claves que el formulario no conoce se conservan intactas (el Worker hace merge) y se ven en la vista JSON avanzada.
- **Guardar es explícito y único.** Un solo botón «Guardar ajustes» al final del formulario (primario, 44 px). No hay auto-guardado por campo. Cambios sin guardar: el botón muestra un punto «· sin guardar» en su texto auxiliar (ver §4.5) y cambiar de pestaña con cambios pendientes pregunta «¿Salir sin guardar los ajustes?» (UX-23).
- **⚠️ Decisión pendiente con el Worker — qué claves existen de verdad.** El Worker guarda `settings` como JSON libre sin esquema tipado (`worker/src/db.ts`: `getSettings`/`saveSettings` no validan claves) y hoy **solo consume dos claves**: `telegram_bot_token` y `telegram_chat_id` (`worker/src/jobs.ts:115-116`). Los límites diarios y la franja horaria **hoy están hardcodeados en `publishGate`** (`worker/src/jobs.ts:145-164`): plan YouTube 3 / resto 2, techo YouTube 5 / resto 3, espaciamiento 2 h, franja 18:00–24:00 (hora de Chile). El estilo de subtítulos vive como columna `subtitle_style` de cada clip (valor por defecto `'tiktok'`), no como ajuste global. **Por tanto, los campos de §4.2–4.3 son la propuesta de esta spec para las claves; el Worker debe confirmar (o crear) la lectura de cada una antes de que el formulario prometa que ese ajuste cambia el comportamiento.** Hasta esa confirmación, cada sección lleva su nota de estado (ver §4.3). Esta spec no inventa que el Worker ya lee esas claves.

### 4.2 Estructura — secciones con un campo por ajuste

```
<section id="tab-ajustes">
  <div class="card">
    <h2>Ajustes</h2>
    <p class="dim">Configuración del panel (subtítulos, límites diarios, franja horaria, Telegram).
       Los cambios se guardan al pulsar Guardar ajustes.</p>
    <form id="settingsForm" novalidate>
      <p class="dim" id="settingsDirty" role="status" aria-live="polite" hidden>Hay cambios sin guardar.</p>

      <fieldset class="settings-section">
        <legend>Subtítulos</legend>
        <p class="dim">Cómo se queman los subtítulos en los clips.</p>
        <label for="set-subtitles-enabled">Subtítulos quemados</label>
        <select id="set-subtitles-enabled">…</select>  <!-- o interruptor; ver §4.3 -->
        <label for="set-subtitle-style">Estilo de subtítulos</label>
        <select id="set-subtitle-style">
          <option value="tiktok">TikTok</option>
          <option value="highlight">Highlight</option>
          <option value="karaoke">Karaoke</option>
        </select>
        <p class="dim" id="set-subtitle-style-hint">Estilo por defecto para los clips nuevos.</p>
      </fieldset>

      <fieldset class="settings-section">
        <legend>Límites diarios por plataforma</legend>
        <p class="dim">Máximo de publicaciones al día por plataforma. 0 = no publicar en esa plataforma.</p>
        <label for="set-limit-youtube">YouTube Shorts — al día</label>
        <input id="set-limit-youtube" type="number" inputmode="numeric" min="0" max="10" step="1">
        <label for="set-limit-tiktok">TikTok — al día</label>
        <input id="set-limit-tiktok" type="number" …>
        <label for="set-limit-instagram">Instagram — al día</label>
        <input id="set-limit-instagram" type="number" …>
        <label for="set-limit-facebook">Facebook — al día</label>
        <input id="set-limit-facebook" type="number" …>
        <p class="field-error" id="set-limit-error" role="alert" hidden></p>
      </fieldset>

      <fieldset class="settings-section">
        <legend>Franja horaria</legend>
        <p class="dim">Horas de Chile (America/Santiago) en las que el Worker puede publicar.</p>
        <label for="set-window-start">Desde</label>
        <input id="set-window-start" type="time" value="18:00">
        <label for="set-window-end">Hasta</label>
        <input id="set-window-end" type="time" value="24:00">  <!-- ver §4.3 -->
        <p class="field-error" id="set-window-error" role="alert" hidden></p>
      </fieldset>

      <fieldset class="settings-section">
        <legend>Telegram</legend>
        <p class="dim">Avisos del Worker por Telegram. Sin configurar, no se envía nada.</p>
        <label for="set-telegram-bot-token">Token del bot</label>
        <input id="set-telegram-bot-token" type="password" autocomplete="off" …>
        <label for="set-telegram-chat-id">ID del chat</label>
        <input id="set-telegram-chat-id" type="text" inputmode="numeric" …>
        <p class="field-error" id="set-telegram-error" role="alert" hidden></p>
      </fieldset>

      <details class="settings-advanced" id="settingsAdvanced">
        <summary>Vista avanzada (JSON)</summary>
        <p class="dim">El JSON completo tal como se guarda en el Worker, incluidas las claves que este formulario no muestra.</p>
        <label for="settingsJson">Ajustes (JSON)</label>
        <textarea id="settingsJson" rows="14" spellcheck="false" aria-describedby="settingsJson-error">{}</textarea>
        <p class="field-error" id="settingsJson-error" role="alert" hidden></p>
        <p class="dim">Editar el JSON y guardar actualiza también el formulario. Si el JSON no es válido, no se guarda nada.</p>
      </details>

      <button type="submit" id="btnSaveSettings">Guardar ajustes</button>
      <p class="dim" id="settingsSavedAt" role="status"></p>
    </form>
  </div>
</section>
```

- **Secciones como `<fieldset>` + `<legend>`** (no solo `h3`): agrupan semánticamente cada grupo y el lector de pantalla anuncia el grupo al entrar en cada campo. Separación entre secciones `var(--space-5)` (24 px); dentro, cada campo con su label encima, su hint debajo y `margin-bottom: var(--space-3)`.
- **Orden de las secciones:** Subtítulos → Límites diarios → Franja horaria → Telegram → Vista avanzada (JSON) → Guardar. Es el orden del texto de cabecera actual y de la auditoría UX-10.
- **El botón Guardar va al final, después del pliegue JSON**, para que guarde tanto el formulario como el JSON editado (una sola acción, un solo endpoint). `min-height: 44px`, ancho completo en móvil, ancho automático en escritorio.
- **IDs nuevos** (`set-*`): son ids de los campos del formulario, no de la API. El único id de Ajustes que la lógica actual usa y que se conserva es `#settingsJson` (ahora dentro del pliegue) y `#btnSaveSettings`. Si el implementador prefiere no usar `<form>`, el botón conserva su handler `click` actual; con `<form>`, pasa a `submit` con `preventDefault()`.

### 4.3 Campos — tipo, validación y estado frente al Worker

| Campo (id propuesto) | Control | Clave JSON propuesta | Validación en cliente | Estado frente al Worker |
|---|---|---|---|---|
| Subtítulos quemados (`set-subtitles-enabled`) | Interruptor (`<button role="switch" aria-checked>` o checkbox con estilo; nunca solo color) o `select` Sí/No — a elegir por el implementador, mismo resultado | `subtitles_enabled` (bool) | — | **Pendiente.** No existe hoy como ajuste global; el estilo vive por clip (`subtitle_style`, defecto `'tiktok'`). El Worker debe confirmar si lo lee como valor por defecto de clips nuevos |
| Estilo de subtítulos (`set-subtitle-style`) | `select` con 3 opciones: TikTok / Highlight / Karaoke (los 3 estilos del editor del panel) | `subtitle_style` (string) | Valor debe ser una de las 3 opciones | **Pendiente** (mismo caso que arriba). Si el Worker no lo lee, el campo no se muestra como «activo» sino con la nota: «Aún no aplicado por el Worker.» (ver nota al pie) |
| Límite YouTube (`set-limit-youtube`) | `input type="number"`, `min 0`, `max 10`, `step 1` | `daily_limit_youtube_shorts` (int) | Entero 0–10; vacío = sin cambio (se conserva el valor guardado) | **Pendiente.** Hoy hardcodeado: plan 3, techo 5 (`publishGate`). El formulario no debe afirmar que cambia el límite hasta que el Worker lea la clave |
| Límite TikTok (`set-limit-tiktok`) | Igual | `daily_limit_tiktok` (int) | Entero 0–10 | **Pendiente.** Hoy plan 2, techo 3 |
| Límite Instagram (`set-limit-instagram`) | Igual | `daily_limit_instagram` (int) | Entero 0–10 | **Pendiente.** Hoy plan 2, techo 3 |
| Límite Facebook (`set-limit-facebook`) | Igual | `daily_limit_facebook` (int) | Entero 0–10 | **Pendiente.** Hoy plan 2, techo 3 |
| Franja desde (`set-window-start`) | `input type="time"` | `publish_window_start` (string `"HH:MM"`) | Hora válida; debe ser anterior a «Hasta» salvo franja que cruza medianoche (ver abajo) | **Pendiente.** Hoy hardcodeado 18:00–24:00 en `publishGate` |
| Franja hasta (`set-window-end`) | `input type="time"` | `publish_window_end` (string `"HH:MM"`) | Hora válida; si «Hasta» ≤ «Desde», se interpreta como franja que cruza medianoche solo si el Worker lo confirma; si no, error «La hora de fin debe ser posterior a la de inicio.» | **Pendiente** (mismo caso). Nota: `type="time"` no admite `24:00`; el valor actual 24:00 se representa como `00:00` del día siguiente o como `23:59` — **decisión pendiente con el Worker**, no se inventa aquí; el hint del campo dice la franja actual («Hoy: 18:00–24:00») hasta que se confirme |
| Token del bot (`set-telegram-bot-token`) | `input type="password"` + toggle Mostrar/Ocultar (igual que §2.4), `autocomplete="off"` aquí sí (es un secreto de bot, no una contraseña de login; el gestor no debe ofrecer guardarlo como login) | `telegram_bot_token` (string) | Si se rellena, mínimo 10 caracteres; si se deja vacío, se conserva el guardado (nunca se borra por dejarlo vacío; para borrarlo, ver nota) | **Existe y la lee el Worker** (`jobs.ts:115`). Es la única sección que hoy cambia comportamiento real |
| ID del chat (`set-telegram-chat-id`) | `input type="text" inputmode="numeric"` | `telegram_chat_id` (string) | Si se rellena, solo dígitos (con `-` inicial permitido para grupos); si el token está vacío y el chat relleno, aviso (no error): «Sin token del bot, el ID del chat no se usará.» | **Existe y la lee el Worker** (`jobs.ts:116`) |

- **Nota de estado por sección (obligatoria hasta la confirmación del Worker).** Cada sección pendiente lleva bajo su descripción una línea `.dim` con el estado real, por ejemplo en Límites: «Hoy el Worker usa sus valores fijos (YouTube 5 al día, resto 3). Estos campos se guardan, pero aún no los aplica.» Y en Franja: «Hoy la franja es 18:00–24:00 (hora de Chile).» Cuando el Worker confirme la lectura, el implementador borra esa línea; no se borra antes. **No se miente al usuario sobre qué cambia el comportamiento.**
- **Valores iniciales:** los que devuelva `GET /api/settings` para cada clave; si una clave no existe, el campo queda vacío (número) o en su valor neutro (select en la primera opción, franja en 18:00–24:00 como hint, no como valor guardado). **No se inventan valores por defecto** distintos de los que el Worker ya usa.
- **Borrar un secreto:** dejar el token vacío conserva el guardado (el `PUT` hace merge y el front no envía la clave si el campo quedó vacío y había valor). Para borrarlo de verdad hace falta una acción explícita — **decisión pendiente**: o un checkbox «Borrar token guardado» que envíe la clave con `""`, o se deja sin borrar hasta que el Worker defina el borrado. No se diseña el borrado silencioso por vacío.
- **Claves desconocidas:** las que vengan en el `GET` y no tengan campo en el formulario se conservan intactas en cada guardado (el front reenvía el objeto completo fusionado, o el Worker hace merge — en ambos casos no se pierden) y se ven en la vista JSON avanzada (§4.4). El formulario nunca borra una clave que no muestra.

### 4.4 Vista avanzada (JSON) — plegable, con error claro junto al campo

- **Contenedor:** `<details class="settings-advanced">` con `<summary>Vista avanzada (JSON)</summary>`. Cerrado por defecto (progressive disclosure, UX-10). Al abrirlo, su contenido se regenera desde los valores actuales del formulario (el JSON es un espejo, no una segunda fuente).
- **Textarea `#settingsJson` (id conservado):** `rows="14"`, `spellcheck="false"`, fuente mono (`var(--font-mono)`, 13 px, `line-height: 1.6`, `tab-size: 2` — UX-32/VIS-18), `font-size` de escritura 13 px (es código, no campo de texto normal; el auto-zoom de iOS no aplica igual a un textarea mono de solo lectura/expansión, pero si el implementador lo edita en móvil, el zoom es aceptable porque es la vista avanzada, no el formulario principal).
- **Editable, pero con reglas.** Dentro del pliegue, el JSON **sí** se puede editar (es la vía avanzada). Al pulsar «Guardar ajustes» con el pliegue abierto y el JSON modificado, el JSON manda: se valida con `JSON.parse`; si es válido, se envía tal cual y el formulario se repuebla desde él; si no es válido, **no se guarda nada** y aparece el error del §4.5 bajo el textarea. Si el pliegue está cerrado o el JSON no se tocó, manda el formulario.
- **Sincronización en un solo sentido por guardado:** formulario → JSON al abrir el pliegue; JSON → formulario solo tras un guardado válido. Nunca se mezclan a medias en el mismo guardado.
- **Botón «Copiar JSON»** (ghost, 44 px) junto al summary: copia el contenido del textarea al portapapeles y anuncia «JSON copiado.» en `#msg role="status"`. No se inventa un endpoint para ello.
- **Sin botón «Probar Telegram»** en esta spec: no existe endpoint de prueba en el Worker. Si se añade, irá en la sección Telegram con su propio loading y su resultado inline. No se diseña un botón que no hace nada.

### 4.5 Guardar — loading, errores y cambios sin guardar

- **Guardar (`#btnSaveSettings`).** Al pulsar: valida todos los campos del formulario (y el JSON si el pliegue está abierto y modificado). Si hay errores, se muestran bajo cada campo (`.field-error`, `role="alert"`, `aria-invalid="true"`, foco al primer inválido) y **no se envía el PUT**. Si todo es válido: botón `disabled` + texto «Guardando ajustes…» hasta resolver; `PUT /api/settings` con el objeto construido. Éxito: `#msg role="status"` «Ajustes guardados.» + línea `#settingsSavedAt` («Guardado: <fecha y hora>») + el indicador de cambios sin guardar desaparece + el JSON del pliegue se regenera desde lo guardado. Error (API o red): `#msg role="alert"` con el mensaje + error inline en el primer campo implicado si el mensaje lo permite identificar; si no, solo `#msg`. Los valores del formulario **se conservan** tras un error (no se repueblan desde el GET).
- **Error de JSON claro y junto al campo (requisito del encargo).** Si `JSON.parse` falla, el error bajo `#settingsJson` dice, en este orden: qué pasó, dónde, cómo arreglarlo. Formato: «El JSON no es válido: <mensaje del parser>. Revisa la zona indicada.» Y si el parser aporta línea/columna (o posición), se añade: «Error cerca de la línea <n>.» + el foco va al textarea y, si se puede, se selecciona la zona. **Nunca** el texto crudo actual («Ajustes no válidos o error: …») como único mensaje. El error no borra el contenido del textarea.
- **Validaciones con mensaje propio (no genéricas):**
  - Límite fuera de rango: «El límite debe ser un número entero entre 0 y 10.»
  - Franja inválida: «La hora de fin debe ser posterior a la de inicio.» (o la regla de medianoche que confirme el Worker).
  - Token corto: «El token del bot parece incompleto. Revisa que lo pegaste entero.»
  - Chat no numérico: «El ID del chat solo lleva números (y un guion al inicio para grupos).»
  - Extra (Cuentas) y JSON (Ajustes) comparten el patrón: causa + cómo arreglarlo, junto al campo.
- **Cambios sin guardar (UX-23).** Cada cambio en un campo marca el formulario como sucio: aparece `#settingsDirty` («Hay cambios sin guardar.») con `role="status"` y el botón Guardar gana el indicador visual (punto + texto auxiliar, no solo color). Al cambiar de pestaña con el formulario sucio, confirmación nativa: «¿Salir sin guardar los ajustes?» (Aceptar = descartar y cambiar; Cancelar = quedarse). Tras guardar, limpio. Al recargar la pestaña (`loadSettings`), limpio. No hay auto-guardado.
- **Carga inicial.** Al entrar a Ajustes, skeleton de 3 líneas en lugar del formulario hasta que llega `GET /api/settings`; si falla, mensaje de error + botón «Reintentar» (reintenta `loadSettings()`). Si el `GET` devuelve `{}` (sin ajustes), el formulario se muestra vacío con sus hints; no es un error.

### 4.6 Checklist de aceptación — Ajustes

- [ ] Formulario por secciones con un campo por ajuste; el JSON crudo ya no es la única vía ni la vista por defecto.
- [ ] Vista avanzada plegable y cerrada por defecto; `#settingsJson` conserva su id; error de JSON claro, con línea si la hay, junto al campo, sin borrar el contenido.
- [ ] Guardar con loading «Guardando ajustes…» + disabled; éxito con fecha de guardado; errores por campo con foco al primer inválido.
- [ ] Cambios sin guardar con indicador y confirmación al salir; claves desconocidas conservadas; sin scroll horizontal a 360 px.
- [ ] Notas de estado («aún no lo aplica el Worker») presentes en Subtítulos, Límites y Franja hasta la confirmación del Worker; Telegram sin nota (sí la lee).

---

## 5. Textos exactos (español, para copiar)

| Dónde | Texto |
|---|---|
| Cuentas — sin credencial (línea de estado) | «Sin credencial guardada. Pega el token o la API key abajo y pulsa Guardar credencial.» |
| Cuentas — no conectada sin motivo | «Credencial guardada, pero la última verificación falló o aún no se ha verificado. Pulsa Verificar ahora.» |
| Cuentas — credencial vacía al guardar | «Pega la credencial primero.» |
| Cuentas — sin credencial al verificar | «Sin credencial guardada. Pégala primero y pulsa Guardar credencial.» |
| Cuentas — éxito al guardar | «Credencial guardada (<masked>). Ahora pulsa Verificar.» |
| Cuentas — éxito al verificar | «Verificación: conectada — <message>» |
| Cuentas — fallo al verificar | «Verificación: error — <message>» |
| Cuentas — extra inválido | «El campo Extra no es JSON válido. Revisa las comillas y las llaves.» |
| Cuentas — vacío | Título «Sin cuentas todavía» · «Las cuentas se crean al migrar los datos del panel anterior o desde el Worker. Cuando haya una, aparecerá aquí para pegar su credencial y verificarla.» · botón «Actualizar» |
| Avisos — contador | «3 avisos sin leer» / «1 aviso sin leer» / «Todo al día. Sin avisos sin leer.» |
| Avisos — vacío | Título «Sin avisos» · «Todo al día. Los avisos de publicaciones, fallos y credenciales aparecerán aquí.» · botón «Actualizar» |
| Avisos — error al marcar | «No se pudo marcar el aviso como leído. Reintenta.» |
| Ajustes — cabecera | «Configuración del panel (subtítulos, límites diarios, franja horaria, Telegram). Los cambios se guardan al pulsar Guardar ajustes.» |
| Ajustes — cambios sin guardar | «Hay cambios sin guardar.» |
| Ajustes — salir sin guardar | «¿Salir sin guardar los ajustes?» |
| Ajustes — éxito | «Ajustes guardados.» + «Guardado: <fecha y hora>» |
| Ajustes — JSON inválido | «El JSON no es válido: <mensaje del parser>. Revisa la zona indicada.» (+ «Error cerca de la línea <n>.» si la hay) |
| Ajustes — JSON copiado | «JSON copiado.» |
| Ajustes — Telegram sin token | «Sin token del bot, el ID del chat no se usará.» (aviso, no error) |
| Global — reintento de carga | «No se pudieron cargar las cuentas / los avisos / los ajustes.» + botón «Reintentar» |

## 6. Decisiones pendientes (no inventar; confirmar con el Worker / el usuario)

1. **«Sin verificar» (Cuentas).** Mapeado a «No conectada» (pill ámbar) en esta spec, según v15. Si el Worker quiere distinguir «nunca verificada» de «verificación fallida», debe definirlo en `verify_ok`/`status`/`verify_message`; el front no crea un quinto estado por su cuenta.
2. **Claves de Ajustes (Subtítulos, Límites, Franja).** El Worker hoy solo lee `telegram_bot_token` y `telegram_chat_id`; límites y franja están hardcodeados en `publishGate` y el estilo de subtítulos es por clip. Confirmar las claves propuestas en §4.3 (`subtitles_enabled`, `subtitle_style`, `daily_limit_*`, `publish_window_start/end`) y si el Worker las leerá; hasta entonces, las notas de estado del §4.3 son obligatorias.
3. **Franja 24:00.** `type="time"` no admite `24:00`. Confirmar cómo se representa el fin de franja actual (00:00 del día siguiente o 23:59) y si se permiten franjas que cruzan medianoche.
4. **Borrado del token de Telegram.** Dejar el campo vacío conserva el guardado. Confirmar el mecanismo de borrado explícito (checkbox o endpoint).
5. **«Marcar todas como leídas» y agrupación por día (Avisos).** Sin endpoint de marcado masivo; queda fuera de esta entrega hasta que el Worker lo añada.
6. **«Probar Telegram».** Sin endpoint de prueba; no se diseña el botón hasta que exista.
7. **Cuenta «Sin crear».** No hay endpoint de creación de cuentas en el front; el empty state no ofrece crearla.

## 7. Checklist final del implementador (estas 3 pestañas)

- [ ] Ningún hex fuera de `tokens.css` (grep `#[0-9a-f]{3,6}` en los 3 bloques = 0, salvo tokens); ningún estilo inline nuevo en `app.js`.
- [ ] Contraste verificado en los pares de estas pestañas: primario/blanco 6,70:1, enlace 8,82:1, error 5,75:1, badge 4,83:1, pills ok/bad/warn según OVERRIDES §2.
- [ ] Foco visible 3 px en todos los controles; labels con `for`; `aria-describedby` + `role="alert"` en errores de campo.
- [ ] Táctil 44 px en botones e inputs; inputs a 16 px; sin scroll horizontal a 360 px.
- [ ] Estados como pills con texto, nunca solo color; solo 4 estados de cuenta; badge con tope «9+» y texto contextual.
- [ ] Botones async con loading + disabled; errores junto al campo con foco al primer inválido; éxito con auto-limpieza a 5 s.
- [ ] `prefers-reduced-motion` respetado; `color-scheme: dark` heredado; safe-area heredada.
- [ ] Ids y endpoints conservados (§0); «Sin verificar» no se renderiza; JSON avanzado plegable y cerrado por defecto.
