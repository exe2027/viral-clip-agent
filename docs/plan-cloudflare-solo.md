# Plan de respaldo degradado: solo Cloudflare $0 + APIs oficiales (+ manual)

> **NOTA 2026-11-19 — §1 actualizada.** La arquitectura de respaldo
> ya NO es "una instalación por persona": es la misma UNA sola
> instalación multi-tenant del plan principal (Pages `avc`, Worker
> `avc`, D1 `clips-avc`, R2 `clips-avc`, `tenant_id` por fila) y el
> cerebro genérico. El tenant "colega" se eliminó el 2026-11-19.

Fecha: 17 nov 2026. **ESTE PLAN YA NO ES LA VÍA PRINCIPAL.**

Decisión del usuario 17 nov 2026 ("Todo automático"): la vía
principal pasa a ser **Cloudflare + GitHub Actions como compute
principal $0** (ver `docs/plan-principal.md`). Este documento queda
como **respaldo degradado** para el caso de que GitHub Actions no
esté disponible: aquí el corte y la elección del momento vuelven a
ser manuales (el usuario corta el clip en el teléfono con CapCut y
sube el MP4 9:16 al panel). Se pierde el corte, los subtítulos y la
detección automáticos; el resto sigue automático tras Iniciar.

## 1. Arquitectura del respaldo (UNA sola instalación, multi-tenant)

| Pieza | Recurso | Qué hace |
|---|---|---|
| Panel web | Cloudflare Pages `avc` | Login por usuario, cuentas, subtítulos, ajustes, botón Iniciar, Cola, Tracking |
| API + cola + estados | Cloudflare Worker `avc` (TypeScript) | Endpoints del panel, máquina de estados, cola |
| Datos | D1 `clips-avc` con `tenant_id` | Clips, estados, cuentas, `started`, registro de atascos |
| Videos | R2 `clips-avc` con prefijo por tenant | MP4 9:16 ya cortados, listos para publicar |
| Scheduler | Cron Triggers del Worker (09:00 America/Santiago) | Revisión diaria: VODs nuevos, cola pendiente |
| Cola | Cloudflare Queues | Trabajo pendiente entre cron y publicación |

Compartido: código, esquema SQL, fórmula de prioridad, reglas.
Por tenant: solo credenciales, cuentas, colas y sesiones (en el panel).

## 2. Qué queda automatizado y qué no (honesto, en este respaldo)

| Paso | Estado en este respaldo | Por qué |
|---|---|---|
| Login, cuentas, ajustes, Iniciar | ✅ Automatizado (panel) | Es solo web + D1 |
| Revisión diaria 09:00 (VOD nuevo de IlloJuan) | ⚠️ Parcial: listar VODs nuevos por API de Twitch/Kick, sin analizar el chat | Listar es HTTP; analizar el chat exige descargar VOD/chat y procesarlo, y el Worker no tiene CPU para eso (10 ms/invocación, 128 MB) |
| Detección de picos por chat | ❌ Manual | Sin compute externo no hay forma de procesar el chat ni el video |
| Corte 9:16 (ffmpeg) | ❌ Manual | Sin ffmpeg no hay corte. El usuario corta el momento en el teléfono (CapCut o similar) y sube el MP4 9:16 al panel → R2 |
| Subtítulos (Whisper) | ❌ Manual | faster-whisper no corre en un Worker. Subtítulos desde el teléfono o sin quemar (TikTok puede auto-subtitular al subir) |
| Títulos / SEO / hashtags | ✅ Automatizado | Gemini API por HTTP desde el Worker (tier gratis, ~500 pet./día) |
| Cola con prioridad (viralidad^0,5 × SEO^0,3 × chat_norm^0,2) | ✅ Automatizado | Es cálculo en el Worker sobre datos de D1; el `chat_norm` queda en su valor manual/estimado hasta que haya detección real |
| YouTube Shorts por API | ⚠️ Por verificar | YouTube Data API v3 es HTTP (OAuth + refresh), pero subir un video grande a través de un Worker tiene límites de tamaño/cuerpo no verificados. No se promete hasta probarlo con 1 Short real |
| Instagram / Facebook por API | ✅ Automatizado (cuando App Review lo permita) | Graph API es solo HTTP: contenedor + publicar; el MP4 sale de R2 |
| TikTok | ✅ Por API oficial / Zernio (ver §3) | El agente navegador NO puede vivir aquí |
| Atascos | ✅ Registrados con motivo y hora | El Worker escribe cada fallo en D1 (`run-log`); el panel lo muestra. Nunca un paro silencioso |

**Conclusión honesta: sin ningún compute externo, el 100%
automático con corte de video no es posible.** Lo que sí queda 100%
automático en este respaldo: panel, cola, estados, SEO,
recordatorios y publicación por API de los MP4 que ya están cortados
en R2. El corte y la elección del momento quedan manuales.

## 3. TikTok en el respaldo: API oficial / Zernio

El agente navegador **NO puede vivir en Cloudflare Workers**:
no corre un navegador con login, la sesión se cierra a los 60 s de
inactividad, las IPs son de centro de datos (identificables como
bots) y el límite es 10 ms de CPU por invocación. Por eso TikTok en
este respaldo va por vía oficial:

1. **API oficial de TikTok (Content Posting API)** — vía principal
   del respaldo. `video.upload` manda el clip a borradores y el
   usuario lo publica con 1 toque, o Direct Post publica directo.
   Es solo HTTP desde el Worker (el MP4 sale de R2). Exige app
   review aprobado para `video.upload` y publicar cada borrador a
   mano (máx. 5 borradores pendientes/24 h). Sin captcha: es la
   solución a largo plazo para no tener captcha.
