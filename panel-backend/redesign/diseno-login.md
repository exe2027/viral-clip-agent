# Diseño de login — Panel AVC

- **Fecha:** 2026-11-17
- **Alcance:** SOLO la pantalla de login. Especificación de diseño; no se editó el front (`frontend/dist/` queda intacto).
- **Fuentes autoritativas:** `design-system/avc-panel/OVERRIDES.md` (manda sobre `MASTER.md` y `tokens.css`), `redesign/auditoria-ux.md` (hallazgos UX-1, UX-2, UX-3, UX-4, UX-6, UX-7, UX-12, UX-13, UX-14, UX-15, UX-16, UX-28) y `redesign/auditoria-visual.md` (VIS-1, VIS-5, VIS-6, VIS-7, VIS-13, VIS-14, VIS-24). Skill `ui-ux-pro-max`: búsquedas `--domain ux` verificadas el 2026-11-17 para *“accessible authentication”* (3 resultados: permitir gestores de contraseñas y pegado, `autocomplete="current-password"`, no bloquear `paste`) y *“error summary validation”* (5 resultados: error junto al campo con `aria-describedby`, `role="alert"`, foco al primer campo inválido, resumen enfocable).
- **Estado actual (leído, no tocado):** `frontend/dist/index.html` líneas 37 y 53–62 (`#loginBox`, texto con nombres, `#loginMsg`, `#username`, `#password`, `#loginBtn`) y `frontend/dist/app.js` líneas 21–22, 70–88 y 245–246 (`showLoginMsg`, `doLogin`, listener de Enter solo en `#password`).

---

## 0. Requisito prioritario — quitar el nombre del usuario del login

**Orden del usuario, no negociable. El login NO nombra a ningún usuario real.**

| Elemento | Antes (actual, a eliminar) | Después (exacto) |
|---|---|---|
| Texto introductorio | `Entra con tu usuario del panel (sebastian o colega). Cada uno ve solo sus datos.` | `Entra con tu usuario del panel.` |
| Placeholder de usuario | `sebastian` | `tu usuario` |
| Texto de ayuda tras fallo (`#loginHint`) | `Si acabas de crear el panel y aún no tienes usuario, hay que crear los usuarios de login primero (seed-users.sql).` | `Si acabas de crear el panel y aún no tienes usuario, pide que te creen uno.` (ver §5) |

**Prohibido en toda la pantalla de login** (texto visible, placeholder, `aria-label`, comentario o atributo): cualquier nombre de usuario real, la enumeración de usuarios del panel y la referencia a `seed-users.sql`. Verificación del implementador: buscar en el bloque de login las cadenas prohibidas debe dar **0 coincidencias**.

> **Fuera de alcance de esta spec (recordatorio para el implementador):** la línea de usuario del panel (`#whoLine` en el header, hoy `sin sesión` / `me.sub + " · " + me.tenant` en `app.js` `showApp()`) NO es el login. La hace el implementador con `whoLine = solo username` (`me.sub` y nada más). Esta spec no la diseña ni la cambia.

---

## 1. Dirección visual

- **Premium, oscuro OLED, móvil primero.** Fondo casi negro, tarjeta apenas separada del fondo por borde + sombra (nota honesta de OVERRIDES §2: tarjeta `#1e222c` vs fondo `#0b0d12` = 1.22:1; la separación la dan el borde `#475569` 2.10:1 y `--shadow-card`, no el color solo).
- **Sin fuentes web, $0:** pila del sistema (`--font-body` / `--font-heading`). Quedan descartadas Cormorant / Montserrat del `MASTER.md` generado.
- **Sin emojis ni glifos como iconos** (OVERRIDES §4, `MASTER.md` anti-patrones). El único icono admitido en esta pantalla sería un SVG inline `aria-hidden="true"` (ojo de mostrar/ocultar) — opcional; el toggle lleva texto, nunca solo icono (ver §3.3).
- **Sin hex crudo en componentes.** Todo color pasa por un token de §2. `grep #[0-9a-f]{3,6}` en el bloque de login = 0 fuera de `tokens.css`.
- **Densidad 8/10, motion 5/10, varianza 6/10** (conservados de la generación, OVERRIDES §6).

### 1.1 Marca / logo

Encima del título, dentro de la misma tarjeta:

