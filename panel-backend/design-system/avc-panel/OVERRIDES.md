# AVC Panel — OVERRIDES (autoritativo)

> **Este archivo MANDA sobre `MASTER.md`, `tokens.json` y `tokens.css` generados.**
> La salida automática de `ui-ux-pro-max` (`--design-system`, 2026-11-17) vino en
> modo claro de lujo (fondo `#FAFAF9`, dorado `#A16207`, serif Cormorant) — es
> incompatible con el panel real: dashboard de creator, oscuro OLED, móvil
> primero, $0 (sin fuentes web de pago ni dependencias). Se conservan de la
> generación solo la densidad 8/10, el motion 5/10 y la varianza 6/10.
> **El implementador usa ÚNICAMENTE los tokens de este archivo**
> (reflejados en `tokens.json` — fuente de verdad — y `tokens.css`).
> No se tocó `frontend/dist/` ni lógica de negocio.

- **Fecha:** 2026-11-17
- **Fuentes:** `../redesign/auditoria-ux.md` (32 hallazgos, 5 críticos),
  `../redesign/auditoria-visual.md` (24 defectos),
  `../redesign/ideas.md` (top 5: matriz de estados, barra de prioridad,
  chips de freno, tarjeta de clip 9:16, semáforo del Worker).
- **Dirección:** OLED oscuro + neón por plataforma + densidad de dashboard.

---

## 1. Correcciones de auditoría (qué cambia y por qué)

