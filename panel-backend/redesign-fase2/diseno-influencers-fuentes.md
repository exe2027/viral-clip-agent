# Diseño — Influencers + Fuentes / VODs (panel AVC, fase 2)

- **Fecha:** 2026-11-17
- **Alcance:** SOLO diseño visual de las dos pestañas. Sin código de implementación, sin lógica de negocio, sin endpoints nuevos, sin tocar `worker/`, `migrations/`, `wrangler.toml` ni los 62 ids estáticos existentes.
- **Fuentes leídas:** `redesign-fase2/inventario.md` (§2.4 Influencers SIN ENDPOINT, §2.5 Fuentes/VODs SIN ENDPOINT, §3.2 ids, §3.3 regla dura), `design-system/avc-panel/OVERRIDES.md` §8 (variante GTA 6 / Vice City, manda sobre §§1–7 en lo que cambia), `tokens-gta6.css` / `tokens-gta6.json`, `frontend/dist/index.html`, `frontend/dist/app.js` (`TAB_NAMES`, `switchTab`, `loadTab`), `worker/src/index.ts` (24 combinaciones, ninguna para influencers ni fuentes), migraciones 0002 (tablas `influencers`, `sources`, `vod_checks`) y 0006 (`sources.game`).
- **Regla dura (del inventario, se respeta):** lo que dice SIN ENDPOINT va con **estado vacío real + acción pendiente, NUNCA datos inventados**. No hay otros influencers, ni estadísticas, ni VODs detectados, ni contadores.
- **Nombres:** solo usernames `seba` / `chacal` (tenants del panel). El streamer se muestra por su handle público `IlloJuan`; no aparece ningún nombre personal en ninguna de las dos pestañas.

## 0. Integración en la navegación (sin romper lo existente)

- Orden de pestañas propuesto: Worker · Cola · Cuentas · **Influencers** · **Fuentes** · Avisos · Ajustes. Van juntas, después de Cuentas y antes de Avisos: configuran de dónde salen los clips antes de ver la Cola; no se altera el orden de las 5 existentes.
- El desarrollador añade `"influencers"` y `"fuentes"` a `TAB_NAMES` en `app.js` (hoy `["worker","cola","cuentas","avisos","ajustes"]`), los dos botones `tabbtn-*`, las dos secciones `tab-*`, y las dos ramas en `loadTab()` (`loadInfluencers()` / `loadFuentes()`). Hashes: `#influencers`, `#fuentes`. El patrón es el mismo de `switchTab`/`loadTab` y de `aria-selected`/`aria-controls` que ya usan las 5 pestañas.
- Las dos pestañas son **estáticas hasta que exista endpoint**: su `load*()` solo pinta el estado vacío real + la tarjeta de configuración de IlloJuan (ver §1.3) y **no hace ningún `fetch`**. No se añade ninguna llamada a rutas que no están en el inventario §2.1.
- Nav: mismo patrón que OVERRIDES §8.3 — pestaña en `--font-display`, 600, mayúsculas, `letter-spacing: .02em`, texto `--color-text-dim`; activa con fondo `--gradient-sunset`, texto blanco 700, barra inferior 3 px `--color-neon-amber`, `aria-selected="true"`. `min-height: 44px` (`--size-touch-target`).

## 1. Pestaña Influencers (`#tab-influencers`)

### 1.1 Qué es y qué NO es

- Es la lista de streamers a clipear. Hoy **no tiene endpoint**: la tabla D1 `influencers` existe (migración 0002: `name, platform, profile_url, language, permission, active, notes, created_at` + `tenant_id` en 0012) pero ninguna ruta del Worker la lee ni la escribe, y `db.ts` no tiene ninguna función de influencers.
- La **única** entrada que se muestra es IlloJuan, y se muestra como **dato de configuración del proyecto, no como dato del Worker** (ver §1.3). No hay ningún otro influencer: ni de ejemplo, ni "próximamente", ni con estadísticas (sin seguidores, sin clips, sin vistas: nada de eso existe en ninguna fuente).
- El permiso real de un streamer hoy lo verifica el puente contra su propio archivo (`streamers.yaml`), no el panel (`worker/src/index.ts`, `streamerAllowed: true` con comentario "el permiso se verifica en el puente"). La pestaña **no verifica permisos**: solo muestra la configuración.

### 1.2 Layout móvil-primero (≤ 640 px, base del diseño)

Apilado vertical, una tarjeta por bloque, gap `--space-4` (16 px), padding lateral del `main` igual que el resto de pestañas. Sin scroll horizontal a 360 px. En escritorio (≥ 641 px) la misma pila, ancho máximo del contenido igual que las demás pestañas; la tarjeta de IlloJuan pasa a dos columnas (datos a la izquierda, toggles a la derecha) y el formulario a dos columnas. No hay tabla en esta pestaña: la lista es de tarjetas, como `.clip-cards` en la Cola.