- Monograma **AVC** en un cuadrado de 48 × 48 px, fondo `--color-primary` (`#1d4ed8`), texto `--color-on-primary` blanco, `font-size: 20px`, `font-weight: 700`, radio `--radius-control` (8 px). Contraste blanco sobre `#1d4ed8` = **6.70:1** (pasa AA).
- Debajo, subtítulo de marca en `--color-text-dim`, 13 px: `Panel de Clips GTA 6`.
- Sin degradados de plataforma en el login (los neones `--color-platform-*` son para la Cola/Cuentas, no para autenticación). Sin imagen de fondo, sin foto, sin animación de entrada.

### 1.2 Layout (móvil primero)

- La tarjeta es la única cosa en la pantalla: `max-width: 380px` (se conserva el valor actual), `width: min(380px, calc(100vw - 32px))`, `margin: 32px auto` en escritorio y **`24px auto` en móvil** (OVERRIDES §1, VIS-24; el actual `60px auto` desperdicia casi una pantalla).
- `body` / contenedor con `padding` con `env(safe-area-inset-*)` (top/right/bottom/left) y `-webkit-tap-highlight-color: transparent` (UX-28 / VIS-23). `min-height: 100dvh` (no `vh`) para centrar sin que el teclado ni la barra de gestos tapen el botón.
- **Sin scroll horizontal a 360 px:** `box-sizing: border-box` en todo, `width: 100%` en inputs y botón, `overflow-wrap: anywhere` en `#loginMsg` y `#loginHint`, ningún ancho fijo mayor que el viewport. Verificar a 360 px y 375 px (checklist OVERRIDES §7).
- `color-scheme: dark` en `:root` + `<meta name="theme-color" content="#0b0d12">` (VIS-14, ya en tokens). Controles nativos (autocompletado, selector) se renderizan en oscuro.

---

## 2. Tokens exactos a usar (solo estos; valores de `tokens.css` / OVERRIDES §3)

### Color

| Uso en el login | Token | Valor |
|---|---|---|
| Fondo de página | `--color-background` | `#0b0d12` |
| Tarjeta `#loginBox` | `--color-surface` | `#1e222c` |
| Fondo de inputs | `--color-surface-input` | `#0f1115` |
| Bordes (tarjeta, inputs, toggle) | `--color-border` | `#475569` |
| Texto principal | `--color-text` | `#e8eaf0` — 16.16:1 sobre fondo, 13.22:1 sobre tarjeta |
| Texto secundario (intro, hint, labels) | `--color-text-dim` | `#9aa3b2` — 7.64:1 sobre fondo, 6.25:1 sobre tarjeta |
| Botón primario | `--color-primary` | `#1d4ed8` — blanco 6.70:1 |
| Hover primario (solo con texto blanco) | `--color-primary-hover` | `#2563eb` — blanco 5.17:1 |
| Texto sobre primario | `--color-on-primary` | `#ffffff` |
| Anillo de foco / enlace | `--color-focus` | `#93c5fd` — 10.78:1 sobre fondo, 8.82:1 sobre tarjeta |
| Texto de error | `--color-bad` | `#f87171` — 5.75:1 sobre tarjeta |
| Fondo de mensaje de error | `--color-bad-bg` | `#7f1d1d` |
| Texto sobre fondo de error | `--color-bad-fg` | `#fecaca` — 6.93:1 sobre `--color-bad-bg` |

**Prohibidos aquí:** `#3b82f6` (blanco 3.68:1, falla AA — es el acento actual del botón), `#2563eb` con texto `#e8eaf0` (4.30:1), `#ef4444` como texto (4.23:1), `#0000EE` (enlace de navegador).

### Tipografía

| Token | Valor | Uso en el login |
|---|---|---|
| `--font-body` / `--font-heading` | `system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif` | Todo el login |
| `--font-size-base` | `16px` | Cuerpo |
| `--font-size-h1` | `20px` | Monograma AVC (20 px, 700) |
| `--font-size-h2` | `16px` | Título `Iniciar sesión` |
| `--font-size-input` | `16px` | `#username`, `#password` — **obligatorio: < 16 px dispara auto-zoom en iOS** (UX-7 / VIS-6) |
| `--font-size-small` | `13px` | Labels, intro, hint, errores de campo |
| `--font-line-height-body` | `1.5` | Cuerpo y mensajes |
| `--font-line-height-heading` | `1.25` | Título y marca |

### Espaciado, radios, táctil, motion, sombra