| # | Hallazgo | Corrección (token / regla) |
|---|---|---|
| UX-1 / VIS-5 | Sin foco visible en ningún control (crítico) | `:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }` en `button, input, textarea, select, a`. `--color-focus: #93c5fd` (10.78:1 sobre fondo, 8.82:1 sobre tarjeta; WCAG 2.2 exige ≥ 3:1). |
| UX-2 / VIS-1 | Blanco sobre `#3b82f6` = 3.68:1, falla AA (crítico) | `--color-primary: #1d4ed8` (blanco 6.70:1; texto `#e8eaf0` 5.57:1). `#2563eb` queda SOLO como `--color-primary-hover` con texto blanco (5.17:1); con texto `#e8eaf0` da 4.30:1 — prohibido. `#3b82f6` eliminado. |
| UX-3 / VIS-19 | Sin loading ni botón deshabilitado en acciones async (crítico) | Botón async: `disabled` + texto de carga (`Guardando… / Publicando… / Verificando…`) hasta resolver. `button:disabled { opacity: .55; cursor: not-allowed; }`. |
| UX-4 | Errores solo en banner superior, sin anuncio ni foco (crítico) | Error bajo su campo con `aria-describedby`, `role="alert"` en `#msg`, foco al primer campo inválido. Tokens: fondo error `--color-bad-bg`, texto `--color-bad-fg`. |
| UX-5 / VIS-2 | Tabla de Cola con scroll horizontal en móvil (crítico) | ≤ 640 px: tarjeta de clip apilada (ver §6, idea #1), no tabla. Tabla de escritorio envuelta en `overflow-x: auto`. |
| UX-6 / VIS-7 | Táctil 33–37 px < 44 px (alta) | `--size-touch-target: 44px`; `min-height: 44px` en `button`, `nav button`, `input`, `select`, `textarea`. Gap entre pestañas 8 px (`--space-2`). |
| UX-7 / VIS-6 | Inputs 14 px → zoom automático en iOS (alta) | `--font-size-input: 16px` en `input, textarea, select`. Base de texto `--font-size-base: 16px`, `line-height: 1.5`. |
| UX-9 / VIS-1 | Pestaña activa solo distinguible por color (alta) | Activa: fondo `--color-primary` + `font-weight: 700` + `aria-selected="true"` + borde inferior 3 px blanco. Nunca solo color. |
| UX-15 / VIS-5 | Sin feedback de prensa (media) | `button:active { transform: scale(.98); }`, `button:hover { filter: brightness(1.12); }`, `touch-action: manipulation`. Transiciones 150–250 ms. |
| UX-16 | `#msg` persistente sin cierre ni anuncio (media) | `role="status" aria-live="polite"` (éxito, auto-limpieza 5 s) / `role="alert"` (error, con ×). `overflow-wrap: anywhere` (URLs largas de R2). |
| UX-27 / VIS-5 | `scrollIntoView smooth` ignora `prefers-reduced-motion` (baja) | Media query `prefers-reduced-motion: reduce` en `tokens.css` (anula transiciones/animaciones) + `behavior: auto` en ese caso. |
| UX-28 / VIS-23 | Sin safe-area (baja) | `padding` con `env(safe-area-inset-*)` en `body`/`main`; `-webkit-tap-highlight-color: transparent`. |
| VIS-3 | Enlaces azul de navegador `#0000EE` = 1.79:1 (alta) | `a { color: var(--color-link); }` — `--color-link: #93c5fd` (8.82:1 sobre tarjeta). `:hover` subraya. **Nunca** `--color-primary` como color de texto de enlace sobre fondo oscuro (2:1 aprox.). |
| VIS-4 | `h3` sin estilo, más grande que los `h2` (alta) | Escala tipográfica en tokens: h1 20 / h2 16 / h3 14 / cuerpo 16 / small 13 / caption 12; `line-height` encabezados 1.25. |
| VIS-8 | 7 hex sueltos fuera de `:root` + estilos inline en `app.js` (alta) | **Prohibido el hex crudo en componentes.** Todo color pasa por un `--color-*` de este archivo. Los 7 hex auditados ya tienen token: `#7f1d1d` → `--color-danger-bg` / `--color-bad-bg`; `#14532d` → `--color-ok-bg`; `#bbf7d0` → `--color-ok-fg`; `#fecaca` → `--color-bad-fg`; `#1e3a5f` → `--color-info-bg`; `#0f1115` → `--color-surface-input`; `#2a2f3a` → eliminado (borde nuevo `--color-border`). Estilos inline de `app.js` → clases (`.account-card`, `.notif-row`, `.btn-block`). |
| VIS-9 | `.row > * { flex:1 }` estira el botón secundario (media) | `.row > button { flex: 0 0 auto; }`; estirado solo con clase explícita `.row-fill` (Worker). |
| VIS-10/11 | Estado de clip como texto plano; "Sin verificar" sin diseño (media) | Estados SIEMPRE como `.pill` semántica (ver §6, idea #3). Solo 4 estados de cuenta (v15): Conectada / Sin credencial / No conectada / Sin crear — "Sin verificar" no se renderiza. |
| VIS-12 | Sin `line-height` en todo el CSS (media) | `body { line-height: 1.5; }`, encabezados 1.25, celdas 1.45. |
| VIS-13 | Tarjeta indistinguible del fondo (media) | Fondo `--color-background: #0b0d12`, tarjeta `--color-surface: #1e222c`, borde `--color-border: #475569` (2.10:1 sobre tarjeta) + `--shadow-card`. Ver nota honesta en §2. |
| VIS-14 | Falta `color-scheme: dark` (media) | `:root { color-scheme: dark; }` + `<meta name="theme-color" content="#0b0d12">`. Estilo propio para `input[type=file]::file-selector-button`. |
| VIS-15 | Badge de avisos como texto "(3)" (media) | `.badge` con `--color-badge-bg: #dc2626`, blanco 4.83:1, `min-width: 20px`, tope visual "9+", oculto en 0. |
| VIS-17 | Header/nav no fijos (media) | `nav { position: sticky; top: 0; z-index: 10; background: var(--color-background); }`. |
| VIS-21 | Encabezados de tabla indistinguibles; números a la izquierda (baja) | `th { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--color-text-dim); }`; columna numérica a la derecha con `font-variant-numeric: tabular-nums`. |
| VIS-24 | Espaciado ad hoc, sin escala (baja) | Escala única `--space-1..6`: 4 / 8 / 12 / 16 / 24 / 32 px. `#loginBox` pasa de `margin: 60px auto` a `32px auto` (24 px en móvil). |
| UX-32 / VIS-18 | JSON de Ajustes en fuente proporcional (baja) | `#settingsJson { font-family: var(--font-mono); font-size: 13px; line-height: 1.6; }`. |
| UX-10 | Ajustes como JSON crudo (alta, no-token) | Formulario por campo; JSON solo como vista avanzada plegable. Sin token implicado. |
| UX-8/11/12/13/17/18/19/20/23/24/25/26/30/31 | Navegación, confirmaciones, labels, skeletons, etc. | Sin token propio: se resuelven en el implementador con los tokens de este archivo (ver checklist §7). |

---

## 2. Paleta final con contrastes verificados (calculados, no estimados)

Calculados con la fórmula WCAG (luminancia relativa). Todo par de **texto**
pasa AA 4.5:1; el anillo de foco pasa 3:1 (WCAG 2.2).

| Uso | Token | Valor | Sobre | Ratio | Veredicto |
|---|---|---|---|---|---|
| Texto principal | `--color-text` | `#e8eaf0` | fondo `#0b0d12` | 16.16:1 | Pasa |
| Texto principal | `--color-text` | `#e8eaf0` | tarjeta `#1e222c` | 13.22:1 | Pasa |
| Texto secundario | `--color-text-dim` | `#9aa3b2` | fondo | 7.64:1 | Pasa |
| Texto secundario | `--color-text-dim` | `#9aa3b2` | tarjeta | 6.25:1 | Pasa |
| Botón primario (blanco) | `--color-primary` | `#1d4ed8` | — | 6.70:1 | Pasa |
| Pestaña activa (texto) | `--color-primary` | `#1d4ed8` | — | 5.57:1 | Pasa |
| Hover primario (blanco) | `--color-primary-hover` | `#2563eb` | — | 5.17:1 | Pasa |
| Enlace / foco | `--color-link` / `--color-focus` | `#93c5fd` | tarjeta | 8.82:1 | Pasa |
| Enlace / foco | `--color-link` / `--color-focus` | `#93c5fd` | fondo | 10.78:1 | Pasa (foco ≥ 3:1) |
| OK (texto) | `--color-ok` | `#22c55e` | tarjeta | 6.98:1 | Pasa |
| OK (texto) | `--color-ok` | `#22c55e` | fondo | 8.53:1 | Pasa |
| Pill OK | `--color-ok-fg` `#bbf7d0` | sobre `--color-ok-bg` `#14532d` | — | 7.52:1 | Pasa |
| Error (texto) | `--color-bad` | `#f87171` | tarjeta | 5.75:1 | Pasa |
| Error (texto) | `--color-bad` | `#f87171` | fondo | 7.03:1 | Pasa |
| Pill error | `--color-bad-fg` `#fecaca` | sobre `--color-bad-bg` `#7f1d1d` | — | 6.93:1 | Pasa |
| Aviso (texto) | `--color-warn` | `#f59e0b` | tarjeta | 7.41:1 | Pasa |
| Aviso (texto) | `--color-warn` | `#f59e0b` | fondo | 9.05:1 | Pasa |
| Pill aviso | `--color-warn-fg` `#fde68a` | sobre `--color-warn-bg` `#451a03` | — | 12.03:1 | Pasa |
| Botón peligro (blanco) | `--color-danger-bg` | `#7f1d1d` | — | 10.02:1 | Pasa |
| Mensaje info (blanco) | `--color-info-bg` | `#1e3a5f` | — | 11.50:1 | Pasa |
| Badge avisos (blanco) | `--color-badge-bg` | `#dc2626` | — | 4.83:1 | Pasa |
| Borde | `--color-border` | `#475569` | tarjeta `#1e222c` | 2.10:1 | Pasa (borde, no texto) |
| YouTube (texto) | `--color-platform-youtube` | `#ff4e45` | tarjeta | 4.87:1 | Pasa |
| TikTok (texto) | `--color-platform-tiktok` | `#25f4ee` | tarjeta | 11.55:1 | Pasa |
| Instagram (texto) | `--color-platform-instagram` | `#f77737` | tarjeta | 5.79:1 | Pasa |
| Instagram rosa (texto) | `--color-platform-instagram-pink` | `#f472b6` | tarjeta | 6.01:1 | Pasa |
| Facebook (texto) | `--color-platform-facebook` | `#60a5fa` | tarjeta | 6.26:1 | Pasa |

### Valores rechazados (no usar)

| Valor | Por qué |
|---|---|
| `#3b82f6` (acento actual) | Blanco 3.68:1 — falla AA. Eliminado. |
| `#2563eb` con texto `#e8eaf0` | 4.30:1 — falla AA. Solo con texto blanco, solo en hover. |
| `#ef4444` como texto sobre tarjeta | 4.23:1 — falla AA. Usar `#f87171` para texto; `#ef4444` solo como icono grande o punto decorativo. |
| `#ff0000`, `#e1306c`, `#fe2c55`, `#1877f2` como texto | 4.0:1 / 3.67:1 / 4.32:1 / ~3.5:1 sobre tarjeta — fallan. Son colores de marca **decorativos** (puntos, bordes, degradados), nunca texto. Usar las variantes claras de la tabla. |
| Blanco sobre `#ef4444` (badge) | 3.76:1 — falla. Badge usa `#dc2626` (4.83:1). |
| `#334155` como borde sobre `#1e222c` | 1.54:1 — borde demasiado débil. Usar `#475569` (2.10:1). |

### Nota honesta sobre superficies (VIS-13)

Tarjeta `#1e222c` vs fondo `#0b0d12` = **1.22:1**, por debajo de la aspiración
de 1.5:1 de la auditoría visual. Se probaron tarjetas más claras (`#2b3242`
llega a 1.52:1) pero rompen el texto de plataforma (YouTube `#ff4e45` cae a
3.92:1) y debilitan el borde. Decisión: se mantiene `#1e222c` y la separación
la dan el borde `#475569` (2.10:1) + `--shadow-card`. WCAG no exige 1.5:1
entre superficies (no es criterio de texto); todos los pares de texto pasan.

---

## 3. Lista completa de tokens (los que debe usar el implementador)

### Color

```css
--color-background: #0b0d12;        /* página */
--color-surface: #1e222c;            /* tarjetas */
--color-surface-input: #0f1115;      /* fondo de inputs */
--color-border: #475569;             /* bordes 1px */
--color-text: #e8eaf0;               /* texto principal */
--color-text-dim: #9aa3b2;           /* texto secundario */
--color-link: #93c5fd;               /* enlaces */
--color-primary: #1d4ed8;            /* acción primaria / pestaña activa */
--color-primary-hover: #2563eb;      /* hover primario, solo con texto blanco */
--color-on-primary: #ffffff;
--color-focus: #93c5fd;              /* anillo de foco */
--color-ok: #22c55e;
--color-ok-bg: #14532d;
--color-ok-fg: #bbf7d0;
--color-bad: #f87171;                /* texto de error sobre oscuro */
--color-bad-bg: #7f1d1d;
--color-bad-fg: #fecaca;
--color-warn: #f59e0b;
--color-warn-bg: #451a03;
--color-warn-fg: #fde68a;
--color-danger-bg: #7f1d1d;          /* botón Parar */
--color-info-bg: #1e3a5f;            /* mensaje info */
--color-badge-bg: #dc2626;           /* badge de avisos no leídos */
--color-platform-youtube: #ff4e45;
--color-platform-tiktok: #25f4ee;
--color-platform-instagram: #f77737;
--color-platform-instagram-pink: #f472b6;
--color-platform-facebook: #60a5fa;
```

### Tipografía

```css
--font-body: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
--font-heading: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
--font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
--font-size-base: 16px;
--font-size-h1: 20px;
--font-size-h2: 16px;
--font-size-h3: 14px;
--font-size-input: 16px;   /* iOS: <16px dispara auto-zoom */
--font-size-small: 13px;
--font-size-caption: 12px;
--font-line-height-body: 1.5;
--font-line-height-heading: 1.25;
```

Sin fuentes web: la pila del sistema es la correcta para este panel ($0,
sin descarga, render nativo en móvil). Las fuentes generadas (Cormorant /
Montserrat) quedan descartadas.

### Espaciado, radios, táctil, motion

```css
--space-1: 4px;  --space-2: 8px;  --space-3: 12px;
--space-4: 16px; --space-5: 24px; --space-6: 32px;
--radius-control: 8px;   /* botones, inputs, mensajes */
--radius-card: 12px;     /* tarjetas */
--radius-pill: 999px;    /* pills, badges */
--size-touch-target: 44px;
--size-focus-ring: 3px;
--size-focus-offset: 2px;
--motion-duration-fast: 150ms;
--motion-duration-base: 200ms;
--motion-duration-slow: 250ms;
--motion-easing: ease-out;
--shadow-card: 0 1px 2px rgba(0, 0, 0, 0.4);
--shadow-elevated: 0 8px 24px rgba(0, 0, 0, 0.5);
```

### Reglas globales obligatorias (ya incluidas en `tokens.css`)

- `:focus-visible` 3 px `--color-focus`, offset 2 px.
- `min-height: 44px` en botones, pestañas, inputs, selects, textareas.
- `font-size: 16px` en inputs.
- `button:hover` brillo 1.12; `button:active` escala 0.98; `button:disabled`
  opacidad 0.55; `touch-action: manipulation`.
- `prefers-reduced-motion: reduce` anula transiciones y animaciones.

---

## 4. Componentes del top 5 de ideas (especificación visual, sin lógica)

### 4.1 Matriz de estados por plataforma (idea #3, Cola + detalle)

Un chip por plataforma: punto/logo en su `--color-platform-*`, nombre en
español, estado como `.pill` semántica (`.pill.ok` / `.pill.bad` /
`.pill.warn` / `.pill.neutral`). Estados: pendiente, en cola, publicado ✓,
fallido ✕, frenado ⏸. El estado nunca es solo color: lleva texto. El texto
crudo de la BD (`queued`) se traduce antes de renderizar. El chip usa fondo
`--color-surface-input`, borde `--color-border`, radio `--radius-pill`.

### 4.2 Barra de prioridad con desglose (idea #2, Cola)

Barra fina bajo cada clip: fondo `--color-surface-input`, relleno con
degradado por tramos (rojo <50 `--color-bad`, ámbar 50–89 `--color-warn`,
verde ≥90 `--color-ok`). Al tocarla, desglose en tres segmentos
(viralidad / SEO / chat_norm) con sus valores numéricos — la fórmula no se
toca, solo se visualiza. Texto numérico con `font-variant-numeric:
tabular-nums`.

### 4.3 Chips de freno (idea #4, detalle del clip)

Cada freno activo es un chip `--color-warn-bg` / `--color-warn-fg`
(12.03:1) o `--color-bad-bg` / `--color-bad-fg` (6.93:1) según severidad,
con el motivo en lenguaje claro ("SEO 87 — faltan 3 puntos", "Sin MP4
subido", "Límite de TikTok de hoy alcanzado") y, cuando aplica, el botón
que lo resuelve al lado. Un freno nunca se comunica solo con color.

### 4.4 Tarjeta de clip 9:16 (idea #1, Cola en móvil)

Miniatura vertical 9:16 (frame del MP4 o placeholder con degradado
`--color-surface` → `--color-surface-input`), título en `--color-text`,
anillo de score con color por tramo (mismos umbrales que §4.2) y la matriz
de plataformas (§4.1) debajo. En ≤640 px sustituye a la fila de tabla;
en escritorio la tabla se mantiene con `th` según VIS-21.

### 4.5 Semáforo del Worker en el header (idea #8, global)

Punto fijo en el header, siempre visible: verde `--color-ok` en marcha,
ámbar `--color-warn` parado, rojo `--color-bad` API caída — siempre con
texto de estado al lado o al tocarlo (nunca solo color). La salud de la API
incluye botón "Reintentar" y hora del último intento.

### Componentes base

- **Pill:** fondo/bg + fg según §3, `padding: 2px 9px`, radio 999 px,
  `font-size: 12px`, `font-weight: 600`. Variantes: `.pill.ok`, `.pill.bad`,
  `.pill.warn`, `.pill.neutral` (fondo `--color-surface-input`, texto
  `--color-text-dim`).
- **Badge:** `.badge` con `--color-badge-bg`, texto blanco, `min-width:
  20px`, `height: 20px`, `line-height: 20px`, tope "9+", oculto en 0.
- **Card:** fondo `--color-surface`, borde 1 px `--color-border`, radio
  12 px, padding `--space-4`, `box-shadow: var(--shadow-card)`.
- **Botón primario:** fondo `--color-primary`, texto blanco, `min-height:
  44px`, `padding: 10px 16px`, radio 8 px. **Secundario (ghost):** fondo
  transparente, borde `--color-border`, texto `--color-text`. **Peligro:**
  fondo `--color-danger-bg`, texto blanco.
- **Input:** fondo `--color-surface-input`, borde `--color-border`,
  texto `--color-text`, `font-size: 16px`, `min-height: 44px`, radio 8 px.
- **Toast / `.msg`:** éxito `--color-ok-bg` + texto `--color-ok-fg`;
  error `--color-bad-bg` + texto `--color-bad-fg`; info `--color-info-bg`
  + blanco; borde izquierdo 3 px del color semántico; `role="status"` /
  `role="alert"`; `overflow-wrap: anywhere`.
- **Skeleton:** bloques `--color-surface-input` con shimmer sutil
  (desactivado bajo `prefers-reduced-motion`), `aria-busy="true"`.
- **Iconos:** solo SVG inline (Lucide/Heroicons), `aria-hidden="true"`.
  Prohibidos emojis y glifos `▶ ■ ●` como iconos (sustituir por SVG o por
  un `.dot` CSS de 8 px).

---

## 5. Motion

- Duraciones 150–250 ms, `ease-out`. Press 0.98, hover brillo 1.12.
- Sin animaciones decorativas; el movimiento solo confirma acciones.
- `prefers-reduced-motion: reduce` → sin transiciones, sin animación,
  `scrollIntoView` con `behavior: auto`.
- El snippet GSAP "Stagger List" del `MASTER.md` generado **no aplica**:
  no usar `back.out` en tablas densas ni stagger en listas de datos.

---

## 6. Lo que NO cambia respecto al `MASTER.md` generado

- Diales: densidad 8/10, motion 5/10, varianza 6/10.
- Radios 8 / 12 / 999 px (ya eran los del front).
- Reglas anti-patrones: sin emojis como iconos, sin hex crudo, sin cambios
  de estado instantáneos, foco visible obligatorio.
- Checklist pre-entrega (pro-rules.md): verificar a 375 px, sin scroll
  horizontal, contraste ≥ 4.5:1, táctil ≥ 44 px, reduced-motion.

---

## 7. Checklist del implementador (antes de dar por hecho el rediseño)

- [ ] Ningún hex fuera de `tokens.css` / `tokens.json` (grep de `#[0-9a-f]{3,6}` en `index.html` y `app.js` = 0, salvo los tokens).
- [ ] Contraste verificado en los 5 pares críticos: primario/blanco 6.70:1, pestaña activa 5.57:1, enlace 8.82:1, error 5.75:1, badge 4.83:1.
- [ ] Foco visible 3 px en todos los controles; skip link "Saltar al contenido".
- [ ] Táctil 44 px en botones, pestañas e inputs; inputs a 16 px.
- [ ] Cola en tarjeta 9:16 a ≤ 640 px; sin scroll horizontal a 360 px.
- [ ] Estados como pills con texto, nunca solo color; solo 4 estados de cuenta.
- [ ] Botones async con loading + disabled; errores junto al campo con `role="alert"`.
- [ ] `color-scheme: dark` + `theme-color #0b0d12`; safe-area aplicada.
- [ ] `prefers-reduced-motion` respetado.

---

## 8. Variante "GTA 6 / Vice City" — atardecer neón (2026-11-17)

> **Orden directa del usuario (MODO GTA 6).** Variante temática sobre los
> §§1–7, que siguen mandando en todo lo que aquí no se cambie
> explícitamente. Solo CSS (degradados, sombras neón, bordes, gradiente de
> texto en títulos). Sin imágenes con copyright, sin logos robados, sin
> assets externos. Activación: `<html data-theme="gta6">` + cargar
> `tokens-gta6.css` DESPUÉS de `tokens.css`. No se tocó `frontend/dist/`
> ni lógica de negocio: la implementación es fase siguiente.
> **Fuente skill:** `ui-ux-pro-max --design-system` ("video dashboard
> creator tool GTA VI Vice City neon sunset") devolvió Soft UI índigo
> claro (`#6366F1` / fondo `#F5F3FF`) — rechazado por incompatible con el
> panel OLED; `--domain color` aportó "Night indigo + dream violet on
> dark" y "Neon red+blue on dark"; `--domain typography` aportó la pareja
> **Barlow Condensed / Barlow** ("Sports/Fitness": condensed, energética,
> acción), adoptada como display/cuerpo. La paleta final NO es la del
> generador: es una paleta propia verificada par a par (abajo).

### 8.1 Qué cambia respecto al MASTER / base y por qué

| Cambio | Base (§§1–7) | Variante GTA 6 | Por qué |
|---|---|---|---|
| Fondo | `#0b0d12` negro frío | `#100a1a` noche violeta | Atardecer Vice City: el violeta es el cielo, no un gris |
| Tarjeta | `#1e222c` gris azulado | `#261a38` violeta oscuro | Misma familia que el fondo; el contraste texto se mantiene (13.56:1) |
| Input | `#0f1115` | `#150d24` violeta casi negro | Coherencia cálida; texto 15.66:1 |
| Borde | `#475569` | `#7c6aa0` lavanda oscuro | Sobre tarjeta violeta da 3.44:1 (base: 2.10:1) — más separación |
| Texto secundario | `#9aa3b2` gris | `#b8b3c9` lavanda gris | 8.02:1 sobre tarjeta (base: 6.25:1); armoniza con el violeta |
| Enlace | `#93c5fd` azul | `#f9a8d4` rosa claro | El azul frío rompe el atardecer; rosa claro 9.00:1 sobre tarjeta |
| Foco | `#93c5fd` azul | `#fbbf24` ámbar neón | Visible sobre violeta Y sobre rosa (9.77:1 tarjeta, 11.64:1 fondo); distinto del enlace |
| Primario | `#1d4ed8` azul sólido | Degradado `135deg #6d28d9 → #9d174d → #c2410c`, texto blanco | Atardecer violeta→rosa→naranja; CADA parada pasa AA con blanco (7.10 / 7.88 / 5.18:1). Fallback sólido `#9d174d` (7.88:1) |
| Primario hover | `#2563eb` | Degradado `#7c3aed → #a21caf → #c2410c` | Mismo criterio: peor parada 5.18:1 con blanco |
| Badge avisos | `#dc2626` rojo | `#be185d` rosa oscuro | Blanco 6.04:1 (mejor que 4.83:1); rosa = identidad Vice City |
| Tipografía display | system-ui | `'Barlow Condensed', 'Arial Narrow', …` | Carácter de póster condensado (skill); fallback sin webfont = $0 |
| Tipografía cuerpo | system-ui | `'Barlow', system-ui, …` | Misma familia que el display; legibilidad intacta |
| h1 / h2 | 20 / 16 px, lh 1.25 | 24 / 18 px, lh 1.1, mayúsculas | Display condensado pide más tamaño y menos interlínea |
| Radios | 8 / 12 px | 10 / 12→16 px (tarjeta) | Un punto más redondeado: póster, no dashboard frío |
| Sombras | Solo negras | + `--shadow-neon-*` (rosa/violeta/atardecer) | Brillo neón decorativo; nunca único indicador de estado |
| Pills nuevas | ok/bad/warn/neutral | + `.pill.sunset` / `.pill.violet` / `.pill.ember` | Estados temáticos (ej. LIVE, Vice City) con fg/bg verificados |
| Header | Fondo plano | `linear-gradient(180deg, #2a0a3d → #100a1a)` + franja atardecer 4 px (`#7c3aed → #ec4899 → #ff5e00`, decorativa) | Cielo de atardecer arriba; la franja no lleva texto encima |
| Logo AVC | Texto plano | Gradiente de texto `#f9a8d4 → #fdba74` (`background-clip: text`) | Solo con paradas claras (9.00 / 9.67:1 sobre tarjeta) |
| Semántico ok/bad/warn | Sin cambio de valor | Sin cambio de valor | Re-verificado sobre tarjeta nueva: 7.16 / 5.90 / 7.60:1 — pasa |
| Plataforma | Sin cambio de valor | Sin cambio de valor | Re-verificado: YT 4.99 / TikTok 12.07 / IG 6.05 / IG rosa 6.16 / FB 6.54:1 sobre tarjeta |
| Base 16 px, táctil 44 px, motion 150–250 ms, `prefers-reduced-motion` | — | **Sin cambio** | La temática no toca accesibilidad ni densidad |

### 8.2 Paleta final con contrastes verificados (calculados, fórmula WCAG)

| Uso | Token | Valor | Sobre | Ratio | Veredicto |
|---|---|---|---|---|---|
| Texto principal | `--color-text` | `#e8eaf0` | tarjeta `#261a38` | 13.56:1 | Pasa |
| Texto principal | `--color-text` | `#e8eaf0` | fondo `#100a1a` / input `#150d24` | 16.16:1 / 15.66:1 | Pasa |
| Texto secundario | `--color-text-dim` | `#b8b3c9` | tarjeta / fondo | 8.02:1 / 9.56:1 | Pasa |
| Enlace | `--color-link` | `#f9a8d4` | tarjeta / fondo | 9.00:1 / 10.72:1 | Pasa |
| Foco | `--color-focus` | `#fbbf24` | tarjeta / fondo | 9.77:1 / 11.64:1 | Pasa (≥ 3:1) |
| Botón primario (blanco) | parada 1 | `#6d28d9` | — | 7.10:1 | Pasa |
| Botón primario (blanco) | parada 2 | `#9d174d` | — | 7.88:1 | Pasa |
| Botón primario (blanco) | parada 3 | `#c2410c` | — | 5.18:1 | Pasa (peor parada) |
| Hover primario (blanco) | paradas | `#7c3aed` / `#a21caf` / `#c2410c` | — | 5.70 / 6.32 / 5.18:1 | Pasa |
| Gradiente texto logo | paradas | `#f9a8d4` / `#fdba74` | tarjeta | 9.00 / 9.67:1 | Pasa |
| Rosa neón (texto) | `--color-neon-pink` | `#f472b6` | tarjeta | 6.16:1 | Pasa |
| Violeta neón (texto) | `--color-neon-violet` | `#a78bfa` | tarjeta | 5.99:1 | Pasa |
| Naranja neón (texto) | `--color-neon-orange` | `#fb923c` | tarjeta | 7.21:1 | Pasa |
| Violeta claro (th) | `--color-neon-violet-text` | `#c4b5fd` | tarjeta | 8.84:1 | Pasa |
| OK / Error / Aviso (texto) | `--color-ok/bad/warn` | `#22c55e` / `#f87171` / `#f59e0b` | tarjeta | 7.16 / 5.90 / 7.60:1 | Pasa |
| Pill sunset | `#fbcfe8` | sobre `#500724` | — | 10.88:1 | Pasa |
| Pill violet | `#ede9fe` | sobre `#4c1d95` | — | 9.23:1 | Pasa |
| Pill ember | `#ffedd5` | sobre `#7c2d12` | — | 8.18:1 | Pasa |
| Badge (blanco) | `--color-badge-bg` | `#be185d` | — | 6.04:1 | Pasa |
| Peligro (blanco) | `--color-danger-bg` | `#7f1d1d` | — | 10.02:1 | Pasa |
| Borde | `--color-border` | `#7c6aa0` | tarjeta | 3.44:1 | Pasa (borde, no texto) |
| YouTube / TikTok / IG / IG rosa / FB | plataforma | `#ff4e45` / `#25f4ee` / `#f77737` / `#f472b6` / `#60a5fa` | tarjeta | 4.99 / 12.07 / 6.05 / 6.16 / 6.54:1 | Pasa |

### Valores rechazados como TEXTO (solo decorativos)

| Valor | Por qué |
|---|---|
| `#ff2e88` rosa neón, `#ff5e00` naranja neón, `#a855f7` violeta neón | Saturación pura: no llegan a 4.5:1; solo franja, brillos y bordes |
| `#ec4899` (5.51:1 fondo / 4.93:1 tarjeta) y `#db2777` (4.23 / 3.78:1) | El primero roza el límite y el segundo falla; rosa de texto es `#f472b6` / `#f9a8d4` |
| `#ea580c` naranja vivo (blanco 3.56:1) | Falla como fondo de botón; el naranja del degradado es `#c2410c` (5.18:1) |
| `#7c3aed` / `#9333ea` / `#8b5cf6` como texto | 3.05–4.10:1 sobre tarjeta — fallan; solo como parada de degradado con texto blanco |
| `#3b82f6` / `#1d4ed8` azul (base) | El azul frío queda fuera de la variante; se conserva solo en la base sin tema |

### Nota honesta sobre superficies (como §2)

Tarjeta `#261a38` vs fondo `#100a1a` = **1.19:1** (base: 1.22:1). Se probó
`#2e2145` (1.31:1) pero YouTube `#ff4e45` cae a 4.53:1, demasiado al límite.
Decisión: se mantiene `#261a38` y la separación la dan el borde `#7c6aa0`
(3.44:1, mejor que el 2.10:1 de la base) + `--shadow-card` con anillo violeta.
WCAG no exige ratio entre superficies; todos los pares de texto pasan.

### 8.3 Tabla de componentes — estilo GTA 6 exacto

| Componente | Estilo GTA 6 exacto |
|---|---|
| **Header** | Fondo `var(--gradient-header)` (`#2a0a3d → #100a1a`), sticky, `z-index: 10`. Título/logo AVC en `--font-display` 700, mayúsculas, gradiente de texto `--gradient-sunset-text`. Franja atardecer 4 px bajo el header (`--gradient-sunset-strip`, decorativa). Semáforo del Worker a la derecha (ver abajo). `<meta name="theme-color" content="#100a1a">`. |
| **Nav de pestañas** | Fondo fondo; pestañas `min-height: 44px`, texto `--color-text-dim` (`#b8b3c9`), `font-family: var(--font-display)`, 600, mayúsculas, `letter-spacing: .02em`. **Activa:** fondo `--gradient-sunset`, texto blanco 700, `aria-selected="true"`, barra inferior 3 px `--color-neon-amber`. Nunca solo color. Incluye futuras pestañas (Cuentas sociales, APIs, Influencers, Fuentes/VODs, Editor de clip, Analíticas, Canales/Salud, Tracking, Usuarios del panel) con el mismo patrón. |
| **Tarjeta** | Fondo `--color-surface #261a38`, borde 1 px `--color-border #7c6aa0`, radio `--radius-card: 16px`, padding `--space-4`, `box-shadow: var(--shadow-card)`. Hover: `var(--shadow-elevated)` (brillo rosa suave). Título de tarjeta en display condensado 18 px. |
| **Botón primario** | Fondo `--gradient-sunset`, texto blanco 600, `min-height: 44px`, `padding: 10px 16px`, radio 10 px, `box-shadow: var(--shadow-neon-sunset)`. Hover: `--gradient-sunset-hover`. Disabled: opacidad .55 + texto de carga. |
| **Botón secundario** | Fondo transparente, borde 1 px `--color-border`, texto `--color-text`. Hover: borde `--color-neon-pink-text` + brillo 1.12. |
| **Botón peligro** | Fondo `--color-danger-bg #7f1d1d`, texto blanco (10.02:1), radio 10 px. |
| **Pill de estado** | `padding: 2px 9px`, radio 999 px, `font-size: 12px`, `font-weight: 600`, siempre con texto. `.pill.ok/bad/warn` con los bg/fg base; nuevas `.pill.sunset` (`#fbcfe8`/`#500724`, 10.88:1), `.pill.violet` (`#ede9fe`/`#4c1d95`, 9.23:1), `.pill.ember` (`#ffedd5`/`#7c2d12`, 8.18:1), `.pill.neutral` (fondo `--color-surface-input`, texto `--color-text-dim`). |
| **Input / select / textarea** | Fondo `--color-surface-input #150d24`, borde `--color-border`, texto `--color-text`, `font-size: 16px`, `min-height: 44px`, radio 10 px. Focus: borde `--color-neon-pink-text` + outline 3 px `--color-focus #fbbf24`, offset 2 px. Placeholder en `--color-text-dim`. |
| **Tabla (escritorio)** | `th`: display condensado, 12 px, 600, mayúsculas, `letter-spacing: .04em`, color `--color-neon-violet-text #c4b5fd` (8.84:1). Filas con borde inferior `rgba(124,106,160,.4)`; hover fila `rgba(236,72,153,.08)`. Numéricos a la derecha, `tabular-nums`. En ≤640 px: tarjeta de clip 9:16 (ver §4.4 base). |
| **Barra de prioridad** | Fondo `--color-surface-input`, altura 6 px, radio 999 px. Relleno por tramos: <50 `--color-bad`; 50–89 degradado `#fbbf24 → #fb923c`; ≥90 degradado `#f472b6 → #a78bfa` + `var(--shadow-neon-pink)`. Número en `--color-text`, `tabular-nums`; desglose viralidad/SEO/chat al tocar. |
| **Semáforo del Worker** | Punto 10 px + texto de estado siempre (nunca solo color). Verde `--color-ok` + glow verde, ámbar `--color-warn` + glow ámbar, rojo `--color-bad` + glow rojo (`box-shadow: 0 0 10px <color> .8`). Botón "Reintentar" secundario + hora del último intento en `--color-text-dim`. |
| **Login** | `#loginBox` centrada (`margin: 32px auto`, 24 px en móvil), borde degradado atardecer 2 px (fondo doble: superficie + `--gradient-sunset-strip`), radio 16 px, `var(--shadow-elevated)`. Logo AVC grande con gradiente de texto. Botón "Entrar" primario atardecer, ancho completo. Error bajo el campo con `role="alert"`. |
| **Badge (Avisos)** | Fondo `--color-badge-bg #be185d`, texto blanco (6.04:1), `min-width: 20px`, `height: 20px`, radio 999 px, tope "9+", oculto en 0. |
| **Toast / `.msg`** | Igual que base (§4): éxito `--color-ok-bg`/`--color-ok-fg`, error `--color-bad-bg`/`--color-bad-fg`, info `--color-info-bg`/blanco, borde izquierdo 3 px semántico. |
| **Tarjeta de clip 9:16** | Miniatura con placeholder degradado `linear-gradient(180deg, #3b0764 → #500724 → #150d24)` (sin imagen), título en `--color-text`, anillo de score con color por tramo (§4.2) + brillo neón en ≥90, matriz de plataformas debajo. |

### 8.4 Checklist del implementador (variante)

- [ ] `<html data-theme="gta6">` + `tokens-gta6.css` cargado después de `tokens.css`; sin tema, el panel sigue siendo la base §§1–7.
- [ ] Ningún hex fuera de `tokens-gta6.css` / `tokens-gta6.json`; neones crudos (`#ff2e88`, `#ff5e00`, `#ec4899`) solo en degradados/bordes/brillos, nunca como `color` de texto.
- [ ] Contraste verificado en los 5 pares críticos: primario peor parada 5.18:1, enlace 9.00:1, foco 9.77:1, error 5.90:1, badge 6.04:1.
- [ ] Gradiente de texto solo en logo/títulos grandes y solo con paradas claras (`#f9a8d4`/`#fdba74`); fallback `color: #f9a8d4` si `background-clip: text` no aplica.
- [ ] `theme-color` actualizado a `#100a1a` cuando el tema está activo.
- [ ] Táctil 44 px, base 16 px, `prefers-reduced-motion` respetado (sin cambio).