Orden de bloques:

1. **Tarjeta de regla de lanzamiento** (§1.4) — arriba del todo, siempre visible.
2. **Tarjeta IlloJuan** (§1.3) — la única entrada.
3. **Estado vacío "Sin más influencers"** (§1.5) — debajo de la tarjeta.
4. **Formulario "Añadir influencer"** (§1.6) — último bloque, plegado en un `<details>` cerrado por defecto en móvil para no empujar la lista; abierto por defecto en escritorio.
5. **Nota de acción pendiente** (§1.7) — al pie, dentro de la misma tarjeta del formulario y repetida en miniatura bajo la tarjeta de IlloJuan.

### 1.3 Tarjeta IlloJuan (configuración del proyecto)

Es una `.card` normal (fondo `--color-surface` `#261a38`, borde 1 px `--color-border` `#7c6aa0`, radio `--radius-card` 16 px, padding `--space-4`, `box-shadow: var(--shadow-card)`), con un distintivo que la separa de una fila de datos: una pill temática en la esquina superior derecha.

Contenido exacto (textos literales):

- **Título (h3, display condensado 18 px, mayúsculas):** `IlloJuan`
- **Subtítulo (`--color-text-dim` `#b8b3c9`, 13 px):** `Twitch · Español`
- **Pill distintiva:** `.pill.violet` — texto `Configuración del proyecto`
- **Nota bajo el subtítulo (`--color-text-dim`, 13 px):** `Dato de configuración del proyecto. No viene del Worker: aún no hay endpoint de influencers.`
- **Fila de pills de estado (siempre con texto, nunca solo color):**
  - Permiso: `.pill.ok` — texto `Permiso: sí`
  - Activo: `.pill.ok` — texto `Activo: sí`
  - Lanzamiento: `.pill.sunset` — texto `En lanzamiento`
- **Fila de plataforma:** punto decorativo `.dot` con color de plataforma Twitch (`--color-platform-tiktok` `#25f4ee` es el neón de la plataforma de vídeo corto; para Twitch se usa el punto violeta decorativo `--color-neon-violet-raw` `#a855f7` SOLO como punto/borde, nunca como texto — ver §4) + texto `Plataforma: Twitch` en `--color-text`.
- **Nota de regla (13 px, `--color-text-dim`):** `Solo IlloJuan está activo con permiso. Cualquier otro influencer queda desactivado.`

Toggles permiso / activo:

- Dos controles tipo interruptor, uno por fila, con su etiqueta a la izquierda y el interruptor a la derecha (44 px de alto táctil):
  - `Permiso para clipear` — estado ON
  - `Activo en la cola` — estado ON
- **Ambos en `disabled`**, con `aria-disabled="true"`. No se pueden cambiar: no hay endpoint que guarde el cambio. Al lado de cada toggle, texto en `--color-text-dim` 12 px: `Pendiente: endpoint del Worker`.
- El interruptor ON usa el degradado `--gradient-sunset` como fondo del carril y blanco para el pomo; el estado deshabilitado aplica `opacity: .55` (regla base OVERRIDES §1, UX-3) y `cursor: not-allowed`. El foco visible, si el control llegara a enfocarse, es outline 3 px `--color-focus` `#fbbf24`, offset 2 px.
- **Botón Eliminar:** botón secundario (ghost, borde `--color-border`, texto `--color-text`, `min-height: 44px`, radio `--radius-control` 10 px) con texto `Eliminar`, también en `disabled`, con la misma nota `Pendiente: endpoint del Worker` debajo. No hay confirmación que diseñar: sin endpoint no hay borrado.

### 1.4 Tarjeta de regla de lanzamiento

- `.card` con borde izquierdo 3 px `--color-neon-pink` `#f472b6` (decorativo) y fondo `--color-surface`.
- **Título (h2, 18 px display, mayúsculas):** `Regla de lanzamiento`
- **Texto exacto (16 px, `--color-text`):** `Solo IlloJuan está activo con permiso (Twitch). Cualquier otro influencer queda desactivado.`
- **Texto secundario (13 px, `--color-text-dim`):** `Esta regla manda sobre cualquier otro influencer que se añada después.`
- Token de acento: el título puede llevar gradiente de texto `--gradient-sunset-text` (paradas `#f9a8d4` / `#fdba74`, verificadas 9.00:1 / 9.67:1 sobre tarjeta) con fallback `color: #f9a8d4` si `background-clip: text` no aplica. Solo en este título, no en cuerpo.

### 1.5 Estado vacío "Sin más influencers"

