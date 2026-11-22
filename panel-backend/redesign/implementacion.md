# Implementación del rediseño — Panel AVC (Rol 9, front)

- **Fecha:** 2026-11-17
- **Archivos modificados** (solo `frontend/dist/`, sin tocar API, fórmulas ni reglas de publicación):
  - `frontend/dist/index.html` — reescrito (estructura nueva, mismos ids).
  - `frontend/dist/app.js` — reescrito (misma API/endpoints, render nuevo).
  - `frontend/dist/styles.css` — nuevo (tokens + todos los componentes, enlazado desde `index.html`).
- **No se desplegó a Pages** (lo hace el coordinador tras el QA).

## Qué se implementó

- **Tokens:** `styles.css` incorpora íntegros los tokens de `design-system/avc-panel/tokens.css` (OVERRIDES.md manda sobre MASTER.md). Cero hex fuera de `:root` en `styles.css` (verificado con `awk`); cero hex en `app.js`. Único hex en `index.html`: `<meta name="theme-color" content="#0b0d12">`, valor del token `--color-background` exigido por OVERRIDES §7 / VIS-14.
- **Login:** solo "Entra con tu usuario del panel.", placeholder "tu usuario", hint tras fallo "Si acabas de crear el panel y aún no tienes usuario, pide que te creen uno."; `<form>` con submit por Enter desde cualquier campo; labels con `for`; errores por campo con `role="alert"` + `aria-invalid` + foco al primer inválido; error de auth genérico; error de red distinto; botón "Entrando…" + `disabled`; toggle Mostrar/Ocultar con `aria-pressed`; monograma AVC; `whoLine` = solo `me.sub` (sin tenant, sin nombre real); `healthLine` ya no nombra tenants (usa "en marcha/parado" genérico).
- **Accesibilidad global:** foco visible 3 px, skip link, `color-scheme: dark`, safe-area, inputs 16 px, táctil 44 px, `prefers-reduced-motion`, sin scroll horizontal previsto a 360 px (tarjetas en vez de tabla, `box-sizing`, anchos `min()`).
- **Tabs:** hash en URL (`#worker/#cola/#cuentas/#avisos/#ajustes`), `role="tab"` / `aria-selected` / `aria-controls`, `hashchange` escuchado.
- **Worker:** semáforo en header (punto + texto, botón a la pestaña) y semáforo grande en tarjeta (pill EN MARCHA/PARADO/SIN CONEXIÓN + "desde …"); timeline de actividad con `<time datetime>`; botones 44 px con loading ("Iniciando…", "Parando…", "Ejecutando ciclo…", "Actualizando…"), los 4 deshabilitados durante una acción; confirmación nativa antes de Parar con el texto de la spec; `Iniciar` deshabilitado en marcha, `Parar` deshabilitado parado/sin conexión, `Ejecutar un ciclo` habilitado con Worker parado; skeleton inicial; error de API → SIN CONEXIÓN sin borrar la actividad anterior; glifos ▶/■ sustituidos por SVG inline.
- **Cola:** tarjetas 9:16 (miniatura 72×128 con placeholder "Sin miniatura" y anillo de score) a ≤640 px; tabla con `<thead>`/`<tbody>`/`scope="col"`/caption oculta y `overflow-x` en escritorio; pills de estado traducidas (Pendiente/En cola/Publicando/Publicado/Fallido/Frenado); matriz por plataforma (orden YouTube, TikTok, Instagram, Facebook, punto + nombre + pill); barra de prioridad solo visual etiquetada "Puntos" (tramos <50/50–89/≥90, número siempre escrito); desglose solo si la API devuelve componentes (hoy no los devuelve: sin botón ni ceros); frenos solo desde `blocked[]`, transitorios, en el detalle; tracking como timeline con pill "Fallo" y "Último paso"; empty state con "Ir al Worker" y "Actualizar"; error con "Reintentar"; botón "Ver detalle" (sin `<a href="#">`); "Cerrar detalle" con retorno de foco; foco a `clipTitle` al abrir.
- **Subida MP4:** modo A (XHR con progreso real: "% + MB subidos / MB totales", botón Cancelar que aborta); validación de tipo/tamaño antes de subir con error bajo el campo; éxito con pill "MP4 listo", `r2_key` en mono, enlace público + botón "Copiar"/"Copiado".
- **Publicar:** confirmación con título y plataformas antes de ejecutar; "Publicando…" + `disabled` (también `btnUpload`); resultado con jobs y frenos; refresco del detalle conservando los frenos.
- **Cuentas:** solo 4 estados (Conectada / Sin credencial / No conectada / Sin crear); "Sin verificar" mapeado a No conectada (pill ámbar, decisión v15); tarjeta por cuenta con marca de plataforma (punto, sin SVG de marca — ver "No implementado"), toggle Mostrar/Ocultar credencial, validación de Extra JSON, errores junto al campo, loading "Guardando…"/"Verificando…", skeleton y empty/error con reintento.
- **Avisos:** badge `.badge` con tope "9+", oculto en 0, `role="status"`; contador "N avisos sin leer" / "Todo al día…"; fila con punto CSS (no glifo), botón "Marcar leída" 44 px con loading "Marcando…" y foco al siguiente aviso; empty state con "Actualizar".
- **Ajustes:** formulario por secciones (Subtítulos, Límites diarios, Franja horaria, Telegram) + vista avanzada JSON en `<details>` cerrado por defecto; notas "Aún no lo aplica el Worker" en Subtítulos, Límites y Franja; Telegram sin nota (sí lo lee el Worker); guardado con "Guardando ajustes…", fecha de guardado, indicador "Hay cambios sin guardar" y confirmación al cambiar de pestaña; error de JSON con línea si el parser da posición; claves desconocidas conservadas (merge con lo último recibido); botón "Copiar JSON".
- **Mensajes (`#msg`):** `role="status"` + auto-limpieza 5 s en éxito, `role="alert"` + botón × en error, `overflow-wrap: anywhere`.

