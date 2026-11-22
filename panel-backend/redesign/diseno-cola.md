# Diseño — Pestaña Cola y detalle de clip (panel AVC)

- **Fecha:** 2026-11-17
- **Rol:** Rol 7 — Diseñador de Cola. **Solo especificación; no se editó el front.**
- **Alcance:** únicamente la pestaña Cola (`#tab-cola`) y el detalle de clip
  (`#clipDetailCard`). Nada de Worker, Cuentas, Avisos, Ajustes, login ni header.
- **Fuentes leídas:**
  `design-system/avc-panel/OVERRIDES.md` (autoritativo — manda sobre
  `MASTER.md`, `tokens.json` y `tokens.css`),
  `design-system/avc-panel/MASTER.md`,
  `design-system/avc-panel/tokens.css`,
  `redesign/auditoria-ux.md` (32 hallazgos), `redesign/auditoria-visual.md`
  (24 defectos), `redesign/ideas.md`,
  `frontend/dist/index.html` y `frontend/dist/app.js` (estado actual).
- **Regla de oro de este documento:** todo lo que se muestra sale de los
  datos que la API ya devuelve hoy (ver §2). Si un dato no existe, se
  **oculta sin romper**; no se inventa endpoint, campo, valor ni cálculo.

## 0. Qué cambia y qué no cambia