- Debajo de la tarjeta de IlloJuan, bloque `.empty-state` (mismo patrón que `loadClips` / `loadAccounts` en `app.js`):
  - **Título (h3):** `Sin más influencers`
  - **Texto exacto (`--color-text-dim`):** `No hay más streamers en la lista. Añade uno con el formulario de abajo cuando el Worker tenga endpoint para guardarlo.`
  - Sin botón de acción aquí: el formulario ya está en la misma pestaña (§1.6). Sin ilustración, sin cuentas de ejemplo.
- Este es el estado vacío real de la **lista D1**: la lista del Worker está vacía; la tarjeta de IlloJuan es configuración, no un registro de esa lista. Cuando exista endpoint y la lista devuelva filas, este bloque desaparece y cada fila se pinta con la misma tarjeta de §1.3 (sin la pill `.pill.violet` de configuración y con sus toggles habilitados).

### 1.6 Formulario "Añadir influencer"

`<details>` con `<summary>` de texto exacto `Añadir influencer`. Dentro, un `<form novalidate>` con los campos de la tabla D1 `influencers` (migración 0002), uno por línea en móvil:

| Campo | Control | Etiqueta exacta | Ayuda / placeholder |
|---|---|---|---|
| Nombre o handle | `input` texto, `required` | `Nombre o handle` | placeholder `Ej.: IlloJuan` (es solo ejemplo de formato, no un segundo influencer) |
| Plataforma | `select`, `required` | `Plataforma` | Opciones: `Twitch`, `YouTube`, `Kick` |
| URL del perfil | `input` url | `URL del perfil` | placeholder `https://…` |
| Idioma | `select` | `Idioma` | Opciones: `Español`, `Inglés`; valor por defecto `Español` |
| Permiso para clipear | `checkbox` | `Tiene permiso para clipear` | — |
| Activo en la cola | `checkbox`, marcado por defecto | `Activo en la cola` | — |
| Notas | `textarea` | `Notas` | placeholder `Opcional` |

- **Botón de envío:** primario atardecer (`background: var(--gradient-sunset)`, texto blanco 600, `min-height: 44px`, radio 10 px, `box-shadow: var(--shadow-neon-sunset)`), texto exacto `Añadir influencer`, en `disabled`.
- **Nota bajo el botón (13 px, `--color-warn-fg` `#fde68a` sobre `--color-warn-bg` `#451a03`, como pill `.pill.warn` de ancho completo o línea con icono SVG; siempre con texto):** `Pendiente: endpoint del Worker. Este formulario no guarda todavía.`
- Errores de validación (cuando el endpoint exista y el formulario se habilite): mismo patrón que el resto del panel — error bajo su campo con `aria-describedby`, `role="alert"`, fondo `--color-bad-bg`, texto `--color-bad-fg`, foco al primer campo inválido (OVERRIDES §1, UX-4). Hoy no se muestra ningún error: el formulario está deshabilitado antes de validar.
- Estilo de campos: fondo `--color-surface-input` `#150d24`, borde `--color-border` `#7c6aa0`, texto `--color-text` `#e8eaf0`, `font-size: 16px` (`--font-size-input`, evita el auto-zoom en iOS), `min-height: 44px`, radio `--radius-control` 10 px. Focus: borde `--color-neon-pink-text` `#f9a8d4` + outline 3 px `--color-focus` `#fbbf24`, offset 2 px (OVERRIDES §8.3).
- El formulario NO precarga ningún influencer. El placeholder `Ej.: IlloJuan` es formato, no dato.

### 1.7 Nota de acción pendiente (Influencers)

Texto exacto, al pie de la pestaña, en `--color-text-dim` 13 px (con enlace de estilo `--color-link` `#f9a8d4` solo si apunta a documentación interna; si no, texto plano):

`Pendiente: endpoint del Worker para leer, crear, editar y eliminar influencers. Trabajo de backend, fuera de este panel.`

### 1.8 Estados de la pestaña Influencers

| Estado | Qué se ve | Tokens |
|---|---|---|
| Carga | 2 skeletons (`.skeleton`, fondo `--color-surface-input`, `aria-busy="true"` en `#influencerList`) con shimmer desactivado bajo `prefers-reduced-motion` | `--color-surface-input` |
| Activo (configuración) | Tarjeta IlloJuan §1.3: pills `Permiso: sí` / `Activo: sí` en `.pill.ok`, `En lanzamiento` en `.pill.sunset` | `.pill.ok` (`--color-ok-bg` `#14532d` / `--color-ok-fg` `#bbf7d0`), `.pill.sunset` (`--color-pill-sunset-bg` `#500724` / `--color-pill-sunset-fg` `#fbcfe8`) |
| Vacío (lista D1) | `Sin más influencers` (§1.5) | texto `--color-text-dim` |
| Pendiente (sin endpoint) | Toggles y botones en `disabled` + nota `Pendiente: endpoint del Worker` (§§1.3, 1.6, 1.7) | `.pill.warn` (`--color-warn-bg` `#451a03` / `--color-warn-fg` `#fde68a`); botón deshabilitado `opacity: .55` |
| Error | No aplica: sin `fetch` no hay error de red que mostrar. Si en el futuro el endpoint falla, se usa el patrón de Cola: `No se pudo cargar la lista.` + botón `Reintentar` (mismo texto que `retryClips`) | `.msg` error (`--color-bad-bg` / `--color-bad-fg`) |

