# Diseño — Cuentas sociales + APIs (panel AVC, fase 2)

- **Rol:** Rol 3 de 10 — Diseñador Cuentas + APIs. Solo especificación visual y de
  contenido. No se escribe código de implementación, no se toca lógica de
  negocio ni endpoints.
- **Fecha:** 2026-11-17
- **Fuentes leídas y respetadas:**
  - `redesign-fase2/inventario.md` (mapa endpoint-por-endpoint; manda en lo que
    existe y lo que no existe).
  - `design-system/avc-panel/OVERRIDES.md` §8 (variante GTA 6 / Vice City),
    `tokens-gta6.css`, `tokens-gta6.json`.
  - `frontend/dist/index.html` (pestaña `#tab-cuentas` actual) y `app.js`
    (`loadAccounts`, `accountState`, `accountStatusLine`, líneas 617–745).
  - `worker/src/index.ts` (rutas reales de cuentas), `worker/src/verify.ts`,
    `worker/src/db.ts` (`api_accounts`), `worker/src/crypto.ts`
    (`maskCredential`).
- **Nombres:** en estas dos pestañas no aparece ningún nombre personal. Solo
  existen los usernames de sesión (`seba` / `chacal`), que ya muestra el
  header (`#whoLine`) y que aquí no se repiten.

---

## 0. Realidad que el diseño refleja (no negociable)

### 0.1 Endpoints reales de cuentas (verificados en `worker/src/index.ts`)

| Método + ruta | Qué hace | Lo usa el diseño |
|---|---|---|
| `GET /api/accounts` | Lista las cuentas API del tenant: `{id, platform, language, label, status, note, verified_at, verify_ok, verify_message, has_credential, credential_masked}`. Nunca devuelve la credencial. | Sí — pestaña APIs |
| `POST /api/accounts/:id/credential` | Guarda `{credential, extra}`; cifra con AES-GCM por tenant, borra el texto plano legacy y pone `status = 'pendiente'`. Devuelve `{ok, masked}`. | Sí — pestaña APIs (única "edición" que existe) |
| `POST /api/accounts/:id/verify` | Verificación real contra la plataforma (`verify.ts`): YouTube (API key `AIza…` o token OAuth), TikTok (`open.tiktokapis.com`), Meta (Graph `/me` con el token). Devuelve `{ok, status, message, verifiedAt}`. Sin credencial cifrada responde 400. | Sí — pestaña APIs |

### 0.2 Lo que NO existe (y el diseño no finge)

| Acción / dato | Estado real | Cómo lo muestra el diseño |
|---|---|---|
| Cuentas sociales (`accounts`, migración 0002) | **SIN ENDPOINT.** Ninguna ruta lee esa tabla. | Pestaña Cuentas sociales: 4 tarjetas en estado vacío real, botón Verificar deshabilitado, nota de pendiente (§2). |
| Crear cuenta API (`POST /api/accounts`) | **SIN ENDPOINT.** Las filas solo existen si ya están en D1 (migración del panel anterior). | Botón "Añadir cuenta" deshabilitado con nota de pendiente (§3.6). Nunca abre un formulario que no pueda guardar. |
| Eliminar cuenta API | **SIN ENDPOINT.** | Botón "Eliminar" deshabilitado con nota de pendiente (§3.6). Nunca borra en el front. |
| Editar `label` / `language` / `platform` | **SIN ENDPOINT.** Solo se puede pegar credencial + `extra`. | `label`, plataforma e idioma se muestran como texto de solo lectura, no como inputs. |
| Salud por canal / plataforma | **SIN ENDPOINT** (solo `GET /api/health` genérico). | No hay semáforo por plataforma. El estado de una cuenta es solo su pill de verificación (§4). |

### 0.3 Corrección al encargo: la máscara de la credencial

El encargo pedía "solo máscara últimos 4 caracteres". La realidad verificada
es más precisa y el diseño la respeta:

- En la **lista** (`GET /api/accounts`), `credential_masked` **no** es
  `****1234`: es el texto literal `"cifrada en D1"`, o `"texto plano legacy:
  volver a pegar"`, o vacío si no hay credencial. La lista muestra ese texto
  tal cual. **Nunca se inventa un `****1234` en la lista.**
- Los últimos 4 caracteres (`****` + últimos 4, `maskCredential` en
  `crypto.ts`) solo existen en la **respuesta del POST de credencial**
  (`r.masked`) y se muestran únicamente en el aviso de éxito tras guardar:
  "Credencial guardada (****1234). Ahora pulsa Verificar."
- Si el desarrollador quiere `****1234` también en la lista, es un cambio
  del Worker (que `GET /api/accounts` devuelva la máscara), no del front.
  Queda como pendiente de backend (§7), no se diseña como si existiera.

---

## 1. Estructura: dos sub-pestañas dentro de `#tab-cuentas`

Hoy `#tab-cuentas` es una sola vista que muestra solo cuentas API
(`#accountList`). El diseño la divide en **dos sub-pestañas** sin romper la
pestaña exterior:

- **No se toca** `TAB_NAMES`, el hash `#cuentas`, `switchTab` ni `loadTab`
  (inventario §3.1). La división vive **dentro** de `#tab-cuentas`, como un
  control segmentado con `role="tablist"`.
- **Sub-pestaña por defecto: APIs.** Es la única con contenido real y
  acciones que funcionan. La sub-pestaña social es informativa (vacío real)
  hasta que exista su endpoint.
