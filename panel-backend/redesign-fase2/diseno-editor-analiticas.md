# Diseño — Editor de clip + Analíticas (panel AVC, fase 2)

- **Fecha:** 2026-11-17
- **Rol:** Rol 5 de 10 — Diseñador Editor de clip + Analíticas. **Solo especificación; no se escribió código de implementación.**
- **Alcance:** únicamente las dos pestañas nuevas, Editor (`#tab-editor`) y Analíticas (`#tab-analiticas`). Nada de Worker, Cola (salvo el punto de entrada), Cuentas, Avisos, Ajustes, login ni header.
- **Fuentes leídas:**
  - `redesign-fase2/inventario.md` (mapa endpoint-por-endpoint; autoritativo para qué existe y qué no).
  - `design-system/avc-panel/OVERRIDES.md` §§1–7 (base) y §8 (variante GTA 6 / Vice City, autoritativa para el tema).
  - `design-system/avc-panel/tokens-gta6.css` y `tokens-gta6.json`.
  - `frontend/dist/index.html` y `frontend/dist/app.js` (pestaña Cola actual, ids que no se rompen).
  - `worker/src/index.ts` y `worker/src/jobs.ts` (endpoints reales de clips y `publishGate`).
  - `migrations/schema.sql` (campos reales de `clips`, `clip_previews`, `clip_stats`).
- **Regla de oro de este documento:** todo lo que se muestra sale de datos que la API ya devuelve hoy. Si un dato no existe, la pestaña muestra **estado vacío real + acción pendiente**; no se inventa endpoint, campo, valor, clip, preview ni medición.

## 0. Realidad que manda en este diseño (del inventario, verificada en código)

| Hecho | Fuente | Consecuencia de diseño |
|---|---|---|
| `publishGate` frena todo clip con `preview_seen = 0` con el motivo «falta ver el preview en el Editor» | `worker/src/jobs.ts:148` | El Editor existe para levantar ese freno; mientras no haya endpoint que escriba `preview_seen`, el freno seguirá apareciendo. El diseño lo muestra, no lo oculta. |
| **Ningún endpoint escribe `preview_seen`** (ni `clip_previews`, ni `captions`, ni `PATCH /api/clips/:id`) | inventario §2.6; `grep` en `worker/src/` | El botón «Marcar preview vista» nace **deshabilitado y marcado como pendiente**. Ver el vídeo no marca nada: solo el botón (cuando exista el endpoint) lo haría. |
| `GET /api/clips/:id` devuelve la fila completa del clip | inventario §2.1 nº 12 | El Editor se alimenta solo de esa fila: `title`, `start_sec`, `end_sec`, `score`, `subtitle_style`, `layout_mode`, `captions` (JSON string), `seo_title`, `seo_description`, `seo_tags`, `platforms`, `preview_seen`, `viral` (JSON string), `seo_overrides` (JSON string). Campos vacíos = estado vacío, nunca valor inventado. |
| `GET /api/clips/:id/video` devuelve los bytes del MP4 desde R2 (o 404 si el clip no tiene MP4) | inventario §2.1 nº 16 | El preview reproduce el MP4 **real** del clip. No hay miniatura generada ni frame inventado: sin MP4, el escenario muestra su vacío real. |
| `POST /api/clips/:id/publish` existe y devuelve `blocked[]` | inventario §2.1 nº 14 | El botón Publicar del Editor usa ese endpoint, sin cuerpo, igual que `#btnPublish` en Cola. No se crea otra vía de publicación. |
| `publishGate` de duración: `end_sec - start_sec` fuera de 15–90 s frena | `worker/src/jobs.ts` | La regla de duración del panel (abajo, §2.4) se muestra **encima** del gate del Worker, sin tocarlo. |
| **Analíticas: SIN ENDPOINT.** Tabla `clip_stats` existe en D1 (`clip_id, platform, views, retention_pct, shares, saves, comments, new_subs, window_label, recorded_at`) sin ninguna ruta | inventario §2.7; `migrations/schema.sql` | La pestaña nace **vacía de verdad**: «Sin mediciones todavía» en todas sus secciones, con el formulario de pegar mediciones como acción pendiente. |
| Los jobs `kind='metrics'` existen como tipo en `jobs.ts`, pero no hay endpoint que los liste ni que devuelva métricas | inventario §2.7 | No se muestra ninguna métrica «del Worker». Solo lo que el usuario pegue o la API devuelva cuando exista endpoint. |

**Lo que NO cambia (prohibido tocar):** `TAB_NAMES` y el hash salvo añadir las dos pestañas nuevas (§1); los 62 ids estáticos del §3.2 del inventario; la base `API`; método, ruta y cuerpo de los 20 endpoints usados; `publishGate`, `seoForPlatform`, `scoreClass` (tramos <50 / 50–89 / ≥90), `STATUS_MAP` / `PLAT_STATE_MAP`; fórmulas de viralidad/SEO; orden de la Cola (lo da la API). Sin nombres personales visibles: solo los usernames `seba` / `chacal` donde el front ya los usa (`#whoLine`).

---

## 1. Integración en la navegación (las dos pestañas nuevas)

Móvil primero: las pestañas nuevas se añaden al mismo `nav#tabs` existente, con el mismo patrón que las cinco actuales. En móvil el `nav` sigue siendo sticky (`top: 0`, `z-index: 10`, fondo `--color-background`) y con scroll horizontal propio si hacen falta siete botones; nunca se aprietan los botones por debajo de 44 px de alto ni se rompe el ancho mínimo táctil.