## 2. Pestaña Fuentes / VODs (`#tab-fuentes`)

### 2.1 Qué es y qué NO es

- Es la lista de fuentes procesadas (YouTube / Twitch / Kick) con el estado de su pipeline y la revisión de VODs nuevos. Hoy **no tiene endpoint**: las tablas D1 `sources` (migración 0002: `url, platform, title, streamer, status DEFAULT 'pendiente', progress, status_note, influencer_id` + `game` en 0006 + `tenant_id` en 0012) y `vod_checks` (migración 0002: `ran_at, summary, findings` + `tenant_id` en 0012) existen pero ninguna ruta del Worker las lee.
- La detección de VODs nuevos hoy la hace GitHub Actions (cerebro, cron 09:00), no el Worker ni el panel (inventario §2.5). La pestaña no muestra VODs "detectados" a partir de la Cola: **un clip en Cola no es un VOD**.
- No hay ninguna fuente, ningún VOD, ninguna fecha de última revisión ni ningún contador: todo eso está vacío de verdad.

### 2.2 Layout móvil-primero (≤ 640 px, base del diseño)

Apilado vertical, gap `--space-4`, sin scroll horizontal a 360 px. En escritorio (≥ 641 px) la lista de fuentes pasa a tabla (ver §2.4); las tarjetas de acción y de última revisión se mantienen apiladas a ancho completo.

Orden de bloques:

1. **Tarjeta de acción "Revisar VODs nuevos"** (§2.3).
2. **Tarjeta "Última revisión"** (§2.5) — estado vacío.
3. **Lista de fuentes** (§2.4) — estado vacío `Sin fuentes todavía`.
4. **Nota de acción pendiente** (§2.6) — al pie.

### 2.3 Tarjeta de acción "Revisar VODs nuevos"

- `.card` con título h2 `Revisar VODs nuevos` y texto secundario exacto: `Busca VODs nuevos de IlloJuan en YouTube, Twitch y Kick.`
- **Botón primario** atardecer (`--gradient-sunset`, blanco 600, `min-height: 44px`, radio 10 px, `box-shadow: var(--shadow-neon-sunset)`), texto exacto `Revisar VODs nuevos`, en `disabled`.
- **Nota bajo el botón (pill `.pill.warn` de ancho completo, siempre con texto):** `Pendiente: endpoint del Worker. La revisión de VODs nuevos hoy la hace GitHub Actions (cerebro, 09:00), no este panel.`
- No hay botón "Actualizar" separado: sin endpoint no hay nada que actualizar; un botón que solo repintara el vacío fingiría una carga. Cuando exista endpoint, el botón `Revisar VODs nuevos` dispara la revisión y la lista se recarga con `loadFuentes()`.
- Sin filtros por plataforma: con cero filas los filtros serían decoración. Se añaden cuando haya filas (ver §2.4, nota al pie).

### 2.4 Lista de fuentes: estado vacío + columnas del pipeline

Estado vacío real (único estado posible hoy), bloque `.empty-state`:

- **Título (h3):** `Sin fuentes todavía`
- **Texto exacto (`--color-text-dim`):** `Aún no hay fuentes procesadas. Cuando el Worker tenga endpoint, aquí verás cada VOD con su estado del pipeline.`
- Sin botón dentro del vacío: la acción ya está en §2.3.

Columnas del pipeline (diseño de la lista cuando haya filas; hoy no hay ninguna fila que pintar, así que las columnas se especifican sin datos):