| Cambia (diseño) | No cambia (prohibido tocar) |
|---|---|
| Tabla de 5 columnas → tarjetas 9:16 en móvil (≤ 640 px) | Orden de la lista: el que devuelve `GET /api/clips` (hoy, por fecha). La barra de prioridad **no reordena** nada; solo visualiza. |
| Estado como texto plano → pills semánticas con texto | Fórmulas de prioridad / viralidad / SEO / chat. La barra es **solo visual**. |
| `publish_states` como texto `k: v` → matriz de chips por plataforma | Lógica de publicación, frenos y límites del Worker. |
| Tracking como `<ul>` de texto → timeline vertical | Los 11 pasos de la idea #5 son una referencia conceptual: se renderizan **exactamente los eventos que devuelve la API**, ni más ni menos. |
| Subir / publicar sin feedback → loading, progreso y confirmación | Endpoints, ids de elementos y contratos de la API (ver §2). |
| Cola vacía como frase gris → empty state con acción | `#msg` sigue siendo el canal de feedback (la idea #13, toasts, es otra oleada; no es este diseño). |

**Numeración de ideas:** en `ideas.md` §20 las ideas son #1 tarjeta 9:16,
#2 barra de prioridad, #3 matriz de estados, #4 chips de freno. La tabla de
priorización final de `ideas.md` las ordena distinto por valor (matriz
primero); esta spec cubre las cuatro, en el orden del encargo.

**Ids que se conservan, sin renombrar ni duplicar:**
`clipList`, `clipDetailCard`, `clipTitle`, `clipDetail`, `clipTracking`,
`videoFile`, `btnUpload`, `btnPublish`. También se conservan `tab-cola`
y `#msg`.

---

## 1. Tokens (única fuente: OVERRIDES.md / tokens.css)

`MASTER.md` generado está **descartado** para colores y tipografía: fondo
claro `#FAFAF9`, dorado `#A16207`, serif Cormorant / Montserrat **no se usan**.
El implementador usa únicamente estos tokens. **Prohibido el hex crudo en
componentes** (VIS-8): todo color es un `var(--color-*)`.

### 1.1 Color

| Uso | Token | Valor |
|---|---|---|
| Fondo página | `--color-background` | `#0b0d12` |
| Tarjeta | `--color-surface` | `#1e222c` |
| Fondo de input / barra / chip | `--color-surface-input` | `#0f1115` |
| Borde 1 px | `--color-border` | `#475569` (2.10:1 sobre tarjeta) |
| Texto principal | `--color-text` | `#e8eaf0` (13.22:1 sobre tarjeta) |
| Texto secundario | `--color-text-dim` | `#9aa3b2` (6.25:1 sobre tarjeta) |
| Enlace / foco | `--color-link` / `--color-focus` | `#93c5fd` (8.82:1 sobre tarjeta) |
| Primario (botón, pestaña activa) | `--color-primary` | `#1d4ed8` (blanco 6.70:1) |
| Hover primario, solo con texto blanco | `--color-primary-hover` | `#2563eb` (blanco 5.17:1) |
| Texto sobre primario | `--color-on-primary` | `#ffffff` |
| OK texto / fondo / texto sobre fondo | `--color-ok` / `--color-ok-bg` / `--color-ok-fg` | `#22c55e` / `#14532d` / `#bbf7d0` (pill 7.52:1) |
| Error texto / fondo / texto sobre fondo | `--color-bad` / `--color-bad-bg` / `--color-bad-fg` | `#f87171` / `#7f1d1d` / `#fecaca` (pill 6.93:1) |
| Aviso texto / fondo / texto sobre fondo | `--color-warn` / `--color-warn-bg` / `--color-warn-fg` | `#f59e0b` / `#451a03` / `#fde68a` (pill 12.03:1) |
| Peligro (fondo botón) / info (fondo mensaje) / badge | `--color-danger-bg` / `--color-info-bg` / `--color-badge-bg` | `#7f1d1d` / `#1e3a5f` / `#dc2626` |

**Rechazados, no usar:** `#3b82f6` (blanco 3.68:1, falla AA),
`#2563eb` con texto `#e8eaf0` (4.30:1), `#ef4444` como texto (4.23:1),
`#0000EE` como enlace (1.79:1). Ver OVERRIDES §2.

### 1.2 Colores de plataforma (solo variantes claras = texto seguro)

| Plataforma | Token | Valor | Contraste sobre tarjeta |
|---|---|---|---|
| YouTube | `--color-platform-youtube` | `#ff4e45` | 4.87:1 |
| TikTok | `--color-platform-tiktok` | `#25f4ee` | 11.55:1 |
| Instagram | `--color-platform-instagram` | `#f77737` | 5.79:1 |
| Instagram (rosa, segundo acento) | `--color-platform-instagram-pink` | `#f472b6` | 6.01:1 |
| Facebook | `--color-platform-facebook` | `#60a5fa` | 6.26:1 |

Reglas: los hex de marca crudos (`#ff0000`, `#e1306c`, `#fe2c55`,
`#1877f2`) son **solo decorativos** (punto, borde, degradado), nunca texto.
El nombre de la plataforma se escribe en `--color-text`; el token de
plataforma se usa en el **punto de 8 px** (`.dot`, CSS, no glifo) y, si se
colorea el nombre, solo con el token claro de la tabla. Nunca solo color:
cada chip lleva nombre + estado con texto (ver §5).

### 1.3 Tipografía, espaciado, radios, táctil, motion

- Fuentes: `--font-body` / `--font-heading`: pila del sistema
  (`system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif`). Sin webfonts ($0).
  Mono: `--font-mono` (`ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`)
  solo para `r2_key` / URLs / JSON.
- Escala: h1 20 px / h2 16 px / **h3 14 px** (VIS-4: hoy el `h3`
  "Seguimiento" sin estilo es más grande que los `h2`; fijar
  `.card h3 { font-size: 14px; font-weight: 600; }`) / cuerpo 16 px /
  small 13 px / caption 12 px. `line-height` cuerpo 1.5, encabezados 1.25,
  celdas 1.45.
- Espaciado: `--space-1..6` = 4 / 8 / 12 / 16 / 24 / 32 px. Solo estos valores.
- Radios: control 8 px / tarjeta 12 px / pill 999 px.
- Táctil: `--size-touch-target: 44px`. `min-height: 44px` en **todos** los
  botones e inputs de esta pestaña (`btnUpload`, `btnPublish`, "Ver detalle",
  "Copiar", "Reintentar", "Ir al Worker", `videoFile`). Gap mínimo 8 px
  entre objetivos táctiles.
- Inputs a 16 px (`--font-size-input`): por debajo, iOS hace auto-zoom (UX-7).
- Motion: 150–250 ms, `ease-out`. `button:hover { filter: brightness(1.12); }`,
  `button:active { transform: scale(.98); }`, `button:disabled { opacity: .55;
  cursor: not-allowed; }`, `touch-action: manipulation`.
  `prefers-reduced-motion: reduce` anula transiciones/animaciones y el
  `scrollIntoView` pasa a `behavior: "auto"` (UX-27).
- Foco: `:focus-visible { outline: 3px solid var(--color-focus);
  outline-offset: 2px; }` en `button, input, a` (UX-1, crítico).
- `color-scheme: dark` en `:root` + `<meta name="theme-color"
  content="#0b0d12">`. Safe-area con `env(safe-area-inset-*)` en `main`.
- Números (puntos, %, MB): `font-variant-numeric: tabular-nums`.
- Iconos: solo SVG inline (`aria-hidden="true"`). Prohibidos emojis y
  glifos `▶ ■ ●` como iconos; el punto de estado es un `.dot` CSS de 8 px.

---

## 2. Datos reales que muestra `app.js` hoy (inventario, sin inventar nada)

Fuente: `loadClips`, `selectClip`, `publishSelected`, `uploadSelected`.
Todo lo que no está en esta tabla **no existe en la API hoy**.

### 2.1 `GET /api/clips` → `clips[]` (lista)

| Campo | Tipo hoy | Dónde se muestra | Si falta / vacío |
|---|---|---|---|
| `id` | número | Clave de selección (`selectClip(id)`); no se muestra como texto salvo en el título de reserva | — (sin id no hay tarjeta) |
| `title` | texto | Título de tarjeta y de detalle (`clipTitle`) | Reserva: `seo_title`, y si tampoco hay: `"Clip " + id` |
| `seo_title` | texto | Subtítulo bajo el título en la tabla; título de reserva | Se omite la línea (no se muestra vacía) |
| `seo_description` | texto | Detalle, bajo la meta (solo si existe) | Se omite |
| `seo_tags` | texto | Detalle, línea "Tags:" (solo si existe) | Se omite |
| `status` | texto crudo de BD | Pill de estado de la tarjeta y del detalle | Se traduce según §4; valor desconocido → pill neutra con el valor tal cual (escapado) |
| `score` | número o `null` | Anillo de score (tarjeta) y "Puntos: …" (detalle); base de la barra de prioridad (§3) | `null` / ausente → `"—"`, anillo neutro, barra neutra (ver §3.3) |
| `platforms` | array o string JSON (`parsePlatforms`) | Matriz de plataformas (§5) | Array vacío → matriz con el texto "Sin plataformas asignadas" (ver §5.4) |
| `publish_states` | string JSON `{"youtube": {"state": "…"} \| "…"}` | Matriz de plataformas (§5): `v.state || v` | `{}` / inválido → todas las plataformas en "Pendiente" (ver §5.3) |
| `created_at` | ms | "Creado" (tabla escritorio) y metadato de la tarjeta | `fmtDate` actual (`es-CL`, `America/Santiago`); si falta → `"—"` |

### 2.2 `GET /api/clips/:id` → `clip` (detalle)

Los mismos campos de §2.1 para ese clip. Nada más.

### 2.3 `GET /api/clips/:id/tracking` → `events[]`

| Campo | Dónde se muestra |
|---|---|
| `at` (ms) | Hora del paso, `fmtDate` (`es-CL`, `America/Santiago`) |
| `step` | Título del paso (tal cual, escapado; no se traduce un valor desconocido) |
| `actor` | Junto al paso: "paso · actor — mensaje" |
| `message` | Texto del paso |
| `failed` (bool) | Paso en rojo + pill "Fallo" (ver §7) |

### 2.4 Respuestas de acciones

| Acción | Devuelve | Uso en diseño |
|---|---|---|
| `POST /api/clips/:id/publish` | `jobs[]` (`{platform, state}`) y `blocked[]` (strings) | Mensaje de resultado + chips de freno **transitorios** (§6) + refresco del detalle (`selectClip` de nuevo) |
| `PUT /api/clips/:id/video?filename=…` | `r2_key`, `public_url` | Confirmación de subida (§8) |

### 2.5 Datos que NO existen hoy — mostrar solo si el campo existe

> **Regla general:** si el campo no viene en la respuesta, la pieza se
> **oculta por completo** (sin hueco, sin `"—"`, sin cero inventado, sin
> placeholder que finja un dato). No se crea endpoint ni se calcula nada
> en el front para suplirlo.

| Dato | Pieza afectada | Condición de aparición |
|---|---|---|
| Desglose de prioridad por componente (`viral_score` / `seo_score` / `chat_norm` o nombres equivalentes, si la API los expone algún día) | Desglose de la barra (§3.2) | Solo si **al menos un** componente viene como número. Si no viene ninguno, no hay desglose ni botón para abrirlo. |
| `priority` (número propio, distinto de `score`) | Etiqueta y valor principal de la barra (§3.1) | Solo si existe. Hoy **no** existe: la barra usa `score` y se etiqueta "Puntos", no "Prioridad". |
| Miniatura (`thumbnail_url`, `poster_url`, frame del MP4 o similar) | Miniatura 9:16 (§4) | Solo si existe. Si no, placeholder con degradado (ver §4.2). Nunca se genera una miniatura en el front ni se usa el MP4 como `<video>` para capturarla. |
| Duración del clip (`duration_s` o similar) | Metadato de tarjeta/detalle; freno "clip de 30 s" | Solo si existe. La regla "ningún clip de 30 s" **no** se puede comprobar ni mostrar como freno sin este campo. |
| Estado "MP4 listo" persistente (`has_video`, `video_url` o similar) | Pill "MP4 listo" en tarjeta/detalle | Solo si existe. Hoy solo se sabe que el MP4 se subió **en esa sesión**, por la respuesta del PUT (§8.4); al recargar, sin campo, no se afirma nada. |
| Motivo de fallo por plataforma | Matriz (§5) y chips de freno (§6) | Solo si viene en `publish_states[*]` o en `blocked[]`. No se inventa el motivo. |
| Segmento LIVE / VOD, filtros, búsqueda | No es este diseño | Idea #10, segunda oleada. Fuera de alcance. |

---

## 3. Barra de prioridad con desglose (idea #2)

**Solo visual. No se recalcula ni se toca ninguna fórmula.**
La fórmula de prioridad (viralidad / SEO / chat) vive en el cerebro; el
front solo la muestra cuando la API la expone (hoy no la expone, ver §2.5).

### 3.1 Barra principal

- **Ubicación:** en cada tarjeta de clip (móvil y escritorio), bajo el
  título y la matriz, a ancho completo; también en el detalle, bajo el
  anillo de score.
- **Valor mostrado hoy:** `c.score` (el único número que devuelve
  `/api/clips`). Etiqueta: **"Puntos"**. No se etiqueta "Prioridad" mientras
  no exista un campo `priority` propio.
  - Si algún día la API devuelve `priority`, la etiqueta pasa a
    "Prioridad" y el valor es ese campo; `score` sigue mostrándose como
    anillo (ver §4.3). No se suman, promedian ni derivan el uno del otro.
- **Escala y color por tramos** (OVERRIDES §4.2, mismos umbrales que el
  anillo): fondo `--color-surface-input`, altura 6 px, radio 999 px.
  Relleno: `< 50` → `--color-bad` (rojo); `50–89` → `--color-warn`
  (ámbar); `≥ 90` → `--color-ok` (verde). Se asume escala 0–100, como
  mandan esos umbrales.
  - A la derecha de la barra, el número en `--color-text`,
    `font-variant-numeric: tabular-nums`, 13 px, semibold. El color del
    tramo nunca va solo: el número siempre está escrito.
- **Texto junto a la barra (móvil):** `"Puntos 87"` a la izquierda del
  número no; formato: etiqueta caption 12 px en `--color-text-dim`
  ("Puntos") + valor 13 px en `--color-text`. Ejemplo: `Puntos · 87`.

### 3.2 Desglose viralidad / SEO / chat (condicional)

- **Solo si existe el dato** (§2.5): si la API devuelve al menos un
  componente numérico, la barra es un `<button>` (no solo un `div`
  clicable) con `aria-expanded="false/true"` y texto visible
  "Ver desglose" / "Ocultar desglose". Al tocarla se despliegan tres
  filas: **Viralidad**, **SEO**, **Chat**, cada una con su valor numérico
  y una mini-barra con el mismo código de color por tramos (§3.1).
- **Si no existe el dato (caso actual):** la barra **no** es un botón, no
  hay "Ver desglose", no hay filas con ceros ni con valores estimados.
  La barra se muestra sola, sin hueco reservado para un desglose.
- **Nombres de los componentes:** se usan los nombres que devuelva la
  API; si vienen en inglés (`viral_score`, `seo_score`, `chat_norm`) se
  traducen en la etiqueta visible (Viralidad / SEO / Chat) pero el valor
  es el del campo, sin transformar.
- **No se muestra la fórmula** en el panel. Tampoco se explica el peso
  de cada componente: solo sus valores.

### 3.3 Casos borde

| Caso | Render |
|---|---|
| `score` es `null` / ausente | Barra vacía en `--color-surface-input`, sin relleno; valor `"—"`; sin desglose |
| `score` fuera de 0–100 | Se muestra el número tal cual; relleno al 100 % si > 100, al 0 % si < 0; no se recorta el número |
| Solo algunos componentes del desglose | Se muestran solo los que existen; los ausentes no aparecen (no se pone `"—"` en una fila de desglose) |

---

## 4. Tarjeta de clip 9:16 con miniatura y anillo de score (idea #1)

### 4.1 Cuándo se usa cada presentación

| Ancho | Presentación de `#clipList` |
|---|---|
| **≤ 640 px (móvil)** | **Tarjetas apiladas.** La tabla no se muestra. Sin scroll horizontal a 360 px (UX-5, crítico). |
| **> 640 px (escritorio)** | **Tabla** (ver §4.5), envuelta en `overflow-x: auto`. |

- Implementación (recomendada, sin tocar lógica): `#clipList` contiene
  dos ramas alimentadas por el mismo array de clips y el mismo handler
  `selectClip(id)` — `.clip-table-wrap` (tabla) y `.clip-cards`
  (tarjetas) — y CSS muestra una u otra según el breakpoint
  (`display: none` en la rama oculta). No se duplica estado: ambas se
  regeneran a la vez en `loadClips()`.
- Verificar a 360 px, 375 px, 640 px y 641 px: a 640 la tarjeta; a 641 la
  tabla. Sin desbordamiento en ningún ancho.

### 4.2 Tarjeta móvil — anatomía (de arriba a abajo, en orden de lectura)

```
┌─────────────────────────────────────────────┐
│ ┌────────┐  Título del clip (2 líneas máx.) │
│ │        │  seo_title · 13 px dim (si hay)  │
│ │ 9:16   │  [pill estado]  Creado 17 nov…   │
│ │ 72×128 │  Matriz de plataformas (2×2)     │
│ │        │  Barra de prioridad + Puntos 87  │
│ └────────┘                                  │
│ [Ver detalle]  (botón, ancho completo)      │
└─────────────────────────────────────────────┘
```

1. **Contenedor `.clip-card`:** fondo `--color-surface`, borde 1 px
   `--color-border`, radio `--radius-card` (12 px), padding `--space-4`
   (16 px), `margin-bottom: var(--space-4)`, sombra `--shadow-card`.
   `display: flex; gap: 12px;` (miniatura a la izquierda, contenido a la
   derecha, `flex: 1`, `min-width: 0` para que el texto pueda truncarse).
   La barra de prioridad va **a ancho completo bajo** esa fila (no dentro
   de la columna de texto).
2. **Miniatura 9:16 `.clip-thumb`:** `width: 72px; height: 128px`
   (`aspect-ratio: 9 / 16`), radio 8 px, `object-fit: cover`,
   `flex-shrink: 0`. Es el formato del Short/Reel real.
   - **Si existe miniatura** (§2.5): `<img>` con `alt=""` decorativa
     (el título ya está en texto al lado; no se repite en el `alt`).
   - **Si no existe (caso actual):** placeholder con degradado
     `--color-surface` → `--color-surface-input`, borde
     `--color-border`, sin imagen rota ni icono inventado. Sobre el
     placeholder, centrado, el texto caption 12 px en
     `--color-text-dim`: `"Sin miniatura"`. Nunca un emoji ni un glifo.
   - **Anillo de score:** superpuesto en la esquina inferior derecha de
     la miniatura (o, si el implementador lo prefiere, en la esquina
     superior derecha del contenido): círculo 40 px de diámetro, fondo
     `--color-surface-input` con opacidad, trazo 4 px en el color del
     tramo (§3.1), número centrado 12 px bold en `--color-text`,
     `tabular-nums`. El número siempre escrito; el color nunca va solo.
     Si `score` es `null`: anillo gris neutro (`--color-border`) con
     `"—"`.
3. **Título:** 16 px, `font-weight: 600`, `--color-text`, `line-height:
   1.25`, máximo 2 líneas con elipsis. Es el texto del clip, no un enlace
   azul (VIS-3: el azul de navegador `#0000EE` es 1.79:1, ilegible).
4. **Subtítulo SEO:** `seo_title` si existe, 13 px, `--color-text-dim`,
   1 línea con elipsis. Si no existe, no se reserva la línea.
5. **Pill de estado + fecha:** pill semántica (§4.4) + a su derecha
   `"Creado " + fmtDate(c.created_at)` en 12 px `--color-text-dim`.
   En una sola fila con `flex-wrap: wrap`.
6. **Matriz de plataformas:** ver §5; en la tarjeta, rejilla de 2
   columnas.
7. **Barra de prioridad:** ver §3; a ancho completo bajo la fila
   miniatura+contenido.
8. **Acción "Ver detalle":** botón ghost a ancho completo, `min-height:
   44px`, texto **"Ver detalle"**. Sustituye al `<a href="#">` actual
   (UX-26: el `href="#"` salta arriba si falla el JS y no anuncia que
   abre un detalle). Toda la tarjeta **no** es clicable: solo el botón
   (evita acciones anidadas y keeps el foco claro). Al pulsarlo,
   `selectClip(id)` como hoy.
   - Estado seleccionado: la tarjeta del clip abierto lleva borde
     `--color-primary` de 2 px + pill "Abierto" (texto, no solo color),
     para que se vea cuál es el detalle visible debajo.

### 4.3 Anillo de score — misma regla en tarjeta y detalle

Color por tramo (§3.1), número siempre visible, `"—"` si no hay `score`.
En el detalle el anillo es mayor (56 px, número 16 px) y va junto al
título, no sobre la miniatura grande (ver §9).

### 4.4 Pill de estado del clip (texto crudo → español)

El `status` llega como string crudo de BD. Traducción antes de renderizar
(VIS-10: nunca texto plano en inglés). Catálogo:

| Valor crudo (ejemplos) | Etiqueta visible | Clase |
|---|---|---|
| `pending`, `draft`, `new` | Pendiente | `.pill.neutral` |
| `queued`, `approved`, `scheduled` | En cola | `.pill.warn` |
| `publishing`, `processing` | Publicando | `.pill.warn` |
| `published` | Publicado | `.pill.ok` |
| `failed`, `error` | Fallido | `.pill.bad` |
| `blocked` | Frenado | `.pill.warn` |
| Cualquier otro valor | El valor tal cual, escapado | `.pill.neutral` |

- `.pill`: `padding: 2px 9px`, radio 999 px, 12 px, `font-weight: 600`.
  `.pill.ok` fondo `--color-ok-bg` / texto `--color-ok-fg`;
  `.pill.bad` fondo `--color-bad-bg` / texto `--color-bad-fg`;
  `.pill.warn` fondo `--color-warn-bg` / texto `--color-warn-fg`;
  `.pill.neutral` fondo `--color-surface-input` / texto
  `--color-text-dim`.
- Si el catálogo real del Worker usa otros valores, el implementador
  añade la fila a esta tabla (no se inventa una etiqueta distinta en
  cada pantalla: la misma traducción vale para tarjeta, tabla y detalle).

### 4.5 Tabla de escritorio (> 640 px)

Se mantiene la tabla actual de 5 columnas, con estas correcciones
(auditorías UX-5, VIS-2, VIS-21):

- Envoltura `.clip-table-wrap { overflow-x: auto;
  -webkit-overflow-scrolling: touch; }`.
- `<table>` con `<thead>` / `<tbody>`, `<th scope="col">` y `<caption>`
  oculta visualmente ("Cola de clips") para lector de pantalla.
- Columnas: **Clip | Estado | Puntos | Plataformas | Creado**.
  `th`: 12 px, `font-weight: 600`, mayúsculas, `letter-spacing: .04em`,
  `--color-text-dim`. `td, th { line-height: 1.45; padding: 8px 6px; }`.
- **Puntos** (columna numérica) alineada a la **derecha**,
  `font-variant-numeric: tabular-nums`.
- Columna Clip: título como botón "Ver detalle" (no `<a href="#">`),
  `seo_title` debajo en 13 px dim, y la matriz de plataformas en su
  forma compacta (§5.5) debajo del subtítulo.
- Columna Estado: pill (§4.4), no texto plano.
- Columna Plataformas: matriz compacta, no texto corrido.
- `th { position: sticky; top: 0; }` con fondo `--color-surface` (la
  navegación ya es sticky; verificar que no tape el foco al abrir el
  detalle).
- Fila con hover sutil (`tr:hover td` con fondo `--color-surface-input`),
  sin cambio de layout.

---

## 5. Matriz de estados por plataforma (idea #3)

### 5.1 Forma

Un chip por plataforma: punto de marca + nombre en español + pill de
estado. Fondo `--color-surface-input`, borde 1 px `--color-border`,
radio 999 px, padding `4px 10px 4px 8px`, gap 6 px entre punto, nombre y
pill. El estado **nunca es solo color**: siempre lleva texto.

### 5.2 Orden y plataformas

Orden fijo, siempre el mismo: **YouTube, TikTok, Instagram, Facebook**.
Se muestran las plataformas de `c.platforms` (tras `parsePlatforms`).
Si una plataforma aparece en `publish_states` pero no en `platforms`,
se añade al final de la matriz (no se pierde información). Una
plataforma desconocida (nombre no previsto) se muestra con punto gris
(`--color-text-dim`) y su nombre tal cual, sin inventarle color de marca.

### 5.3 Traducción de estados por plataforma

`publish_states` es `{"youtube": {"state": "…"} }` o `{"youtube": "…"}`;
el estado efectivo es `v.state || v`. Catálogo (los 5 estados del
encargo):

| Valor crudo | Etiqueta visible | Pill |
|---|---|---|
| `pending`, `draft`, `not_started` | Pendiente | `.pill.neutral` |
| `queued`, `scheduled` | En cola | `.pill.warn` |
| `publishing`, `processing`, `uploading` | Publicando | `.pill.warn` |
| `published`, `done`, `success` | Publicado | `.pill.ok` |
| `failed`, `error` | Fallido | `.pill.bad` |
| `blocked`, `held`, `skipped` | Frenado | `.pill.warn` |
| Cualquier otro valor | El valor tal cual, escapado | `.pill.neutral` |

- **Sin `publish_states` o vacío:** todas las plataformas de la matriz
  quedan en **"Pendiente"** (`.pill.neutral`). No se deja el chip sin
  estado.
- **Texto visible por chip (ejemplo):** `YouTube · Publicado`,
  `TikTok · En cola`, `Instagram · Frenado`, `Facebook · Pendiente`.
  El punto de color va antes del nombre; la pill, después.
- **"Sin verificar" no existe aquí:** ese estado era de Cuentas y la
  auditoría v15 lo eliminó; en la Cola no se renderiza (UX-21 / VIS-11).

### 5.4 Sin plataformas

Si `platforms` está vacío y `publish_states` también: en lugar de la
matriz, texto 13 px en `--color-text-dim`: **"Sin plataformas
asignadas"**. No se dibujan cuatro chips vacíos.

### 5.5 Variantes por contexto

| Contexto | Forma |
|---|---|
| Tarjeta móvil (§4.2) | Rejilla 2 columnas, chips a ancho de columna, pill a la derecha del nombre; si un chip no cabe, pasa a la fila siguiente (wrap, nunca recorte) |
| Tabla escritorio (§4.5) | Misma matriz, en una sola columna apilada (chips en columna, no en fila corrida) para que la celda no se ensanche |
| Detalle del clip (§9) | Rejilla 2 columnas en móvil / 4 en una fila en escritorio; mismo chip, con más aire (`padding: 6px 12px`) |

---

## 6. Chips de freno (idea #4)

**Qué es un freno:** un motivo por el que el clip no publica (o no
publicó en una plataforma). Se muestra como chip con su motivo en
lenguaje claro y, cuando aplica, el botón que lo resuelve al lado.
Un freno nunca se comunica solo con color.

### 6.1 De dónde sale el motivo (únicas fuentes)

1. **`blocked[]` de la respuesta de publicar** (`POST
   /api/clips/:id/publish`): cada string es un freno. Es la única fuente
   estructurada que existe hoy.
2. **Motivo por plataforma en `publish_states[*]`**, solo si el Worker lo
   incluye (hoy no lo incluye: solo `state`).

**No se infiere ningún freno.** En particular:

- No se deduce "SEO bajo" a partir de `score`: `score` no es el SEO, y
  no hay campo `seo_score` hoy (§2.5).
- No se deduce "Sin MP4" a partir de nada: no hay campo `has_video` hoy.
- No se deduce "clip de 30 s" sin campo de duración.
- No se deduce "límite diario alcanzado" sin campo de límites.

### 6.2 Render

- **Ubicación:** en el detalle del clip, entre la matriz (§5) y el
  seguimiento (§7), en su propio bloque con `h3` **"Frenos"** (14 px,
  ver §1.3). En la tarjeta de la lista **no** se muestran frenos (no hay
  dato persistente por clip para hacerlo sin mentir).
- **Chip `.brake-chip`:** fondo `--color-warn-bg` / texto
  `--color-warn-fg` (12.03:1) por defecto. Fondo `--color-bad-bg` /
  texto `--color-bad-fg` (6.93:1) solo cuando el freno acompaña a un
  fallo de publicación (`failed` en tracking o `state: failed` en la
  plataforma); como hoy no hay severidad en `blocked[]`, el valor por
  defecto es **warn**. No se clasifica por palabras clave dentro del
  texto del freno.
  - Radio 8 px (no 999: un freno es una frase, no una etiqueta corta),
    padding `8px 12px`, 13 px, `line-height: 1.5`, `overflow-wrap:
    anywhere`. Un chip por freno, apilados en columna.
  - Texto: el string de `blocked[]` tal cual (escapado). Ejemplos de
    copia **solo si el backend los devuelve así** (no son una lista
    fija que el front invente): "SEO 87 — faltan 3 puntos",
    "Sin MP4 subido", "Límite de TikTok de hoy alcanzado",
    "Clip de 30 s — ajusta la duración".
- **Botón que lo resuelve:** solo cuando la acción existe en este
  detalle:
  - Freno "Sin MP4 subido" → botón ghost **"Subir MP4"** que mueve el
    foco a `#videoFile` (y hace scroll al bloque de subida).
  - Cualquier otro freno → **sin botón**. No se pone un botón que no
    lleva a ninguna parte.
- **Persistencia:** los chips de `blocked[]` son **transitorios**: se
  muestran tras pulsar "Publicar ahora" y se mantienen visibles mientras
  el detalle de ese clip siga abierto. Al cerrar el detalle o recargar,
  desaparecen (no hay campo persistente que los respalde). No se guardan
  en `localStorage` ni se inventa un historial de frenos.
- **Si no hay frenos:** el bloque "Frenos" **no se renderiza**. No se
  escribe "Sin frenos": sin dato persistente, afirmar que no hay frenos
  sería falso. Excepción: justo después de una publicación sin
  `blocked[]`, se muestra una línea 13 px en `--color-text-dim`:
  "Sin frenos en esta publicación." (describe esa respuesta, no el clip).

### 6.3 Relación con "Publicar ahora"

Los frenos son **informativos**: no deshabilitan `btnPublish`. Hoy la
API publica y devuelve `blocked[]`; bloquear el botón en el front
cambiaría la lógica de negocio y dejaría fuera frenos que solo el
Worker conoce. Si un freno debe impedir publicar, lo decide el Worker,
no este diseño.

---

## 7. Seguimiento / tracking como timeline (idea #5)

Fuente: `GET /api/clips/:id/tracking` → `events[]` (§2.3). Hoy es un
`<ul class="activity">` de texto; pasa a timeline vertical.

### 7.1 Estructura

- Contenedor: `<ol class="timeline">` dentro de `#clipTracking`
  (se conserva el id; cambia la clase y el marcado interno).
- **Sin pasos inventados:** se renderiza exactamente cada evento
  devuelto, en el orden en que viene de la API. No se rellenan los
  "11 pasos" de la idea #5 si la API no los devolvió; no se ordena de
  nuevo (si la API los devuelve cronológicos, se ven cronológicos).
- Cada ítem:
  1. **Línea vertical** a la izquierda (2 px, `--color-border`) que une
     los puntos; el último ítem no prolonga la línea.
  2. **Punto (`.dot`, 10 px):** `--color-ok` si el paso fue bien,
     `--color-bad` si `failed` es verdadero, `--color-text-dim` para
     pasos informativos sin resultado. El color nunca va solo: el paso
     fallido lleva además la pill "Fallo" (ver punto 5).
  3. **Hora:** `fmtDate(e.at)` en 12 px `--color-text-dim`,
     `tabular-nums`.
  4. **Paso:** `e.step` en 14 px semibold `--color-text`.
  5. **Actor y mensaje:** `"· " + e.actor + " — " + e.message` en 13 px
     `--color-text-dim`. Si `actor` o `message` están vacíos, se omiten
     (no se escribe "—").
  6. **Fallo:** si `e.failed`, pill `.pill.bad` con el texto
     **"Fallo"** junto al paso, y el mensaje del paso se mantiene
     (es la causa). El ítem fallido no se oculta ni se colapsa.
- **Último paso:** el último ítem de la lista lleva además el texto
  12 px en `--color-text-dim`: **"Último paso"**. Es el único marcador
  de "actual": no hay campo que diga cuál es el paso en curso, así que
  no se marca ninguno como "en curso" ni se usa animación de pulso.
  (Si la API algún día marca un paso como actual, ese paso llevará el
  texto "En curso"; hasta entonces, no.)
- Espaciado: `gap: 16px` entre ítems; `padding-left: 24px` para la línea
  y los puntos. `line-height: 1.5`.

### 7.2 Estados

| Caso | Render |
|---|---|
| Hay eventos | Timeline como §7.1 |
| `events` vacío | Una línea 13 px en `--color-text-dim`: **"Sin seguimiento todavía."** (texto actual de `app.js`, se conserva) |
| Carga en curso | 3 esqueletos de línea (ver §10.2), `aria-busy="true"` en `#clipTracking` |
| Error al cargar tracking | El detalle del clip se muestra igual; bajo "Seguimiento", mensaje de error con `role="alert"` + botón ghost **"Reintentar"** que vuelve a llamar a `selectClip(id)` |

---

## 8. Subir MP4 con progreso visible (`videoFile` + `btnUpload`)

### 8.1 Layout (detalle, bloque de subida)

Se conservan el `label` y los ids. Correcciones respecto al front actual
(UX-13, UX-31):

- `<label for="videoFile">Subir MP4 9:16 (máx. 128 MB)</label>` — el
  `for` hoy falta; sin él, tocar el texto no enfoca el campo.
- `<input type="file" id="videoFile" accept="video/mp4,video/*">` con
  `font-size: 16px`, `min-height: 44px`, fondo
  `--color-surface-input`. Estilo propio para
  `input[type=file]::file-selector-button` (fondo `--color-surface`,
  borde `--color-border`, texto `--color-text`, radio 8 px), porque el
  botón nativo se dibuja en claro sin `color-scheme`.
- Texto de ayuda bajo el campo, 13 px `--color-text-dim`:
  "Solo MP4 vertical 9:16, hasta 128 MB."
- `btnUpload` es primario, `min-height: 44px`, a ancho completo en móvil.
  El bloque de subida y el de publicación van en columna en móvil
  (el `.row` actual con `flex: 1` estira los botones; en la Cola, cada
  bloque ocupa su propia fila — ver VIS-9: `.row > button { flex: 0 0
  auto; }` salvo clase explícita).

### 8.2 Validación antes de subir (al elegir el archivo, no al enviar)

| Comprobación | Error inline (bajo el campo, `aria-describedby`, `role="alert"`) |
|---|---|
| Sin archivo | Al pulsar "Subir MP4 a R2" sin archivo: **"Elige un archivo MP4 primero."** (texto actual, se conserva) |
| Tipo no MP4 (`f.type` no es `video/mp4` ni empieza por `video/`) | **"Ese archivo no es un vídeo MP4. Elige un archivo .mp4."** |
| Tamaño > 128 MB | **"El archivo pesa X MB y el máximo es 128 MB. Elige uno más ligero."** (X = tamaño real redondeado) |

- Mientras el archivo no sea válido, `btnUpload` está `disabled`.
  Al elegir un archivo válido, se muestra bajo el campo su nombre y
  tamaño: `"nombre.mp4 · 42 MB"` (13 px, `--color-text-dim`,
  `overflow-wrap: anywhere`).
- El error no va solo a `#msg`: va **bajo el campo** (UX-4, crítico) y
  `#msg` queda para el resultado de la subida.

### 8.3 Progreso visible — advertencia técnica obligatoria

> **Hoy `uploadSelected()` usa `fetch` con `body: f`. `fetch` no expone
> el progreso de subida.** Un porcentaje inventado o animado sería
> falso. Por eso el progreso tiene dos modos, y el implementador debe
> elegir uno y documentarlo; no se finge el otro:

| Modo | Cuándo | Qué se ve |
|---|---|---|
| **A. Determinado (recomendado)** | Si la subida se reimplementa con `XMLHttpRequest` + `upload.onprogress` (mismo endpoint, mismo método PUT, mismas cabeceras) | Barra de progreso (fondo `--color-surface-input`, relleno `--color-primary`, altura 8 px, radio 999 px) con **% real**, **MB subidos / MB totales** (`"18 de 42 MB · 43 %"`) y velocidad si `onprogress` la permite calcular. Botón ghost **"Cancelar"** (aborta el XHR) visible solo durante la subida. Al cancelar: mensaje "Subida cancelada." y el botón vuelve a "Subir MP4 a R2". |
| **B. Indeterminado (si se mantiene `fetch`)** | Sin cambio de transporte | Barra indeterminada (animación de desplazamiento, desactivada con `prefers-reduced-motion`) + texto **"Subiendo nombre.mp4 (42 MB)…"** + spinner. **Sin % y sin botón Cancelar**: no se muestra un botón que no puede cancelar nada. |

- En ambos modos, durante la subida `btnUpload` está `disabled` y su
  texto es **"Subiendo…"** (UX-3, crítico: hoy solo `doLogin()`
  deshabilita; publicar/subir no lo hacen). Al terminar, vuelve a
  "Subir MP4 a R2".
- No se usa `showMsg("Subiendo…")` como único feedback: el progreso
  vive **dentro del bloque de subida**, junto al botón, no en el banner
  superior.

### 8.4 Éxito y error

- **Éxito:** la barra llega al 100 % y se muestra, bajo el campo:
  - Pill `.pill.ok` **"MP4 listo"** (estado transitorio de esta sesión;
    ver §2.5: sin campo persistente, al recargar no se afirma).
  - `"MP4 guardado en R2: " + r2_key` en `--font-mono` 13 px.
  - La URL pública como **enlace real** `<a href="public_url">` en
    `--color-link`, con `overflow-wrap: anywhere`, + botón ghost
    **"Copiar"** (copia `public_url` al portapapeles; al copiar, su
    texto pasa a **"Copiado"** 2 s y vuelve). (UX-29: hoy la URL es
    texto largo sin ajuste ni forma de copiarla.)
  - `#msg` con `role="status"`: "MP4 subido." (éxito, auto-limpieza 5 s).
- **Error:** `btnUpload` se reactiva, la barra se oculta y aparece el
  error bajo el campo (`role="alert"`) + en `#msg` (`role="alert"`):
  el mensaje de la API tal cual, y botón ghost **"Reintentar"** que
  reintenta la subida con el mismo archivo (sin obligar a elegirlo de
  nuevo). No se borra el archivo elegido tras un error.

---

## 9. Detalle del clip (`#clipDetailCard`)

### 9.1 Apertura

- Se abre con el botón **"Ver detalle"** de la tarjeta o de la tabla
  (§4.2). `selectClip(id)` como hoy; `clipTitle` con
  `c.title || c.seo_title || ("Clip " + id)`.
- Tras abrir, `scrollIntoView` al detalle con `behavior` `"auto"` si
  `prefers-reduced-motion` y `"smooth"` en caso contrario (UX-27).
  El foco se mueve al `h2#clipTitle` (`tabindex="-1"`) para que el
  lector de pantalla anuncie el cambio (UX-17).
- Cerrar: botón ghost **"Cerrar detalle"** al final del detalle (hoy no
  existe forma clara de volver). Al cerrar, se oculta `#clipDetailCard`,
  se quita el estado "Abierto" de la tarjeta (§4.2) y el foco vuelve al
  botón "Ver detalle" del clip.
- En móvil el detalle sigue siendo una tarjeta apilada bajo la lista
  (no bottom sheet: la idea #17 es otra oleada; no se introduce aquí
  para no cambiar el modelo de navegación).

### 9.2 Anatomía (orden fijo)

1. `h2#clipTitle` (16 px) + anillo de score grande (56 px, §4.3) a su
   derecha, en la misma fila.
2. `#clipDetail` — línea meta: pill de estado (§4.4) · **"Puntos: X"**
   (o `"—"`) · plataformas como texto corto (`"YouTube, TikTok"`, solo
   nombres; la matriz completa va en el punto 4).
3. Barra de prioridad + desglose (§3).
4. **Miniatura grande (condicional):** si existe miniatura (§2.5),
   imagen 9:16 de `144px` de ancho máximo, radio 8 px, a la izquierda
   de la meta; si no existe, no se reserva hueco (el placeholder pequeño
   ya está en la tarjeta; en el detalle no se repite).
5. **Matriz de plataformas** (§5.5, variante detalle).
6. **Frenos** (§6), solo si hay.
7. `seo_description` si existe (párrafo 14 px, `--color-text`).
   `seo_tags` si existe: línea 13 px dim, **"Tags: …"**.
8. `h3` **"Seguimiento"** (14 px) + timeline (§7) en `#clipTracking`.
9. Bloque de subida (§8) y bloque de publicación (§10), en columna en
   móvil, en dos columnas en escritorio.
10. Botón ghost **"Cerrar detalle"**.

### 9.3 Carga del detalle

Mientras `selectClip` espera a la API: el detalle muestra 3 esqueletos
de línea + el título "Cargando clip…" y `#clipDetailCard` con
`aria-busy="true"`. Si falla la carga: `#msg` con `role="alert"` y el
detalle no se abre (la tarjeta no queda en estado "Abierto").

---

## 10. Publicar con confirmación y loading (`btnPublish`)

### 10.1 Confirmación (antes de ejecutar)

Publicar encola trabajos reales (UX-11, alta). Al pulsar `btnPublish`:

- Diálogo de confirmación (nativo `confirm()` como mínimo aceptable;
  preferible diálogo propio con los mismos textos). Texto exacto:
  - Título: **"¿Publicar este clip?"**
  - Cuerpo: **"Se va a publicar «{título del clip}» en: {plataformas
    del clip, separadas por comas}. Se encolan los trabajos y el Worker
    los procesa según los límites diarios."**
  - Si hay frenos visibles (§6): se añade la línea **"Hay frenos
    activos en este clip (ver arriba). Publicar igualmente los dejará
    registrados."**
  - Botones: **"Publicar"** (primario) / **"Cancelar"** (ghost).