| Elemento | Texto exacto | Token GTA 6 |
|---|---|---|
| Botón de pestaña Editor | `Editor` | Pestaña: texto `--color-text-dim` (`#b8b3c9`), `font-family: var(--font-display)`, 600, mayúsculas, `letter-spacing: .02em`, `min-height: 44px`. Activa: fondo `--gradient-sunset`, texto blanco 700, `aria-selected="true"`, barra inferior 3 px `--color-neon-amber` (`#fbbf24`). Nunca solo color. |
| Botón de pestaña Analíticas | `Analíticas` | Igual que Editor. |
| Secciones | `#tab-editor`, `#tab-analiticas`, `role="tabpanel"`, `aria-labelledby` con su botón | Fondo página `--color-background` (`#100a1a`). |

**Punto de entrada al Editor (único):** desde el detalle de Cola. En `#clipDetailCard`, junto a «Cerrar detalle», botón secundario «Abrir en el Editor» (id nuevo `btnOpenEditor`, §5). Abre `#editor` con el clip ya seleccionado (`selectedClipId`, el mismo que usa `selectClip`). No hay selector de clip dentro del Editor: el clip llega desde la Cola.

**Entrada directa por hash (`#editor` / `#analiticas`) sin clip seleccionado:** el Editor muestra su vacío real (§2.1). Analíticas no necesita clip seleccionado: muestra todas las plataformas (§3).

---

## 2. Editor de clip 9:16 (`#tab-editor`)

### 2.1 Estructura móvil-primero (≤ 640 px) y escritorio

Orden vertical en móvil, de arriba a abajo, sin scroll horizontal a 360 px:

1. Encabezado de tarjeta: título del clip + pill de preview (§2.6).
2. Escenario 9:16 (§2.2).
3. Controles de reproducción (§2.2).
4. Duración y regla de oro (§2.4).
5. Subtítulos (§2.5).
6. Título y SEO por plataforma (§2.3).
7. Bloque de publicación: preview + Publicar (§2.6).
8. Enlace «Volver a la Cola».

En escritorio (≥ 641 px): dos columnas. Izquierda, escenario 9:16 fijo (ancho máximo 400 px, centrado en su columna). Derecha, duración, subtítulos, SEO por plataforma y publicación, en ese orden. La tarjeta es `.card` GTA 6: fondo `--color-surface` (`#261a38`), borde 1 px `--color-border` (`#7c6aa0`), radio `--radius-card: 16px`, padding `--space-4` (16 px), `box-shadow: var(--shadow-card)`.

**Vacío real — sin clip seleccionado:** tarjeta con título «Editor de clip» y texto exacto: «Elige un clip en la Cola para editarlo. El Editor se abre desde el detalle de un clip.» + botón «Ir a la Cola» (secundario, lleva a `#cola`). Nada de clip de ejemplo, nada de preview de ejemplo.

**Vacío real — clip sin MP4** (`GET /api/clips/:id/video` responde 404): el escenario muestra el placeholder degradado `linear-gradient(180deg, #3b0764 → #500724 → #150d24)` (token `.clip-thumb-placeholder` de `tokens-gta6.css`) con el texto exacto «Sin MP4 todavía» y debajo: «Sube el MP4 desde la Cola para ver el preview aquí.» + botón «Ir a la Cola». Los controles de reproducción quedan deshabilitados. La duración, los subtítulos y el SEO se siguen mostrando (vienen de la fila del clip, no del MP4).

### 2.2 Escenario 9:16 — juego arriba, cámara abajo

El escenario es un marco vertical `aspect-ratio: 9 / 16`, ancho 100% hasta 400 px, fondo `--color-surface-input` (`#150d24`), borde 1 px `--color-border`, radio `--radius-card: 16px`. Dentro, dos zonas fijas, sin solaparse:

| Zona | Posición en el marco | Contenido | Token |
|---|---|---|---|
| Juego | Arriba, ~70% de la altura | El vídeo MP4 real del clip (`GET /api/clips/:id/video`). Sin MP4: placeholder degradado (§2.1). | Vídeo a ancho completo de la zona; fondo `--color-surface-input`. |
| Cámara | Abajo, ~30% de la altura | El mismo vídeo (la cámara forma parte del MP4 renderizado). La zona lleva una etiqueta fija «Cámara» en su esquina superior izquierda. | Etiqueta: pill `.pill.neutral` (fondo `--color-surface-input`, texto `--color-text-dim` `#b8b3c9`), 12 px, 600. |

**Reglas de layout que el escenario hace visibles (no negociables en el diseño):**

- El juego nunca se recorta para que quepa la cámara: si el juego no se ve, la cámara se achica, nunca al revés.
- Nada tapa el minimapa ni el HUD: el escenario lleva guías de zona segura opcionales (botón «Ver zonas seguras», id `edSafeToggle`), que dibujan un marco interior con borde discontinuo `--color-neon-amber` (`#fbbf24`, decorativo, nunca único indicador) y el texto «Zona segura» en `--color-text-dim`. Las guías son estáticas de layout, no datos del clip.
- Los subtítulos quemados van **encima de la cámara, sin tapar la cara**: banda de subtítulos justo sobre la zona de cámara, nunca sobre ella. En el escenario, la banda es una franja con fondo semitransparente `--color-surface-input` y texto en `--color-text` (`#e8eaf0`), centrada. Si el clip no tiene `captions`, la banda muestra «Sin subtítulos guardados» en `--color-text-dim`.

**Controles de reproducción** (debajo del escenario, en una fila):