| Columna | Contenido (cuando haya endpoint) | En móvil | Tokens |
|---|---|---|---|
| Fuente | `title` o `url` (título en `--color-text`, URL en `--color-text-dim` 13 px, `overflow-wrap: anywhere`) | Primera línea de la tarjeta | `--color-text` / `--color-text-dim` |
| Plataforma | YouTube / Twitch / Kick, con punto decorativo en su `--color-platform-*` | Chip junto al título | YT `#ff4e45`, TikTok `#25f4ee`, IG `#f77737`, FB `#60a5fa` (colores de plataforma verificados sobre tarjeta §8.2); para Twitch/Kick, punto violeta decorativo, nombre siempre en texto |
| Streamer | `streamer` (handle, ej. IlloJuan) | Línea secundaria | `--color-text-dim` |
| Estado del pipeline | Pill semántica con texto (ver vocabulario abajo) | Pill a la derecha del título | según estado |
| Progreso | `progress` 0–100, barra fina 6 px + número con `tabular-nums` | Barra bajo el título | fondo `--color-surface-input`; relleno por tramos como la barra de prioridad §8.3 (`<50 --color-bad`; `50–89` degradado `#fbbf24 → #fb923c`; `≥90` degradado `#f472b6 → #a78bfa`) |
| Nota | `status_note`, texto libre del pipeline | Última línea, 13 px | `--color-text-dim` |
| Actualizada | `updated_at` con formato de fecha del panel (`fmtDate`) | Pie de tarjeta, 12 px | `--color-text-dim` |

Vocabulario de estados del pipeline (diseño, no datos: ningún estado se muestra hoy porque no hay filas):

| Estado | Pill | Texto exacto de la pill |
|---|---|---|
| Pendiente | `.pill.neutral` | `Pendiente` |
| Revisando | `.pill.violet` | `Revisando` |
| Procesada | `.pill.ok` | `Procesada` |
| Con error | `.pill.bad` | `Con error` |

- El valor por defecto en D1 es `status = 'pendiente'` (migración 0002); la pill `Pendiente` es la que corresponde a una fuente recién creada. No se inventa ningún otro estado ni se traduce ningún valor crudo de BD sin mapearlo primero a esta tabla.
- En escritorio (≥ 641 px) la lista es una tabla envuelta en `overflow-x: auto`, con `th` en display condensado 12 px, 600, mayúsculas, `letter-spacing: .04em`, color `--color-neon-violet-text` `#c4b5fd` (8.84:1), filas con borde inferior `rgba(124,106,160,.4)`, hover de fila `rgba(236,72,153,.08)`, numéricos (progreso) a la derecha con `tabular-nums` (OVERRIDES §8.3). En ≤ 640 px cada fila es una tarjeta apilada (mismo patrón que la tarjeta de clip 9:16 en móvil, sin miniatura).

### 2.5 Tarjeta "Última revisión"

- `.card` con título h2 `Última revisión` (de VODs, según `vod_checks`: `ran_at, summary, findings`).
- Estado vacío real, texto exacto: `Sin revisiones todavía.`
- Texto secundario exacto (`--color-text-dim`, 13 px): `La detección de VODs nuevos hoy la hace GitHub Actions (cerebro, 09:00). Cuando el Worker tenga endpoint, aquí verás la fecha, el resumen y cuántos VODs encontró la última revisión.`
- No se muestra ninguna fecha, ningún resumen ni ningún número: `findings` es 0 por ausencia de datos, no un "0 VODs encontrados" que fingiría una revisión hecha. Cuando exista endpoint, esta tarjeta muestra tres líneas: `Fecha` (`ran_at`, formato del panel), `Resumen` (`summary`) y `VODs encontrados` (`findings`, número con `tabular-nums`).

### 2.6 Nota de acción pendiente (Fuentes)

Texto exacto, al pie de la pestaña, en `--color-text-dim` 13 px:

`Pendiente: endpoint del Worker para leer fuentes y revisiones de VODs (o seguir en GitHub Actions). Trabajo de backend, fuera de este panel.`

### 2.7 Estados de la pestaña Fuentes

| Estado | Qué se ve | Tokens |
|---|---|---|
| Carga | 2 skeletons (fondo `--color-surface-input`, `aria-busy="true"` en `#sourceList` y `#vodCheckCard`) | `--color-surface-input` |
| Vacío | `Sin fuentes todavía` (§2.4) + `Sin revisiones todavía` (§2.5) | texto `--color-text-dim` |
| Pendiente (sin endpoint) | Botón `Revisar VODs nuevos` en `disabled` + nota `Pendiente: endpoint del Worker` (§2.3) | `.pill.warn` (`--color-warn-bg` / `--color-warn-fg`); botón deshabilitado `opacity: .55` |
| Pipeline (futuro, sin filas hoy) | Pills `Pendiente` / `Revisando` / `Procesada` / `Con error` + barra de progreso por tramos (§2.4) | `.pill.neutral` (fondo `--color-surface-input`, texto `--color-text-dim`), `.pill.violet` (`#ede9fe` / `#4c1d95`), `.pill.ok`, `.pill.bad` (`--color-bad-bg` `#7f1d1d` / `--color-bad-fg` `#fecaca`) |
| Error | No aplica hoy: sin `fetch` no hay error de red. En el futuro, patrón de Cola (`No se pudo cargar las fuentes.` + `Reintentar`) | `.msg` error |

## 3. Tokens GTA 6 por componente (resumen para el implementador)