```css
--space-1: 4px;  --space-2: 8px;  --space-3: 12px;
--space-4: 16px; --space-5: 24px; --space-6: 32px;
--radius-control: 8px;   /* botones, inputs, mensajes */
--radius-card: 12px;     /* #loginBox */
--size-touch-target: 44px;
--size-focus-ring: 3px;
--size-focus-offset: 2px;
--motion-duration-fast: 150ms;
--motion-duration-base: 200ms;
--motion-easing: ease-out;
--shadow-card: 0 1px 2px rgba(0, 0, 0, 0.4);
--shadow-elevated: 0 8px 24px rgba(0, 0, 0, 0.5); /* tarjeta al enfocar, opcional */
```

- Tarjeta: `padding: var(--space-4)` (16 px), `margin-bottom` n/a (única tarjeta), `box-shadow: var(--shadow-card)`.
- Gap vertical entre campos: `--space-3` (12 px); entre el último campo y el botón: `--space-4` (16 px).
- Transiciones 150–200 ms `ease-out` solo para `filter`/`transform` del botón y color de borde del input. `prefers-reduced-motion: reduce` → sin transiciones ni animaciones (regla ya en `tokens.css`).

---

## 3. Estructura HTML propuesta

IDs existentes que **se conservan tal cual** (la lógica de `app.js` — `doLogin`, `showLoginMsg`, `showLogin`/`showApp` — sigue encontrándolos): `loginBox`, `loginMsg`, `username`, `password`, `loginBtn`, `loginHint`. IDs nuevos solo para lo que hoy no existe: `loginForm`, `username-error`, `password-error`, `passwordToggle`, `passwordWrap`.

```html
<div id="loginBox" class="card">
  <p class="brand-mark" aria-hidden="true">AVC</p>
  <p class="brand-sub">Panel de Clips GTA 6</p>
  <h2>Iniciar sesión</h2>
  <p class="dim">Entra con tu usuario del panel.</p>

  <form id="loginForm" novalidate>
    <div id="loginMsg" role="alert"></div>

    <label for="username">Usuario</label>
    <input id="username" name="username" type="text"
           autocomplete="username" autocapitalize="none" autocorrect="off"
           spellcheck="false" placeholder="tu usuario" required
           aria-describedby="username-error">
    <p class="field-error" id="username-error" role="alert" hidden></p>

    <label for="password">Contraseña</label>
    <div class="password-wrap" id="passwordWrap">
      <input id="password" name="password" type="password"
             autocomplete="current-password" placeholder="••••••••" required
             aria-describedby="password-error">
      <button type="button" id="passwordToggle" class="ghost"
              aria-controls="password" aria-pressed="false">Mostrar</button>
    </div>
    <p class="field-error" id="password-error" role="alert" hidden></p>

    <button id="loginBtn" type="submit" class="btn-block">Entrar</button>
  </form>

  <p class="dim" id="loginHint"></p>
</div>
```

### Notas de estructura (qué cambia respecto al actual y por qué)

1. **`<form id="loginForm" novalidate>`** (UX-12): hoy `#loginBox` es un `<div>` y Enter solo funciona en `#password`. Con `<form>` + botón `type="submit"`, **Enter envía desde cualquier campo** y en móvil aparece el botón “Ir” del teclado. Se quita el listener `keydown Enter` solo de `#password` (app.js línea 246) y el `click` de `#loginBtn` (línea 245) pasa a ser `submit` del form. `novalidate` para que los mensajes sean los exactos de §5 (no el globo nativo del navegador, que no se puede redactar ni posicionar junto al campo).
2. **`label` con `for`** (UX-13): hoy ningún label está asociado. `for="username"` / `for="password"` hacen que tocar el texto enfoque el campo (objetivo táctil ganado) y que el lector de pantalla anuncie el nombre del campo. Los labels se conservan visibles — nunca placeholder-only (lo que hoy está bien, no tocar).
3. **`autocomplete` conservado** (`username` / `current-password`) y **sin bloqueo de pegado** (skill: *accessible authentication*, WCAG 2.2 AA). El gestor de contraseñas debe poder rellenar ambos campos. `autocapitalize="none" autocorrect="off" spellcheck="false"` solo en `#username`.
4. **`#loginMsg` con `role="alert"`** (UX-4, UX-16): es el resumen de error de autenticación/red (ver §5). Vacía no ocupa espacio (`:empty { display: none; }`); con contenido usa el estilo `.msg.err` existente (`--color-bad-bg` + `--color-bad-fg`, borde izquierdo 3 px `--color-bad`, `overflow-wrap: anywhere`).
5. **Errores por campo** (`#username-error`, `#password-error`): `<p class="field-error" role="alert" hidden>`, colocados **inmediatamente debajo de su campo**, enlazados con `aria-describedby`. Solo se muestran cuando ese campo es inválido; se ocultan (`hidden`) al corregir. Nunca se sustituye el error junto al campo por solo el banner superior.
6. **`#loginHint`**: párrafo de ayuda, **no** es un error. Solo se rellena tras un fallo de autenticación (ver §5) y se vacía al reintentar con éxito o al empezar un nuevo intento. Nunca contiene jerga de desarrollo ni nombres.
7. **Sin `autofocus` al cargar** (decisión de diseño): en móvil abriría el teclado y haría zoom antes de que el usuario toque nada. El foco solo se mueve tras un envío fallido (ver §4.3).
8. **Sin enlace “¿Olvidaste tu contraseña?”**: no existe recuperación en este panel; no inventar un enlace muerto.

