# Rediseño premium del panel AVC — Lluvia de ideas (Rol 3)

**Fecha:** 2026-11-17 · **Alcance:** solo ideas visuales y de experiencia. Sin código, sin tocar lógica de negocio, API, fórmulas de viralidad/SEO ni reglas de publicación.
**Front leído:** `frontend/dist/index.html` + `app.js` (6 pantallas: Login, Worker, Cola, Cuentas, Avisos, Ajustes). Hoy es una tabla plana con CSS mínimo: fondo `#0f1115`, tarjetas `#1a1d24`, acento azul `#3b82f6`, tabs superiores, banner `#msg` para todo el feedback, sin miniaturas, sin skeletons, sin progreso de subida y con los estados de publicación como texto plano (`k: v`).

## Inspiración (skill ui-ux-pro-max, `search.py`)

- `--domain product "creator dashboard video"`: **0 resultados** (sin match en la base).
- Reintento `--domain product "entertainment social"`: **Social Media App** → Vibrant & Block-based + Motion-Driven, micro-interacciones, colores vibrantes de engagement; **Gaming** → neón + retro-futurismo; **News/Media** → Minimalismo + Dark Mode (OLED), Media Analytics Dashboard.
- `--domain style "dark premium dashboard"`: **dark-mode-oled** (negro profundo `#000000`/`#121212`, azul medianoche `#0A0E27`, acentos neón, contraste 7:1+, glow mínimo) y **data-dense-dashboard** (grid 12 col, KPI cards, tablas ordenables, headers fijos, gaps 8–12 px). Skeuomorphism descartado (coste alto, fuera de tono).
- `--domain style "glassmorphism dark"`: blur 10–20 px, translúcido, borde sutil — útil para nav/modales, no para toda la app.
- Dirección que se desprende: **OLED oscuro + neón por plataforma + densidad de dashboard + micro-interacciones**, sin perder la lectura a golpe de vista en el móvil.

---

## Las 20 ideas

### 1. Tarjeta de clip 9:16 con miniatura y anillo de score
**Pantalla:** Cola
Hoy la Cola es una tabla sin imagen: título, estado, un número de puntos y plataformas en texto. Cada clip pasa a ser una tarjeta horizontal con miniatura vertical 9:16 (frame del MP4 o placeholder con degradado), título, y el score como anillo de progreso con color por tramo (rojo <50, ámbar 50–89, verde ≥90).
**Impacto:** Alto · **Esfuerzo:** Medio

### 2. Barra de prioridad con desglose viralidad / SEO / chat
**Pantalla:** Cola
La prioridad hoy es invisible (la tabla se ordena por fecha, no por prioridad). Una barra fina bajo cada clip muestra la prioridad calculada y, al tocarla, el desglose en tres segmentos: viralidad, SEO y chat_norm, con sus valores. Solo visualiza la fórmula existente, no la cambia.
**Impacto:** Alto · **Esfuerzo:** Bajo

### 3. Matriz de estados por plataforma (4 chips con marca y color)
**Pantalla:** Cola + detalle del clip
Los estados de publicación hoy son texto plano (`tiktok: queued`). Cada plataforma se convierte en un chip con su logo, su color de marca (rojo YouTube, negro/cian TikTok, degradado Instagram, azul Facebook) y un icono de estado: pendiente, en cola, publicado ✓, fallido ✕, frenado ⏸. De un vistazo se ve qué falta por publicar.
**Impacto:** Alto · **Esfuerzo:** Bajo

### 4. Chips de freno: por qué este clip no publica
**Pantalla:** Detalle del clip
Hoy los frenos (SEO <90, límite diario, falta de MP4, clip de 30 s) solo quedan registrados como texto tras pulsar Publicar. Cada freno activo aparece como chip ámbar/rojo con su motivo en lenguaje claro ("SEO 87 — faltan 3 puntos", "Sin MP4 subido", "Límite de TikTok de hoy alcanzado") y, cuando aplica, el botón que lo resuelve.
**Impacto:** Alto · **Esfuerzo:** Bajo

### 5. Timeline vertical de seguimiento (11 pasos)
**Pantalla:** Detalle del clip
El tracking hoy es una lista `<ul>` de texto con fecha y mensaje. Pasa a ser una línea de tiempo vertical con icono por paso (detectado → puntuado → MP4 → encolado → publicado), hora, actor y motivo; los pasos fallidos quedan en rojo con su causa y el paso actual con pulso animado.
**Impacto:** Alto · **Esfuerzo:** Medio