| Control | Texto / comportamiento | Token |
|---|---|---|
| Reproducir / Pausar | Botón primario con texto «Reproducir» / «Pausa» (texto, no solo icono; SVG inline `aria-hidden="true"` si lleva icono). `min-height: 44px`. | Primario: fondo `--gradient-sunset`, texto blanco, radio 10 px, `box-shadow: var(--shadow-neon-sunset)`. |
| Tiempo | Texto «0:12 / 0:40» (tiempo actual / duración del archivo, leído del propio MP4; si no hay MP4: «— / —»). Números con `font-variant-numeric: tabular-nums`. | Texto `--color-text`, 13 px (`--font-size-small`). |
| Silencio | Botón secundario «Silencio» / «Sonido». | Secundario: fondo transparente, borde `--color-border`, texto `--color-text`. Hover: borde `--color-neon-pink-text` (`#f9a8d4`). |
| Zonas seguras | Botón secundario «Ver zonas seguras» / «Ocultar zonas seguras». | Secundario, igual que Silencio. |

Sin barra de progreso inventada: si el reproductor la tiene, es la del propio elemento de vídeo. Sin contador de visitas ni métricas en el Editor.

### 2.3 Título y SEO por plataforma

Una tarjeta por plataforma del clip (`platforms` de la fila; orden fijo: YouTube, TikTok, Instagram, Facebook — el mismo `PLATFORMS_ORDER` de `app.js`). Cada tarjeta muestra **solo** lo que devuelve `GET /api/clips/:id`:

| Campo mostrado | Origen real | Si está vacío |
|---|---|---|
| Título SEO | `seo_title` (si vacío, `title`) | «Sin título SEO» en `--color-text-dim`. |
| Descripción | `seo_description` | Fila oculta; texto «Sin descripción» en `--color-text-dim`. |
| Tags | `seo_tags` | «Sin tags» en `--color-text-dim`. |
| Puntos SEO de esa plataforma | `viral.platforms[plataforma].score` (JSON parseado; `null` = sin medir) | Pill `.pill.neutral` con texto «SEO sin medir». Nunca un 0. |
| Override SEO | `seo_overrides` contiene la plataforma | Pill `.pill.sunset` (`#fbcfe8` sobre `#500724`) con texto «Override SEO». Si no: nada. |
| Estado en esa plataforma | `publish_states[plataforma]` (mismo `PLAT_STATE_MAP` que Cola) | Pill según el mapa existente (Pendiente / En cola / Publicado / Fallido / Frenado). |

**Edición: pendiente.** Los campos se muestran en lectura. No hay inputs editables de título, descripción ni tags en esta fase, porque no existe `PATCH /api/clips/:id` ni ningún endpoint que guarde esos cambios. Debajo del bloque, nota fija con texto exacto: «Aún no se puede editar el título ni el SEO desde el panel: falta el endpoint para guardar cambios en el clip.» (estilo `.dim`, `--color-text-dim`). Cuando exista el endpoint, estos mismos campos pasan a ser inputs de 16 px con `min-height: 44px`; el diseño no cambia de sitio.

Título de la sección: `Título y SEO por plataforma` (`h2`, `--font-display`, 18 px, mayúsculas, `line-height: 1.1`).

### 2.4 Duración y regla de oro

La duración se calcula de la fila del clip: `end_sec - start_sec` (el mismo cálculo de `publishGate`). Se muestra en grande, con `font-variant-numeric: tabular-nums`, y con una pill de estado. Texto exacto del encabezado: «Duración del clip».

| Banda (solo visualización; la lógica no se toca) | Pill | Texto exacto bajo el número |
|---|---|---|
| 36–60 s | `.pill.ok` «En rango» | «Dentro del objetivo: mínimo 36 s, objetivo 40 s, tope 60 s.» |
| 25–35 s (incluye exactamente 30 s) | `.pill.bad` «Prohibido» | «Regla de oro: nunca 30 segundos. El rango 25–35 s está prohibido.» |
| 15–24 s o 61–90 s | `.pill.warn` «Fuera del objetivo» | «Fuera del objetivo de 36–60 s, pero dentro de la franja del Worker (15–90 s).» |
| Menos de 15 s o más de 90 s | `.pill.bad` «El Worker lo frena» | «Fuera de 15–90 s: el Worker frena la publicación por duración.» |
| `end_sec - start_sec` es 0 o no hay datos | `.pill.neutral` «Sin duración» | «Sin duración guardada en el clip.» |

Debajo, una regla visual fija (no interactiva) con las marcas 15 – 25 – 30 – 35 – 36 – 40 – 60 – 90, con el tramo 25–35 marcado en `--color-bad-bg` (`#7f1d1d`) y el tramo 36–60 en `--color-ok` (`#22c55e`). La marca 30 lleva la etiqueta «30 s — prohibido». Es una guía estática, no un control: la duración no se edita aquí (no hay endpoint).

### 2.5 Subtítulos

Tarjeta «Subtítulos» (`h2`, display 18 px). Contenido, solo desde la fila del clip:

| Elemento | Origen real | Si está vacío |
|---|---|---|
| Estilo | `subtitle_style` (`tiktok` / `highlight` / `karaoke`; por defecto `tiktok` en el esquema) | «Sin estilo guardado» en `--color-text-dim`. Se muestra como pill `.pill.violet` (`#ede9fe` sobre `#4c1d95`) con el nombre del estilo. |
| Subtítulos guardados | `captions` (JSON string; líneas con texto) | Lista de líneas en lectura (texto `--color-text`, 16 px). Si vacío: «Sin subtítulos guardados.» |
| Layout | `layout_mode` (`auto` por defecto) | Texto «Layout: automático» (o el valor guardado) en `--color-text-dim`. |

Selector de estilo: se diseña como tres botones de opción (TikTok / Highlight / Karaoke, `min-height: 44px` cada uno, opción activa con fondo `--gradient-sunset` y texto blanco), **pero deshabilitados en esta fase** con la nota exacta: «Aún no se puede cambiar el estilo desde el panel: falta el endpoint para guardar cambios en el clip.» Igual que §2.3: el control ya está diseñado en su sitio; solo le falta el endpoint.

