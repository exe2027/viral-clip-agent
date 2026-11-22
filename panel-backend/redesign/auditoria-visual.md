# Auditoría visual — Panel AVC (Clips GTA 6)

- **Fecha:** 2026-11-17
- **Alcance:** `frontend/dist/index.html` (150 líneas, estilos inline en `<style>`) y `frontend/dist/app.js` (258 líneas). Pestañas: Login, Worker, Cola, Cuentas, Avisos, Ajustes. Modo oscuro, móvil primero.
- **Método:** lectura completa de ambos archivos + skill obligatoria `ui-ux-pro-max` (búsquedas: `"dark dashboard" --domain style`, `"dark mode contrast" --domain color`, `"dashboard typography hierarchy" --domain typography`; checklist `references/pro-rules.md`). Contrastes calculados, no estimados (ratio WCAG).
- **Regla del encargo:** solo auditoría visual. No se escribió ni editó código del front; no se tocó lógica de negocio ni API.
- **Veredicto general:** base sana (hay 9 tokens en `:root`, radios y bordes consistentes, textos secundarios con buen contraste), pero con **2 defectos críticos de contraste y de tabla en móvil** que rompen el uso real del panel, y una capa de interacción (foco, hover, táctil) inexistente. El panel se ve "correcto" en escritorio a primera vista; en un teléfono — su uso principal — falla.

## Referencias de la skill aplicadas

- Estilo `dark-mode-oled`: exige contraste de texto 7:1+, foco visible y `color-scheme: dark`.
- Paletas `color` (dark): fondo `#020617` / tarjeta `#0E1223` / borde `#334155` / texto atenuado `#94A3B8` — el panel usa un borde (`#2a2f3a`) y un atenuado (`#9aa3b2`) más apagados que el patrón.
- Tipografía `Dashboard Data` / `Bold Typography Mobile`: escala con etiquetas 12 px, cuerpo 16 px, subhead 22 px; line-height 1.5 base (regla §6 de la skill).
- `pro-rules.md`: objetivos táctiles ≥ 44 pt, foco visible, sin emojis como iconos, contraste primario ≥ 4.5:1 en modo oscuro, tokens semánticos en vez de hex sueltos.

## Contrastes medidos (referencia para los defectos)

| Par | Ratio | Veredicto |
|---|---|---|
| Texto `#e8eaf0` / fondo `#0f1115` | 15.71:1 | OK |
| Atenuado `#9aa3b2` / fondo y tarjeta | 7.43:1 / 6.63:1 | OK (el "gris sobre gris" de texto NO es el problema) |
| Blanco / acento `#3b82f6` (botones primarios) | 3.68:1 | **Falla AA (mín. 4.5:1)** |
| Texto `#e8eaf0` / acento `#3b82f6` (pestaña activa) | 3.06:1 | **Falla AA** |
| Enlace sin estilo `#0000EE` (UA) / tarjeta `#1a1d24` | 1.79:1 | **Falla AA** |
| Tarjeta `#1a1d24` vs fondo `#0f1115` | 1.12:1 | Superficies casi idénticas |
| Borde `#2a2f3a` vs tarjeta `#1a1d24` | 1.26:1 | Borde casi invisible |
| Blanco / `#2563eb` (reemplazo propuesto) | 5.17:1 | OK |
| Texto `#e8eaf0` / `#1d4ed8` (alternativa) | 5.57:1 | OK |
| Pills ok/bad (`#bbf7d0/#14532d`, `#fecaca/#7f1d1d`) | 7.52:1 / 6.93:1 | OK |
| Texto / `.msg.err #7f1d1d`, `.msg.info #1e3a5f` | 8.33:1 / 9.56:1 | OK |

---

## Defectos