### 6. Progreso real de subida del MP4 (% + MB + cancelar)
**Pantalla:** Detalle del clip
Subir el MP4 hoy solo muestra un mensaje de texto ("Subiendo…") sin porcentaje ni forma de cancelar. Una barra de progreso con % subido, MB restantes y velocidad estimada, más botón Cancelar y reintento si falla. El clip queda marcado "MP4 listo" con su duración al terminar.
**Impacto:** Alto · **Esfuerzo:** Medio

### 7. KPI strip del día en Worker
**Pantalla:** Worker
El Worker hoy solo dice EN MARCHA/PARADO y una lista de actividad. Arriba, cuatro cifras grandes: publicados hoy, en cola, fallidos y próximo hueco de publicación (según límites diarios y franja horaria). Es la primera cosa que se ve al entrar y responde "¿cómo va el día?".
**Impacto:** Alto · **Esfuerzo:** Medio

### 8. Semáforo del Worker en el header (siempre visible)
**Pantalla:** Global (header)
La salud de la API y el estado del Worker hoy viven en una línea de texto pequeña que se pierde. Un punto de color fijo en el header — verde en marcha, ámbar parado, rojo API caída — con el estado al tocarlo, visible en las 5 pantallas sin hacer scroll.
**Impacto:** Alto · **Esfuerzo:** Bajo

### 9. Bottom nav móvil con badge de avisos
**Pantalla:** Global (navegación)
Las 5 pestañas hoy son botones en fila arriba que se envuelven y quedan lejos del pulgar. En móvil pasan a una barra inferior fija con iconos + etiqueta (Worker, Cola, Cuentas, Avisos con badge de no leídos, Ajustes), con área táctil mínima de 44×44 px y estado activo con acento neón.
**Impacto:** Alto · **Esfuerzo:** Medio

### 10. Segmentos LIVE / VOD + filtros y búsqueda en la Cola
**Pantalla:** Cola
La Cola hoy es una lista única ordenada por fecha, sin forma de encontrar nada. Dos segmentos arriba (LIVE con contador, VOD con contador), más filtros por plataforma y estado, y búsqueda por título. LIVE queda arriba por defecto, como manda la decisión de prioridad LIVE sobre VOD.
**Impacto:** Alto · **Esfuerzo:** Medio

### 11. Skeletons de carga por pantalla
**Pantalla:** Global
Al cambiar de pestaña hoy aparece contenido vacío o el texto "Sin actividad todavía" mientras carga la API. Cada pantalla muestra esqueletos con la forma de su contenido (tarjetas de clip, filas de avisos) durante la carga, para que nunca se vea una pantalla en blanco ni un salto de layout.
**Impacto:** Medio · **Esfuerzo:** Bajo

### 12. Empty states con ilustración y acción
**Pantalla:** Cola, Avisos, Cuentas, Worker
Los estados vacíos hoy son una frase gris ("La cola está vacía"). Cada uno gana una ilustración simple, una frase útil y su acción: Cola vacía → "El cerebro detecta clips solo" + botón de ejecutar un ciclo; sin avisos → "Todo al día"; sin cuentas → "Conecta tu primera plataforma".
**Impacto:** Medio · **Esfuerzo:** Bajo

### 13. Toasts en vez del banner #msg
**Pantalla:** Global
Todo el feedback hoy va a un banner `#msg` arriba que hay que buscar y que se pisa entre acciones. Pasan a ser toasts apilados abajo (éxito verde, error rojo, info azul) con auto-cierre, y confirmación animada (check) al guardar credenciales, publicar o cambiar ajustes.
**Impacto:** Medio · **Esfuerzo:** Bajo

### 14. Login premium con marca AVC
**Pantalla:** Login
El login hoy es una tarjeta genérica con dos inputs. Gana identidad: logo AVC, degradado oscuro con acento neón (guiño GTA), mostrar/ocultar contraseña, estado de carga en el botón ("Entrando…") y mensaje de error junto al campo, sin perder el aviso de que cada usuario ve solo sus datos.
**Impacto:** Medio · **Esfuerzo:** Bajo

### 15. Tarjetas de Cuentas con logo, 4 estados canónicos y verificar con spinner
**Pantalla:** Cuentas
Las cuentas hoy son tarjetas de texto con un pill; además el front aún muestra el estado ámbar "Sin verificar" que la auditoría v15 eliminó. Cada cuenta pasa a tarjeta con logo de plataforma, estado grande en uno de los 4 canónicos (Conectada / Sin credencial / No conectada / Sin crear), fecha de última verificación, y botón Verificar con spinner y resultado inmediato.
**Impacto:** Medio · **Esfuerzo:** Bajo