## Checklist de verificación propio (probado localmente)

- [x] `node --check app.js` → OK (también tras cada corrección).
- [x] Servidor local `python3 -m http.server` en `dist/`: `index.html`, `app.js` y `styles.css` responden HTTP 200.
- [x] `grep` hex: `app.js` = 0; `styles.css` = solo líneas 7–36 dentro de `:root`; `index.html` = solo el `theme-color` (valor de token).
- [x] `grep -riE 'Sebastián|sebastian o colega|seed-users\.sql'` en `dist/` → 0 coincidencias. `grep 'Sin verificar'` → 0 coincidencias.
- [x] Script Python: los 35 ids requeridos existen en `index.html`; las refs `$("…")` sin id estático son solo elementos creados dinámicamente (`msgClose`, `retry*`, `empty*`, `btnCopyUrl`); endpoints verificados: los 15 de siempre, sin cambios de ruta/método/cuerpo.
- [x] Sin glifos ▶ ■ ● en `index.html`/`app.js` (puntos `.dot` CSS y SVG inline en su lugar).
- [x] Presencia de `role="tab"`, `aria-selected`, `role="alert"`, `aria-live`, `for="username/password/videoFile"` y `prefers-reduced-motion` verificada por grep.

## Lo que NO se pudo implementar y por qué

1. **Logos SVG de plataforma en Cuentas** (spec §2.2): se usa un punto de color por plataforma + nombre en texto. No se dibujaron SVGs de marca (YouTube/TikTok/Instagram/Facebook) porque no hay una fuente local de iconos sin CDN y dibujarlos a mano arriesga marcas incorrectas; el punto + nombre cumple "nunca solo color" y la identificación visual.
2. **Franja "Hasta 24:00"** (spec §4.3, decisión pendiente con el Worker): `input type="time"` no admite `24:00`; si el ajuste guardado trae `24:00`, el campo queda vacío y el hint dice "hoy la franja es 18:00–24:00". Pendiente la decisión del Worker (00:00 del día siguiente o 23:59).
3. **Verificación visual real en navegador** (360/375/640 px, contraste medido, lector de pantalla): como subagente no dispongo de navegador en vivo; la verificación fue estática (código, grep, `node --check`, servidor local con HTTP 200). Queda para el QA del coordinador: abrir el panel, entrar, y recorrer Worker/Cola/Cuentas/Avisos/Ajustes a 360 px y en escritorio.
4. **"Sin crear" como tarjeta**: por diseño de la spec no se renderiza (no hay endpoint de creación); solo aparece vía empty state si no hay cuentas.
5. **Agrupar avisos por día / "Marcar todas como leídas" / "Probar Telegram" / borrado del token de Telegram**: fuera de la spec (sin endpoint); no se inventaron.