### 3.1 Estilos propuestos para los elementos nuevos (el implementador los vuelca a clases, no a inline — VIS-8)

```css
#loginBox { max-width: 380px; width: min(380px, calc(100vw - 32px)); margin: 32px auto; }
@media (max-width: 640px) { #loginBox { margin: 24px auto; } }

.brand-mark {
  width: 48px; height: 48px; margin: 0 0 var(--space-2);
  display: grid; place-items: center;
  background: var(--color-primary); color: var(--color-on-primary);
  font-size: var(--font-size-h1); font-weight: 700; line-height: 1;
  border-radius: var(--radius-control);
}
.brand-sub { color: var(--color-text-dim); font-size: var(--font-size-small); margin: 0 0 var(--space-4); }

#loginBox label { display: block; font-size: var(--font-size-small); color: var(--color-text-dim); margin: 0 0 var(--space-1); }
#loginBox input {
  width: 100%; min-height: var(--size-touch-target);
  background: var(--color-surface-input); color: var(--color-text);
  border: 1px solid var(--color-border); border-radius: var(--radius-control);
  font-size: var(--font-size-input); line-height: var(--font-line-height-body);
  padding: 10px 12px; margin: 0 0 var(--space-3);
}
#loginBox input::placeholder { color: var(--color-text-dim); opacity: 1; }

.password-wrap { position: relative; }
.password-wrap input { padding-right: 96px; } /* hueco para el toggle, sin tapar el texto */
#passwordToggle {
  position: absolute; right: var(--space-1); top: 0;
  min-height: var(--size-touch-target); min-width: 44px;
  padding: 10px 12px; background: transparent;
  border: 1px solid transparent; color: var(--color-link);
  font-size: var(--font-size-small); font-weight: 600;
}

.btn-block { width: 100%; } /* sustituye al style="width:100%" inline actual (VIS-8) */
.field-error { color: var(--color-bad); font-size: var(--font-size-small); margin: calc(-1 * var(--space-2)) 0 var(--space-3); }
#loginMsg:empty { display: none; }
#loginMsg .msg.err { overflow-wrap: anywhere; }
```

### 3.2 Botón Entrar

- Primario: fondo `--color-primary`, texto `--color-on-primary` blanco, `font-size: 16px`, `font-weight: 600`, `min-height: 44px`, `padding: 10px 16px`, radio `--radius-control`, ancho 100%.
- Hover: `filter: brightness(1.12)` (fondo efectivo `--color-primary-hover`, solo con texto blanco — nunca con `#e8eaf0`). Active: `transform: scale(.98)`. `touch-action: manipulation`, `cursor: pointer`. Transiciones 150–200 ms (reglas globales de `tokens.css`).
- **Sin spinner decorativo obligatorio:** el estado de carga se comunica con el texto `Entrando…` + `disabled` (ver §4.4). Si el implementador añade un spinner, que sea SVG inline `aria-hidden="true"` de 16 px a la izquierda del texto, nunca emoji ni glifo.

### 3.3 Toggle mostrar / ocultar contraseña (UX-12)

- Botón `type="button"` (nunca `submit`), dentro de `.password-wrap`, a la derecha del campo, **44 × 44 px mínimo**, texto visible `Mostrar` / `Ocultar` (nunca solo icono: la skill exige nombre accesible; si se añade un ojo SVG, lleva `aria-hidden="true"`).
- Atributos: `aria-controls="password"`, `aria-pressed="false"` → `"true"` al mostrar. Al activarse cambia `#password` entre `type="password"` y `type="text"` **sin borrar ni mover el valor**, sin tocar el foco del campo salvo que el usuario lo pida, y sin revalidar. El texto del botón cambia a `Ocultar` y `aria-pressed` a `true`; al volver, `Mostrar` / `false`.
- Al enviar el formulario con éxito o al cerrar sesión, la contraseña vuelve a `type="password"` y el toggle a `Mostrar`.