- **Orden visual:** primero "Cuentas sociales", segundo "APIs" (el orden del
  encargo), pero la seleccionada al entrar es "APIs".

### 1.1 Layout móvil-primero

| Ancho | Layout |
|---|---|
| ≤ 640 px (móvil, base) | Una columna. Control segmentado a ancho completo (2 botones, 50% cada uno). Tarjetas apiladas, una por fila, `gap: 12px` (`--space-3`). Sin scroll horizontal a 360 px. |
| 641–1023 px | Tarjetas en 2 columnas (`gap: 16px`, `--space-4`). El control segmentado pasa a ancho automático, alineado a la izquierda. |
| ≥ 1024 px | Igual que tablet, con ancho máximo de contenido sin cambios (el panel no fija un max-width distinto; no se inventa uno). |

- El header y el `nav` de pestañas siguen siendo `sticky` (OVERRIDES §1,
  VIS-17). El control segmentado **no** es sticky: queda dentro de la tarjeta.
- Todo control táctil mide mínimo 44 px (`--size-touch-target`); los inputs
  usan 16 px (`--font-size-input`) para no disparar el auto-zoom de iOS.

### 1.2 Control segmentado (nuevo componente)

| Propiedad | Token / valor GTA 6 |
|---|---|
| Contenedor | Fondo `--color-surface-input: #150d24`, borde 1 px `--color-border: #7c6aa0`, radio `--radius-pill: 999px`, padding `4px`, `display: flex`, `gap: 4px` (`--space-1`) |
| Botón inactivo | Fondo transparente, texto `--color-text-dim: #b8b3c9` (8.02:1 sobre tarjeta), tipografía `--font-display: 'Barlow Condensed', 'Arial Narrow', system-ui, sans-serif`, 600, mayúsculas, `letter-spacing: .02em`, tamaño 16 px (`--font-size-h2`), `min-height: 44px`, radio `--radius-pill: 999px` |
| Botón activo | Fondo `--gradient-sunset: linear-gradient(135deg, #6d28d9 0%, #9d174d 52%, #c2410c 100%)`, texto blanco 700, barra inferior 3 px `--color-neon-amber: #fbbf24`, `aria-selected="true"` (nunca solo color: fondo + peso + barra + atributo) |
| Foco | `outline: 3px solid var(--color-focus: #fbbf24)`, `outline-offset: 2px` |
| Motion | Transición 150–200 ms `ease-out`; `prefers-reduced-motion: reduce` la anula |

Textos exactos del control: **"Cuentas sociales"** y **"APIs"**.

### 1.3 Cabecera de la pestaña (se reescribe)

El párrafo actual ("Aquí conectas YouTube, TikTok y Meta. Si dice Conectada,
está lista…") ya no es cierto para la mitad de la pestaña (lo social no se
puede conectar). Se sustituye por dos intros, una por sub-pestaña:

- **Cuentas sociales:** "Tus canales de YouTube, TikTok, Instagram y
  Facebook. Todavía no se pueden conectar desde este panel."
- **APIs:** "Las claves que usa el Worker para publicar. Si dice Conectada,
  está lista. Las credenciales nunca se muestran: solo se pegan y se cifran
  en el Worker."

El `h2` "Cuentas" se conserva.

---

## 2. Sub-pestaña A — Cuentas sociales (vacío real)

### 2.1 Contenido: 4 tarjetas, sin datos inventados

Una tarjeta por plataforma, en este orden: **YouTube, TikTok, Instagram,
Facebook**. Cada tarjeta muestra solo lo que es verdad hoy:

| Campo de la tarjeta | Contenido exacto |
|---|---|
| Nombre | Nombre de la plataforma en español (YouTube, TikTok, Instagram, Facebook) |
| Marca de plataforma | Punto de 8 px (`.dot` CSS, nunca emoji ni glifo) en su `--color-platform-*` (§5.1) + nombre. Sin logos robados ni imágenes externas. |
| Estado (pill) | `.pill.neutral`: **"Sin conectar"** (las 4 tarjetas, sin excepción) |
| Handle / usuario | **No se muestra.** No hay handle: no se inventa `@…`, no se pone "—" como si fuera un dato, no se pone fecha. |
| Fecha de conexión | **No se muestra.** No existe. |
| Botón Verificar | **Deshabilitado** (`disabled`, `opacity: .55`, `cursor: not-allowed`), texto **"Verificar"**. Nunca finge que verifica. |
| Botón Conectar | **No existe.** No se diseña un botón "Conectar" que no pueda funcionar; el único botón visible es "Verificar" deshabilitado, para que el hueco quede explícito. |

### 2.2 Meta unificado (Instagram + Facebook)

Un solo token de Página de Meta conecta Instagram y Facebook a la vez.
El diseño lo refleja así, sin inventar que ya están conectados:

- Las tarjetas de Instagram y Facebook llevan, bajo la pill, la misma nota
  en texto atenuado: **"Se conecta junto con Facebook mediante un solo token
  de Página de Meta."** (en la tarjeta de Facebook: **"Se conecta junto con
  Instagram mediante un solo token de Página de Meta."**)
- Cuando exista el endpoint, un solo token activará las dos tarjetas. Hoy
  las dos dicen "Sin conectar". No hay una tarjeta "Meta" separada: son 4
  tarjetas, 2 de ellas vinculadas por esa nota.

### 2.3 Nota honesta de pendiente (al pie de la sub-pestaña)

Texto exacto, en `.dim` (`--color-text-dim: #b8b3c9`), al final de la lista
de tarjetas:

> **"Pendiente: conectar con el Worker. Todavía no hay forma de conectar
> cuentas sociales desde este panel. Cuando exista, aparecerán aquí con su
> estado real."**

Esta nota no se quita hasta que el endpoint exista (misma regla que las
notas "Aún no lo aplica el Worker" de Ajustes, inventario §2.11).

### 2.4 Estado de carga y de error (social)

- **Cargando:** no hay fetch que hacer (no existe endpoint), así que **no
  hay skeleton ni spinner** en esta sub-pestaña. Las 4 tarjetas se renderizan
  directamente. Inventar una carga sería fingir una llamada.
- **Error:** no aplica (nada que falle). Si el panel pierde sesión, el
  comportamiento global de 401 (`showLogin`) manda, como en el resto del
  panel; no se diseña un error propio aquí.

### 2.5 Componente: tarjeta social

| Propiedad | Token / valor GTA 6 |
|---|---|
| Fondo | `--color-surface: #261a38` |
| Borde | 1 px `--color-border: #7c6aa0` (3.44:1 sobre tarjeta) |
| Radio | `--radius-card: 16px` |
| Padding | `--space-4: 16px` |
| Sombra | `--shadow-card: 0 1px 2px rgba(0,0,0,0.5), 0 0 0 1px rgba(124,58,237,0.08)`; hover `--shadow-elevated` (brillo rosa suave, decorativo) |
| Título (nombre plataforma) | `--font-display`, 700, 18 px (`--font-size-h2`), mayúsculas, `line-height: 1.1`, color `--color-text: #e8eaf0` (13.56:1 sobre tarjeta) |
| Nota Meta / texto secundario | `--font-body: 'Barlow', system-ui, sans-serif`, 13 px (`--font-size-small`), `line-height: 1.5`, color `--color-text-dim: #b8b3c9` (8.02:1) |
| Pill "Sin conectar" | `.pill.neutral`: fondo `--color-surface-input: #150d24`, texto `--color-text-dim: #b8b3c9`, `padding: 2px 9px`, radio `--radius-pill: 999px`, `font-size: 12px`, `font-weight: 600` |
| Botón Verificar (deshabilitado) | Secundario ghost: fondo transparente, borde `--color-border`, texto `--color-text`, `min-height: 44px`, radio `--radius-control: 10px`, `opacity: .55` |

---

## 3. Sub-pestaña B — APIs (parcial: listar / pegar / verificar existen)

### 3.1 Qué muestra: una tarjeta por cuenta API real

La lista es exactamente lo que devuelve `GET /api/accounts`, en el orden
que devuelve la API (por `id`, `db.ts: listApiAccounts`). **No se reordena
en el front.** Cada tarjeta muestra solo campos de la respuesta:

| Campo API | Cómo se muestra |
|---|---|
| `label` (o `platform` si `label` vacío) | Título `h3` de la tarjeta |
| `platform` + `language` | Subtítulo: `YouTube · es` / `TikTok · en` / `Meta · es` (texto tal cual; `language` se muestra en minúscula como viene de la API, sin traducir a "Español/English" para no inventar un catálogo) |
| `verify_ok`, `has_credential` | Pill de estado (§4) |
| `verify_message` + `verified_at` | Línea de estado (§3.3) |
| `has_credential` + `credential_masked` | Línea de credencial (§3.4, texto literal de la API) |
| `status`, `note` | `note` se muestra si viene no vacío, como texto atenuado bajo la línea de estado. `status` (`'pendiente'` / `'conectada'` / `'error'`) **no** se muestra como pill: la pill se deriva de `verify_ok` + `has_credential` (función `accountState` actual), no del campo `status`. |

### 3.2 Agrupación por plataforma + idioma

- Las tarjetas se agrupan bajo encabezados `h3` por plataforma, en este
  orden: **YouTube**, **TikTok**, **Meta**. Dentro de cada grupo, primero
  `es` y luego `en`.
- **Plataforma "Meta":** una sola cuenta API `platform: "meta"` cubre
  Instagram y Facebook (la verificación usa Graph `/me` para `instagram` /
  `facebook` / `meta` por igual, `verify.ts`). No se diseñan cuentas API
  separadas de Instagram y de Facebook: si la API devuelve `platform:
  "instagram"` o `"facebook"`, se agrupa bajo el encabezado **Meta** y el
  subtítulo conserva el valor literal (`instagram · es`).
- **Idioma:** los valores que maneja el panel son `es` y `en`. Si una fila
  llega con otro `language`, se muestra tal cual en su grupo de plataforma;
  nunca se descarta una fila de la API.
- **Huecos "Sin crear":** para cada combinación esperada (YouTube es/en,
  TikTok es/en, Meta es/en) que **no** tenga fila en la respuesta, se muestra
  una tarjeta placeholder "Sin crear" (§3.5). No es una cuenta inventada:
  es el marcador de ausencia del 4º estado real (inventario §2.3, auditoría
  v15). Si la API devuelve una fila fuera de esas 6 combinaciones, se añade
  como tarjeta normal al final de su grupo.

### 3.3 Línea de estado (textos exactos, según `accountStatusLine`)