- Al cancelar: no pasa nada, no se muestra mensaje, el botón queda
  como estaba. Al confirmar: §10.2.
- No se pide confirmación dos veces ni se confirma por plataforma:
  una confirmación por clip.

### 10.2 Loading y resultado

- Durante la petición: `btnPublish` `disabled` + texto
  **"Publicando…"** (+ spinner si el implementador lo añade; el texto
  es obligatorio, el spinner opcional). `btnUpload` también se
  deshabilita mientras tanto (evita subir un MP4 a la vez que se
  publica el mismo clip).
- **Éxito:** `#msg` (`role="status"`, auto-limpieza 5 s) con el texto
  actual de `app.js`, conservado:
  **"Encolado: {plataforma} ({estado}), …"** y, si hay bloqueos,
  **" · Frenado: {…} · {…}"**. Después, `selectClip(id)` de nuevo para
  refrescar matriz, frenos y tracking. Los frenos de `blocked[]` se
  muestran como chips (§6.2).
- **Error:** `#msg` (`role="alert"`, con botón × para cerrar) con el
  mensaje de la API; `btnPublish` vuelve a "Publicar ahora". No se
  reintenta solo.
- **Sin clip seleccionado:** `btnPublish` no es alcanzable (vive dentro
  de `#clipDetailCard`, oculta hasta `selectClip`). No se añade un
  estado "sin clip" inventado.