### 1. Contraste de la pestaña activa y de todos los botones primarios por debajo de AA
- **Pantalla:** Todas (navegación superior) y todas las pantallas con botón primario (Login, Worker, Cola, Cuentas, Ajustes).
- **Elemento:** `nav button.active` (`background:var(--acc)`, texto `var(--txt)`) y `button` primario (`background:var(--acc)`, texto `#fff`).
- **Defecto:** el acento `#3b82f6` es demasiado claro para llevar texto encima: pestaña activa 3.06:1 y botón primario 3.68:1, ambos por debajo del mínimo WCAG AA 4.5:1 para texto normal (14 px). La pestaña activa — el elemento que indica dónde está el usuario — es el texto menos legible de todo el panel. La skill exige ≥ 4.5:1 en modo oscuro.
- **Severidad:** Crítica.
- **Corrección concreta:** cambiar `--acc` a `#1d4ed8` (blanco 6.70:1, texto `#e8eaf0` 5.57:1) o como mínimo a `#2563eb` (blanco 5.17:1; ojo: con texto `#e8eaf0` queda en 4.30:1, insuficiente — si se usa `#2563eb`, el texto de la pestaña activa debe ser `#fff`). Añadir además un segundo indicador de pestaña activa además del color (p. ej. `font-weight:600` + `box-shadow: inset 0 -2px 0 #fff` o subrayado), porque hoy el estado activo depende solo del color.

### 2. La tabla de la Cola no tiene tratamiento móvil: 5 columnas provocan scroll horizontal
- **Pantalla:** Cola.
- **Elemento:** `<table>` generada en `loadClips()` (Clip, Estado, Puntos, Plataformas, Creado), `width:100%` sin contenedor con overflow.
- **Defecto:** no hay `@media`, ni wrapper con `overflow-x:auto`, ni transformación a tarjetas. En un teléfono de 360 px, 5 columnas con títulos largos, fechas `toLocaleString` y listas de plataformas no caben: la tabla desborda el `main` (padding 18 px) y la página entera gana scroll horizontal, o las celdas se aplastan hasta ser ilegibles. Es el defecto que más rompe el "móvil primero" declarado. La skill (§5) prohíbe el scroll horizontal.
- **Severidad:** Crítica.
- **Corrección concreta:** envolver la tabla en `<div class="table-wrap">` con `overflow-x:auto; -webkit-overflow-scrolling:touch` y `min-width` por columna, y en ≤ 640 px convertir cada fila en tarjeta: ocultar la tabla y renderizar `.clip-card` con título, pill de estado y metadatos apilados (o, como mínimo, `display:block` con `td::before` con el nombre de la columna). Añadir `th { position:sticky; top:0 }` en escritorio.

### 3. Los títulos de clip son enlaces sin ningún estilo: azul de navegador sobre fondo oscuro
- **Pantalla:** Cola.
- **Elemento:** `<a href="#" data-clip="…"><b>título</b></a>` en `loadClips()`; no existe ninguna regla `a` en el CSS.
- **Defecto:** el enlace hereda el azul por defecto del navegador (`#0000EE`), con contraste 1.79:1 sobre la tarjeta — prácticamente ilegible y visualmente ajeno a la paleta. Además no hay `:hover`, `:visited` ni `:focus-visible` para enlaces, y el título es el principal punto de entrada de la Cola.
- **Severidad:** Alta.
- **Corrección concreta:** añadir `a { color:var(--acc); text-decoration:none; } a:hover { text-decoration:underline; }` (con el nuevo acento oscuro del defecto 1, el enlace queda ≥ 4.5:1) y `a:focus-visible { outline:2px solid var(--acc); outline-offset:2px; }`. Considerar que el título del clip use `font-weight:600` en vez de `<b>` dentro de `<a>`.