Nota fija sobre la banda de subtítulos: «Los subtítulos van quemados encima de la cámara, sin tapar la cara.»

### 2.6 Vista previa y publicación — el freno `preview_seen`

Este es el bloque crítico del Editor. Encabezado: «Vista previa antes de publicar» (`h2`).

| Elemento | Estado hoy (sin endpoint) | Texto exacto |
|---|---|---|
| Pill de preview | `preview_seen = 0` → `.pill.warn` «Preview no vista». `preview_seen = 1` → `.pill.ok` «Preview vista». El valor sale de `GET /api/clips/:id`. | Debajo: «Tienes que ver el preview en este Editor antes de publicar. Sin preview vista, el Worker frena la publicación.» |
| Botón «Marcar preview vista» | **Deshabilitado, siempre, hasta que exista el endpoint.** `disabled` + `opacity: .55` + `cursor: not-allowed` (OVERRIDES §1, UX-3). No hace llamada, no escribe en D1, no finge que marca nada. | Nota junto al botón, con `role="status"`: «Pendiente: aún no existe el endpoint para guardar que viste el preview. Este botón se activará cuando exista.» |
| Botón «Publicar ahora» | **Deshabilitado mientras `preview_seen = 0`**, con el motivo visible debajo (nunca solo color, nunca solo deshabilitado sin explicar por qué). Cuando `preview_seen = 1`, habilitado y llama a `POST /api/clips/:id/publish` (sin cuerpo), igual que `#btnPublish`; durante la llamada, `disabled` + texto «Publicando…». | Motivo visible mientras está bloqueado: «Publicar está bloqueado: falta ver el preview en el Editor.» Si además `POST /api/clips/:id/publish` devolvió frenos (`blocked[]`), se muestran como chips de freno (`.pill.warn` / `.pill.bad` con el motivo en lenguaje claro, igual que `#clipBrakes` en Cola). |

**Aclaración honesta que el diseño deja por escrito:** ver el vídeo completo en el escenario **no** marca el preview como visto. Hoy no hay ninguna acción en el panel que levante ese freno: ni ver el vídeo, ni subir el MP4, ni pulsar Publicar. El freno «falta ver el preview en el Editor» seguirá apareciendo al intentar publicar hasta que el backend cree el endpoint. El front no lo oculta ni lo reescribe.

**Acción pendiente (backend, fuera de `frontend/dist/`):** endpoint que registre el preview visto (por ejemplo, marcar `preview_seen = 1` para el clip y el usuario `seba` / `chacal` que lo vio) y, después, el editor real con guardado de título, SEO, estilo de subtítulos y duración.

---

## 3. Analíticas (`#tab-analiticas`)

### 3.1 Estructura móvil-primero

Orden vertical en móvil, sin scroll horizontal a 360 px:

1. Encabezado «Analíticas» + subtítulo (§3.2).
2. Resumen por plataforma: cuatro tarjetas, una por plataforma (§3.3).
3. Ranking por plataforma (§3.4).
4. Clips sin medición (§3.5).
5. Formulario para pegar mediciones (§3.6).

En escritorio: las cuatro tarjetas de resumen en fila de 4 (o 2×2); el ranking por plataforma en tabla (§3.4); el formulario en una columna lateral o debajo, a ancho máximo 560 px.

### 3.2 Encabezado y estado vacío general

Título: `Analíticas` (`h2`, `--font-display`, 18 px, mayúsculas). Subtítulo exacto: «Vistas y retención reales por plataforma y clip. Solo lo medido: nada inventado.»

**Estado vacío general (el estado real de hoy):** como no existe endpoint y no hay ninguna medición guardada, la pestaña abre con una tarjeta de vacío con texto exacto:

> «Sin mediciones todavía.»
> «Aún no hay vistas ni retención guardadas para ningún clip. Cuando pegues la primera medición (abajo), aparecerá aquí el ranking por plataforma.»

Botón secundario en el vacío: «Pegar una medición» (lleva el foco al formulario, id `anPasteForm`, §3.6). No hay cifras, no hay gráficos con datos de ejemplo, no hay «0 vistas» como si fuera un dato: **sin medición se muestra «sin medición», nunca 0**.

### 3.3 Resumen por plataforma — cuatro tarjetas

Una tarjeta por plataforma (YouTube, TikTok, Instagram, Facebook), siempre en el mismo orden. Cada tarjeta:

| Elemento | Token GTA 6 |
|---|---|
| Nombre de plataforma + punto de color | Punto 8 px (`.dot`) en `--color-platform-youtube` (`#ff4e45`) / `--color-platform-tiktok` (`#25f4ee`) / `--color-platform-instagram` (`#f77737`) / `--color-platform-facebook` (`#60a5fa`); nombre en `--color-text`, display 14 px, mayúsculas. |
| Mediana de vistas | Número grande en `--color-text` (`#e8eaf0`), `font-variant-numeric: tabular-nums`; etiqueta «Mediana de vistas» en `--color-text-dim` (`#b8b3c9`), 12 px. |
| Mediana de retención | Número con «%» en `--color-text`; etiqueta «Mediana de retención» en `--color-text-dim`. |
| Clips medidos | Texto «3 de 12 clips medidos» (números reales cuando haya datos) en `--color-text-dim`, 13 px. |

**Hoy (sin datos):** cada tarjeta muestra «Sin mediciones todavía» en lugar de las medianas, y «0 de N clips medidos» solo si `GET /api/clips` devuelve N clips publicados en esa plataforma (N real; si no hay clips, «Sin clips en esta plataforma»). La mediana con menos de 1 medición no existe: se muestra «—», nunca 0.