| Estado | Línea de estado exacta |
|---|---|
| Conectada | `<verify_message o "conectada"> · verificado: <fecha> · credencial: guardada (<credential_masked>)` — si no hay `verified_at`, se omite la parte de fecha, no se pone "—" |
| Sin credencial | **"Sin credencial guardada. Pega el token o la API key abajo y pulsa Guardar credencial."** |
| No conectada | `<verify_message o "Credencial guardada, pero la última verificación falló o aún no se ha verificado. Pulsa Verificar ahora."> · verificado: <fecha si la hay> · credencial: guardada (<credential_masked>)` |
| Sin crear (placeholder) | **"Esta cuenta aún no existe. No hay forma de crearla desde el panel."** |

La fecha usa el formato actual del front (`fmtDate(verified_at)`); no se
cambia el formato.

### 3.4 Credencial: cifrada, nunca visible

- El campo de credencial **siempre aparece vacío** al cargar. La credencial
  guardada nunca se rellena ni se muestra: la API no la devuelve.
- **Input:** `type="password"`, `autocomplete="current-password"`,
  `placeholder="Pega aquí el token / API key"`, `font-size: 16px`,
  `min-height: 44px`, fondo `--color-surface-input: #150d24`, borde
  `--color-border: #7c6aa0`, texto `--color-text: #e8eaf0`, radio
  `--radius-control: 10px`. Botón "Mostrar / Ocultar" al lado (con
  `aria-pressed`, como hoy). Pegar no está bloqueado.
- **Línea de credencial bajo el estado:** el texto literal de
  `credential_masked`: `"cifrada en D1"` o `"texto plano legacy: volver a
  pegar"` o nada. Si dice texto plano legacy, se añade la frase: **"Vuelve
  a pegarla para cifrarla."** (el POST ya borra el texto plano al cifrar).
- **Campo Extra (opcional):** solo para Meta/Instagram. Label exacto:
  **"Extra (opcional)"**. Placeholder exacto: `{"ig_user_id":"…","page_id":"…"}`.
  Hint exacto: **"Solo para Meta/Instagram: IDs de Instagram y de Página,
  en JSON."** Se valida JSON en cliente antes de guardar; si no es válido,
  error inline (§6).
- **Qué credencial va en cada plataforma** (hint bajo el campo, texto
  exacto por plataforma; no es un dato nuevo, es lo que `verify.ts`
  acepta):
  - YouTube: **"API key (empieza por AIza) o token OAuth."**
  - TikTok: **"Token de acceso de TikTok."**
  - Meta: **"Token LARGO de la Página (el corto vence en 1 hora)."**

### 3.5 Tarjeta placeholder "Sin crear"

| Propiedad | Valor |
|---|---|
| Título | `YouTube · en` (plataforma + idioma del hueco, mismo formato que el subtítulo de una tarjeta real) |
| Pill | `.pill.neutral`: **"Sin crear"** |
| Texto | **"Esta cuenta aún no existe. No hay forma de crearla desde el panel."** |
| Acciones | Sin campo de credencial, sin Verificar, sin Guardar. Solo el botón deshabilitado **"Crear cuenta"** (§3.6). |
| Estilo | Misma tarjeta (§2.5) pero con borde discontinuo (`border-style: dashed`, mismo `--color-border: #7c6aa0`) para distinguir a simple vista que no es una cuenta real. El resto de tokens no cambia. |

### 3.6 Acciones: lo que existe y lo que está pendiente

| Acción | Estado | Comportamiento diseñado |
|---|---|---|
| **Guardar credencial** (por tarjeta real) | Existe (`POST /api/accounts/:id/credential`) | Botón primario: fondo `--gradient-sunset`, texto blanco 600, `min-height: 44px`, `padding: 10px 16px`, radio 10 px, `box-shadow: var(--shadow-neon-sunset)`. Al pulsar: valida campo no vacío → si vacío, error inline + foco (§6); si hay valor: `disabled` + texto **"Guardando…"** hasta resolver; éxito: aviso `#msg` "Credencial guardada (****1234). Ahora pulsa Verificar." (el `****1234` es `r.masked` de la respuesta), se vacía el campo y se recarga la lista; error: error inline + aviso, el valor pegado **se conserva** para reintentarlo. Tras guardar, el Worker pone `status = 'pendiente'`: la pill **no cambia** hasta verificar (se deriva de `verify_ok`), y el aviso ya dice que hay que pulsar Verificar. |
| **Verificar ahora** (por tarjeta real) | Existe (`POST /api/accounts/:id/verify`) y verifica **de verdad** contra la plataforma | Botón secundario ghost: fondo transparente, borde `--color-border: #7c6aa0`, texto `--color-text: #e8eaf0`, `min-height: 44px`, radio 10 px. Al pulsar: `disabled` + texto **"Verificando…"** hasta resolver (estado transitorio §4.5); éxito: aviso "Verificación: conectada — <message>"; fallo: aviso `role="alert"` "Verificación: error — <message>" **y** error inline bajo la credencial con el mismo `<message>`; en ambos casos se recarga la lista. Sin credencial guardada (400 del Worker): error inline **"Sin credencial guardada. Pégala primero y pulsa Guardar credencial."** + foco al campo, sin recargar. |
| **Añadir cuenta** (cabecera de la sub-pestaña) | **SIN ENDPOINT** (`POST /api/accounts` no existe) | Botón primario **deshabilitado** (`disabled`, `opacity: .55`, `cursor: not-allowed`), texto **"Añadir cuenta"**, con nota al lado (texto exacto): **"Pendiente: sin endpoint para crear cuentas. Las cuentas aparecen al migrarlas desde el panel anterior."** Nunca abre un formulario. |
| **Editar etiqueta / idioma** | **SIN ENDPOINT** | No hay botón de editar ni inputs para `label`/`language`. Se muestran como texto. |
| **Eliminar** (por tarjeta real) | **SIN ENDPOINT** | Botón peligro **deshabilitado** (`disabled`, `opacity: .55`), texto **"Eliminar"**, fondo `--color-danger-bg: #7f1d1d`, texto blanco (10.02:1), radio 10 px. Al ser un botón deshabilitado no necesita confirmación: no hace nada. Nota al pie de la sub-pestaña (texto exacto): **"Pendiente: sin endpoint para eliminar cuentas. Para quitar una cuenta hay que borrarla en la base de datos."** |