### 4. El `h3` "Seguimiento" no tiene estilo: es más grande que los `h2`
- **Pantalla:** Cola (detalle de clip).
- **Elemento:** `<h3>Seguimiento</h3>` en `#clipDetailCard`; el CSS define `h1 { 18px }` y `.card h2 { 16px }`, pero ningún `h3`.
- **Defecto:** el `h3` cae al tamaño por defecto del navegador (~18.7 px, negrita), mayor que los `h2` de 16 px. La jerarquía se invierte: un subtítulo dentro de una tarjeta se ve más grande que el título de la tarjeta. Es el único encabezado sin token tipográfico.
- **Severidad:** Alta.
- **Corrección concreta:** añadir `.card h3 { margin:14px 0 8px; font-size:14px; font-weight:600; color:var(--txt); }` (o 15 px si se quiere distinguir del cuerpo) y, de paso, fijar la escala completa en tokens: `--fs-h1:20px; --fs-h2:16px; --fs-h3:14px; --fs-body:16px; --fs-small:13px; --fs-caption:12px`.

### 5. No existe ningún estado `:focus-visible`, `:hover` ni `:active` en todo el front
- **Pantalla:** Todas.
- **Elemento:** `button`, `nav button`, `input`, `textarea`, `select`, enlaces de clip.
- **Defecto:** no hay una sola regla de foco, hover o transición en `index.html` (verificado por búsqueda: cero ocurrencias de `focus`, `hover`, `transition`, `outline`). El usuario de teclado solo ve el outline fino por defecto del navegador; el usuario táctil no recibe ninguna confirmación al pulsar; los botones no cambian en absoluto al pasar el cursor. La skill lo marca CRÍTICO (prioridades 1 y 2: foco visible y feedback de interacción).
- **Severidad:** Alta.
- **Corrección concreta:** añadir un bloque único: `:focus-visible { outline:2px solid var(--acc); outline-offset:2px; }`, `button:hover { filter:brightness(1.12); }`, `nav button:hover { border-color:var(--dim); }`, `button:active { transform:translateY(1px); }` y `tr:hover td { background:#20242d; }`. Respetar `prefers-reduced-motion` para el `scrollIntoView({behavior:"smooth"})` de `selectClip()`.

### 6. Inputs a 14 px: iOS hace zoom automático al enfocar cada campo
- **Pantalla:** Login, Cuentas, Ajustes.
- **Elemento:** `input, textarea, select { font-size:14px; }`.
- **Defecto:** Safari en iOS amplía la vista (zoom) cuando se enfoca un campo con `font-size < 16px`. En un panel móvil primero, cada vez que el usuario toca Usuario, Contraseña, Credencial o el JSON de Ajustes, la página hace zoom, desplaza el layout y hay que alejar manualmente. Es un defecto clásico de formularios móviles.
- **Severidad:** Alta.
- **Corrección concreta:** subir `input, textarea, select { font-size:16px; }` en móvil (mantener 14 px solo en escritorio vía `@media (min-width:768px)` si se quiere densidad), o fijar `font-size:16px` en todos los campos y compensar la densidad con padding.

### 7. Objetivos táctiles de ~33–35 px, por debajo del mínimo de 44 px
- **Pantalla:** Todas.
- **Elemento:** `button { padding:9px 16px; font-size:14px }` (~35 px de alto) y `nav button { padding:8px 14px }` (~33 px). El botón "Marcar leída" y los de cuenta usan el mismo tamaño.
- **Defecto:** la skill (prioridad 2, CRÍTICA; `pro-rules.md`) exige ≥ 44×44 pt/dp. Las pestañas — la navegación principal del panel — miden ~33 px de alto: en un teléfono son difíciles de acertar y quedan pegadas entre sí (gap de 6 px, por debajo de los 8 px recomendados entre objetivos).
- **Severidad:** Alta.
- **Corrección concreta:** `button { min-height:44px; padding:10px 16px; }`, `nav button { min-height:44px; padding:10px 16px; }`, y subir el `gap` del `nav` de 6 px a 8 px. En la fila del Worker (4 botones) seguirán cabiendo 2×2 en móvil gracias al `flex-wrap` existente.