---

## 11. Estados de la lista: carga, vacío y error

### 11.1 Carga (`loadClips` en curso)

- 3 tarjetas esqueleto en móvil (misma forma que §4.2: rectángulo 9:16
  + líneas) y 3 filas esqueleto en la tabla de escritorio.
- `#clipList` con `aria-busy="true"`; esqueletos en
  `--color-surface-input` con shimmer sutil, desactivado con
  `prefers-reduced-motion` (OVERRIDES §4, componente Skeleton).
- No se muestra "La cola está vacía" mientras carga (hoy el vacío y la
  carga son indistinguibles: UX-22).

### 11.2 Cola vacía (empty state)

Texto actual de `app.js`, ampliado con acción (UX-25: los vacíos
explican pero no ofrecen acción):

- Título (h2 de la tarjeta, 16 px): **"La cola está vacía"**
- Cuerpo (13 px, `--color-text-dim`): **"Los clips aparecen aquí cuando
  el cerebro los detecta o cuando se migran del panel anterior."**
- Acciones (en fila, `gap: 8px`, cada una `min-height: 44px`):
  - Botón primario **"Ir al Worker"** → cambia a la pestaña Worker
    (misma acción que tocar la pestaña; no es un enlace nuevo).
  - Botón ghost **"Actualizar"** → vuelve a llamar a `loadClips()`.