### 3.7 Lista vacía total (la API devuelve cero cuentas)

Si `GET /api/accounts` devuelve lista vacía, **no** se muestran 6
placeholders "Sin crear" seguidos de una lista vacía: se muestra el empty
state actual, conservado, con la agrupación por plataforma como encabezados
vacíos debajo:

> **"Sin cuentas todavía"**
> "Las cuentas se crean al migrar los datos del panel anterior o desde el
> Worker. Cuando haya una, aparecerá aquí para pegar su credencial y
> verificarla."
> Botón ghost **"Actualizar"** (reintenta `loadAccounts`).

Los 6 huecos "Sin crear" solo aparecen cuando la lista **no** está vacía
(hay al menos una cuenta real y se ven los huecos que faltan). Con cero
cuentas, el empty state manda: 6 tarjetas de "no existe nada" serían ruido.

### 3.8 Componente: tarjeta API

Igual que la tarjeta social (§2.5) más:

| Propiedad | Token / valor GTA 6 |
|---|---|
| Encabezado de grupo (`h3` YouTube / TikTok / Meta) | `--font-display`, 600, 14 px (`--font-size-h3`), mayúsculas, `letter-spacing: .04em`, color `--color-neon-violet-text: #c4b5fd` (8.84:1 sobre tarjeta) |
| Subtítulo plataforma · idioma | `--font-body`, 13 px, color `--color-text-dim: #b8b3c9` |
| Punto de plataforma (`.dot`, 8 px) | YouTube `--color-platform-youtube: #ff4e45` (4.99:1), TikTok `--color-platform-tiktok: #25f4ee` (12.07:1), Meta/Instagram `--color-platform-instagram: #f77737` (6.05:1) o rosa `--color-platform-instagram-pink: #f472b6` (6.16:1), Facebook `--color-platform-facebook: #60a5fa` (6.54:1). El punto es decorativo; el nombre va siempre en texto. Para la cuenta Meta se usa el punto rosa de Instagram (`#f472b6`) con el nombre "Meta". |
| Foco en input | Borde `--color-neon-pink-text: #f9a8d4` + `outline: 3px solid var(--color-focus: #fbbf24)`, `offset: 2px` |
| Fila de acciones | `display: flex`, `gap: 8px` (`--space-2`), `flex-wrap: wrap`; en móvil los botones ocupan el ancho disponible en columna (Guardar a ancho completo, Verificar y Eliminar en fila debajo). `.row > button { flex: 0 0 auto; }` salvo la clase explícita de ancho completo. |

---

## 4. Estados (matriz completa)

Solo existen estos 4 estados de cuenta (auditoría v15). **"Sin verificar"
no se renderiza nunca.**

| Estado | Condición real (código) | Pill | Texto de estado | Acciones habilitadas |
|---|---|---|---|---|
| **Conectada** | `verify_ok === 1` | `.pill.ok`: fondo `--color-ok-bg: #14532d`, texto `--color-ok-fg: #bbf7d0` (7.52:1) | `verify_message` + fecha + credencial guardada (§3.3) | Guardar credencial, Verificar, Eliminar (deshabilitado, pendiente) |
| **Sin credencial** | `verify_ok !== 1` y `!has_credential` | `.pill.bad`: fondo `--color-bad-bg: #7f1d1d`, texto `--color-bad-fg: #fecaca` (6.93:1) | "Sin credencial guardada…" (§3.3) | Guardar credencial; Verificar habilitado pero responde 400 con el mensaje de §3.6 |
| **No conectada** | `has_credential` y `verify_ok !== 1` | `.pill.warn`: fondo `--color-warn-bg: #451a03`, texto `--color-warn-fg: #fde68a` (12.03:1) | `verify_message` o texto de reserva + fecha + credencial (§3.3) | Guardar credencial, Verificar |
| **Sin crear** | No hay fila para esa plataforma + idioma (APIs) | `.pill.neutral`: fondo `--color-surface-input: #150d24`, texto `--color-text-dim: #b8b3c9` | "Esta cuenta aún no existe…" (§3.5) | Solo "Crear cuenta" deshabilitado |
| **Sin conectar** | Cuentas sociales, siempre (sin endpoint) | `.pill.neutral` (igual que Sin crear) | Sin línea de estado: solo la nota Meta cuando aplica (§2.2) | Verificar deshabilitado |
| **Verificando…** (transitorio) | Mientras `POST /verify` está en vuelo | La pill **no cambia** hasta la respuesta; el botón dice "Verificando…" y queda `disabled` | La línea de estado no se toca durante la verificación | Ninguna en esa tarjeta hasta resolver |
| **Guardando…** (transitorio) | Mientras `POST /credential` está en vuelo | La pill no cambia | Sin cambio | Ninguna en esa tarjeta hasta resolver |
| **Error de carga** | `GET /api/accounts` falla (red / 401 / 500) | Sin pills | **"No se pudieron cargar las cuentas."** + mensaje del error en `.dim` + botón **"Reintentar"** (id actual `#retryAccounts`) | Reintentar |