### 8. Paleta a medias: 7 hex sueltos fuera de `:root` y estilos inline en `app.js`
- **Pantalla:** Todas.
- **Elemento:** `:root` define 9 tokens, pero fuera de él hay 7 valores hex en 9 usos: `#2a2f3a` (fondo de `.pill`), `#0f1115` (fondo de inputs), `#7f1d1d` (×3: botón `.danger`, `.pill.bad`, `.msg.err`), `#14532d`, `#bbf7d0`, `#fecaca`, `#1e3a5f` (`.msg.info`). Además `app.js` inyecta estilos inline (`style="margin-bottom:10px"`, `style="border-bottom:1px solid var(--line); padding:8px 0"`, `style="flex:1"`, `style="width:100%"` en el botón Entrar).
- **Defecto:** la skill (§6) prohíbe el hex crudo en componentes: cualquier cambio de paleta exige cazar valores en dos archivos, y ya hay deriva real — `#7f1d1d` significa tres cosas distintas (botón peligro, pill mala, fondo de error) sin un token que lo declare. Los estilos inline de `app.js` no se pueden sobrescribir desde la hoja de estilos sin `!important`.
- **Severidad:** Alta.
- **Corrección concreta:** completar los tokens en `:root` (`--danger:#7f1d1d; --danger-bg:#7f1d1d; --ok-bg:#14532d; --ok-fg:#bbf7d0; --bad-fg:#fecaca; --info-bg:#1e3a5f; --surface:#0f1115;`) y sustituir cada hex suelto por su `var(...)`; mover los estilos inline de `app.js` a clases (`.account-card`, `.notif-row`, `.btn-block`).

### 9. `.row > * { flex:1 }` estira todos los botones por igual y rompe la jerarquía en Avisos
- **Pantalla:** Avisos (y en menor medida Worker y Cuentas).
- **Elemento:** `.row { display:flex; gap:10px; } .row > * { flex:1; min-width:140px; }` aplicado al botón "Marcar leída" de cada aviso.
- **Defecto:** la regla no distingue contenido de acción: en cada fila de aviso, el botón fantasma "Marcar leída" ocupa la misma fracción de ancho que el texto del aviso (flex:1, mínimo 140 px) y se ve como un bloque grande, compitiendo con el mensaje. En el Worker el estirado es aceptable (4 acciones), pero en Avisos convierte una acción secundaria en el elemento dominante de la fila. Mismo patrón en Cuentas: "Guardar credencial" y "Verificar ahora" pesan visualmente lo mismo pese a que una es primaria y la otra secundaria.
- **Severidad:** Media.
- **Corrección concreta:** cambiar a `.row > * { flex:1 1 auto; }` y añadir `.row > button { flex:0 0 auto; }` como base, con una clase explícita `.row-fill` solo donde el estirado sea intencionado (Worker). En Avisos, el botón debe ser `flex:0 0 auto; align-self:center;`.

### 10. Estado de clip como texto plano en Cola, pero como pill en Cuentas: dos lenguajes de estado
- **Pantalla:** Cola (tabla y detalle) frente a Cuentas.
- **Elemento:** en `loadClips()`, `<td>${esc(c.status)}</td>` y `<p>Estado: <b>…</b></p>` muestran el string crudo de la base de datos; en `loadAccounts()` el estado es un `.pill` (ok/bad/neutro). Los `publish_states` por plataforma se vuelcan además como texto corrido atenuado (`youtube: queued · tiktok: …`).
- **Defecto:** el usuario debe leer e interpretar texto en una pantalla y color/forma en la otra; el estado — el dato más importante de la Cola — no tiene jerarquía visual ni código de color, y los valores crudos (p. ej. en inglés, `queued`) no están traducidos. La densidad de la tabla lo agrava: la columna Estado es indistinguible de las demás.
- **Severidad:** Media.
- **Corrección concreta:** mapear cada `status` y cada `publish_states[*].state` a un `.pill` con clase semántica (`.pill.ok/.bad/.warn/.neutral`) y etiqueta en español, reutilizando exactamente el componente de Cuentas. Añadir además `.pill.warn { background:#451a03; color:#fde68a; }` — hoy `--warn` existe como token pero no tiene variante de pill.