Variante activa con `<html data-theme="gta6">` + `tokens-gta6.css` DESPUÉS de `tokens.css`. Sin tema, el panel sigue siendo la base OVERRIDES §§1–7. Cero hex fuera de los tokens; neones crudos (`#ff2e88`, `#ff5e00`, `#ec4899`, `#db2777`, `#ea580c`) solo en degradados/bordes/brillos, nunca como `color` de texto.

| Componente | Tokens GTA 6 |
|---|---|
| Página | fondo `--color-background` `#100a1a`; texto `--color-text` `#e8eaf0` (16.16:1 sobre fondo) |
| Tarjeta (`.card`) | fondo `--color-surface` `#261a38`, borde 1 px `--color-border` `#7c6aa0` (3.44:1 sobre tarjeta), radio `--radius-card` 16 px, padding `--space-4` 16 px, `box-shadow: var(--shadow-card)`; hover `var(--shadow-elevated)` |
| Título h2 de tarjeta | `--font-display` (Barlow Condensed, fallback Arial Narrow / system-ui), 18 px, `line-height: 1.1`, mayúsculas; solo el título de §1.4 lleva gradiente de texto `--gradient-sunset-text` con fallback `#f9a8d4` |
| Título h3 (nombres, vacíos) | `--font-display`, 14 px, `line-height: 1.1`, mayúsculas |
| Texto secundario | `--color-text-dim` `#b8b3c9` (8.02:1 sobre tarjeta), 13 px (`--font-size-small`) |
| Botón primario | fondo `--gradient-sunset` (`135deg #6d28d9 → #9d174d → #c2410c`, peor parada 5.18:1 con blanco), texto blanco 600, `min-height: 44px`, radio `--radius-control` 10 px, `box-shadow: var(--shadow-neon-sunset)`; hover `--gradient-sunset-hover`; disabled `opacity: .55` + texto de carga si aplica |
| Botón secundario (ghost / Eliminar) | fondo transparente, borde 1 px `--color-border`, texto `--color-text`; hover borde `--color-neon-pink-text` `#f9a8d4` + brillo 1.12 |
| Pestaña activa | fondo `--gradient-sunset`, texto blanco 700, barra inferior 3 px `--color-neon-amber` `#fbbf24`, `aria-selected="true"` |
| Pills | `.pill.ok` (`#bbf7d0` / `#14532d`), `.pill.bad` (`#fecaca` / `#7f1d1d`), `.pill.warn` (`#fde68a` / `#451a03`), `.pill.neutral` (fondo `--color-surface-input` `#150d24`, texto `--color-text-dim`), `.pill.sunset` (`#fbcfe8` / `#500724`, 10.88:1), `.pill.violet` (`#ede9fe` / `#4c1d95`, 9.23:1), `.pill.ember` (`#ffedd5` / `#7c2d12`, 8.18:1); `padding: 2px 9px`, radio 999 px, 12 px, 600, siempre con texto |
| Input / select / textarea | fondo `--color-surface-input` `#150d24`, borde `--color-border`, texto `--color-text`, `font-size: 16px`, `min-height: 44px`, radio 10 px; focus borde `--color-neon-pink-text` + outline 3 px `--color-focus` `#fbbf24`, offset 2 px |
| Toggle ON | carril `--gradient-sunset`, pomo blanco; disabled `opacity: .55`, `cursor: not-allowed` |
| Barra de progreso | fondo `--color-surface-input`, altura 6 px, radio 999 px; relleno por tramos §2.4 (≥90 con `var(--shadow-neon-pink)`) |
| Tabla (escritorio, Fuentes) | `th` `--font-display` 12 px, 600, mayúsculas, `letter-spacing: .04em`, `--color-neon-violet-text` `#c4b5fd`; filas borde inferior `rgba(124,106,160,.4)`, hover `rgba(236,72,153,.08)` |
| Foco visible (global) | `:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }` en `button, input, textarea, select, a` |
| Motion | 150–250 ms, `ease-out`; `button:active scale(.98)`; `prefers-reduced-motion: reduce` anula transiciones/animaciones y `scrollIntoView` pasa a `behavior: auto` |
| Safe-area | `padding` con `env(safe-area-inset-*)` en `body`/`main` (regla base, sin cambio) |

## 4. Textos exactos en español (tabla única, copiar literal)