- Sin ilustración con emoji. Si el implementador añade una ilustración,
  SVG simple en `--color-text-dim`, `aria-hidden="true"`, sin texto
  dentro de la imagen.
- El detalle (`#clipDetailCard`) queda oculto cuando la cola está vacía.

### 11.3 Error al cargar la lista

- `#clipList` muestra: **"No se pudo cargar la cola."** (14 px,
  `--color-text`) + el mensaje de la API en 13 px dim + botón primario
  **"Reintentar"** (vuelve a llamar a `loadClips()`).
- Además `#msg` con `role="alert"` y el mismo error. No se deja la lista
  en blanco ni se muestra el vacío como si no hubiera clips.

---

## 12. Textos en español (catálogo completo de esta pestaña)

Todos los textos visibles de la Cola y el detalle, en un solo lugar para
que el implementador no tenga que inventar copia. Los que ya existen en
`app.js` se conservan tal cual y se marcan con "(actual)".

| Dónde | Texto |
|---|---|
| Encabezado de la tarjeta Cola | "Cola de clips" (actual) |
| Subtítulo de la tarjeta Cola | "Clips en carpeta, aprobados o programados, ordenados por fecha. Pulsa un clip para ver su seguimiento, subir su MP4 o publicarlo." (actual) |
| Tarjeta, placeholder de miniatura | "Sin miniatura" |
| Tarjeta, botón | "Ver detalle" |
| Tarjeta, clip abierto | Pill "Abierto" |
| Tarjeta, fecha | "Creado {fecha}" |
| Barra | "Puntos · {n}" / "Prioridad · {n}" (solo si existe `priority`) |
| Desglose, botón | "Ver desglose" / "Ocultar desglose" |
| Desglose, filas | "Viralidad", "SEO", "Chat" |
| Matriz, sin plataformas | "Sin plataformas asignadas" |
| Estados de clip / plataforma | Pendiente, En cola, Publicando, Publicado, Fallido, Frenado (ver §4.4 y §5.3) |
| Detalle, bloque frenos | h3 "Frenos" |
| Detalle, freno sin MP4, botón | "Subir MP4" |
| Detalle, tras publicar sin frenos | "Sin frenos en esta publicación." |
| Detalle, descripción / tags | "Tags: {tags}" (actual) |
| Detalle, seguimiento | h3 "Seguimiento" (actual); vacío: "Sin seguimiento todavía." (actual); paso fallido: pill "Fallo"; último paso: "Último paso" |
| Detalle, subida, label | "Subir MP4 9:16 (máx. 128 MB)" (actual) |
| Detalle, subida, ayuda | "Solo MP4 vertical 9:16, hasta 128 MB." |
| Detalle, subida, archivo válido | "{nombre} · {n} MB" |
| Detalle, subida, errores | "Elige un archivo MP4 primero." (actual); "Ese archivo no es un vídeo MP4. Elige un archivo .mp4."; "El archivo pesa {n} MB y el máximo es 128 MB. Elige uno más ligero." |
| Detalle, subida, botón | "Subir MP4 a R2" (actual) / cargando: "Subiendo…" |
| Detalle, subida, progreso | "{n} de {total} MB · {n} %" (modo A) / "Subiendo {nombre} ({n} MB)…" (modo B) |
| Detalle, subida, cancelar | "Cancelar" / "Subida cancelada." |
| Detalle, subida, éxito | Pill "MP4 listo"; "MP4 guardado en R2: {r2_key}"; enlace a la URL pública; botón "Copiar" / "Copiado"; `#msg`: "MP4 subido." |
| Detalle, subida, error | "Reintentar" |
| Detalle, publicación, label | "Publicar" (actual) |
| Detalle, publicación, ayuda | "Encola la publicación en las plataformas del clip. Los frenos (SEO, límites diarios, falta de MP4) quedan registrados." (actual) |
| Detalle, publicación, botón | "Publicar ahora" (actual) / cargando: "Publicando…" |
| Detalle, publicación, confirmación | Título "¿Publicar este clip?"; cuerpo "Se va a publicar «{título}» en: {plataformas}. Se encolan los trabajos y el Worker los procesa según los límites diarios."; con frenos: "Hay frenos activos en este clip (ver arriba). Publicar igualmente los dejará registrados."; botones "Publicar" / "Cancelar" |
| Detalle, publicación, resultado | "Encolado: {plataforma} ({estado}), …" + " · Frenado: {…}" (actual) |
| Detalle, cerrar | "Cerrar detalle" |
| Detalle, carga | "Cargando clip…" |
| Lista, carga | Esqueletos (sin texto) |
| Lista, vacía | "La cola está vacía" / "Los clips aparecen aquí cuando el cerebro los detecta o cuando se migran del panel anterior." (actual) / botones "Ir al Worker" / "Actualizar" |
| Lista, error | "No se pudo cargar la cola." / botón "Reintentar" |