### 16. Ajustes por secciones con controles (adiós JSON crudo)
**Pantalla:** Ajustes
Los ajustes hoy son un `<textarea>` con JSON de 14 filas: un error de sintaxis rompe el guardado y nada explica cada clave. Pasan a secciones con controles reales — subtítulos (interruptor + estilo), límites diarios (deslizadores por plataforma), franja horaria (selectores de hora), Telegram (campo + probar) — con el JSON solo como vista avanzada plegable.
**Impacto:** Alto · **Esfuerzo:** Alto

### 17. Bottom sheet de detalle del clip en móvil
**Pantalla:** Cola
Hoy el detalle se abre abajo de la lista con `scrollIntoView`, lejos del clip tocado y sin forma clara de volver. En móvil el detalle se abre como hoja inferior deslizante (arrastrable para cerrar) con miniatura, score, matriz de plataformas, timeline y acciones; en escritorio sigue como panel lateral.
**Impacto:** Medio · **Esfuerzo:** Medio

### 18. Sistema de micro-interacciones (150–250 ms, reduced-motion)
**Pantalla:** Global
Hoy no hay feedback táctil: los botones cambian de estado sin transición. Press con leve escala (0,97), hover sutil en tarjetas, transiciones de 150–250 ms con easing, y respeto a `prefers-reduced-motion` (sin animación para quien la desactiva). Nada de animaciones decorativas que distraigan.
**Impacto:** Medio · **Esfuerzo:** Bajo

### 19. Avisos agrupados por día + "marcar todas como leídas"
**Pantalla:** Avisos
Los avisos hoy son una lista plana con un botón "Marcar leída" por fila. Se agrupan por día (Hoy / Ayer / fecha), con icono por tipo (publicado, fallo, freno, sistema), filtro por tipo y un botón "Marcar todas como leídas" que vacía el badge de una vez.
**Impacto:** Medio · **Esfuerzo:** Medio

### 20. Sistema oscuro OLED + acentos neón por plataforma
**Pantalla:** Global (design system)
Hoy hay un solo azul `#3b82f6` sobre `#0f1115`. Se define el sistema: fondo negro OLED `#000000`/`#121212`, azul medianoche `#0A0E27`, texto con contraste 7:1, acento neón propio por plataforma (rojo, cian, magenta, azul), tokens semánticos (`--ok`, `--bad`, `--warn`) en vez de hex sueltos, y tipografía con una sola familia + escala clara de tamaños.
**Impacto:** Medio · **Esfuerzo:** Medio

---

## Priorización final — Top 5

Ordenadas por valor inmediato para la pregunta que el panel debe responder a golpe de vista: **"¿qué pasa con mis clips ahora mismo?"**. Las cinco juntas transforman la Cola de tabla de texto en un tablero de control, sin tocar ninguna regla de negocio.

| # | Idea | Pantalla | Impacto | Esfuerzo | Por qué primero |
|---|------|----------|---------|----------|-----------------|
| 1 | **#3 Matriz de estados por plataforma** | Cola + detalle | Alto | Bajo | Es la duda nº 1 ("¿ya salió en TikTok?") y hoy exige leer texto plano por clip. |
| 2 | **#2 Barra de prioridad con desglose** | Cola | Alto | Bajo | Hace visible el orden real de la Cola (hoy se ordena por fecha) y explica por qué un clip va antes que otro. |
| 3 | **#4 Chips de freno** | Detalle | Alto | Bajo | Convierte un fallo silencioso en una causa legible con su solución al lado; evita publicar a ciegas. |
| 4 | **#1 Tarjeta de clip 9:16 con miniatura y anillo de score** | Cola | Alto | Medio | Es el cambio visual que más se nota: la Cola deja de ser una tabla y se reconoce cada clip por su imagen. |
| 5 | **#8 Semáforo del Worker en el header** | Global | Alto | Bajo | El estado del sistema (¿está publicando solo o parado?) debe verse siempre, no solo en la pestaña Worker. |

**Segunda oleada recomendada:** #6 (progreso de subida), #10 (LIVE/VOD + filtros), #7 (KPI del día) y #16 (Ajustes por secciones, la única de esfuerzo Alto).