---

## 4. Estados

### 4.1 Normal

- Inputs: fondo `--color-surface-input`, borde 1 px `--color-border`, texto `--color-text` 16 px. Placeholder en `--color-text-dim` (nunca como único label).
- Botón: fondo `--color-primary`, blanco. Intro y hint en `--color-text-dim` 13 px, `line-height: 1.5`.
- Sin errores visibles: `#username-error` / `#password-error` con `hidden`, `#loginMsg` vacío (sin espacio reservado), `#loginHint` vacío.

### 4.2 Foco (UX-1 / VIS-5 — crítico)

- **Regla global obligatoria (ya en `tokens.css`):**
  ```css
  :focus-visible { outline: var(--size-focus-ring) solid var(--color-focus); outline-offset: var(--size-focus-offset); }
  ```
  Es decir: **anillo de 3 px `#93c5fd`, offset 2 px**, en `button, input, a` del login. Contraste del anillo 10.78:1 sobre fondo (WCAG 2.2 exige ≥ 3:1).
- Foco en input, además del anillo: borde `2px solid var(--color-focus)` (el borde crece hacia dentro para no mover el layout; usar `box-shadow: inset 0 0 0 1px var(--color-focus)` si el implementador prefiere no cambiar el ancho).
- El foco **nunca se quita** (`outline: none` prohibido sin sustituto). El orden de tabulación es el orden visual: usuario → contraseña → toggle → Entrar.
- Foco visible también en el toggle y en el botón deshabilitado no aplica (un botón `disabled` no recibe foco; ver §4.4).

### 4.3 Error (UX-4 — crítico; skill *error summary validation*)

Reglas de comportamiento (para `doLogin` / handler de `submit`):

1. **Validación al enviar:** si `#username` está vacío → error de campo usuario; si `#password` está vacío → error de campo contraseña. Si ambos están vacíos, se muestran **los dos** errores y el foco va al **primer campo inválido** (`#username`).
2. **Error junto al campo:** el `<p class="field-error" role="alert">` bajo el campo muestra el texto de §5; el campo lleva `aria-invalid="true"` y borde `2px solid var(--color-bad)` (texto de error `--color-bad`, 5.75:1 sobre tarjeta). El error **no es solo color**: lleva texto.
3. **Anuncio:** `role="alert"` en cada error de campo y en `#loginMsg` lo anuncia el lector de pantalla al aparecer. No mover el foco en cada `blur` (la skill lo prohíbe); solo tras el envío fallido.
4. **Foco al campo inválido:** tras mostrar los errores, `.focus()` al primer campo inválido. En fallo de autenticación (credenciales incorrectas), el foco va a `#username`, se **vacía `#password`** y se conserva el usuario escrito (no se revela qué parte falló — no enumerar usuarios).
5. **Limpieza al corregir:** al escribir en un campo con error, su error se oculta y se quita `aria-invalid` (validación inline al teclear, no solo al enviar). `#loginMsg` se vacía al empezar un nuevo intento.
6. **Resumen (`#loginMsg`):** solo para fallos que no son de un campo concreto (credenciales incorrectas, red/API, sesión caducada). No duplicar el mismo texto en el resumen y en el campo: los campos vacíos usan solo error de campo; el fallo de credenciales usa solo `#loginMsg` + `#loginHint`.

### 4.4 Cargando (UX-3 — crítico)

- Al enviar un intento válido: `#loginBtn` pasa a `disabled` **y** su texto cambia de `Entrar` a **`Entrando…`** hasta que la promesa resuelve (hoy `doLogin` ya deshabilita pero no cambia el texto — eso se corrige aquí).
- `button:disabled { opacity: .55; cursor: not-allowed; }` (regla de `tokens.css`). El botón deshabilitado no acepta un segundo envío: el handler debe ignorar envíos mientras está deshabilitado (guardia contra doble POST).
- Al resolver con éxito: transición a la app (sin mensaje de éxito en el login; `#loginHint` se vacía). Al fallar: el botón vuelve a `Entrar` y `disabled = false`, se muestra el error de §5 y el foco va al campo indicado en §4.3.
- Sin skeleton ni contenido que salte: `#loginMsg` vacío no reserva espacio; al aparecer, el desplazamiento es aceptable porque el botón queda debajo del formulario (el usuario no pierde el botón de vista en móvil: el formulario cabe en una pantalla de 360 × 640 con el margen de 24 px).