Reglas:

- El estado **nunca es solo color**: siempre lleva texto en la pill.
- El texto de error de verificación (`verify_message` / `message` de la
  respuesta) se muestra **tal cual** lo devuelve la plataforma vía el Worker
  (ej. "Meta no aceptó la clave. Tiene que ser el token LARGO de la
  Página…"). No se reescribe ni se resume.
- Tras guardar credencial, `status` pasa a `'pendiente'` en D1 pero la pill
  se deriva de `verify_ok`: puede seguir diciendo "Conectada" con la
  credencial nueva sin verificar. Es el comportamiento actual y no se oculta:
  el aviso "Ahora pulsa Verificar." es lo que lo explica.

---

## 5. Tokens GTA 6 por componente (resumen para el desarrollador)

Todos los valores salen de `tokens-gta6.css` / `tokens-gta6.json`
(OVERRIDES §8). Cero hex fuera de los tokens; los neones saturados
(`#ff2e88`, `#ff5e00`, `#ec4899`) son solo decorativos (franja, brillos),
nunca texto.

| Componente | Color | Tipografía | Radio | Sombra |
|---|---|---|---|---|
| Página | Fondo `--color-background: #100a1a` | — | — | — |
| Tarjeta (social / API) | Fondo `--color-surface: #261a38`, borde `--color-border: #7c6aa0` | Título `--font-display` 700, 18 px, mayúsculas, lh 1.1; cuerpo `--font-body` 16 px, lh 1.5 | `--radius-card: 16px` | `--shadow-card`; hover `--shadow-elevated` |
| Tarjeta "Sin crear" | Igual + borde discontinuo | Igual | 16 px | Igual (sin hover) |
| Control segmentado | Fondo `--color-surface-input: #150d24`, borde `--color-border` | `--font-display` 600, 16 px, mayúsculas | `--radius-pill: 999px` | Ninguna |
| Botón primario (Guardar, Añadir) | `--gradient-sunset`, texto blanco; hover `--gradient-sunset-hover`; fallback sólido `--color-primary: #9d174d` (blanco 7.88:1) | `--font-body` 600, 16 px | `--radius-control: 10px` | `--shadow-neon-sunset` |
| Botón secundario (Verificar) | Transparente, borde `--color-border`, texto `--color-text: #e8eaf0`; hover borde `--color-neon-pink-text: #f9a8d4` + brillo 1.12 | `--font-body` 600, 16 px | 10 px | Ninguna |
| Botón peligro (Eliminar) | `--color-danger-bg: #7f1d1d`, texto blanco (10.02:1) | `--font-body` 600, 16 px | 10 px | Ninguna |
| Botón deshabilitado (cualquiera) | `opacity: .55`, `cursor: not-allowed` | Igual | Igual | Sin sombra neón |
| Input / select | Fondo `--color-surface-input: #150d24`, borde `--color-border`, texto `--color-text` (15.66:1), placeholder `--color-text-dim` | `--font-body` 16 px (`--font-size-input`) | 10 px | Ninguna |
| Pill ok / bad / warn / neutral | §4 (fondos `#14532d` / `#7f1d1d` / `#451a03` / `#150d24`) | 12 px, 600 | `--radius-pill: 999px` | Ninguna |
| Encabezado de grupo plataforma | Texto `--color-neon-violet-text: #c4b5fd` (8.84:1) | `--font-display` 600, 14 px, mayúsculas, `letter-spacing: .04em` | — | — |
| Enlace | `--color-link: #f9a8d4` (9.00:1 sobre tarjeta), hover subraya | `--font-body` | — | — |
| Foco visible | `outline: 3px solid var(--color-focus: #fbbf24)` (9.77:1 sobre tarjeta), offset 2 px | — | — | — |
| Aviso `#msg` éxito / error | Éxito `--color-ok-bg` + `--color-ok-fg`; error `--color-bad-bg` + `--color-bad-fg`; info `--color-info-bg: #1e3a5f` + blanco; borde izquierdo 3 px del color semántico | `--font-body` 16 px | `--radius-control: 10px` | Ninguna |
| Error bajo campo (`.field-error`) | Texto `--color-bad: #f87171` (5.90:1 sobre tarjeta) | `--font-body` 13 px | — | — |
| Skeleton (solo APIs, durante el GET) | Bloques `--color-surface-input` con shimmer sutil, `aria-busy="true"`; desactivado con `prefers-reduced-motion` | — | 16 px (tarjeta) | Ninguna |

Contrastes verificados que no se rompen: texto principal 13.56:1,
secundario 8.02:1, enlace 9.00:1, foco 9.77:1, badge 6.04:1, peor parada
del degradado primario 5.18:1 con blanco. Base 16 px, táctil 44 px,
motion 150–250 ms `ease-out`, `prefers-reduced-motion` respetado.

---

## 6. Textos exactos en español (tabla única, para copiar)

| Dónde | Texto exacto |
|---|---|
| Control segmentado | "Cuentas sociales" · "APIs" |
| `h2` pestaña | "Cuentas" |
| Intro social | "Tus canales de YouTube, TikTok, Instagram y Facebook. Todavía no se pueden conectar desde este panel." |
| Intro APIs | "Las claves que usa el Worker para publicar. Si dice Conectada, está lista. Las credenciales nunca se muestran: solo se pegan y se cifran en el Worker." |
| Pill social (siempre) | "Sin conectar" |
| Nota Meta en tarjeta Instagram | "Se conecta junto con Facebook mediante un solo token de Página de Meta." |
| Nota Meta en tarjeta Facebook | "Se conecta junto con Instagram mediante un solo token de Página de Meta." |
| Nota pendiente social (pie) | "Pendiente: conectar con el Worker. Todavía no hay forma de conectar cuentas sociales desde este panel. Cuando exista, aparecerán aquí con su estado real." |
| Grupo APIs | "YouTube" · "TikTok" · "Meta" |
| Pill API | "Conectada" · "Sin credencial" · "No conectada" · "Sin crear" |
| Estado Sin credencial | "Sin credencial guardada. Pega el token o la API key abajo y pulsa Guardar credencial." |
| Estado No conectada (sin motivo) | "Credencial guardada, pero la última verificación falló o aún no se ha verificado. Pulsa Verificar ahora." |
| Estado Sin crear | "Esta cuenta aún no existe. No hay forma de crearla desde el panel." |
| Label credencial | "Credencial (se cifra al guardar, nunca se muestra)" |
| Placeholder credencial | "Pega aquí el token / API key" |
| Hint credencial | "Se cifra en el Worker al guardar. Pegar no muestra el valor guardado." |
| Hint YouTube | "API key (empieza por AIza) o token OAuth." |
| Hint TikTok | "Token de acceso de TikTok." |
| Hint Meta | "Token LARGO de la Página (el corto vence en 1 hora)." |
| Label Extra | "Extra (opcional)" |
| Hint Extra | "Solo para Meta/Instagram: IDs de Instagram y de Página, en JSON." |
| Credencial legacy | "Vuelve a pegarla para cifrarla." |
| Botones por tarjeta | "Guardar credencial" · "Verificar ahora" · "Eliminar" |
| Botón añadir | "Añadir cuenta" |
| Nota añadir pendiente | "Pendiente: sin endpoint para crear cuentas. Las cuentas aparecen al migrarlas desde el panel anterior." |
| Nota eliminar pendiente (pie APIs) | "Pendiente: sin endpoint para eliminar cuentas. Para quitar una cuenta hay que borrarla en la base de datos." |
| Empty state APIs | "Sin cuentas todavía" / "Las cuentas se crean al migrar los datos del panel anterior o desde el Worker. Cuando haya una, aparecerá aquí para pegar su credencial y verificarla." / botón "Actualizar" |
| Error de carga APIs | "No se pudieron cargar las cuentas." + mensaje del error / botón "Reintentar" |
| Error guardar sin valor | "Pega la credencial primero." |
| Error Extra no JSON | "El campo Extra no es JSON válido. Revisa las comillas y las llaves." |
| Error verificar sin credencial | "Sin credencial guardada. Pégala primero y pulsa Guardar credencial." |
| Éxito guardar | "Credencial guardada (****1234). Ahora pulsa Verificar." (el `****1234` es `r.masked` real) |
| Éxito verificar | "Verificación: conectada — <message>" |
| Fallo verificar | "Verificación: error — <message>" |
| Botón en vuelo | "Guardando…" · "Verificando…" |
| Mostrar / Ocultar credencial | "Mostrar" · "Ocultar" (con `aria-label` "Mostrar credencial" / "Ocultar credencial") |

Errores bajo campo: `.field-error` con `role="alert"`, `aria-invalid="true"`
en el campo y foco al primer campo inválido (OVERRIDES UX-4). El valor
pegado se conserva tras un error de red o de API.

---

## 7. Ids nuevos de HTML/JS que necesita el desarrollador

### 7.1 Ids nuevos (HTML estático en `index.html`, dentro de `#tab-cuentas`)

| Id nuevo | Qué es |
|---|---|
| `#cuentasSubtabs` | Control segmentado (`role="tablist"`, `aria-label="Cuentas sociales o APIs"`) con los dos botones de sub-pestaña |
| `#subtabbtn-social` | Botón del segmento "Cuentas sociales" (`role="tab"`, `aria-selected`, `aria-controls="subtab-social"`) |
| `#subtabbtn-apis` | Botón del segmento "APIs" (`role="tab"`, `aria-selected="true"` por defecto, `aria-controls="subtab-apis"`) |
| `#subtab-social` | Panel de cuentas sociales (`role="tabpanel"`, `aria-labelledby="subtabbtn-social"`) |
| `#subtab-apis` | Panel de APIs (`role="tabpanel"`, `aria-labelledby="subtabbtn-apis"`) |
| `#socialList` | Contenedor de las 4 tarjetas sociales (se renderiza directo, sin fetch) |
| `#socialPendingNote` | Párrafo `.dim` con la nota "Pendiente: conectar con el Worker…" (§2.3) |
| `#apiGroups` | Contenedor de los grupos YouTube / TikTok / Meta dentro de la sub-pestaña APIs (envuelve a `#accountList`, que se conserva) |
| `#btnAddApiAccount` | Botón "Añadir cuenta" (deshabilitado, §3.6) en la cabecera de la sub-pestaña APIs |
| `#apiAddPendingNote` | Nota junto a `#btnAddApiAccount`: "Pendiente: sin endpoint para crear cuentas…" |
| `#apiDeletePendingNote` | Nota al pie de la sub-pestaña APIs: "Pendiente: sin endpoint para eliminar cuentas…" |

### 7.2 Ids dinámicos nuevos (creados por `innerHTML` en la función que renderiza, como los actuales `cred-<id>`)

| Id / atributo nuevo | Qué es |
|---|---|
| `#social-card-youtube`, `#social-card-tiktok`, `#social-card-instagram`, `#social-card-facebook` | Las 4 tarjetas sociales (artículos estáticos en contenido, dinámicos solo si el desarrollador prefiere renderizarlas por JS; en ambos casos estos ids son fijos y únicos) |
| `[data-verify-social="youtube\|tiktok\|instagram\|facebook"]` | Botón "Verificar" deshabilitado de cada tarjeta social (atributo solo para identificarlo; no lleva handler) |
| `#api-group-youtube`, `#api-group-tiktok`, `#api-group-meta` | Secciones de grupo dentro de `#apiGroups`, cada una con su `h3` |
| `#api-placeholder-<platform>-<language>` | Tarjeta placeholder "Sin crear" (ej. `#api-placeholder-youtube-en`); solo existe si falta esa combinación y la lista no está vacía (§3.7) |
| `[data-delete="<id>"]` | Botón "Eliminar" deshabilitado de cada tarjeta API real (sin handler; existe para que el hueco sea visible y el futuro endpoint tenga dónde engancharse) |
| `[data-add-pending]` | Atributo del botón `#btnAddApiAccount` (sin handler; marca explícita de pendiente) |

### 7.3 Funciones JS nuevas / renombradas (nombres, no código)

| Función | Qué hace |
|---|---|
| `switchCuentasSubtab(name)` (nueva) | Cambia entre `"social"` y `"apis"`: alterna `hidden` en `#subtab-social` / `#subtab-apis`, actualiza `aria-selected` en los dos botones y mueve el foco al panel visible. No toca el hash ni `TAB_NAMES`. Al entrar en `#tab-cuentas` se muestra `"apis"` por defecto. |
| `renderSocialAccounts()` (nueva) | Renderiza las 4 tarjetas sociales en `#socialList` con pill "Sin conectar" y Verificar deshabilitado. **No hace ningún `fetch`**: no existe endpoint que llamar. |
| `loadAccounts()` (se conserva, sin renombrar) | Sigue siendo el cargador de la sub-pestaña APIs (`GET /api/accounts` + render de tarjetas reales, placeholders "Sin crear" y empty state). Solo se amplía para agrupar por plataforma + idioma (§3.2) y renderizar los botones deshabilitados de Añadir/Eliminar. |
| `accountState(a)` / `accountStatusLine(a)` (se conservan) | Sin cambios: las 3 pills derivadas + "Sin crear" como ausencia de fila, no como cuarto valor de la función. |

### 7.4 Ids que NO se tocan (inventario §3.2)

`#tab-cuentas`, `#tabbtn-cuentas`, `#accountList`, `#retryAccounts`,
`cred-<id>`, `extra-<id>`, `cred-<id>-error`, `extra-<id>-error`,
`data-save`, `data-verify`, `data-toggle-cred`, `#msg`, y la base
`API = "https://avc.vikingosfoodspa.workers.dev"` con
`credentials: "include"`. Tampoco se tocan `TAB_NAMES`, el hash `#cuentas`
ni `switchTab` / `loadTab`.

---

## 8. Accesibilidad y checklist de aceptación

- [ ] A 360 px no hay scroll horizontal; tarjetas a una columna; botones ≥ 44 px.
- [ ] Inputs a 16 px; foco visible ámbar 3 px en todos los controles, incluido
      el control segmentado.
- [ ] Estados siempre como pill con texto; "Sin verificar" no aparece en
      ningún sitio; ningún handle, fecha ni estado inventado en lo social.
- [ ] Botones async con texto de carga + `disabled` ("Guardando…",
      "Verificando…"); errores junto al campo con `role="alert"` y foco al
      primer inválido.
- [ ] La credencial nunca se muestra: el campo siempre arranca vacío; en la
      lista solo aparece el texto literal de `credential_masked`; el
      `****1234` solo sale en el aviso tras guardar.
- [ ] `prefers-reduced-motion: reduce` respetado; `color-scheme: dark`;
      `theme-color #100a1a` con el tema GTA 6 activo.
- [ ] Cero hex fuera de `tokens-gta6.css` / `tokens-gta6.json`; cero nombres
      personales en estas pestañas.
- [ ] `node --check frontend/dist/app.js` OK tras el cambio; `grep` de ids
      usados vs existentes sin faltantes.

## 9. Pendientes que NO son trabajo de front (para el coordinador / backend)

1. **Endpoint de cuentas sociales** (lectura y luego creación) para la tabla
   `accounts`: sin él, la sub-pestaña social seguirá en vacío real.
2. **`POST /api/accounts`** (crear cuenta API) y **`DELETE /api/accounts/:id`**
   (eliminar): sin ellos, "Añadir cuenta" y "Eliminar" seguirán
   deshabilitados con su nota de pendiente.
3. **Editar `label` / `language`**: sin endpoint, se muestran como texto.
4. **Máscara `****1234` en la lista** (opcional): que `GET /api/accounts`
   devuelva la máscara de `maskCredential` en vez del texto descriptivo;
   hasta entonces la lista muestra `"cifrada en D1"` tal cual.
5. **Verificación en vivo pendiente:** entrar al panel, abrir Cuentas,
   comprobar que lo social dice "Sin conectar" en las 4 tarjetas y que las
   APIs listan solo filas reales de D1, a 360 px y en escritorio.