| Dónde | Texto exacto |
|---|---|
| Nav | `Influencers` · `Fuentes` |
| Influencers, regla | `Regla de lanzamiento` / `Solo IlloJuan está activo con permiso (Twitch). Cualquier otro influencer queda desactivado.` / `Esta regla manda sobre cualquier otro influencer que se añada después.` |
| Influencers, tarjeta | `IlloJuan` / `Twitch · Español` / `Configuración del proyecto` / `Dato de configuración del proyecto. No viene del Worker: aún no hay endpoint de influencers.` / `Permiso: sí` / `Activo: sí` / `En lanzamiento` / `Plataforma: Twitch` |
| Influencers, toggles | `Permiso para clipear` / `Activo en la cola` / `Pendiente: endpoint del Worker` |
| Influencers, eliminar | `Eliminar` |
| Influencers, vacío | `Sin más influencers` / `No hay más streamers en la lista. Añade uno con el formulario de abajo cuando el Worker tenga endpoint para guardarlo.` |
| Influencers, formulario | `Añadir influencer` (summary y botón) / `Nombre o handle` / `Plataforma` / `URL del perfil` / `Idioma` / `Tiene permiso para clipear` / `Activo en la cola` / `Notas` / `Pendiente: endpoint del Worker. Este formulario no guarda todavía.` |
| Influencers, pie | `Pendiente: endpoint del Worker para leer, crear, editar y eliminar influencers. Trabajo de backend, fuera de este panel.` |
| Fuentes, acción | `Revisar VODs nuevos` (título y botón) / `Busca VODs nuevos de IlloJuan en YouTube, Twitch y Kick.` / `Pendiente: endpoint del Worker. La revisión de VODs nuevos hoy la hace GitHub Actions (cerebro, 09:00), no este panel.` |
| Fuentes, vacío | `Sin fuentes todavía` / `Aún no hay fuentes procesadas. Cuando el Worker tenga endpoint, aquí verás cada VOD con su estado del pipeline.` |
| Fuentes, última revisión | `Última revisión` / `Sin revisiones todavía.` / `La detección de VODs nuevos hoy la hace GitHub Actions (cerebro, 09:00). Cuando el Worker tenga endpoint, aquí verás la fecha, el resumen y cuántos VODs encontró la última revisión.` |
| Fuentes, pipeline | `Pendiente` / `Revisando` / `Procesada` / `Con error` / `VODs encontrados` |
| Fuentes, pie | `Pendiente: endpoint del Worker para leer fuentes y revisiones de VODs (o seguir en GitHub Actions). Trabajo de backend, fuera de este panel.` |
| Error futuro (ambas) | `No se pudo cargar la lista.` / `No se pudo cargar las fuentes.` / `Reintentar` |

Notas de redacción: "VODs" se escribe así en todo el panel (coherente con `vod_checks` y con el inventario). "Revisar VODs nuevos" es la acción; "Última revisión" es el historial. No se usa "streamer" como título de sección (solo como columna y en el vacío de influencers, donde el usuario ya lo usa); el handle visible es `IlloJuan`.

## 5. Ids nuevos que necesita el desarrollador

Nuevos en `index.html` (estáticos; no renombrar ni eliminar ninguno de los 62 existentes del inventario §3.2):

| Id | Elemento |
|---|---|
| `tabbtn-influencers` | Botón de pestaña Influencers (`role="tab"`, `aria-controls="tab-influencers"`, `data-tab="influencers"`) |
| `tab-influencers` | Sección Influencers (`role="tabpanel"`, `aria-labelledby="tabbtn-influencers"`) |
| `tabbtn-fuentes` | Botón de pestaña Fuentes (`role="tab"`, `aria-controls="tab-fuentes"`, `data-tab="fuentes"`) |
| `tab-fuentes` | Sección Fuentes (`role="tabpanel"`, `aria-labelledby="tabbtn-fuentes"`) |
| `influencerRule` | Tarjeta de regla de lanzamiento (§1.4) |
| `influencerList` | Contenedor de la lista de influencers (tarjeta IlloJuan + vacío) |
| `influencerEmpty` | Bloque vacío `Sin más influencers` (§1.5) |
| `infTogglePermission` | Toggle `Permiso para clipear` de IlloJuan (disabled) |
| `infToggleActive` | Toggle `Activo en la cola` de IlloJuan (disabled) |
| `infDelete` | Botón `Eliminar` de IlloJuan (disabled) |
| `infPendingNote` | Nota `Pendiente: endpoint del Worker` bajo la tarjeta de IlloJuan |
| `influencerFormDetails` | `<details>` del formulario Añadir influencer |
| `influencerForm` | `<form novalidate>` Añadir influencer |
| `inf-name` | Input Nombre o handle |
| `inf-name-error` | Error del campo nombre (`role="alert"`, `hidden`) |
| `inf-platform` | Select Plataforma |
| `inf-profile-url` | Input URL del perfil |
| `inf-language` | Select Idioma |
| `inf-permission` | Checkbox Tiene permiso para clipear |
| `inf-active` | Checkbox Activo en la cola |
| `inf-notes` | Textarea Notas |
| `infSubmit` | Botón `Añadir influencer` (disabled) |
| `infFormNote` | Nota `Pendiente: endpoint del Worker. Este formulario no guarda todavía.` |
| `btnCheckVods` | Botón `Revisar VODs nuevos` (disabled) |
| `vodCheckNote` | Nota pendiente bajo el botón (§2.3) |
| `vodCheckCard` | Tarjeta `Última revisión` (§2.5) |
| `vodCheckEmpty` | Texto `Sin revisiones todavía.` dentro de la tarjeta |
| `sourceList` | Contenedor de la lista de fuentes |
| `sourceEmpty` | Bloque vacío `Sin fuentes todavía` (§2.4) |
| `fuentesPendingNote` | Nota de acción pendiente al pie de Fuentes (§2.6) |

