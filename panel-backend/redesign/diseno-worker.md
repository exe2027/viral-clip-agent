# Diseño — Pestaña Worker (panel AVC)

- **Fecha:** 2026-11-17
- **Rol:** 6 — Diseñador de Worker. **Solo especificación: no se edita el front.**
- **Alcance:** únicamente la pestaña Worker (`#tab-worker`) y el semáforo
  del Worker en el header (idea #8 del top 5). Nada de Cola, Cuentas,
  Avisos ni Ajustes. Nada de login.
- **Fuentes (en este orden):**
  `design-system/avc-panel/OVERRIDES.md` (autoritativo, manda sobre
  `MASTER.md` y sobre los valores generados), `MASTER.md` (solo se
  conservan densidad 8/10, motion 5/10, varianza 6/10 y radios 8/12/999),
  `tokens.css` / `tokens.json`, `redesign/auditoria-ux.md` (32 hallazgos),
  `redesign/auditoria-visual.md` (24 defectos), `redesign/ideas.md`
  (top 5: semáforo del Worker en header = idea #8).
- **Front leído:** `frontend/dist/index.html` (sección `#tab-worker`,
  header con `#healthLine` y `#logoutBtn`) y `frontend/dist/app.js`
  (`loadWorker`, `workerAction`, `checkHealth`, `fmtDate`).
- **Regla de oro de este diseño:** no cambia la lógica. Endpoints, ids,
  textos de éxito y formato de fecha se conservan tal cual (§7).
  Solo cambia cómo se ve, se lee y se confirma lo que ya existe.

---

## 1. Qué problema resuelve este diseño

Hoy la pestaña Worker funciona pero no se lee a golpe de vista:

- El estado (`EN MARCHA` / `PARADO` en `#wRunning`) es texto pequeño
  dentro de un párrafo, sin forma de semáforo y sin presencia fuera de
  la pestaña (auditoría UX-24: la salud vive en una línea pequeña que
  se pierde; idea #8).
- Los 4 botones miden ~37 px (auditoría UX-6 / VIS-7: por debajo de
  44 px), no muestran carga ni se deshabilitan durante la acción
  (UX-3, crítico: doble envío posible) y `Parar` se ejecuta al primer
  toque sin confirmar qué se para (UX-11, alta).
- La actividad es una lista plana `<ul>` sin jerarquía visual ni
  línea de tiempo (idea #5, adaptada aquí al Worker); al cargar no
  hay skeleton y no se distingue "cargando" de "vacío" (UX-22).
- No hay foco visible, ni feedback de prensa, ni `aria-live`
  (UX-1, UX-15, UX-16).

Este diseño corrige todo eso sin tocar un solo endpoint.

---

## 2. Estructura — orden visual (de arriba a abajo)

### 2.1 Header (global, siempre visible) — semáforo del Worker

El header ya existe: título, `#whoLine`, `.spacer`, `#healthLine`,
`#logoutBtn`. Se añade **dentro del header, a la izquierda de
`#healthLine`** (no se elimina ni se mueve nada de lo que hay):

```
[AVC — Panel de Clips GTA 6 · sebastian]  [● EN MARCHA]  [API conectada · …]  [Salir]
```

| Elemento | Contenido | Id / clase propuesta |
|---|---|---|
| Semáforo | Punto + texto de estado del Worker (siempre visible) | `.worker-light` (nuevo; sin id nuevo obligatorio — el implementador puede reutilizar el estado que ya calcula `checkHealth()` / `loadWorker()`) |
| Salud API | Sin cambio de contenido ni de id | `#healthLine` (se conserva; ver §2.1.1) |
| Salir | Sin cambio | `#logoutBtn` (se conserva) |

**Reglas del semáforo del header:**

- **Siempre texto + punto, nunca solo color** (OVERRIDES §4.5,
  UX-9 / VIS-10). El punto es un `.dot` CSS de 10 px
  (`width: 10px; height: 10px; border-radius: 50%`), no un glifo
  `●` ni un emoji (OVERRIDES §4, VIS-20: prohibidos `▶ ■ ●`
  como iconos; se sustituyen por SVG o por `.dot` CSS).
- Texto corto y exacto, igual que en la tarjeta (§3):
  `En marcha`, `Parado` o `API caída` (ver §3).
- Es un botón o enlace a la pestaña Worker: al tocarlo, abre
  `#tab-worker` (`switchTab("worker")`, la función ya existe).
  `aria-label` completo: `"Worker: en marcha. Ir a la pestaña Worker"`
  (o `parado` / `API caída` según el estado). No es solo
  decorativo: `aria-hidden="true"` solo en el punto, nunca en el texto.
- `aria-live="polite"` en el texto del semáforo: el cambio de estado
  se anuncia sin robar el foco.
- En móvil (≤ 640 px) el header hace `flex-wrap` (ya lo hace):
  el semáforo no se oculta ni se reduce a solo el punto; si falta
  espacio, baja a su propia línea antes que `#healthLine`.
  Nunca se recorta el texto a una sola letra.
- El estado del header y el de la tarjeta (`#wRunning`) muestran
  **siempre lo mismo**: ambos se alimentan del último
  `/api/worker/state` (y de `/api/health` para el caso `API caída`).
  Si discrepan, manda el de la tarjeta, que es el dato fresco.

#### 2.1.1 `#healthLine` — sin cambio de diseño en esta spec

`#healthLine` se conserva tal cual (texto `API conectada · Worker:
…` / `API no responde`). La corrección pendiente de UX-24
(botón "Reintentar" + hora del último intento) pertenece al
diseño global del header, no a la pestaña Worker, y queda fuera
de este documento. El semáforo nuevo no sustituye a `#healthLine`:
uno dice si el **Worker** avanza la cola, la otra si la **API**
responde. Cuando la API cae, el semáforo pasa a rojo `API caída`
(§3) y `#healthLine` sigue mostrando `API no responde`.

### 2.2 Tarjeta 1 — Estado del Worker

Conserva la estructura actual y la reordena en tres bloques
visuales. **No se cambia ningún id.**

```
┌─ Card: Worker ─────────────────────────────────────┐
│  h2  Worker                                          │
│                                                       │
│  ┌─ Semáforo grande ─────────────────────────────┐   │
│  │  ●  EN MARCHA                                  │   │
│  │     desde 17 nov 2026, 15:24                   │   │
│  └────────────────────────────────────────────────┘   │
│                                                       │
│  p.dim #wNote — nota del Worker, si la hay            │
│                                                       │
│  [▶ Iniciar]  [■ Parar]                               │
│  [Ejecutar un ciclo ahora]  [Actualizar]              │
│                                                       │
│  p.dim — texto explicativo (sin cambio)               │
└───────────────────────────────────────────────────────┘
```

| Bloque | Elementos | Notas |
|---|---|---|
| Título | `h2` "Worker" | 16 px, 700, `line-height: 1.25` (§5). |
| Semáforo grande | `#wRunning` + `#wStarted` | §3: punto 16 px + pill + texto grande. Es el elemento dominante de la tarjeta. |
| Nota | `#wNote` (`p.dim`) | Texto tal cual lo devuelve `s.lastNote`. Si viene vacío, el párrafo queda vacío/oculto: no se inventa texto. |
| Acciones | `#btnStart`, `#btnStop`, `#btnRunOnce`, `#btnRefreshW` | §4: 44 px, loading, confirmación en Parar. Contenedor `.row.row-fill` (ver §5.4). |
| Explicación | `p.dim` existente | Texto **sin cambio**: "Al iniciar, el Worker dispara el cerebro en GitHub Actions y el cron cada 5 min avanza la cola solo. Parado, la cola queda en pausa y nada se pierde." Es la frase que la confirmación de Parar debe repetir (§4.3). |

### 2.3 Tarjeta 2 — Actividad reciente (timeline)

Conserva el contenedor y el id de la lista. Cambia el render
de cada fila: de línea de texto plana a **timeline vertical**
(adaptación al Worker de la idea #5, pensada para seguimiento).

```
┌─ Card: Actividad reciente ───────────────────────────┐
│  h2  Actividad reciente                               │
│                                                       │
│  │ ●  17 nov 2026, 15:24                              │
│  │    ciclo — 3 jobs procesados, 0 atascados          │
│  │                                                     │
│  │ ●  17 nov 2026, 15:19                              │
│  │    inicio — Worker iniciado                        │
│  │                                                     │
│  └  (o, si no hay nada: empty state, §6)              │
└───────────────────────────────────────────────────────┘
```

- Contenedor: `.card` + `h2` "Actividad reciente" (sin cambio).
- Lista: `<ul class="activity timeline" id="wActivity">`
  (el id `#wActivity` se conserva; la clase `timeline` es nueva).
- Cada evento es un `<li>` con:
  1. **Fecha y hora en su propia línea, en negrita** — `<time>`
     con el texto de `fmtDate(a.at)` (ya existe:
     `toLocaleString("es-CL", { timeZone: "America/Santiago" })`).
     Nunca solo la hora ni solo la fecha: siempre fecha + hora.
  2. **Tipo (`a.kind`) en negrita + mensaje (`a.message`)**
     en la línea siguiente, en texto normal.
  3. Punto (`.dot`, 8 px) sobre una línea vertical continua
     a la izquierda (la "línea de tiempo").
- Orden: el que devuelve la API (más reciente primero, como hoy).
  No se reordena, no se pagina ni se trunca en esta spec.
- El último evento (el más reciente) lleva el punto en
  `--color-ok` si el Worker está en marcha y en
  `--color-text-dim` si está parado; los anteriores, siempre en
  `--color-text-dim`. No se inventa un color por tipo de evento:
  `a.kind` no trae severidad en la API actual y no se colorea
  por adivinanza. La única distinción es recencia, no tipo.
- `line-height: 1.5` en cada fila (VIS-12); separación entre
  eventos `var(--space-3)` (12 px).

---

## 3. Estados — el semáforo (tarjeta + header)

El estado es **siempre punto + texto**. Nunca solo color
(UX-9, VIS-10, OVERRIDES §4.5). El texto de la tarjeta es
mayúsculas (`EN MARCHA` / `PARADO`, como hoy); el del header,
capitalizado (`En marcha` / `Parado`).

| Estado | Cuándo | Punto (tarjeta 16 px / header 10 px) | Pill / texto (tarjeta) | Texto (header) | `#wStarted` |
|---|---|---|---|---|---|
| **EN MARCHA** | `s.running === true` | `--color-ok` `#22c55e` | `.pill.ok` — `EN MARCHA` (fondo `--color-ok-bg` `#14532d`, texto `--color-ok-fg` `#bbf7d0`, 7.52:1) | `● En marcha` | `desde ` + `fmtDate(s.startedAt)` (solo si `startedAt` existe; si no, vacío) |
| **PARADO** | `s.running === false` | `--color-warn` `#f59e0b` | `.pill.warn` — `PARADO` (fondo `--color-warn-bg` `#451a03`, texto `--color-warn-fg` `#fde68a`, 12.03:1) | `● Parado` | Vacío (no se inventa un "parado desde": la API no lo devuelve) |
| **API caída** | `GET /api/worker/state` (o `/api/health`) falla | `--color-bad` `#f87171` | `.pill.bad` — `SIN CONEXIÓN` (fondo `--color-bad-bg` `#7f1d1d`, texto `--color-bad-fg` `#fecaca`, 6.93:1) | `● API caída` | Vacío |
| **Comprobando…** | Carga inicial / `Actualizar` en curso (antes de la primera respuesta) | `--color-text-dim` `#9aa3b2`, sin parpadeo | Texto `.dim` — `Comprobando…` (sin pill) + skeleton (§6.1) | `● Comprobando…` | Vacío |

Notas:

- La pill `.pill.warn` no existe hoy en el front (VIS-10 la pide:
  fondo `#451a03`, texto `#fde68a`). Se crea con los tokens de
  OVERRIDES §3; no se reutiliza la pill neutra gris para `PARADO`.
- El punto grande de la tarjeta (16 px) va a la izquierda del
  texto `EN MARCHA` / `PARADO`, en la misma línea, con `gap: 8px`.
  El texto del estado es `font-size: 16px`, `font-weight: 700`.
- `PARADO` es ámbar, no rojo: parado es un estado normal y
  reversible ("la cola queda en pausa y nada se pierde"), no un
  error. El rojo queda reservado para `API caída` / error real.
  Es la misma correspondencia de OVERRIDES §4.5 (verde en marcha,
  ámbar parado, rojo API caída).
- `prefers-reduced-motion: reduce` → el punto no pulsa ni anima
  nunca. No hay animación de pulso en este diseño: el estado se
  comunica con punto + texto, no con movimiento.
- `#wRunning` conserva su id y su contenido en HTML es
  `<span class="pill ok|warn|bad">EN MARCHA|PARADO|SIN CONEXIÓN</span>`
  precedido del punto CSS. El implementador mantiene el patrón
  actual (`$("wRunning").innerHTML = …` en `loadWorker()`).

---

## 4. Botones — 4 acciones, 44 px, loading, confirmación

### 4.1 Tabla de botones (ids y endpoints sin cambio)

| Id | Etiqueta (reposo) | Variante | Endpoint | Texto de éxito en `#msg` (sin cambio, `workerAction`) |
|---|---|---|---|---|
| `#btnStart` | Iniciar | Primario | `POST /api/worker/start` | "Worker iniciado: la cola avanza sola." |
| `#btnStop` | Parar | Peligro | `POST /api/worker/stop` | "Worker parado: la cola queda en pausa." |
| `#btnRunOnce` | Ejecutar un ciclo ahora | Secundario (ghost) | `POST /api/worker/run-once` | "Ciclo ejecutado." (+ ` (N jobs procesados, M atascados).` cuando la respuesta trae `processed`) |
| `#btnRefreshW` | Actualizar | Secundario (ghost) | `GET /api/worker/state` (solo `loadWorker()`, sin POST) | Sin mensaje (recarga el estado y la actividad) |

- **Iconos:** los glifos `▶` (Iniciar) y `■` (Parar) del front
  actual se sustituyen por **SVG inline** (play / stop, una sola
  familia, trazo 1.5 px, 16×16, `aria-hidden="true"`) o se eliminan
  y queda solo el texto (VIS-20, OVERRIDES §4). Nunca emoji.
  El texto manda: sin icono también se entiende.
- **Jerarquía:** una sola acción primaria por tarjeta
  (`Iniciar`, auditoría visual "lo que está bien"). `Parar` es
  peligro, no primario. `Ejecutar un ciclo ahora` y `Actualizar`
  son ghost. No hay dos primarios.
- **Layout:** contenedor `.row.row-fill` — los 4 botones se
  estiran por igual **solo aquí** (VIS-9: `.row > button`
  es `flex: 0 0 auto` por defecto; el estirado es explícito con
  `.row-fill`). En móvil (≤ 640 px) se reparten en **2 columnas
  × 2 filas** (`flex-wrap` ya existe); en escritorio, 4 en una
  fila si caben. `gap: 10px` entre botones.
  `min-width` por botón: 140 px (el actual), sin recortar texto.

### 4.2 Tamaño, foco y prensa (todos los botones)

- `min-height: 44px` (`--size-touch-target`), `padding: 10px 16px`,
  `font-size: 16px` para la etiqueta del botón (el cuerpo es 16 px;
  la etiqueta no baja a 14 px), `border-radius: 8px`
  (`--radius-control`), `line-height: 1.5`.
- `:focus-visible { outline: 3px solid var(--color-focus);
  outline-offset: 2px; }` (UX-1, OVERRIDES §1).
- `button:hover { filter: brightness(1.12); }`,
  `button:active { transform: scale(.98); }`,
  `touch-action: manipulation` (UX-15, tokens.css).
- `button:disabled { opacity: .55; cursor: not-allowed; }`
  (OVERRIDES §1, UX-3). Un botón deshabilitado no responde al
  toque ni al teclado.
- Transiciones 150–250 ms, `ease-out`; anuladas bajo
  `prefers-reduced-motion: reduce` (tokens.css, UX-27).

### 4.3 Estados de cada botón durante la acción (loading)

Regla general (UX-3, crítico; OVERRIDES §1): **toda acción
asíncrona deshabilita su botón y cambia su texto hasta resolver**.
Igual que ya hace `doLogin()` con `loginBtn`.

| Botón | Texto durante la acción | Comportamiento |
|---|---|---|
| `#btnStart` | `Iniciando…` | `disabled` hasta resolver. Al resolver con éxito, `loadWorker()` refresca el estado y el botón vuelve a `Iniciar`. |
| `#btnStop` | `Parando…` | `disabled` hasta resolver. Solo se muestra tras aceptar la confirmación (§4.4). Si se cancela la confirmación, no hay loading ni llamada. |
| `#btnRunOnce` | `Ejecutando ciclo…` | `disabled` hasta resolver. |
| `#btnRefreshW` | `Actualizando…` | `disabled` mientras `loadWorker()` está en curso. |

- **Durante cualquier acción del Worker, los 4 botones quedan
  deshabilitados** (no solo el pulsado): así no se puede pulsar
  `Parar` mientras `Iniciar` aún no resolvió, ni lanzar dos
  ciclos a la vez. Al resolver (éxito o error), los 4 vuelven a
  su estado según el Worker (ver §4.5) y recuperan su etiqueta.
- **Si la acción falla:** `#msg` con `role="alert"`, clase
  `.msg.err` (fondo `--color-bad-bg`, texto `--color-bad-fg`),
  mensaje de error de la API; los botones recuperan su etiqueta
  y el semáforo **no cambia** hasta el próximo `loadWorker()`
  correcto. No se muestra un estado falso.
- El texto de carga lleva siempre `…` (puntos suspensivos) y es
  el **único** cambio del botón: no aparece spinner aparte en
  esta spec (el texto + `disabled` es el feedback; un spinner
  CSS de 16 px es aceptable si el implementador lo prefiere,
  siempre junto al texto, nunca en su lugar).

### 4.4 Confirmación antes de Parar (obligatoria)

`Parar` es la única acción del Worker que pide confirmación
(UX-11: "Parar el Worker y Publicar no piden confirmación").
`Iniciar`, `Ejecutar un ciclo ahora` y `Actualizar` **no**
piden confirmación: son reversibles o de solo lectura.

- **Mecanismo:** `confirm()` nativo, como propone la auditoría
  UX-11. Sin diálogo propio en esta spec (menos código, mismo
  efecto; el diálogo propio queda para una iteración posterior).
- **Texto exacto del diálogo:**

  > **¿Parar el Worker?**
  >
  > La cola queda en pausa y nada se pierde. El cron de cada
  > 5 minutos deja de avanzar la cola y no se publica nada nuevo
  > hasta que vuelvas a pulsar Iniciar. Los clips ya publicados
  > no se borran.

  La primera frase repite el texto explicativo de la tarjeta
  (§2.2); el resto dice **qué se para** (el avance automático
  cada 5 min) y **qué no se pierde** (la cola, los clips).
- **Si cancela:** no se llama a `/api/worker/stop`, no cambia
  nada, el foco vuelve a `#btnStop`.
- **Si acepta:** botón a `Parando…` + `disabled` (§4.3) y se
  ejecuta `workerAction("/api/worker/stop", …)` como hoy.
- El diálogo se muestra **antes** de cualquier cambio visual:
  pulsar `Parar` con el Worker ya parado no debería llegar al
  diálogo (ver §4.5: el botón está deshabilitado en ese caso).

### 4.5 Cuándo está deshabilitado cada botón (sin acción en curso)

El estado deshabilitado se deriva del último `loadWorker()`
correcto y se recalcula en cada `loadWorker()` y tras cada
acción. No es lógica nueva: es presentación del estado que la
API ya devuelve.

| Botón | Deshabilitado cuando… | Motivo visible |
|---|---|---|
| `#btnStart` | Worker `EN MARCHA` | Ya está en marcha; no hay nada que iniciar. (`title` / `aria-disabled` con el motivo: "El Worker ya está en marcha".) |
| `#btnStop` | Worker `PARADO` o `SIN CONEXIÓN` | No hay nada que parar / no hay conexión con la API. |
| `#btnRunOnce` | `SIN CONEXIÓN` (la API no responde) | Sin conexión no se puede ejecutar el ciclo. En `PARADO` **sigue habilitado**: un ciclo manual es justo lo que se hace con el Worker parado. |
| `#btnRefreshW` | `SIN CONEXIÓN` no lo deshabilita | `Actualizar` es el reintento: siempre habilitado salvo durante su propia carga. |

- Si el estado aún no se conoce (primera carga, `Comprobando…`),
  los 4 botones parten deshabilitados hasta la primera respuesta
  de `/api/worker/state`, para no dejar pulsar a ciegas.
- Un botón deshabilitado por estado (no por carga) mantiene su
  etiqueta normal (`Iniciar`, `Parar`), solo con `opacity: .55`.

---

## 5. Tokens — lo que usa esta pestaña (todos de OVERRIDES / tokens.css)

**Prohibido el hex crudo en componentes** (VIS-8, OVERRIDES §1).
Todo color pasa por un `--color-*` de esta tabla. Los valores son
los de `tokens.css` (fuente: `tokens.json`).

### 5.1 Color

| Uso en Worker | Token | Valor | Contraste verificado |
|---|---|---|---|
| Fondo de página | `--color-background` | `#0b0d12` | — |
| Tarjeta | `--color-surface` | `#1e222c` | Borde 2.10:1 |
| Borde de tarjeta / línea de timeline | `--color-border` | `#475569` | 2.10:1 sobre tarjeta |
| Texto principal | `--color-text` | `#e8eaf0` | 16.16:1 sobre fondo / 13.22:1 sobre tarjeta |
| Texto secundario (`.dim`, fechas secundarias) | `--color-text-dim` | `#9aa3b2` | 7.64:1 / 6.25:1 |
| Botón primario (Iniciar) | `--color-primary` | `#1d4ed8` | Blanco 6.70:1 |
| Hover primario (solo con texto blanco) | `--color-primary-hover` | `#2563eb` | Blanco 5.17:1 |
| Texto sobre primario | `--color-on-primary` | `#ffffff` | — |
| Foco visible | `--color-focus` | `#93c5fd` | 10.78:1 sobre fondo (≥ 3:1) |
| Punto / pill EN MARCHA | `--color-ok` / `--color-ok-bg` / `--color-ok-fg` | `#22c55e` / `#14532d` / `#bbf7d0` | Texto 8.53:1; pill 7.52:1 |
| Punto / pill SIN CONEXIÓN | `--color-bad` / `--color-bad-bg` / `--color-bad-fg` | `#f87171` / `#7f1d1d` / `#fecaca` | Texto 7.03:1; pill 6.93:1 |
| Punto / pill PARADO | `--color-warn` / `--color-warn-bg` / `--color-warn-fg` | `#f59e0b` / `#451a03` / `#fde68a` | Texto 9.05:1; pill 12.03:1 |
| Botón Parar | `--color-danger-bg` | `#7f1d1d` | Blanco 10.02:1 |
| Mensaje info (`#msg`) | `--color-info-bg` | `#1e3a5f` | Blanco 11.50:1 |
| Skeleton | `--color-surface-input` | `#0f1115` | — |

**Valores rechazados aquí:** `#3b82f6` (blanco 3.68:1, falla AA),
`#ef4444` como texto (4.23:1), `#2563eb` con texto `#e8eaf0`
(4.30:1). Ver OVERRIDES §2.

### 5.2 Tipografía

| Elemento | Token / valor |
|---|---|
| Familia (cuerpo y títulos) | `--font-body` / `--font-heading`: `system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif` (sin webfonts, $0; Cormorant/Montserrat del `MASTER.md` generado quedan descartadas) |
| Cuerpo / etiqueta de botón | `--font-size-base: 16px`, `line-height: 1.5` |
| `h2` (Worker, Actividad reciente) | `--font-size-h2: 16px`, `font-weight: 700`, `line-height: 1.25` |
| Estado grande (`EN MARCHA` / `PARADO`) | 16 px, `font-weight: 700`, `line-height: 1.25` |
| Texto secundario (`.dim`, `#wNote`, explicación) | `--font-size-small: 13px`, `line-height: 1.5`, `--color-text-dim` |
| Fecha/hora de cada evento | 13 px, `font-weight: 700`, `--color-text`, `line-height: 1.5` |
| Fechas / datos | `font-variant-numeric: tabular-nums` (las horas no bailan de ancho) |

### 5.3 Espaciado, radios, sombras

| Uso | Token / valor |
|---|---|
| Gap entre botones | `10px` (fila `.row` actual) |
| Gap semáforo (punto ↔ texto) | `--space-2: 8px` |
| Separación entre eventos del timeline | `--space-3: 12px` |
| Padding de tarjeta | `--space-4: 16px` |
| Margen entre tarjetas | `--space-4: 16px` (la tarjeta actual usa 14 px; se unifica a la escala) |
| Radio de botón | `--radius-control: 8px` |
| Radio de tarjeta | `--radius-card: 12px` |
| Radio de pill | `--radius-pill: 999px` |
| Sombra de tarjeta | `--shadow-card: 0 1px 2px rgba(0,0,0,.4)` |
| Padding de página / safe-area | `main` con `env(safe-area-inset-*)` (UX-28, VIS-23); `color-scheme: dark` + `<meta name="theme-color" content="#0b0d12">` ya definidos en tokens.css |

### 5.4 Clases propuestas (el implementador las crea; no existen hoy)

| Clase | Uso |
|---|---|
| `.worker-light` | Semáforo del header: punto + texto, botón/enlace a `#tab-worker`. |
| `.worker-status` | Semáforo grande de la tarjeta: punto 16 px + pill + `#wStarted` debajo. |
| `.dot` | Punto CSS (`8px` en timeline, `10px` en header, `16px` en tarjeta grande). Variantes por color vía el token, no por hex. |
| `.pill.warn` | **Nueva:** fondo `--color-warn-bg`, texto `--color-warn-fg` (ver §3). `.pill.ok` / `.pill.bad` ya existen. |
| `.row-fill` | Fila de botones del Worker con estirado explícito (VIS-9). |
| `.timeline` / `.timeline li` | Lista de actividad como línea de tiempo (§2.3, §6). |
| `.skeleton` | Filas de carga (§6.1), con shimmer desactivado bajo `prefers-reduced-motion`. |
| `.empty-state` | Estado vacío de actividad (§6.2). |

---

## 6. Actividad reciente — carga, vacío y error

### 6.1 Cargando (skeleton, no vacío falso)

Mientras `loadWorker()` está en curso y `#wActivity` aún no tiene
datos, se muestran **3 filas skeleton** con `aria-busy="true"` en
la lista (UX-22, idea #11):

- Cada fila: punto gris + dos líneas (`--color-surface-input`,
  radio 8 px). Sin texto real.
- El semáforo muestra `Comprobando…` (§3) al mismo tiempo.
- Al resolver, el skeleton se sustituye por el timeline o por el
  empty state. Nunca se ve "Sin actividad todavía" mientras carga:
  ese texto significa "la API respondió y no hay eventos", no
  "aún no sabemos".

### 6.2 Vacío — texto exacto

Si la API responde y `s.activity` viene vacía o ausente, se muestra
el empty state con el **texto actual, sin cambio**:

> **Sin actividad todavía.**

- Render actual que se conserva: `<li class='dim'>Sin actividad
  todavía.</li>` — en el nuevo diseño pasa a ser un bloque
  `.empty-state` con ese texto como título, sin punto de timeline
  ni línea vertical (no hay eventos que unir).
- Subtexto (nuevo, 13 px `.dim`):

  > Cuando el Worker haga algo, aparecerá aquí con su fecha y hora.

- **Acción:** el empty state no añade un botón duplicado. La
  acción ya existe en la tarjeta de arriba (`Iniciar` /
  `Ejecutar un ciclo ahora`, §4); el subtexto puede enlazar a
  ella con el texto "Ejecuta un ciclo o inicia el Worker para ver
  la primera actividad." (idea #12: vacío con acción; UX-25:
  vacío con vía de acción — aquí la vía es la tarjeta de Estado,
  no un segundo botón con el mismo endpoint).
- Sin ilustración en esta spec (idea #12: la ilustración es
  opcional y queda para el rediseño visual global).

### 6.3 Error al cargar el estado

Si `GET /api/worker/state` falla:

- Semáforo (tarjeta + header) a `SIN CONEXIÓN` / `API caída` (§3).
- `#msg` con `role="alert"`, `.msg.err`, el mensaje de error de
  la API. No se borra el último estado conocido sin avisar:
  si había un estado previo en pantalla, queda visible con el
  semáforo en rojo indicando que no está actualizado.
- `#wActivity` conserva su contenido anterior (no se vacía a
  "Sin actividad todavía" por un fallo de red: eso mentiría).
- `Actualizar` sigue habilitado: es el reintento (§4.5).

---

## 7. Lo que NO cambia (límite del diseño)

| Elemento | Se conserva tal cual |
|---|---|
| Endpoints | `GET /api/worker/state`, `POST /api/worker/start`, `POST /api/worker/stop`, `POST /api/worker/run-once`, `GET /api/health` (base `https://avc.vikingosfoodspa.workers.dev`). Ninguno cambia de ruta, método ni payload. |
| Ids | `#wRunning`, `#wStarted`, `#wNote`, `#wActivity`, `#btnStart`, `#btnStop`, `#btnRunOnce`, `#btnRefreshW`, `#healthLine`, `#msg`, `#tab-worker`. Ningún id se renombra ni se duplica. |
| Funciones | `loadWorker()`, `workerAction(path, okText)`, `checkHealth()`, `fmtDate()` (es-CL, `America/Santiago`), `switchTab()`. Solo se amplía lo que renderizan, no lo que piden. |
| Textos de éxito | "Worker iniciado: la cola avanza sola.", "Worker parado: la cola queda en pausa.", "Ciclo ejecutado." (+ ` (N jobs procesados, M atascados).`). |
| Texto explicativo | El párrafo de GitHub Actions / cron 5 min / "nada se pierde" (§2.2) no se reescribe. |
| Texto vacío | "Sin actividad todavía." (§6.2). |
| Datos | No se añade paginación, filtros ni límite a la actividad; no se inventan campos (`startedAt` parado, severidad por `kind`, duración del ciclo). Lo que la API no devuelve, no se muestra. |
| Fuera de alcance | KPI strip del día (idea #7), toasts en vez de `#msg` (idea #13), bottom nav (idea #9), segmentos LIVE/VOD (idea #10), hash de pestaña en URL (UX-8). Se nombran para no confundirlos con este diseño; pertenecen a otros roles. |

---

## 8. Textos en español — catálogo completo de la pestaña

Todos los textos visibles de la pestaña Worker, para que el
implementador no tenga que inventar ninguno:

| Dónde | Texto exacto |
|---|---|
| `h2` tarjeta 1 | Worker |
| Semáforo (tarjeta) | `EN MARCHA` / `PARADO` / `SIN CONEXIÓN` / `Comprobando…` |
| Semáforo (header) | `En marcha` / `Parado` / `API caída` / `Comprobando…` |
| `#wStarted` | `desde 17 nov 2026, 15:24` (formato de `fmtDate`; solo si hay `startedAt`) |
| `#wNote` | Tal cual lo devuelva la API (`s.lastNote`); vacío si no hay. |
| `#btnStart` | `Iniciar` → durante la acción: `Iniciando…` |
| `#btnStop` | `Parar` → durante la acción: `Parando…` |
| `#btnRunOnce` | `Ejecutar un ciclo ahora` → durante la acción: `Ejecutando ciclo…` |
| `#btnRefreshW` | `Actualizar` → durante la acción: `Actualizando…` |
| Confirmación Parar | Ver §4.4 (título + qué se para + qué no se pierde). |
| Explicación | "Al iniciar, el Worker dispara el cerebro en GitHub Actions y el cron cada 5 min avanza la cola solo. Parado, la cola queda en pausa y nada se pierde." |
| `h2` tarjeta 2 | Actividad reciente |
| Evento del timeline | Fecha y hora (`fmtDate`) en negrita + `kind` en negrita + `message`. |
| Vacío | `Sin actividad todavía.` + "Cuando el Worker haga algo, aparecerá aquí con su fecha y hora." |
| Cargando | `Comprobando…` (semáforo) + skeleton sin texto. |
| Éxito / error | Textos de `workerAction` (§7) y errores de la API en `#msg`. |

Reglas de redacción: español neutro (el panel es `lang="es"`),
sin jerga de desarrollo en lo visible (nada de `seed-users.sql`,
`jobs`, `stalled` o `queued` sin traducir en la interfaz; los
contadores de jobs solo aparecen dentro del mensaje de éxito ya
existente), sin emojis, con `…` (no `...`) en los textos de carga.

---

## 9. Accesibilidad y responsive — checklist de la pestaña

- [ ] Foco visible 3 px `--color-focus` en los 4 botones y en el
  semáforo del header (UX-1).
- [ ] Estado nunca solo por color: punto + texto en tarjeta y en
  header; pill con texto (UX-9, VIS-10).
- [ ] Contrastes verificados (§5.1): primario/blanco 6.70:1,
  pill warn 12.03:1, pill ok 7.52:1, pill bad 6.93:1, texto
  16.16:1. Ningún par por debajo de 4.5:1.
- [ ] Botones 44 px, gap 10 px, sin texto recortado a 360 px;
  2×2 en móvil, 4 en fila en escritorio; sin scroll horizontal
  (UX-6, VIS-7, checklist pre-entrega).
- [ ] Botón async: `disabled` + texto `…` hasta resolver; sin
  doble envío (UX-3).
- [ ] Confirmación antes de Parar, con qué se para (§4.4, UX-11).
- [ ] `aria-live="polite"` en el semáforo (header y tarjeta) y en
  `#msg` para éxitos; `role="alert"` en `#msg` para errores
  (UX-16, OVERRIDES §1). El cambio de estado no roba el foco.
- [ ] Fecha/hora de cada evento en `<time>` con `datetime`
  (ISO) y texto legible; `tabular-nums` en horas.
- [ ] Skeleton con `aria-busy="true"` mientras carga; empty
  state distinguible de la carga (§6).
- [ ] `prefers-reduced-motion: reduce`: sin transiciones, sin
  pulso, sin animación (tokens.css, UX-27).
- [ ] `color-scheme: dark`, `theme-color #0b0d12`, safe-area
  (tokens.css, UX-28).
- [ ] Sin hex crudo fuera de `tokens.css`; sin glifos `▶ ■ ●`
  como iconos; iconos SVG con `aria-hidden="true"` (VIS-8, VIS-20).
- [ ] Teclado: Tab llega a los 4 botones y al semáforo del
  header en orden visual; Enter/Espacio activan; Esc/cancelar en
  el `confirm()` devuelve el foco a `#btnStop`.
- [ ] Verificado a 375 px (y 360 px) y en escritorio: el
  semáforo del header no tapa el título ni empuja `#healthLine`
  fuera de la pantalla.

---

## 10. Verificación antes de darlo por hecho (implementador)

1. Con el Worker parado: abrir la pestaña — semáforo ámbar
   `PARADO` en tarjeta y header, `Iniciar` habilitado, `Parar`
   deshabilitado, actividad como timeline o "Sin actividad todavía.".
2. Pulsar `Iniciar` — botón a `Iniciando…` + deshabilitado
   (los 4), luego `EN MARCHA` verde + `desde <fecha>` + mensaje
   "Worker iniciado: la cola avanza sola.".
3. Pulsar `Parar` — aparece el diálogo del §4.4; cancelar no
   cambia nada; aceptar → `Parando…` → `PARADO`.
4. Pulsar `Ejecutar un ciclo ahora` con el Worker parado —
   funciona (no pide confirmación, no exige estar en marcha) y
   el mensaje trae `(N jobs procesados, M atascados).`.
5. Pulsar `Actualizar` dos veces seguidas — la segunda no se
   dispara hasta resolver la primera; el texto es `Actualizando…`.
6. Simular API caída (o estado sin respuesta) — semáforo rojo
   `SIN CONEXIÓN` / `API caída`, error en `#msg` con
   `role="alert"`, actividad anterior conservada.
7. Teclado + lector de pantalla: el cambio `PARADO → EN MARCHA`
   se anuncia; el foco es visible en cada botón.
8. 375 px: botones 2×2 sin scroll horizontal, semáforo del
   header visible sin recortar, timeline legible.