### 11. El estado intermedio "Sin verificar" existe en el código pero no tiene diseño propio
- **Pantalla:** Cuentas.
- **Elemento:** en `loadAccounts()`, `has_credential && !verify_ok` renderiza `<span class="pill">Sin verificar</span>` — el pill neutro gris, idéntico a una etiqueta genérica.
- **Defecto:** el estado intermedio (credencial guardada, verificación pendiente) es visualmente indistinguible de "sin estado": mismo fondo `#2a2f3a` que cualquier pill sin clase. Además contradice la decisión de panel v15 registrada en memoria (solo 4 estados; el ámbar "Sin verificar" se eliminó) — el front sigue emitiendo ese estado, sin tratamiento visual. El usuario no distingue "falta pegar credencial" (pill roja) de "pegada pero sin verificar" (gris).
- **Severidad:** Media.
- **Corrección concreta:** o se elimina el estado intermedio del render (acorde a v15: solo Conectada / Sin credencial / No conectada / Sin crear) o se le da identidad propia con `.pill.warn` (fondo ámbar oscuro `#451a03`, texto `#fde68a`, icono "…"). Decidir una de las dos; no dejar el gris neutro.

### 12. No hay `line-height` en todo el CSS: texto denso y pegado
- **Pantalla:** Todas.
- **Elemento:** `body` y todos los componentes; ningún `line-height` declarado (el valor efectivo es el `normal` del navegador, ~1.2).
- **Defecto:** la skill exige base 1.5. Se nota en `.activity li` (fechas + texto en 13 px con 6 px de separación), en las celdas de la tabla (varias líneas por celda: título + SEO + estados) y en los párrafos `.dim` explicativos. La lectura de seguimiento y actividad — contenido principal del Worker y la Cola — queda apretada.
- **Severidad:** Media.
- **Corrección concreta:** `body { line-height:1.5; }`, `h1,h2,h3 { line-height:1.25; }`, `.activity li { line-height:1.5; }`, `td, th { line-height:1.45; }`.

### 13. Grises sobre grises en superficies: la tarjeta es indistinguible del fondo
- **Pantalla:** Todas.
- **Elemento:** `--bg:#0f1115` vs `--card:#1a1d24` (contraste 1.12:1) y `--line:#2a2f3a` vs tarjeta (1.26:1).
- **Defecto:** las tarjetas no se distinguen del fondo por color: solo las separa un borde de 1 px con contraste 1.26:1. El panel se ve plano, sin profundidad; los inputs (fondo `#0f1115`, igual que la página) dentro de tarjetas parecen agujeros. Matiz importante: el texto atenuado NO tiene este problema (7.43:1, pasa AA); el "gris sobre gris" real es de superficies y bordes, no de texto. La paleta de referencia de la skill usa borde `#334155` y tarjeta `#0E1223` sobre fondo `#020617` — más separación.
- **Severidad:** Media.
- **Corrección concreta:** subir el borde a `--line:#334155` (o `#3a4358`) y/o oscurecer el fondo a `#0b0d12` y aclarar la tarjeta a `#1e222c`, buscando ≥ 1.5:1 tarjeta/fondo y ≥ 2:1 borde/tarjeta; añadir una sombra sutil a `.card` (`box-shadow:0 1px 2px rgba(0,0,0,.4)`) para dar profundidad sin cambiar la paleta.

### 14. Falta `color-scheme: dark`: controles nativos se renderizan en claro
- **Pantalla:** Login, Cuentas, Ajustes, Cola (subida de MP4).
- **Elemento:** `<style>` sin `color-scheme`; `input[type=file]`, `select`, `textarea`, scrollbars.
- **Defecto:** sin `color-scheme: dark`, el navegador dibuja los controles nativos (botón "Elegir archivo" del input file, desplegable del select, scrollbar, menú de autocompletado) con tema claro sobre el fondo oscuro. El estilo `dark-mode-oled` de la skill lo exige explícitamente. También falta `<meta name="theme-color" content="#0f1115">`.
- **Severidad:** Media.
- **Corrección concreta:** añadir `:root { color-scheme: dark; }` y `<meta name="theme-color" content="#0f1115">`. Dar estilo propio al input file (`input[type=file]::file-selector-button` con fondo de tarjeta y borde) porque su botón nativo seguirá viéndose ajeno.