---

## 13. Accesibilidad (resumen aplicable a esta pestaña)

- Foco visible 3 px en todos los controles (§1.3); orden de foco =
  orden visual; skip link global ya exigido por OVERRIDES §7.
- Labels con `for`: `videoFile` (§8.1). Todo botón tiene texto visible
  (nada de icono solo).
- Estados nunca solo con color: pill con texto, punto de plataforma +
  nombre, número junto a barra/anillo.
- `#msg`: `role="status" aria-live="polite"` (éxito, auto-limpieza 5 s)
  / `role="alert"` (error, con ×). Errores de subida bajo el campo con
  `aria-describedby` (UX-4).
- Desglose de prioridad: `<button>` con `aria-expanded`.
- Timeline: `<ol>` real; la hora es texto, no solo posición.
- Tabla: `<thead>` / `<tbody>`, `scope="col"`, caption oculta.
- Contraste: todos los pares de texto ≥ 4.5:1 (tabla en §1.1–1.2).
- `prefers-reduced-motion`: sin transiciones, sin shimmer, sin pulso,
  `scrollIntoView` con `behavior: "auto"`.
- Táctil 44 px, inputs 16 px, safe-area (§1.3).

---

## 14. Checklist de aceptación (implementador)

- [ ] A 360 px y 375 px: tarjetas 9:16, sin scroll horizontal en toda
      la página; a 640 px tarjeta, a 641 px tabla.