Dinámicos (futuros, cuando exista endpoint; se crean por `innerHTML` en su función, como `cred-<id>` en Cuentas — no existen en `index.html`): por influencer `inf-card-<id>`, `inf-toggle-permission-<id>`, `inf-toggle-active-<id>`, `inf-delete-<id>`; por fuente `src-row-<id>` / `src-card-<id>`. El patrón de nombres sigue al de Cuentas (`cred-<id>`, `extra-<id>`).

En `app.js` (sin tocar endpoints ni `TAB_NAMES` de las 5 pestañas salvo añadir las dos nuevas):

- `TAB_NAMES`: añadir `"influencers"` y `"fuentes"` (orden: después de `"cuentas"`).
- `loadTab()`: dos ramas — `if (name === "influencers") await loadInfluencers();` / `if (name === "fuentes") await loadFuentes();`.
- `loadInfluencers()` / `loadFuentes()`: hoy solo pintan el contenido estático de §§1–2 (sin `fetch`); el día que exista endpoint, la misma función hace el `fetch` y sustituye el vacío por las tarjetas/filas. No se crea ninguna otra función de red.
- No se toca: base `API`, `credentials: "include"`, `switchTab`, `publishGate`, `seoForPlatform`, `scoreClass`, `STATUS_MAP` / `PLAT_STATE_MAP`, ni los 62 ids estáticos existentes.

## 6. Qué NO se diseña (límites explícitos)

- Sin estadísticas de influencers (seguidores, clips, vistas): no existen en ninguna fuente.
- Sin lista de VODs detectados, sin fechas de VOD, sin miniaturas: no hay endpoint ni datos.
- Sin filtros, buscador ni ordenación en Fuentes: con cero filas serían decoración; se diseñan cuando haya filas.
- Sin confirmación de borrado ni edición inline de influencers: sin endpoint no hay acción que confirmar.
- Sin verificación de permiso en el panel: el permiso lo verifica el puente, no esta pestaña.
- Sin contar "0 influencers" ni "0 fuentes" como cifra en un encabezado: el vacío se comunica con el texto de §§1.5/2.4, no con un contador que fingiría una lectura hecha.
- Sin nombres personales: solo `IlloJuan` (handle público) y los usernames de tenant `seba` / `chacal` (que ya usa `#whoLine`). Verificar con `grep -riE` de nombres personales en `dist/` = 0 tras implementar, como exige el inventario §4.

## 7. Checklist de QA para el desarrollador (antes de dar por hecho el diseño)

- [ ] `node --check frontend/dist/app.js` OK; las 7 pestañas navegan por hash (`#influencers`, `#fuentes`) y `switchTab` no rompe las 5 existentes.
- [ ] A 360 px: sin scroll horizontal; tarjetas apiladas; botones e inputs con `min-height: 44px`; inputs a 16 px.
- [ ] Toggles, botón Eliminar, botón Añadir y botón Revisar VODs nuevos en `disabled`, cada uno con su nota `Pendiente: endpoint del Worker` visible.
- [ ] Ningún dato inventado: la pestaña Influencers muestra solo IlloJuan (configuración) + `Sin más influencers`; Fuentes muestra solo `Sin fuentes todavía` + `Sin revisiones todavía`.
- [ ] Estados como pills con texto, nunca solo color; foco visible 3 px `--color-focus` en todos los controles.
- [ ] Cero hex fuera de `tokens.css` / `tokens-gta6.css` en `index.html` y `app.js` (salvo `theme-color`, que con el tema GTA 6 pasa a `#100a1a`).
- [ ] `grep` de nombres personales en `dist/` = 0; solo `IlloJuan`, `seba`, `chacal`.
- [ ] `prefers-reduced-motion` respetado (sin shimmer ni transiciones).
- [ ] Verificación en vivo pendiente (la hace el coordinador): abrir el panel, entrar, recorrer las 7 pestañas a 360 px y en escritorio, y comprobar que Influencers y Fuentes no disparan ningún `fetch` (pestaña Red del navegador: cero llamadas a `/api/influencers` o `/api/sources`, que no existen).