---

## 5. Textos exactos en español

Todos los textos visibles del login. **No añadir ningún otro texto que nombre usuarios, roles o archivos internos.**

| Elemento | Texto exacto |
|---|---|
| Marca (monograma) | `AVC` |
| Subtítulo de marca | `Panel de Clips GTA 6` |
| Título | `Iniciar sesión` |
| Intro | `Entra con tu usuario del panel.` |
| Label usuario | `Usuario` |
| Placeholder usuario | `tu usuario` |
| Label contraseña | `Contraseña` |
| Placeholder contraseña | `••••••••` (se conserva; es solo visual, el label es el nombre real) |
| Toggle contraseña (oculta) | `Mostrar` |
| Toggle contraseña (visible) | `Ocultar` |
| Botón (reposo) | `Entrar` |
| Botón (cargando) | `Entrando…` |
| Error campo usuario vacío | `Escribe tu usuario.` |
| Error campo contraseña vacía | `Escribe tu contraseña.` |
| Error autenticación (`#loginMsg`) | `Usuario o contraseña incorrectos. Revisa ambos y vuelve a intentarlo.` |
| Ayuda tras fallo de autenticación (`#loginHint`) | `Si acabas de crear el panel y aún no tienes usuario, pide que te creen uno.` |
| Error red / API (`#loginMsg`) | `No se pudo conectar con el panel. Comprueba tu conexión y vuelve a intentarlo.` |
| Sesión caducada (`#loginMsg`, ya existe en `api()`) | `Sesión caducada, entra de nuevo.` |

Notas sobre los textos:

- **Error de autenticación genérico a propósito:** no dice si falló el usuario o la contraseña (no enumerar usuarios). Es el mismo mensaje para usuario inexistente, contraseña incorrecta o combinación errónea.
- **El `#loginHint` actual se elimina íntegramente** (UX-14): `seed-users.sql` es jerga de desarrollo sin vía de recuperación para quien entra. El texto nuevo dice qué hacer (pedir que le creen un usuario) sin decir cómo se crea por dentro.
- **Distinguir fallo de credenciales de fallo de red** (UX-14): si `fetch` falla por red / la API no responde, el mensaje es el de red, no el de credenciales, y `#loginHint` queda vacío.
- Sin punto final extra ni mayúsculas distintas a las de la tabla. La elipsis de carga es `…` (un solo carácter), no `...`.

---

## 6. Accesibilidad — checklist de esta pantalla

- [ ] Contraste ≥ 4.5:1 en todos los pares de texto (§2; primario/blanco 6.70:1, error 5.75:1, foco 10.78:1).
- [ ] Foco visible 3 px `--color-focus` en usuario, contraseña, toggle y botón (§4.2).
- [ ] Labels con `for`, placeholders nunca como único label (§3).
- [ ] Inputs a 16 px, táctil 44 px en campos, toggle y botón (§2, §3.1).
- [ ] `<form>` con submit por Enter desde cualquier campo (§3).
- [ ] Gestor de contraseñas y pegado permitidos; `autocomplete` correcto (§3, skill *accessible authentication*).
- [ ] Error junto al campo con `aria-describedby` + `role="alert"`, foco al primer campo inválido (§4.3, skill *error summary validation*).
- [ ] Botón loading `Entrando…` + `disabled`, sin doble envío (§4.4).
- [ ] Toggle con texto visible, `aria-pressed` y `aria-controls` (§3.3).
- [ ] Safe-area aplicada, sin scroll horizontal a 360 px (§1.2).
- [ ] `prefers-reduced-motion: reduce` respetado (sin transiciones/animaciones).
- [ ] Cero nombres de usuario reales y cero referencias internas en la pantalla (§0).
- [ ] `#whoLine` queda como recordatorio para el implementador (solo username), fuera de esta spec (§0).

## 7. Qué NO incluye esta spec (para el implementador)

- No se toca `frontend/dist/index.html` ni `app.js` (esta spec es solo diseño).
- No se diseña el header, la navegación, el Worker, la Cola, Cuentas, Avisos ni Ajustes (sus specs van aparte; sus hallazgos están en las auditorías citadas).
- No se cambia la lógica de autenticación (endpoint, sesión, `seed-users.sql` por dentro): solo los textos y estados visibles del login.
- No se añade recuperación de contraseña, registro de usuarios ni “recordarme”.