- [ ] Miniatura 72×128 con `aspect-ratio: 9 / 16`; sin miniatura, el
      placeholder con degradado y "Sin miniatura" (no hay imagen rota).
- [ ] Anillo y barra usan los tramos <50 / 50–89 / ≥90 con el número
      siempre escrito; `score: null` → `"—"` y barra neutra.
- [ ] Desglose **oculto** cuando la API no devuelve componentes (caso
      actual): no hay botón "Ver desglose" ni ceros.
- [ ] Matriz: 4 plataformas en orden fijo, chip con punto + nombre +
      pill con texto; `publish_states` vacío → "Pendiente"; estado
      desconocido → pill neutra con el valor tal cual.
- [ ] Ningún `publish_states` en texto `k: v`; ningún `status` en texto
      plano; ningún `<a href="#">` para abrir el detalle.
- [ ] Frenos: solo desde `blocked[]`; sin frenos no hay bloque
      "Frenos"; ningún freno inferido de `score`, duración o MP4.
- [ ] Timeline: exactamente los eventos de la API, en su orden; fallo
      con pill "Fallo"; vacío con "Sin seguimiento todavía.".
- [ ] Subida: validación de tipo/tamaño antes de subir, error bajo el
      campo, `btnUpload` deshabilitado con "Subiendo…"; progreso
      determinado (modo A) o indeterminado honesto (modo B), nunca un
      % fingido; éxito con enlace + "Copiar".