**Definición de mediana (solo visualización, para cuando haya datos):** mediana de `views` y de `retention_pct` entre los clips medidos de esa plataforma, en la ventana guardada (`window_label`, por defecto `7d`). Si hay mediciones en varias ventanas, se muestra la ventana más reciente por clip. No se promedia con clips sin medición: los sin medición no entran en la mediana.

### 3.4 Ranking por plataforma

Un bloque por plataforma con su propio ranking. Título de bloque: `Ranking — YouTube` (y así con cada plataforma; `h3`, 14 px, display, mayúsculas).

| Columna (escritorio, tabla) | Contenido |
|---|---|
| # | Posición (1, 2, 3…), `tabular-nums`, alineada a la derecha. |
| Clip | Título del clip (de `GET /api/clips`). |
| Vistas | `views` real, `tabular-nums`, a la derecha. |
| Retención | `retention_pct` real con «%», a la derecha. |
| Guardado | Fecha de `recorded_at` (hora de Chile, mismo formato que el resto del panel). |

Orden: por vistas, de mayor a menor, dentro de cada plataforma. Empate en vistas: por retención, de mayor a menor. En móvil (≤ 640 px): cada fila es una tarjeta apilada (título arriba, vistas y retención en una fila con etiquetas, sin tabla ni scroll horizontal).

**Sin datos:** cada bloque de ranking muestra su vacío propio con texto exacto: «Sin mediciones todavía en YouTube.» (y así por plataforma). El bloque no se oculta: se ve vacío, para que se entienda que la plataforma existe y solo le faltan datos.

**Datos que el ranking muestra cuando existan (campos reales de `clip_stats`):** `views`, `retención %`, `shares`, `saves`, `comments`, `new_subs`. En la tabla se muestran vistas y retención; al abrir el detalle de una fila (botón «Ver detalle», id dinámico `an-row-<clip_id>-<platform>`) se ven las demás cifras. Ninguna cifra se deriva del score de la Cola: el score no es una medición.

### 3.5 Clips sin medición

Debajo del ranking, bloque «Sin medición» (`h3`). Lista los clips que están publicados en alguna plataforma (según `publish_states` de `GET /api/clips`) y no tienen ninguna fila en `clip_stats` para esa plataforma. Cada fila: título del clip + pill `.pill.neutral` «Sin medición» + nombre de la plataforma. Texto de ayuda exacto: «Estos clips ya están publicados pero aún no tienen vistas ni retención guardadas. Pega su medición abajo.»

Si no hay clips publicados: «Sin clips publicados todavía.» Si todos los publicados tienen medición: «Todos los clips publicados tienen medición.»

### 3.6 Formulario para pegar mediciones (acción pendiente)

Tarjeta «Pegar una medición» (`h2`). Texto de ayuda exacto: «Pega las cifras que ves en la plataforma (YouTube Studio, TikTok Studio, Meta Business Suite). Solo se guarda lo que pegues: nada se estima ni se inventa.»

Campos (todos `font-size: 16px`, `min-height: 44px`, fondo `--color-surface-input` `#150d24`, borde `--color-border` `#7c6aa0`, radio 10 px; foco: borde `--color-neon-pink-text` + outline 3 px `--color-focus` `#fbbf24`):

| Campo (id nuevo) | Tipo | Origen de las opciones / validación |
|---|---|---|
| Clip (`anPasteClip`) | Select | Clips reales de `GET /api/clips` (título visible). Sin clips: el select muestra «Sin clips» y todo el formulario queda deshabilitado. |
| Plataforma (`anPastePlatform`) | Select | YouTube / TikTok / Instagram / Facebook. |
| Vistas (`anPasteViews`) | Número, entero ≥ 0 | Obligatorio. |
| Retención % (`anPasteRetention`) | Número, 0–100, un decimal | Opcional; vacío = «sin retención», no 0. |
| Compartidos (`anPasteShares`) | Número, entero ≥ 0 | Opcional. |
| Guardados (`anPasteSaves`) | Número, entero ≥ 0 | Opcional. |
| Comentarios (`anPasteComments`) | Número, entero ≥ 0 | Opcional. |
| Nuevos seguidores (`anPasteSubs`) | Número, entero ≥ 0 | Opcional. |
| Ventana (`anPasteWindow`) | Select | `7d` (por defecto, es el `window_label` del esquema), `28d`. |
| Fecha de la medición (`anPasteDate`) | Fecha | Por defecto, hoy (hora de Chile). |

**Botón «Guardar medición» (`anPasteSave`): deshabilitado en esta fase**, con la nota exacta debajo (con `role="status"`): «Pendiente: aún no existe el endpoint para guardar mediciones. Este formulario se activará cuando exista; por ahora, nada de lo que pegues se guarda.»

**Errores de validación (cuando el botón se active):** error bajo su campo con `role="alert"`, texto en `--color-bad-fg` sobre `--color-bad-bg`; foco al primer campo inválido. Textos exactos: vistas vacías → «Escribe las vistas.»; retención fuera de 0–100 → «La retención va de 0 a 100 %.»; número negativo → «No puede ser negativo.»; sin clip elegido → «Elige un clip primero.»

**Acción pendiente (backend, fuera de `frontend/dist/`):** endpoint de lectura de `clip_stats` (por plataforma y clip) y endpoint para guardar una medición (campos del §3.6, tabla `clip_stats`). Hasta entonces, la pestaña entera es vacío real + formulario pendiente.

---

## 4. Tokens GTA 6 por componente (resumen)