### 15. El badge de avisos no leídos es texto plano "(3)", no un badge
- **Pantalla:** Todas (pestaña Avisos).
- **Elemento:** `<span id="unreadBadge"></span>` rellenado en `loadNotifications()` con `` `(${unread})` ``.
- **Defecto:** el contador se renderiza como texto literal "(3)" pegado al label de la pestaña, sin fondo, sin color de acento, sin forma de píldora: no se distingue como indicador de "no leído" y se confunde con parte del nombre de la pestaña. Además no hay límite visual (con 25 avisos se leería "(25)").
- **Severidad:** Media.
- **Corrección concreta:** `.badge { display:inline-block; min-width:20px; height:20px; padding:0 6px; border-radius:999px; background:var(--bad); color:#fff; font-size:11px; font-weight:700; line-height:20px; text-align:center; }` y renderizar solo el número (con tope visual "9+"). Cuando sea 0, ocultar el elemento en vez de dejarlo vacío.

### 16. Header y navegación no comparten el ancho máximo del contenido: desalineación en escritorio
- **Pantalla:** Todas (vista ≥ 1000 px).
- **Elemento:** `header { padding:14px 18px }` y `nav { padding:10px 18px }` a ancho completo, frente a `main { max-width:980px; margin:0 auto }`.
- **Defecto:** en escritorio el título y las pestañas empiezan a 18 px del borde izquierdo, mientras el contenido empieza centrado a `(viewport − 980px)/2`. Las tres franjas no alinean sus bordes izquierdos; el panel parece montado con dos rejillas distintas.
- **Severidad:** Baja.
- **Corrección concreta:** envolver el contenido de `header` y `nav` en el mismo ancho: `header, nav { padding-left:max(18px, calc((100vw - 980px)/2)); padding-right:max(18px, calc((100vw - 980px)/2)); }` o un `.wrap { max-width:980px; margin:0 auto; }` compartido.

### 17. El encabezado no es fijo: las pestañas desaparecen al bajar en listas largas
- **Pantalla:** Cola, Avisos, Cuentas.
- **Elemento:** `header` y `nav` estáticos; la Cola puede tener decenas de clips y el seguimiento es largo.
- **Defecto:** al hacer scroll, la navegación (única forma de cambiar de pestaña) se va con la página; para cambiar de pestaña hay que volver arriba. En móvil, con listas largas, es fricción constante.
- **Severidad:** Media.
- **Corrección concreta:** `nav { position:sticky; top:0; z-index:10; background:var(--bg); }` (el `header` puede quedarse estático). Verificar que el sticky no tape el foco al usar `scrollIntoView` en el detalle del clip.

### 18. El JSON de Ajustes se edita en fuente proporcional
- **Pantalla:** Ajustes.
- **Elemento:** `<textarea id="settingsJson" rows="14">` sin `font-family` propia (hereda `system-ui`).
- **Defecto:** editar JSON en fuente proporcional dificulta ver llaves, comas y sangría; es el único lugar del panel donde el usuario edita código, y es donde más errores de sintaxis se cometen (el propio `saveSettings` valida con `JSON.parse` y falla sin indicar la línea). El `pre` sí tiene estilo (12 px atenuado), pero el `textarea` no.
- **Severidad:** Media.
- **Corrección concreta:** `textarea#settingsJson { font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:13px; line-height:1.6; tab-size:2; }`. Ideal: mostrar el mensaje de error de `JSON.parse` con el nº de línea si el navegador lo aporta.