- [ ] Publicar: confirmación con título y plataformas, `btnPublish`
      deshabilitado con "Publicando…", resultado con jobs y bloqueos,
      refresco del detalle.
- [ ] Vacío con "Ir al Worker" y "Actualizar"; carga con esqueletos y
      `aria-busy`; error con "Reintentar".
- [ ] Ningún hex fuera de `tokens.css` / `tokens.json` (grep de
      `#[0-9a-f]{3,6}` en los archivos tocados = 0, salvo los tokens);
      ningún color de marca crudo como texto.
- [ ] Foco visible, táctil 44 px, inputs 16 px, `prefers-reduced-motion`,
      contraste verificado en los pares de §1.
- [ ] Ids conservados: `clipList`, `clipDetailCard`, `clipTitle`,
      `clipDetail`, `clipTracking`, `videoFile`, `btnUpload`, `btnPublish`.

---

## 15. Fuera de alcance (no diseñar ni implementar aquí)

- Segmentos LIVE / VOD, filtros y búsqueda en la Cola (idea #10).
- Bottom sheet de detalle en móvil (idea #17).
- Toasts en vez de `#msg` (idea #13); KPI del día (idea #7); semáforo
  del Worker (idea #8, global, no de la Cola).
- Generación de miniaturas, transcodificación o recorte del MP4.
- Nuevos endpoints o campos de API; cambio de fórmulas, de orden de la
  lista o de reglas de freno/límites.
- Cambios en Worker, Cuentas, Avisos, Ajustes, login o header.
- Cambios de copy fuera del catálogo del §12.