Todos los tokens salen de `tokens-gta6.css` / `tokens-gta6.json` (OVERRIDES §8). **Cero hex fuera de los tokens** en `index.html` y `app.js`; único hex permitido en `index.html`: `theme-color #100a1a` cuando el tema está activo.

| Componente | Tokens |
|---|---|
| Fondo página | `--color-background` `#100a1a` |
| Tarjeta | `--color-surface` `#261a38`, borde `--color-border` `#7c6aa0`, radio `--radius-card: 16px`, `box-shadow: var(--shadow-card)`; hover `var(--shadow-elevated)` |
| Input / select / textarea | Fondo `--color-surface-input` `#150d24`, borde `--color-border`, texto `--color-text` `#e8eaf0`, `font-size: 16px`, `min-height: 44px`, radio `--radius-control: 10px`; placeholder `--color-text-dim` `#b8b3c9` |
| Texto principal / secundario | `--color-text` `#e8eaf0` (13.56:1 sobre tarjeta) / `--color-text-dim` `#b8b3c9` (8.02:1) |
| Títulos | `--font-display` (Barlow Condensed, fallback Arial Narrow / system-ui), `h2` 18 px, `h3` 14 px, mayúsculas, `line-height: 1.1` |
| Cuerpo | `--font-body` (Barlow, fallback system-ui), 16 px, `line-height: 1.5` |
| Botón primario | Fondo `--gradient-sunset` (`135deg #6d28d9 → #9d174d → #c2410c`), texto blanco, radio 10 px, `box-shadow: var(--shadow-neon-sunset)`; hover `--gradient-sunset-hover`; fallback sólido `--color-primary` `#9d174d`; deshabilitado `opacity: .55` |
| Botón secundario | Fondo transparente, borde `--color-border`, texto `--color-text`; hover borde `--color-neon-pink-text` `#f9a8d4` + brillo 1.12 |
| Botón peligro | Fondo `--color-danger-bg` `#7f1d1d`, texto blanco |
| Pestaña activa | Fondo `--gradient-sunset`, texto blanco 700, barra inferior 3 px `--color-neon-amber` `#fbbf24`, `aria-selected="true"` |
| Foco visible | `outline: 3px solid var(--color-focus)` `#fbbf24` (9.77:1 sobre tarjeta), `outline-offset: 2px`, en `button, input, textarea, select, a` |
| Enlace | `--color-link` `#f9a8d4` (9.00:1 sobre tarjeta); hover subraya; nunca `--color-primary` como texto de enlace |
| Pills | `.pill.ok` (`#bbf7d0` / `#14532d`), `.pill.bad` (`#fecaca` / `#7f1d1d`), `.pill.warn` (`#fde68a` / `#451a03`), `.pill.neutral` (fondo `--color-surface-input`, texto `--color-text-dim`); nuevas `.pill.sunset` (`#fbcfe8` / `#500724`), `.pill.violet` (`#ede9fe` / `#4c1d95`), `.pill.ember` (`#ffedd5` / `#7c2d12`); `padding: 2px 9px`, radio 999 px, 12 px, 600, siempre con texto |
| Plataformas | YouTube `--color-platform-youtube` `#ff4e45`, TikTok `--color-platform-tiktok` `#25f4ee`, Instagram `--color-platform-instagram` `#f77737` (rosa `--color-platform-instagram-pink` `#f472b6`), Facebook `--color-platform-facebook` `#60a5fa`; solo como punto / borde / nombre con su color claro, nunca el color de marca crudo como texto |
| Mensajes | Éxito `--color-ok-bg` / `--color-ok-fg`; error `--color-bad-bg` / `--color-bad-fg`; info `--color-info-bg` / blanco; borde izquierdo 3 px semántico; `role="status"` / `role="alert"`; `overflow-wrap: anywhere` |
| Escenario 9:16 | Placeholder `linear-gradient(180deg, #3b0764 → #500724 → #150d24)` (`.clip-thumb-placeholder`); guías de zona segura con borde `--color-neon-amber` |
| Barra de duración | Tramo 25–35 `--color-bad-bg`; tramo 36–60 `--color-ok`; resto fondo `--color-surface-input` |
| Motion | 150–250 ms, `ease-out`; `button:active { transform: scale(.98) }`; `prefers-reduced-motion: reduce` anula transiciones; táctil `min-height: 44px`; safe-area con `env(safe-area-inset-*)` |

**Neones crudos prohibidos como texto** (`#ff2e88`, `#ff5e00`, `#ec4899`, `#db2777`, `#ea580c`, `#7c3aed` / `#9333ea` / `#8b5cf6`): solo degradados, bordes, brillos y la franja del header. Texto rosa: `#f472b6` / `#f9a8d4`. Texto violeta: `#a78bfa` / `#c4b5fd`. Texto naranja: `#fb923c` / `#fdba74`.

---

## 5. Ids nuevos que necesita el desarrollador

Ninguno de estos ids existe hoy en `index.html` / `app.js`. Todos son nuevos; los 62 ids estáticos existentes **no se renombran ni se eliminan** (inventario §3.2). Convención: `ed-` para Editor, `an-` para Analíticas. El desarrollador debe además añadir `"editor"` y `"analiticas"` a `TAB_NAMES` (hoy `["worker","cola","cuentas","avisos","ajustes"]`) y el hash `#editor` / `#analiticas` con `switchTab` / `loadTab`, sin tocar el resto del enrutado.

### 5.1 Navegación y entrada