2. **Zernio (provisional, sin verificar en vivo)** — vía oficial
   auditada según anuncia, $0. Exige prueba en vivo: conectar por
   OAuth (sin entregar contraseña, sin conectar Gmail), subir 1 clip
   real respetando 1/día, visible en público desde otra
   cuenta/incógnito, con URL y hora. Si falla o pide pago/Gmail, se
   descarta.
3. **Manual como respaldo** — el panel entrega MP4 + título +
   descripción + hashtags copiables y el usuario sube a mano desde
   `tiktok.com/upload` o TikTok Studio. Plan B de todos los días.

## 4. El flujo real (respaldo)

```
1. Worker detecta VOD nuevo de IlloJuan → aparece en el panel
2. Usuario: marca el momento (inicio/fin) O sube el MP4 9:16 ya
   cortado desde el teléfono  ← ÚNICO paso manual habitual
3. Worker: genera títulos/SEO con Gemini, aplica gates, programa
4. Usuario: pulsa Iniciar (una vez) → el Worker corre solo
5. Worker: sube por API oficial a cada plataforma, con evidencia
6. Si algo se atasca: Tracking con motivo y hora + aviso visible
   en el panel (campana). Nunca en silencio.
```

## 5. El agente navegador (pieza que tampoco vive en Cloudflare)

La pregunta original era: ¿no puede el backend abrir un navegador,
loguearse en TikTok, dejar la sesión abierta y publicar? **Sí, esa
es exactamente la vía del agente navegador**
(`clipagent/tiktok_browser.py`). Esquema concreto:

- **Librería y lenguaje elegidos: Python + Playwright.** El cerebro
  ya es Python (mismo CLI `clipagent`, mismo estado en disco, 503
  tests); Playwright Python es oficial y tiene la misma API que la
  versión Node.
- **Dónde corre: NO en Cloudflare Workers** (ver §3). Corre en
  **runner autoalojado en la PC propia** (mismo dispositivo/IP,
  $0; la PC solo necesita estar prendida en la ventana de
  publicación, no 24/7) o en GitHub Actions (sesión guardada como
  secreto: IP de centro de datos → mucho captcha, no recomendado).
  VPS descartado por el usuario.
- **Sesión: login UNA vez, reutilización siempre.** Playwright
  `launch_persistent_context` con `user_data_dir` en disco local
  (`~/.cache/clipagent/tiktok-profile`): guarda cookies +
  localStorage + IndexedDB. Cada publicación abre el mismo perfil
  sin volver a loguearse. El login lo hace el humano una vez con
  `clipagent tiktok-browser login` (navegador visible); el módulo
  jamás pide ni guarda contraseñas. Si la sesión expira, se repite
  el login manual una vez.
- **Cómo se publica:** entra a `tiktok.com/upload`,
  `set_input_files` con el MP4 9:16, rellena
  título/descripción/hashtags en el `contenteditable`, verifica que
  el texto quedó, revisa la preview (captura), clica Publicar.
  1 clip/día, sin excepciones, en cuenta nueva.
- **Cómo se verifica publicado: `posted` solo con evidencia.**
  Toast de éxito + URL con `/video/` + captura + verificación en
  el perfil (`tiktok.com/@cuenta` muestra el vídeo). Sin las
  cuatro, no es `posted`: queda `needs_verification` (lo confirma
  el humano con `verify`) o `failed`. El MP4 nunca se borra desde
  el agente.
- **Anti-duplicado:** clave `sha256(clip|tiktok|cuenta|fecha)`
  guardada ANTES de subir; estados
  `scheduled → processing → posted/failed`.
- **Captcha en esta vía:** la sesión persistente y el mismo
  dispositivo/IP lo **reducen, pero NO lo eliminan**. Cuando
  aparece, el agente se detiene de inmediato, marca
  `waiting_captcha`, guarda captura y escribe
  `state/panel_status.json` para que el panel lo muestre (pausa
  avisada, no paro silencioso). El humano lo resuelve en esa misma
  sesión y se retoma con `--resume`, verificando mismo
  MP4/título/descripción. Nunca auto-solver. Ante rechazo: pausa
  2 h sin reintentos, un reintento con la misma sesión; si vuelve a
  fallar, Plan B ese día. **La solución a largo plazo para no tener
  captcha es la API oficial (§3).**

## 6. ¿Algún otro compute gratis y simple que él ya tenga?

Evaluado sin asumir un servidor (por si el respaldo se necesita):

| Opción | ¿Sirve para el cerebro? | Veredicto |
|---|---|---|
| Google Apps Script (gratis, triggers por hora) | Solo HTTP: puede llamar APIs (Gemini, TikTok, YouTube, Twitch), pero sin ffmpeg, sin Whisper, sin navegador; límite ~6 min/ejecución | Útil como orquestador alternativo al Worker, **no** para video |
| Google Colab (gratis) | Python + ffmpeg instalables | Sesiones se cortan, sin scheduler fiable, exige intervención. No sirve como servicio 24/7 |
| Teléfono Android + Termux | Python + ffmpeg técnicamente instalables | Batería, el sistema lo duerme, sin scheduler fiable, frágil. No recomendado |
| Cloudflare Containers | Sí correría Python + ffmpeg | **$5/mes**: no es $0, descartado |
| Oracle Always Free / VPS | Sí | Es un servidor: el usuario no quiere VPS. Descartado |
| GitHub Actions ($0: repo privado 2.000 min/mes, público ilimitado) | Sí: es donde el cerebro corre en el plan principal | **Es el plan principal** (ver `docs/plan-principal.md`), no un respaldo de este documento |
| Runner autoalojado en su PC | Sí, para el agente navegador (misma IP/dispositivo) | Solo haría falta la PC prendida en la ventana de publicación, no 24/7 |