### 19. Sin estados de carga: al cambiar de pestaña no hay feedback
- **Pantalla:** Todas.
- **Elemento:** `loadTab()` / `loadWorker()` / `loadClips()` / `loadAccounts()` — mientras el `fetch` está en curso, el contenido anterior permanece (o vacío, la primera vez), sin spinner ni esqueleto.
- **Defecto:** la skill (prioridad 2) exige feedback de carga. En conexiones lentas el usuario no sabe si la pestaña está cargando o rota, y puede pulsar dos veces (p. ej. "Ejecutar un ciclo ahora" no se deshabilita durante la acción; solo el botón Entrar tiene `disabled`).
- **Severidad:** Media.
- **Corrección concreta:** mostrar un estado de carga mínimo en cada sección (`<p class="dim">Cargando…</p>` con un spinner CSS de 16 px) antes de cada `fetch`, y deshabilitar el botón que disparó la acción hasta que resuelva (`btnStart/btnStop/btnRunOnce/btnPublish/btnUpload`).

### 20. Símbolos ▶ ■ ● usados como iconos, sin consistencia ni alternativa
- **Pantalla:** Worker, Avisos.
- **Elemento:** `▶ Iniciar`, `■ Parar`, `<b>● </b>` en avisos no leídos.
- **Defecto:** son glifos de texto, no iconos: se renderizan distinto según la plataforma/fuente (algunos sistemas los muestran como emoji a color), no tienen `aria-hidden`, y el resto del panel no usa ningún icono — hay una sola familia visual improvisada con tres caracteres. La skill prohíbe emojis como iconos y exige una familia de iconos SVG consistente.
- **Severidad:** Baja.
- **Corrección concreta:** sustituir por SVG inline (play, stop, punto) de una sola familia (p. ej. trazos de 1.5 px, 16×16, `aria-hidden="true"`), o eliminar los glifos y dejar solo el texto ("Iniciar", "Parar", y el punto de no leído como `.dot` CSS: `width:8px; height:8px; border-radius:50%; background:var(--acc)`).

### 21. Encabezados de tabla indistinguibles de las celdas; números alineados a la izquierda
- **Pantalla:** Cola.
- **Elemento:** `td, th { text-align:left; padding:8px 6px; }` — `th` solo hereda la negrita por defecto; no hay distinción de color, tamaño ni mayúsculas. La columna "Puntos" (numérica) va a la izquierda.
- **Defecto:** el encabezado no se lee como encabezado: mismo tamaño (14 px), mismo color y mismo peso visual que el cuerpo. Los puntos (números) alineados a la izquierda dificultan comparar de un vistazo.
- **Severidad:** Baja.
- **Corrección concreta:** `th { font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; color:var(--dim); }` y `td:nth-child(3), th:nth-child(3) { text-align:right; font-variant-numeric:tabular-nums; }`. Además, la tabla no usa `<thead>`/`<tbody>` (accesibilidad y sticky header lo necesitan).

### 22. Los mensajes `.msg` aparecen sin reservar espacio y sin `role`
- **Pantalla:** Todas.
- **Elemento:** `<div id="msg"></div>` + `.msg { padding:10px 12px; border-radius:8px; margin-bottom:12px; }`; `showMsg()` sustituye el `innerHTML`.
- **Defecto:** al aparecer un mensaje, todo el contenido baja de golpe (salto de layout, sin espacio reservado); no hay `role="alert"`/`aria-live`, así que un lector de pantalla no anuncia errores ni confirmaciones; error e info solo se distinguen por el tono del fondo, sin icono ni borde. El texto en sí tiene buen contraste (8.33:1 / 9.56:1), eso no es el problema.
- **Severidad:** Baja.
- **Corrección concreta:** `<div id="msg" role="status" aria-live="polite">` (y `role="alert"` para errores), añadir borde izquierdo de 3 px (`border-left:3px solid var(--bad)` / `var(--acc)`) y un icono SVG, y reservar el espacio con `#msg:empty { display:none }` + transición, o un contenedor de altura mínima.