| Id nuevo | Dónde | Qué es |
|---|---|---|
| `tabbtn-editor` | `nav#tabs` | Botón de pestaña «Editor», `role="tab"`, `aria-controls="tab-editor"`. |
| `tabbtn-analiticas` | `nav#tabs` | Botón de pestaña «Analíticas», `role="tab"`, `aria-controls="tab-analiticas"`. |
| `tab-editor` | `main#main` | Sección del Editor, `role="tabpanel"`, `aria-labelledby="tabbtn-editor"`. |
| `tab-analiticas` | `main#main` | Sección de Analíticas, `role="tabpanel"`, `aria-labelledby="tabbtn-analiticas"`. |
| `btnOpenEditor` | `#clipDetailCard` (Cola) | Botón secundario «Abrir en el Editor». Abre `#editor` con el `selectedClipId` actual. No toca `#btnPublish` ni `#btnUpload`. |

### 5.2 Editor (`#tab-editor`)

| Id nuevo | Qué es |
|---|---|
| `edEmpty` | Contenedor del vacío «sin clip seleccionado» (§2.1). |
| `edTitle` | `h2` con el título del clip (de `GET /api/clips/:id`). |
| `edPreviewPill` | Pill de preview: «Preview no vista» (warn) / «Preview vista» (ok), desde `preview_seen`. |
| `edStage` | Marco 9:16 (juego arriba, cámara abajo). |
| `edVideo` | Elemento de vídeo del preview; fuente `GET /api/clips/:id/video` (con credenciales, igual que el resto de llamadas). Sin MP4: no se renderiza; se muestra `edNoVideo`. |
| `edNoVideo` | Vacío «Sin MP4 todavía» + «Sube el MP4 desde la Cola para ver el preview aquí.» + botón a `#cola`. |
| `edSafeToggle` | Botón secundario «Ver zonas seguras» / «Ocultar zonas seguras». |
| `edSafeGuides` | Guías de zona segura (marco + «Zona segura»), ocultas por defecto. |
| `edSubtitleBand` | Banda de subtítulos sobre la zona de cámara (texto de `captions` o «Sin subtítulos guardados»). |
| `edPlayPause` | Botón primario «Reproducir» / «Pausa». |
| `edTime` | Texto «0:12 / 0:40» (actual / duración del archivo). |
| `edMute` | Botón secundario «Silencio» / «Sonido». |
| `edDurationValue` | Número de duración (`end_sec - start_sec`), `tabular-nums`. |
| `edDurationPill` | Pill de banda de duración (§2.4). |
| `edDurationNote` | Texto de la banda (textos exactos del §2.4). |
| `edDurationScale` | Regla visual fija con marcas 15 – 25 – 30 – 35 – 36 – 40 – 60 – 90. |
| `edSubtitleStyle` | Pill del estilo (`subtitle_style`) + lista de líneas de `captions` en lectura. |
| `edStyleOptions` | Tres botones de opción TikTok / Highlight / Karaoke — **deshabilitados** hasta que exista endpoint (§2.5). |
| `edSeoList` | Contenedor de las tarjetas de SEO por plataforma (§2.3). |
| `edMarkPreview` | Botón «Marcar preview vista» — **deshabilitado** hasta que exista el endpoint (§2.6). |
| `edMarkPreviewNote` | Nota «Pendiente: aún no existe el endpoint…» (`role="status"`). |
| `edPublish` | Botón «Publicar ahora» del Editor; deshabilitado mientras `preview_seen = 0`; habilitado llama a `POST /api/clips/:id/publish` sin cuerpo. |
| `edPublishNote` | Motivo visible del bloqueo: «Publicar está bloqueado: falta ver el preview en el Editor.» |
| `edBrakes` | Contenedor de chips de freno (`blocked[]` de la respuesta de publicar), mismo patrón que `#clipBrakes`. |
| `edBackToCola` | Enlace «Volver a la Cola». |

### 5.3 Analíticas (`#tab-analiticas`)

| Id nuevo | Qué es |
|---|---|
| `anEmpty` | Tarjeta de vacío general «Sin mediciones todavía» (§3.2). |
| `anSummary` | Contenedor de las cuatro tarjetas de resumen por plataforma (§3.3). |
| `anSummary-youtube`, `anSummary-tiktok`, `anSummary-instagram`, `anSummary-facebook` | Tarjeta de resumen por plataforma (medianas + «N de M clips medidos» o «Sin mediciones todavía»). |
| `anRanking` | Contenedor de los cuatro bloques de ranking (§3.4). |
| `anRanking-youtube`, `anRanking-tiktok`, `anRanking-instagram`, `anRanking-facebook` | Bloque de ranking por plataforma (tabla en escritorio, tarjetas en móvil; vacío propio «Sin mediciones todavía en …»). |
| `anUnmeasured` | Bloque «Sin medición» (§3.5). |
| `anPasteForm` | Formulario «Pegar una medición» (§3.6). |
| `anPasteClip` | Select de clip (clips reales de `GET /api/clips`). |
| `anPastePlatform` | Select de plataforma. |
| `anPasteViews` | Input de vistas (obligatorio, entero ≥ 0). |
| `anPasteRetention` | Input de retención % (opcional, 0–100). |
| `anPasteShares` | Input de compartidos (opcional). |
| `anPasteSaves` | Input de guardados (opcional). |
| `anPasteComments` | Input de comentarios (opcional). |
| `anPasteSubs` | Input de nuevos seguidores (opcional). |
| `anPasteWindow` | Select de ventana (`7d` / `28d`). |
| `anPasteDate` | Input de fecha de la medición. |
| `anPasteSave` | Botón «Guardar medición» — **deshabilitado** hasta que exista el endpoint (§3.6). |
| `anPasteNote` | Nota «Pendiente: aún no existe el endpoint para guardar mediciones…» (`role="status"`). |
| `anPasteViews-error`, `anPasteRetention-error`, `anPasteClip-error` | Errores bajo su campo (`role="alert"`, textos exactos del §3.6). |
| `an-row-<clip_id>-<platform>` | (Dinámico, por fila de ranking) Botón «Ver detalle» con shares / saves / comments / new_subs de esa medición. |

---

## 6. Estados — matriz de verificación

| Pestaña | Estado | Qué se ve |
|---|---|---|
| Editor | Sin clip seleccionado | `edEmpty`: «Elige un clip en la Cola para editarlo…» + «Ir a la Cola». Nada más. |
| Editor | Clip sin MP4 | Escenario con placeholder + «Sin MP4 todavía» + «Sube el MP4 desde la Cola…»; reproducción deshabilitada; duración, subtítulos y SEO se muestran igual. |
| Editor | Clip con MP4, `preview_seen = 0` | Vídeo reproducible; pill «Preview no vista»; «Marcar preview vista» deshabilitado + nota pendiente; «Publicar ahora» deshabilitado + motivo visible. **Este es el estado real de hoy para todos los clips.** |
| Editor | Clip con MP4, `preview_seen = 1` | Pill «Preview vista»; «Publicar ahora» habilitado (llama al endpoint real; los demás frenos —SEO, tope diario, franja 18:00–24:00— los devuelve `POST /api/clips/:id/publish` y se muestran como chips). |
| Editor | Duración 25–35 s | Pill «Prohibido» + «Regla de oro: nunca 30 segundos…» (§2.4). |
| Editor | `captions` vacío / `seo_title` vacío / `subtitle_style` vacío | «Sin subtítulos guardados» / «Sin título SEO» / «Sin estilo guardado». Nunca un texto inventado. |
| Editor | Error al cargar el clip o el vídeo | Mensaje en `#msg` (`role="alert"`) con el error de la API + botón «Reintentar». No se muestra un clip de ejemplo. |
| Analíticas | Sin mediciones (hoy) | `anEmpty` + las cuatro tarjetas «Sin mediciones todavía» + los cuatro rankings con su vacío propio + formulario pendiente deshabilitado. |
| Analíticas | Con mediciones (cuando exista endpoint) | Resumen con medianas reales, ranking ordenado por vistas, bloque «Sin medición» con los publicados sin medir. |
| Analíticas | Sin clips en la cuenta | Formulario deshabilitado; select de clip «Sin clips». |
| Ambas | API caída / sesión caducada | Mismo patrón que el resto del panel: `#msg` con el error y login si es 401. Sin datos inventados para tapar el error. |

---

## 7. Acciones pendientes (backend — no son trabajo de front)

1. **Endpoint que marque el preview visto** (brecha nº 1 del inventario): sin él, ningún clip puede publicarse por el gate de `preview_seen`, por mucho Editor que haya. Cuando exista, `edMarkPreview` se habilita y `edPreviewPill` / `edPublish` se actualizan con la respuesta real.
2. **Endpoint de guardado del clip** (`PATCH /api/clips/:id` o equivalente): para activar la edición de título, SEO, estilo de subtítulos y duración que este diseño ya deja en su sitio y deshabilitada (§2.3, §2.5).
3. **Endpoints de Analíticas**: lectura de `clip_stats` por plataforma y clip + guardado de una medición (§3.6). Cuando existan, `anPasteSave` se habilita y el vacío «Sin mediciones todavía» se sustituye por datos reales.
4. **Nada de esto se simula en el front**: sin endpoint, los botones pendientes quedan deshabilitados con su nota visible. No se guarda nada en `localStorage`, no se marca `preview_seen` desde el front, no se escriben mediciones a mano en la vista.

---

## 8. Checklist del implementador (antes de dar por hecho estas dos pestañas)

- [ ] `TAB_NAMES` incluye `"editor"` y `"analiticas"`; hash `#editor` / `#analiticas` funcionan; las cinco pestañas anteriores siguen en su sitio.
- [ ] Ningún id de los 62 estáticos del inventario §3.2 renombrado ni eliminado; los ids nuevos del §5 son los únicos añadidos.
- [ ] `node --check frontend/dist/app.js` OK; `grep` de ids usados vs ids existentes; `grep -riE` de nombres personales en `dist/` = 0 (solo `seba` / `chacal` donde ya los usa el front).
- [ ] Ningún hex fuera de `tokens-gta6.css` / `tokens-gta6.json` en componentes; `theme-color #100a1a` con el tema activo.
- [ ] Contraste verificado en los pares críticos GTA 6: primario peor parada 5.18:1, enlace 9.00:1, foco 9.77:1, error 5.90:1, badge 6.04:1; pills sunset / violet / ember según OVERRIDES §8.2.
- [ ] Táctil 44 px en todos los botones e inputs; inputs a 16 px; foco visible 3 px `#fbbf24`; `prefers-reduced-motion` respetado; sin scroll horizontal a 360 px.
- [ ] Editor con un clip real con `preview_seen = 0`: «Marcar preview vista» deshabilitado con su nota, «Publicar ahora» deshabilitado con «Publicar está bloqueado: falta ver el preview en el Editor.»; al pulsar Publicar en Cola, el freno aparece tal cual lo devuelve la API (no se oculta).
- [ ] Analíticas sin endpoint: toda la pestaña en «Sin mediciones todavía», formulario presente con «Guardar medición» deshabilitado y su nota pendiente; cero cifras inventadas.
- [ ] Cada `fetch` nuevo apunta a una ruta del inventario §2.1 (`GET /api/clips/:id`, `GET /api/clips/:id/video`, `POST /api/clips/:id/publish`, `GET /api/clips`): ninguna ruta inventada.