### 23. Sin soporte de safe-area ni ajustes de viewport móvil
- **Pantalla:** Todas (iPhone con notch / gesto home).
- **Elemento:** `main { padding:18px }`, `header`, `nav` — ningún `env(safe-area-inset-*)`; `body` sin `-webkit-tap-highlight-color` ni `text-size-adjust`.
- **Defecto:** en teléfonos con notch o barra de gestos, el contenido puede quedar pegado a los bordes del sistema; el resaltado táctil por defecto (gris azulado) aparece al tocar botones y rompe el tema oscuro.
- **Severidad:** Baja.
- **Corrección concreta:** `main { padding:18px 18px calc(18px + env(safe-area-inset-bottom)); }` y `body { -webkit-tap-highlight-color:transparent; }`. No es urgente porque no hay navegación inferior fija que choque con la barra de gestos.

### 24. Densidad y espaciado sin escala: valores ad hoc en cada componente
- **Pantalla:** Todas.
- **Elemento:** `header 14px/18px`, `nav 10px/18px`, `main 18px`, `.card padding 16px / margin-bottom 14px`, `gap 6px/10px/12px`, `.pill 2px/9px`, `.msg 10px/12px`, `td 8px/6px`, `#loginBox margin 60px auto`.
- **Defecto:** no hay escala de espaciado (`--space-*`) ni ritmo vertical compartido: 14 px de separación entre tarjetas, 16 px de padding interno, 18 px de margen de página, 10 px de gap en filas — cuatro valores cercanos pero distintos que impiden percibir una rejilla. La tarjeta de login usa `margin:60px auto`, que en móvil desperdicia casi una pantalla de alto antes del formulario.
- **Severidad:** Baja.
- **Corrección concreta:** definir `--space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px; --space-5:24px; --space-6:32px;` y reescribir paddings/margins/gaps con esos tokens (p. ej. `.card { padding:var(--space-4); margin-bottom:var(--space-4); }`, `main { padding:var(--space-4); }`); bajar `#loginBox` a `margin:32px auto` y en móvil a `24px auto`.

---

## Lo que está bien (no tocar)

- **Tokens base existen:** 9 variables en `:root` cubren fondo, tarjeta, línea, texto, atenuado, acento y semáforos; el panel no es un caos de hex.
- **Texto principal y atenuado con contraste sobrado** (15.71:1 y 7.43:1): el "gris sobre gris" no afecta al texto.
- **Pills ok/bad con buen contraste** (7.52:1 / 6.93:1) y forma consistente (999 px).
- **Radios coherentes:** 8 px (botones, inputs, mensajes) / 12 px (tarjetas) / 999 px (pills). No hay mezcla arbitraria.
- **Bordes consistentes:** 1 px `var(--line)` en tarjetas, navegación, inputs y cabecera.
- **Login con `autocomplete` correcto** (`username` / `current-password`) y botón a ancho completo.
- **Jerarquía de botones en general correcta:** un primario por tarjeta, fantasma para secundarias, peligro solo para "Parar".

## Prioridad de corrección sugerida

1. **Ahora (críticas):** defectos 1 (acento) y 2 (tabla móvil). Son los únicos que impiden usar el panel.
2. **Esta iteración (altas):** 3 (enlaces), 4 (h3), 5 (foco/hover), 6 (zoom iOS), 7 (44 px), 8 (tokens).
3. **Siguiente (medias):** 9–15, 17–19 (estados, carga, badge, superficies, sticky nav).
4. **Pulido (bajas):** 16, 20–24.

## Notas fuera de alcance (no son defectos visuales)

- `app.js` sigue emitiendo el estado "Sin verificar" (defecto 11) pese a la decisión v15 de eliminarlo: es una divergencia de estado, no solo visual; confirmarla con el responsable antes de corregir.
- Los estados de clip llegan como strings crudos de la BD (defecto 10): el mapeo a pills necesita acordar el catálogo de estados con el Worker.
