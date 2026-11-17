# Agentes en código para Clips GTA 6 — ¿Python o Go?
**Fecha:** 17 nov 2026 · **Contexto:** cerebro actual en Python (`viral-clip-agent`), panel en TypeScript (Cloudflare D1/SQLite), canal ES primero, $0, independiente de Muse, listo lo antes posible.
**Aclaración de diseño incorporada:** el colega de Sebastián **NO comparte panel**. Cada persona tiene su **propio panel independiente** (cuentas, cola, credenciales, sesiones separados). El mismo código base se despliega **dos veces, una por persona**, y cada panel tiene su propio cerebro/worker. Este punto se trata explícitamente en §7.

---

## 1. Qué significa "dejar agentes en código"

No es un chat. No es Muse hablando. Es **software que corre solo, con estado guardado, que se despierta por hora o por evento, hace un trabajo, guarda el resultado, y si falla, lo reintenta o lo deja marcado para un humano**.

En palabras simples:

| Forma | Qué es, en una frase | Ejemplo concreto en este proyecto |
|---|---|---|
| **Cron job (tarea programada)** | "Haz X a tal hora, todos los días" | Cada día 09:00 America/Santiago: buscar VODs nuevos de IlloJuan en Twitch/YouTube. Si hay VOD nuevo, crear un trabajo pendiente. No corre todo el día: se despierta, trabaja, se apaga. |
| **Worker (trabajador)** | Un programa que está esperando trabajos en una cola y los procesa uno a uno | *Worker de corte:* saca de la cola "clip #42, del minuto 12:03 al 12:47", llama a ffmpeg, produce el MP4 vertical 9:16 y marca el trabajo como terminado o fallido. |
| **Máquina de estados** | Cada clip tiene un estado y solo puede pasar al siguiente por una regla clara | `detectado → pico → admitido → cortado → subtitulado → empaquetado → en_revisión → aprobado → en_cola → publicado / fallido / expirado`. Nunca se salta pasos, nunca se publica un clip que no pasó por aprobación. Esto es lo que el panel ya llama "Tracking". |
| **Cola con prioridad** | Una lista ordenada, no por llegada, sino por puntuación | La cola ordena por la fórmula ya aprobada: viralidad^0,5 × SEO^0,3 × chat_norm^0,2. Un clip LIVE excelente pasa por delante de un VOD flojo. La cola decide *qué* se publica hoy; el worker solo ejecuta. |
| **Servicio que se despierta por evento** | No espera a la hora: reacciona cuando algo pasa | Cuando Sebastián pulsa "Aprobar" en el panel, eso crea un evento y el worker de publicación se despierta. Cuando Twitch avisa (o el cron detecta) que terminó un directo, se dispara la detección de picos del VOD. |
| **Orquestador** | El "jefe" que no hace el trabajo pesado: reparte trabajos, vigila reintentos y no deja duplicados | Recibe "VOD nuevo", crea trabajos en orden: 1) detectar picos, 2) cortar los 3 mejores, 3) subtitular, 4) empaquetar, 5) encolar. Si el paso 3 falla, reintenta solo ese paso, no vuelve a cortar. |
| **Agente de navegador** | Un navegador real (Chrome) manejado por código, con sesión guardada | *Worker TikTok:* abre `tiktok.com/upload` con la sesión guardada de @elclipdegta6, sube el MP4, pone título/hashtags, publica. Si aparece un captcha, **no lo resuelve solo**: deja el trabajo en `espera_humano` y avisa. |

### El flujo completo, como cadena de trabajadores

```
09:00 cron ──► Worker VOD: ¿hay VOD nuevo de IlloJuan?
                    │ sí
                    ▼
              Worker picos: lee chat Twitch/Kick, calcula picos, score
                    │
                    ▼
              Worker corte (ffmpeg): 9:16, juego arriba / cámara abajo
                    │
                    ▼
              Worker subtítulos (Whisper): transcribe ES, genera .srt/.ass
                    │
                    ▼
              Worker empaquetar: vídeo + subtítulos + título + hashtags + SEO
                    │
                    ▼
              Panel (humano aprueba / rechaza) ──► Cola por prioridad
                    │
                    ▼
              Worker publicación: 1 clip/día por plataforma
                ├─ YouTube: API oficial
                ├─ Meta (IG/FB): API oficial Graph
                └─ TikTok: navegador (sesión guardada, captcha → humano)
                    │
                    ▼
              Anti-duplicado + reintentos + registro (qué se publicó, dónde, cuándo, con qué URL)
```

**Regla de oro que no depende del lenguaje:** cada paso debe ser **idempotente** (si se ejecuta dos veces por error, no publica dos veces) y debe guardar su estado en la base de datos (SQLite/D1), no en la memoria del proceso. Si el worker se cae a mitad de una subida, al reiniciar debe saber exactamente dónde quedó. Eso se logra con una clave única por clip+plataforma (`clip_id + plataforma`) y con estados, no con "espero que no se caiga".

---

## 2. Go vs Python, pieza por pieza

Idea previa que cambia toda la comparación: **el cuello de botella de este proyecto NO es el lenguaje**. Cortar un vídeo lo hace ffmpeg (en C). Transcribir lo hace Whisper (en C/CUDA). Subir lo limita la red y la cuota de cada plataforma. El lenguaje solo es el pegamento que llama a esas herramientas, ordena trabajos y habla con APIs. Por eso Go puede ser 10–50× más rápido que Python en cálculo puro y aun así no hacer que un clip salga ni un minuto antes.

### 2.1 Corte de vídeo con ffmpeg

| | Python | Go |
|---|---|---|
| Cómo se hace en la práctica | Llamar al binario `ffmpeg` por línea de comandos, o usar `ffmpeg-python` | Llamar al mismo binario `ffmpeg`, o usar `ffmpeg-go` |
| Librería | `ffmpeg-python` es el wrapper más popular; construye el grafo de filtros y lo convierte en argumentos de ffmpeg | `ffmpeg-go` es un port de `ffmpeg-python` a Go: la misma idea (grafo DAG → argumentos CLI), también exige ffmpeg instalado en el PATH |
| Rendimiento real | Idéntico: en ambos casos el trabajo lo hace el binario ffmpeg en C, no Python ni Go | Idéntico |
| Veredicto | **Empate técnico. Ganador práctico: Python**, porque el código de corte ya existe y está probado (503 tests en el repo). Reescribirlo en Go es copiar la misma llamada a ffmpeg con otra sintaxis, sin ganar velocidad | |

Fuentes:
- https://json2video.com/how-to/ffmpeg-course/ffmpeg-wrappers.html?size=32&stub=1&reqid=1733513771943160-17316254282400762435-balancer-l7leveler-kubr-yp-vla-209-BAL
- https://deepwiki.com/u2takey/ffmpeg-go
- https://www.mpegflow.com/topics/encoding/ffmpeg

### 2.2 Subtítulos con Whisper

Esta es la pieza donde **Python gana con más claridad**.

| | Python: `faster-whisper` (actual) | Go |
|---|---|---|
| Qué es | Reimplementación de Whisper con CTranslate2. Hasta 4× más rápida que el Whisper original, menos memoria, cuantización int8, VAD Silero integrado, no exige ffmpeg | Hay dos caminos, ninguno tan maduro: (a) `whisper.cpp` como binario externo (C/C++ puro, sin Python, excelente en CPU con SIMD, Metal/CUDA/Vulkan); (b) `go-whisper-ct2`, bindings de Go a CTranslate2 |
| Estado de `go-whisper-ct2` | — | Existe y promete paridad con faster-whisper (mismos modelos, timestamps por palabra, VAD Silero). Pero: usa cgo, **no se instala con un simple `go get`, hay que compilar desde fuente**, solo backend CPU, y en su propio benchmark es **1,23× más lento que faster-whisper con hilos bien configurados (5,51 s frente a 4,47 s) y 2,35× más lento sin configurarlos (10,5 s)** |
| Riesgo | Bajo: es el estándar de facto en 2026 para transcripción local en Python | Medio-alto: librería de nicho, menos usuarios, si cambia la API de CTranslate2 o hay un bug de timestamps, lo arregla Sebastián (o nadie) |
| Veredicto | **Python, claro.** Y hay un truco que lo hace compatible con cualquier lenguaje: `whisper.cpp` como **binario aparte** (`whisper-cli -m modelo.bin -f audio.wav`). Tanto Python como Go como TypeScript pueden llamarlo como un programa externo. No hace falta que el lenguaje "tenga" Whisper: basta con saber llamar a un ejecutable | |

Fuentes:
- https://github.com/parkscloud/hearsay/blob/HEAD/research-summary.md
- https://www.sinologic.net/en/2026-05/vosk-vs-whisper-local-the-ultimate-2026-guide-to-self-hosted-speech-recognition-stt.html
- https://pkg.go.dev/github.com/xPrimeTime/go-whisper-ct2
- https://www.x-cmd.com/install/whisper.cpp/

### 2.3 Detección de picos por chat (Twitch/Kick)

- **Qué hace:** conectarse al chat en vivo (IRC de Twitch / API de Kick), contar mensajes por segundo, detectar picos, cruzarlos con el minuto del VOD, puntuar.
- **Python:** gana por ecosistema. Hay clientes IRC/websocket maduros, y el código ya existe en el repo. El trabajo es I/O (esperar mensajes), no cálculo: Python con `asyncio` va sobrado para un solo directo.
- **Go:** gana en teoría por goroutines (miles de conexiones baratas, ~2 KB por goroutine frente a ~1 MB por hilo del sistema). En la práctica, **aquí hay UN solo directo (IlloJuan)**. Tener capacidad para 25.000 conexiones simultáneas no sirve de nada si solo se vigila una. Es como comprar un autobús para llevar a una persona.
- **Veredicto: empate irrelevante / Python por código ya hecho.** Go solo ganaría si un día se vigilaran 50 streamers a la vez.

Fuentes sobre concurrencia (para dimensionar, no para decidir por velocidad):
- https://www.toolmingo.com/blog/golang-vs-python
- https://medium.com/@sharadhimarpalli/python-3-14-vs-go-concurrency-benchmark-on-m1-mac-updated-with-go-1-25-3-9024d86e53ff
- https://dasroot.net/posts/2025/12/go-for-python-developers-idiomatic-go/

### 2.4 Navegador para TikTok.com (la pieza más frágil)

TikTok no tiene (para este caso) una API pública usable sin auditoría — ver §2.5 — así que la publicación en TikTok es con **navegador real en `tiktok.com/upload`, sesión guardada, y captcha que resuelve un humano**. Esta pieza decide más que ninguna otra, porque es la que más se rompe cuando TikTok cambia su web.

| | Python / TypeScript | Go |
|---|---|---|
| Playwright oficial | **Sí.** Playwright tiene bindings oficiales de Microsoft para Node.js/TypeScript, Python, Java y .NET. Reciben nuevas versiones del navegador y nuevas funciones primero | **No.** `playwright-go` es un port comunitario (`playwright-community/playwright-go`): por dentro lleva un runtime Node.js de ~50 MB que se comunica por stdio, instala su propio Chromium, y va por detrás de los oficiales |
| Alternativas nativas Go | — | `chromedp` (cliente directo del protocolo Chrome DevTools, sin Node; v0.21.0 de oct 2026, el más rápido en un benchmark reciente: 3,9 s en la tarea completa) y `go-rod` (API más cómoda, con `go-rod/stealth` anti-detección). Pero: `chromedp` cambió su API en la v0.21 y el código viejo deja de compilar; `go-rod` no tiene release desde el 12 jul 2024 y en ese mismo benchmark sus clics se atascaban en emulación móvil (19,5 s) |
| Sesión guardada + captcha | Playwright (Python/TS) guarda contexto/sesión (`storage_state`) de forma documentada y estable | `chromedp`/`go-rod` también pueden reutilizar un perfil de Chrome, pero hay que hacerlo a mano y la documentación de "sesión persistente + pausa para captcha humano" es más escasa |
| Veredicto | **Python o TypeScript, claro.** Es la única pieza donde elegir Go es objetivamente peor: se pierde el soporte oficial, se depende de una librería comunitaria o de una que no se actualiza desde 2024, justo en la integración más frágil del proyecto | |

Fuentes:
- https://dashboard.statproxies.com/blog/golang-headless-browser
- https://www.web-developpeur.com/en/blog/yeux-claude-code-navigateur-playwright-mcp-devtools
- https://v2-frontier-make.vercel.app/blog/best-language-for-web-scraping
- https://socket.dev/go/package/github.com/playwright-community/playwright-go?section=overview
- https://raw.githubusercontent.com/mycalf/playwright-go/master/README.md

### 2.5 APIs: YouTube, Meta, TikTok

Todas son **HTTP + JSON + OAuth**. Cualquier lenguaje las puede llamar. La diferencia es la calidad del cliente oficial.

| API | Python | Go | TypeScript |
|---|---|---|---|
| **YouTube Data API v3** | Cliente oficial `google-api-python-client`, muy usado, ejemplos de subida (`videos.insert`) en todos lados | Cliente oficial `google.golang.org/api/youtube/v3`, con quickstart oficial de Google. Advertencia real: Google dice que estas librerías autogeneradas están **en modo mantenimiento** (solo bugs críticos y seguridad, sin funciones nuevas) y recomienda hacer *vendoring* local. Funciona, pero no esperes mejoras | Cliente oficial también; y es el lenguaje del panel, así que compartir tipos es gratis |
| **Meta Graph API (IG/FB Reels)** | Llamadas HTTP simples: crear contenedor `POST /{ig-user-id}/media` (`media_type=REELS`, `video_url` pública), sondear `GET /{container-id}?fields=status_code` hasta `FINISHED`, publicar `POST /{ig-user-id}/media_publish`. Límite: 100 publicaciones API por cuenta y día; contenedor caduca a las 24 h; vídeo hasta 300 MB. Ningún lenguaje tiene ventaja: es esperar y reintentar | Igual | Igual |
| **TikTok Content Posting API** | Existe, pero **Direct Post (publicar solo) exige auditoría de TikTok**; sin auditar, todo lo publicado queda privado (`SELF_ONLY`) y solo 5 usuarios pueden postear en 24 h. El modo *Upload* (manda el vídeo a borradores/inbox y el creador lo termina en la app) no exige auditoría, pero no es automático: alguien tiene que abrir TikTok. Límites: 6 inits/min, ~15 posts/día por cuenta, MP4 hasta 4 GB / 10 min. Conclusión: la API oficial no sustituye hoy al navegador; el navegador sigue siendo el camino para publicar de verdad, y la API es un plan B (borrador) | Igual | Igual |
| **Veredicto** | Empate. El trabajo real aquí no es el lenguaje, es **guardar y refrescar tokens OAuth, respetar cuotas, reintentar con backoff exponencial y no publicar dos veces** | | |

Fuentes:
- https://developers.google.com/youtube/data/quickstart/go
- https://developers.google.com/youtube/data/reference/rest/v3/videos/insert
- https://pkg.go.dev/github.com/googleapis/google-api-go-client@v0.0.0-20181229000844-f26a60c56f14
- https://postdom.com/blog/instagram-reels-api
- https://www.postzen.dev/blog/instagram-api-integration-guide
- https://www.postbase.so/blog/tiktok-content-posting-api
- https://blog.lots.social/tiktok-content-posting-api
- https://uplonic.com/blog/tiktok-video-specs

### 2.6 Concurrencia y colas

| | Python | Go | TypeScript |
|---|---|---|---|
| Concurrencia | `asyncio` (bien para I/O), `multiprocessing` para CPU, GIL que impide paralelismo real con hilos en CPU puro. Para 1 clip/día, irrelevante | Goroutines + channels: el mejor modelo de los tres para miles de tareas concurrentes. Para 1 clip/día, irrelevante | Event loop (como Node): bien para I/O, un solo hilo |
| Cola duradera | Celery (estándar Python desde 2009, Redis/RabbitMQ, reintentos, prioridades, `celery-beat` para cron) o algo más simple: una tabla en SQLite + un worker que hace polling. Temporal (Python/Go/TS) si se quiere ejecución duradera con replay | Asynq (cola Go sobre Redis, reintentos con backoff, prioridades, UI Asynqmon) o Temporal en Go (su lenguaje nativo) | BullMQ (cola estándar Node/TS sobre Redis, backoff, delayed jobs, prioridades) |
| Lo que de verdad importa | Ninguna de estas colas es obligatoria a este volumen. **Una tabla `jobs` en SQLite/D1 con estados (`pending → running → done/failed`), `attempts`, `next_retry_at` y `idempotency_key` es suficiente y es lo más simple de operar a $0** (sin Redis que mantener, sin otro servidor que se pueda caer). Redis/Celery/Temporal son para cuando haya 100× más volumen o varios servidores | | |
| Veredicto | Empate práctico. Go gana en elegancia de concurrencia; Python y TS ganan en no necesitar nada nuevo | | |

Fuentes:
- https://www.devtoolsguide.com/background-job-tools/
- https://www.inngest.com/blog/best-job-queue-alternatives
- https://github.com/h0rv/buquet/blob/HEAD/docs/guides/task-queue-comparison.md

### 2.7 Despliegue, $0, velocidad, binario y arranque en frío

| | Python | Go | TypeScript |
|---|---|---|---|
| **Cloudflare Workers / Pages (panel actual)** | Python soportado (Pyodide), pero con sandbox limitado: muchos paquetes con extensiones nativas **no corren**; 100.000 req/día, **10 ms de CPU por invocación, 128 MB** en gratis. Imposible correr ffmpeg, Whisper o Chrome ahí. Solo vale para el panel/API ligera | Go **no es nativo** en Workers: solo vía WebAssembly (compilar a WASM). Peor opción para Workers | **Nativo.** JS/TS es el lenguaje de Workers. El panel ya está aquí y debe quedarse |
| **Railway** | Funciona, pero el plan gratis real en 2026 es **$1 de crédito al mes, 1 vCPU, 0,5 GB RAM, 1 proyecto, 3 servicios**; el trial es $5 por 30 días y luego Hobby son $5/mes. **No es $0 sostenible** para un worker que procesa vídeo (0,5 GB de RAM es poco para Whisper + ffmpeg + Chrome a la vez) | Igual en precio, pero el binario pesa menos y arranca más rápido, así que aprovecha mejor esos 0,5 GB | Igual |
| **GitHub Actions (cron)** | **La mejor opción $0 para el cron 09:00 y trabajos por lotes.** Gratis en repos públicos (minutos ilimitados) y 2.000 min/mes en privados; trabajo de hasta 6 h; cron mínimo cada 5 min. Contras: la hora es aproximada (puede retrasarse con carga), los cron se auto-deshabilitan tras 60 días sin actividad en el repo, y no hay disco persistente (el estado debe vivir en D1/SQLite externo o en artefactos). Python viene preinstalado en el runner | Igual de bueno; Go se instala rápido en el runner, pero hay que compilar | Igual |
| **VPS gratis (Oracle Always Free)** | **La mejor opción $0 para "siempre encendido".** Oracle Always Free: 2 OCPU ARM + 12 GB RAM en total, 200 GB de disco, 10 TB de salida/mes. Contras reales: exige tarjeta para verificar identidad, la capacidad ARM en la región puede no estar disponible al crearla, y Oracle puede **reclamar la instancia si está ociosa 7 días** (CPU/red/memoria por debajo del 20 %). Es un VPS que hay que administrar (Linux, Docker, systemd) | Igual, con ventaja: el binario Go es un solo archivo, sin venv ni `pip install` en cada despliegue | Igual |
| **Fly.io** | **Ya no hay tier gratis para cuentas nuevas** (solo trial de 2 h de máquina o 7 días). No contar con él como $0 | Igual | Igual |
| **Tamaño del binario / arranque en frío** | Intérprete + venv: cientos de MB, arranque 50–200 ms solo el intérprete, más la carga de `torch`/`faster-whisper` (segundos). En un cron que corre 1 vez al día, irrelevante | **Binario único estático, ~5–20 MB, arranque ~5 ms.** Es la gran ventaja de Go: copiar un archivo y ejecutar. Sin dependencias, sin "en mi máquina sí funcionaba" | `node_modules` pesado, arranque intermedio |
| **Velocidad** | Lenta en cálculo puro (10–50× más lenta que Go en CPU puro según comparativas 2025). Irrelevante aquí: el tiempo lo dominan ffmpeg, Whisper, la subida y las APIs | Rápida en cálculo puro y en HTTP concurrente. No acorta el pipeline porque no es el cuello de botella | Intermedia |
| **Manejo de errores y reintentos** | Excepciones + `tenacity`/`backoff`. Código ya escrito y con tests. El tipado es opcional y los errores de tipo aparecen en ejecución | **Mejor por diseño:** errores como valores de retorno (`if err != nil`), tipado estático que caza errores en compilación, `context.Context` con timeout/cancelación en cada llamada. Es el lenguaje donde es más difícil olvidar un reintento o un timeout | Promesas/`async-await`, tipado con TypeScript (bueno), pero los errores no tratados en promesas son un clásico |
| **Mantenibilidad (con 2 personas, sin equipo)** | La más fácil de leer y cambiar rápido; Sebastián ya conoce el código; la comunidad de IA/vídeo es Python | Sintaxis simple, se aprende en ~1 mes según casos citados, pero **hay que reescribirlo todo** y mantener dos lenguajes | La más fácil para el panel; para el cerebro de vídeo, menos librerías de IA que Python |

Fuentes:
- https://en.inithtml.com/resources/cloudflare-free-tier-everything-you-get-in-2026-dns-cdn-ddos-r2-workers-d1/
- https://stacksfree.com/blog/cloudflare-workers-free-tier-guide-2026/
- https://outplane.com/blog/railway-pricing
- https://stacksfree.com/blog/railway-free-tier-guide-2026/
- https://toolfreebie.com/free-hosting-ai-agents/
- https://stacksfree.com/blog/free-cron-jobs-scheduled-tasks-2026/
- https://space-node.net/blog/best-free-hosting-side-projects-2026
- https://xcloud.host/best-free-docker-hosting/
- https://www.toolmingo.com/blog/golang-vs-python

### 2.8 Tabla resumen Go vs Python por pieza

| Pieza | Ganador | Por qué, en una línea |
|---|---|---|
| Corte ffmpeg | Empate (Python por código hecho) | El trabajo lo hace ffmpeg en C en ambos |
| Subtítulos Whisper | **Python** | `faster-whisper` es el estándar; `go-whisper-ct2` es nicho, cgo, 1,23× más lento |
| Picos por chat | Empate (Python por código hecho) | 1 solo directo: la concurrencia de Go sobra |
| Navegador TikTok | **Python/TS** | Playwright oficial; Go solo tiene port comunitario o `go-rod` sin release desde 2024 |
| APIs YT/Meta/TikTok | Empate | Todo es HTTP+OAuth; lo difícil son tokens, cuotas y reintentos, no el lenguaje |
| Concurrencia/colas | **Go** en teoría, empate en la práctica | Goroutines > asyncio, pero a 1 clip/día no se nota |
| Despliegue $0 | Empate (Go en binario) | El $0 lo decide el hosting (Oracle/GitHub Actions), no el lenguaje |
| Arranque/binario | **Go** | Un solo binario de ~10 MB, 5 ms |
| Errores/reintentos | **Go** | Errores explícitos y tipado estático |
| Velocidad | **Go** | Pero irrelevante: el cuello es ffmpeg/Whisper/red |
| Mantenimiento hoy | **Python** | El código ya existe, tiene 503 tests, Sebastián lo conoce |

**Conclusión del §2: Go gana en 4 categorías que no son el cuello de botella, Python gana en las 2 que sí lo son (Whisper y navegador TikTok) y empata en el resto.**

---

## 3. Otros lenguajes

### TypeScript / Node.js
- **A favor:** es el lenguaje del panel (ya existe, ya se mantiene); Playwright es nativo y oficial (es su casa: Playwright nació en Node); el mejor cliente de navegador; `BullMQ` para colas; compartir tipos entre panel y cerebro evita errores de contrato; despliegue en Cloudflare Workers nativo para la parte ligera.
- **En contra:** para vídeo/IA es el más débil de los tres. No hay `faster-whisper` en Node (habría que llamar a `whisper.cpp` como binario externo, igual que en Go); el ecosistema de procesamiento de vídeo es llamar a ffmpeg por CLI, sin ventaja sobre Python; `node_modules` y el consumo de memoria de Chrome + Node en un VPS de 0,5 GB es el peor de los tres.
- **Veredicto:** **sí para el panel y la API, no para el cerebro de vídeo.** Es exactamente el reparto que ya existe. No reescribir el cerebro en TS.

### Rust
- **A favor:** el más rápido y el más seguro de todos (sin recolector de basura, sin errores de memoria en compilación); binario único como Go; `ffmpeg-next`/`ffmpeg-the-third` para ffmpeg, `whisper-rs` (bindings de `whisper.cpp`) y `whisper-subs` (CLI real que hace MP4 → ffmpeg → 16 kHz PCM → whisper.cpp → SRT/VTT) demuestran que el pipeline existe en Rust; ideal si un día el proyecto fuera un producto con miles de usuarios.
- **En contra:** es el más lento de escribir con diferencia. El propio ecosistema lo admite: compilar `whisper.cpp` desde Rust exige toolchain C++ + `cmake`, la API de `whisper-rs` cambia entre versiones (el ejemplo de `ez-ffmpeg` + `whisper-rs` 0.16 documenta que `full_n_segments()` cambió y el código viejo ya no compila), y `whisper-rs` se mudó de GitHub a Codeberg. Para 2 personas y "listo lo antes posible", Rust es la peor opción de todas: se pagaría el coste de aprendizaje y de peleas con el compilador sin que ningún clip salga antes.
- **Veredicto:** **no ahora, quizá nunca para este volumen.** Solo si el proyecto escalara a decenas de canales y el coste de CPU fuera el problema (no lo es: el coste es $0 y el volumen es 1 clip/día).

Fuentes Rust:
- https://lib.rs/keywords/ffmpeg
- https://lib.rs/crates/whisper-subs
- https://dev.to/yeauty/feed-audio-to-whisper-in-rust-without-ever-shelling-out-to-ffmpeg-4if5
- https://www.utak3r.pl/2026/09/08/running-whisper-on-gpu-in-rust-with-onnx-and-directml/

---

## 4. Opciones puntuadas

Puntuación 1–5 (5 = mejor) en: rapidez de puesta en marcha, $0 real, fiabilidad, mantenimiento por 2 personas, futuro/escalabilidad. El esfuerzo es orientativo para una persona que ya conoce el proyecto.

### Opción A — Todo en Python (cerebro actual, envuelto en workers)
| | |
|---|---|
| **Qué es** | No se reescribe nada. El `viral-clip-agent` actual se parte en workers (`vod`, `picos`, `corte`, `subtítulos`, `empaquetar`, `publicar`) que leen/escriben en SQLite/D1, con un cron 09:00 (GitHub Actions) y un servicio ligero siempre encendido (o por demanda) en Oracle VPS. TikTok sigue en Playwright Python. |
| Rapidez | **5** — es envolver, no reescribir |
| $0 real | **5** — GitHub Actions + Oracle Always Free + Cloudflare gratis |
| Fiabilidad | **4** — código ya probado (503 tests); el riesgo es Python en sí (tipos, errores en ejecución), mitigable con tests y `mypy` |
| Mantenimiento | **5** — un solo lenguaje en el cerebro, el que Sebastián ya conoce |
| Futuro | **3** — escala bien hasta decenas de clips/día; si un día hay 100× más volumen, el orquestador se puede reescribir sin tocar vídeo/IA |
| **Total** | **22/25** |
| Pros | Sale esta semana; cero riesgo de perder lo que ya funciona; Whisper y Playwright en su mejor versión; depurar es leer Python, no pelear con cgo |
| Contras | Arranque más lento, binario pesado, concurrencia limitada (irrelevante a este volumen); si el worker se cae, hay que vigilarlo (systemd + logs) |
| Qué se reescribe | Nada de lógica. Solo: dividir `main` en workers, tabla `jobs`, cron, y un endpoint HTTP mínimo para que el panel apruebe/rechace |
| Esfuerzo | **1 (el menor). 2–4 días** para tenerlo corriendo independiente de Muse |

### Opción B — Todo en Go
| | |
|---|---|
| Rapidez | **1** |
| $0 real | **5** — mismo hosting, binario más ligero |
| Fiabilidad | **3** — Go es robusto, pero el código nuevo no está probado; el riesgo se traslada a Whisper (nicho) y a TikTok (sin Playwright oficial) |
| Mantenimiento | **3** — un solo lenguaje, pero nuevo para el proyecto; cada cambio en Whisper/navegador costará más |
| Futuro | **5** — el mejor si el volumen se multiplica por 100 |
| **Total** | **17/25** |
| Pros | Un solo binario, arranque instantáneo, errores explícitos, concurrencia real, despliegue trivial (copiar un archivo) |
| Contras | Se pierde `faster-whisper` (se cambia por `go-whisper-ct2` 1,23× más lento o por llamar a `whisper.cpp` externo); se pierde Playwright oficial en la pieza más frágil; se reescriben los 503 tests; se retrasa el lanzamiento semanas |
| Qué se reescribe | **Todo el cerebro**: picos, scores, corte, subtítulos, empaquetado, cola, publicación YT/Meta/TikTok |
| Esfuerzo | **5 (el mayor de las viables). 3–6 semanas** a tiempo parcial, con riesgo de regresiones |

### Opción C — Híbrido Go (orquestador/worker) + Python (vídeo/IA)
| | |
|---|---|
| **Qué es** | Go hace lo que Go hace bien: orquestador, cola, cron, llamadas a APIs (YouTube/Meta), reintentos, anti-duplicado, servidor HTTP del panel. Python queda como **"caja de herramientas"**: un script `cortar.py`, un script `subtitular.py` (faster-whisper), un script `detectar_picos.py`, que Go llama como subprocesos con argumentos y JSON por stdout. El contrato entre ambos es una línea de comandos, no una librería |
| Rapidez | **3** — hay que escribir el orquestador Go, pero no se toca vídeo/IA |
| $0 real | **5** |
| Fiabilidad | **4** — lo frágil (vídeo/IA) sigue en Python probado; lo nuevo (orquestador) es código Go simple y testeable |
| Mantenimiento | **3** — dos lenguajes, dos toolchains, dos despliegues; cada cambio hay que pensar en qué lado cae |
| Futuro | **5** — es la arquitectura "correcta" a largo plazo: el día que se quiera, se sustituye un script Python por un binario Go/Rust sin tocar el resto |
| **Total** | **20/25** |
| Pros | Lo mejor de cada uno sin reescribir lo difícil; binario Go fácil de desplegar; Python solo donde gana; frontera clara y sustituible |
| Contras | Dos lenguajes que mantener; depurar un fallo exige mirar dos logs (¿falló Go al llamar o Python al transcribir?); hay que definir bien el contrato CLI/JSON entre ambos o se convierte en un dolor |
| Qué se reescribe | Orquestador, cola, publicación por API y servidor HTTP en Go. No se reescriben corte, subtítulos ni picos |
| Esfuerzo | **3. 1–2 semanas** (después de la Opción A, o en paralelo) |

### Opción D — Híbrido TypeScript (panel/API) + Go/Python (cerebro)
| | |
|---|---|
| **Qué es** | El panel sigue en TypeScript (Cloudflare). La API que el panel llama también en TypeScript (Workers/Node). El cerebro sigue en Python (Opción A) o en Go (Opción C), detrás de esa API. Es la Opción A o C con la frontera puesta en HTTP en vez de en CLI |
| Rapidez | **4** (si el cerebro es Python) / **2** (si es Go) |
| $0 real | **4** — Workers gratis para la API ligera, pero el cerebro pesado sigue necesitando VPS/Actions; hay dos sitios que vigilar |
| Fiabilidad | **4** — separar panel y cerebro por HTTP es sano: si el cerebro se cae, el panel sigue mostrando la cola |
| Mantenimiento | **4** — TS es el lenguaje del panel; el contrato HTTP es más explícito que un CLI |
| Futuro | **4** — es la forma estándar de separar frontend y backend |
| **Total** | **20/25 (con cerebro Python) / 18/25 (con cerebro Go)** |
| Pros | Reutiliza el panel tal cual; el panel no depende de que el worker esté vivo; tipos compartidos TS |
| Contras | Añade una capa (API HTTP) que hay que autenticar, versionar y vigilar; para 2 usuarios es un poco de sobre-ingeniería si la cola ya vive en D1 |
| Qué se reescribe | La API entre panel y cerebro (si no existe ya); el cerebro, nada (Python) o todo (Go) |
| Esfuerzo | **2 (con Python). 3–5 días** para la API + Opción A |

### Opción E — Todo en TypeScript/Node
| Rapidez 2 · $0 4 · Fiabilidad 2 · Mantenimiento 3 · Futuro 3 · **Total 14/25** |
| Pros: un solo lenguaje en todo el proyecto; Playwright nativo; panel y cerebro comparten código |
| Contras: el peor para vídeo/IA (sin faster-whisper, sin ecosistema de transcripción); habría que llamar a `whisper.cpp` y ffmpeg como externos igualmente, así que no se gana nada frente a Python y se pierde el código ya hecho |
| Esfuerzo: **4. 2–4 semanas** |

### Opción F — Rust (cerebro) + TS (panel)
| Rapidez 1 · $0 5 · Fiabilidad 3 (código nuevo) · Mantenimiento 2 · Futuro 5 · **Total 16/25** |
| Pros: lo más rápido y seguro a largo plazo; binario único |
| Contras: el más lento de escribir; peleas con `whisper-rs`/`cmake`/FFmpeg; nadie en el proyecto escribe Rust hoy; retrasa el lanzamiento meses |
| Esfuerzo: **6 (el mayor). 6+ semanas** |

### Orden por esfuerzo (de menos a más)
1. **A — Todo Python** (2–4 días)
2. **D — TS + Python** (3–5 días, es A + API HTTP)
3. **C — Go + Python** (1–2 semanas)
4. **E — Todo TS** (2–4 semanas)
5. **B — Todo Go** (3–6 semanas)
6. **F — Rust** (6+ semanas)

---

## 5. Ranking final

| # | Opción | Total /25 | Cuándo elegirla |
|---|---|---|---|
| 1 | **A — Todo Python (workers + cron + VPS)** | **22** | **Hoy: es la recomendación** |
| 2 | **C — Go orquestador + Python vídeo/IA** | **20** | Fase 2, cuando A esté corriendo y se quiera endurecer el orquestador |
| 2 | **D — TS panel/API + Python cerebro** | **20** | Si se prefiere la frontera por HTTP en vez de por CLI; compatible con A y C |
| 4 | **B — Todo Go** | **17** | Solo si el volumen se multiplica por 100 y el equipo crece |
| 5 | **F — Rust** | **16** | No, salvo que el proyecto se convierta en producto |
| 6 | **E — Todo TS** | **14** | No para el cerebro |

---

## 6. Recomendación final: Opción A ahora, con la puerta abierta a C

**Haced la Opción A esta semana. No reescribáis a Go.**

**Qué hacer, en orden:**
1. **Partir el cerebro Python actual en workers** con una tabla `jobs` en SQLite/D1 (`id`, `clip_id`, `plataforma`, `paso`, `estado`, `intentos`, `proximo_reintento`, `clave_anti_duplicado`). Cada worker hace un solo paso y marca su resultado. Si se cae, al reiniciar retoma donde quedó.
2. **Cron 09:00 con GitHub Actions** (gratis, repo público o 2.000 min/mes en privado) para detectar VODs nuevos. El resto de pasos los dispara el worker o la aprobación humana en el panel.
3. **Desplegar el worker en Oracle Cloud Always Free** (2 OCPU ARM + 12 GB, $0) con Docker o systemd. Ahí viven ffmpeg, faster-whisper y Playwright Python. Cloudflare se queda solo con el panel y D1.
4. **Dejar el contrato listo para Go:** que cada paso pesado sea un comando con entrada/salida clara (`cortar.py --in … --out …`, `subtitular.py --in … --out …json`). Eso es lo que permitirá, en la Fase 2, escribir el orquestador en Go (Opción C) sin tocar ni una línea de vídeo/IA.
5. **TikTok se queda en Playwright Python** con sesión guardada y pausa para captcha humano. No se toca hasta que TikTok apruebe Direct Post (y aun entonces, solo como plan B).
6. **Fase 2 (solo si hace falta):** reescribir únicamente el orquestador, la cola y la publicación por API en Go (Opción C). Es 1–2 semanas, no 3–6, porque vídeo/IA no se mueve.

**Por qué esta y no las otras:**
- **No todo Go (B):** se pagaría una reescritura de 3–6 semanas para ganar velocidad donde no hace falta (el cuello es ffmpeg/Whisper/red), y se perdería lo mejor de Python justo en las dos piezas más difíciles (Whisper y TikTok). Es cambiar un motor que funciona por uno que hay que probar desde cero, la semana del lanzamiento.
- **No híbrido Go+Python (C) de entrada:** es la mejor arquitectura a largo plazo, pero cuesta 1–2 semanas más que A y el proyecto necesita estar independiente *ahora*, no perfecto dentro de un mes. C es la Fase 2, no la Fase 1.
- **No todo TS (E) ni Rust (F):** TS pierde en vídeo/IA sin ganar nada, y Rust es el más caro de escribir para un volumen de 1 clip/día.
- **Sí Python ahora:** el código ya existe, tiene 503 tests, usa las mejores librerías en las piezas difíciles, y el $0 es alcanzable con GitHub Actions + Oracle + Cloudflare. La independencia de Muse no la da el lenguaje, la dan los workers + la cola + el cron + el estado en base de datos. Eso se puede tener en Python en días.

---

## 7. Ajuste por aislamiento por tenant (paneles independientes)

**Respuesta corta: el aislamiento por tenant NO cambia el ranking de lenguajes. No cambia nada en Go vs Python vs TypeScript. Lo que cambia es el despliegue y la operación — y en eso, el ranking sale reforzado a favor de la Opción A.**

Por qué no cambia el lenguaje:
- Que haya 2 instalaciones en vez de 1 no hace que Go sea más rápido, ni que Python transcriba mejor, ni que Playwright Go se vuelva oficial. Las ventajas y desventajas por pieza (§2) son idénticas con 1 o con 2 tenants. El cuello de botella sigue siendo ffmpeg/Whisper/red, no el lenguaje.
- Tampoco cambia la puntuación: A sigue siendo 22/25, C 20/25, B 17/25. Ninguna opción gana o pierde puntos por duplicar la instalación.

Qué sí cambia (y hay que diseñarlo así desde el día 1):

1. **No hace falta multi-tenancy en el código.** Es la mejor noticia del diseño. No hay que escribir aislamiento por filas, ni permisos por tenant, ni "este clip es de Sebastián y este del colega", ni preocuparse de que un bug mezcle las colas. Cada instalación tiene **su propia base de datos (su propio D1/SQLite), su propia cola, sus propias credenciales y su propia sesión de TikTok**. Un error en una instalación no contamina la otra. Esto elimina de un plumazo la razón principal para usar un orquestador pesado (Temporal, colas compartidas con namespaces, etc.): con 1 tenant por instalación, una tabla `jobs` en SQLite basta.
2. **Desplegar dos veces el mismo código.** El coste es operativo, no de lenguaje:
   - **Go lo hace más fácil:** un solo binario → se copia el mismo archivo a dos servidores (o dos contenedores), cada uno con su propio fichero de entorno (`.env.sebastian`, `.env.colega`). No hay `pip install` que pueda fallar de forma distinta en cada lado. Esta es la única ventaja real que Go gana con el diseño por tenant — y es pequeña frente a lo que se pierde en Whisper/TikTok.
   - **Python lo hace igual de bien si se empaqueta con Docker:** una sola imagen Docker, dos contenedores, dos volúmenes/env. Sin Docker, hay que repetir `venv + pip install` dos veces, con riesgo de que las versiones diverjan. **Por eso la recomendación A incluye Docker desde el día 1.**
   - **TypeScript/panel:** igual. El panel ya es un artifact/Cloudflare: se despliega dos veces con distinta configuración (distinto D1, distinto subdominio).
3. **Mantener dos instalaciones actualizadas.** El riesgo real no es el lenguaje, es la **divergencia**: actualizar el panel de Sebastián y olvidar el del colega, o viceversa. Mitigación obligatoria, independiente del lenguaje:
   - Un solo repositorio, una sola rama principal, una sola imagen/binario versionado (`v1.2.3`).
   - Un script `deploy-all.sh` que despliega a las dos instalaciones en orden y comprueba salud en cada una (`/health` responde 200). Nunca desplegar a mano a una sola.
   - Nunca editar configuración directamente en el servidor: todo en variables de entorno versionadas (sin secretos) por tenant.
   - Un `health check` por instalación en el panel (o un cron que avise si una de las dos lleva 24 h sin publicar).
4. **Coste $0 × 2.** Duplicar no duplica el coste si se comparte la máquina: **un solo VPS Oracle Always Free puede alojar las dos instalaciones** como dos procesos/contenedores (12 GB de RAM dan de sobra para 2 workers que procesan 1 clip/día cada uno, no a la vez). En Railway, en cambio, el plan gratis ($1/mes, 0,5 GB) no da para dos workers de vídeo: ahí sí que duplicar rompería el $0. Otro motivo para elegir Oracle + GitHub Actions y no Railway.
5. **Secretos duplicados, no compartidos.** Cada tenant tiene su propio OAuth de YouTube, su propio token de Meta, su propia sesión de TikTok y su propio login del panel. No se comparten. Si el colega revoca su token, el panel de Sebastián sigue funcionando. Guardarlos por separado es más trabajo inicial (dos altas), pero es lo correcto y es más simple que un sistema de permisos compartido.

**En una línea:** el diseño "una instalación por persona" no cambia qué lenguaje elegir, pero sí confirma la recomendación: con un tenant por instalación, no hace falta ninguna infraestructura multi-tenant, y la solución más simple (Python + SQLite/D1 + Docker, desplegada dos veces desde la misma imagen) es también la más robusta.

---

## Fuentes consultadas (URLs exactas del buscador)

- Go / navegador: https://dashboard.statproxies.com/blog/golang-headless-browser · https://www.web-developpeur.com/en/blog/yeux-claude-code-navigateur-playwright-mcp-devtools · https://v2-frontier-make.vercel.app/blog/best-language-for-web-scraping · https://socket.dev/go/package/github.com/playwright-community/playwright-go?section=overview · https://raw.githubusercontent.com/mycalf/playwright-go/master/README.md
- Whisper: https://github.com/parkscloud/hearsay/blob/HEAD/research-summary.md · https://www.sinologic.net/en/2026-05/vosk-vs-whisper-local-the-ultimate-2026-guide-to-self-hosted-speech-recognition-stt.html · https://pkg.go.dev/github.com/xPrimeTime/go-whisper-ct2 · https://www.x-cmd.com/install/whisper.cpp/
- ffmpeg: https://json2video.com/how-to/ffmpeg-course/ffmpeg-wrappers.html?size=32&stub=1&reqid=1733513771943160-17316254282400762435-balancer-l7leveler-kubr-yp-vla-209-BAL · https://deepwiki.com/u2takey/ffmpeg-go · https://www.mpegflow.com/topics/encoding/ffmpeg
- YouTube: https://developers.google.com/youtube/data/quickstart/go · https://developers.google.com/youtube/data/reference/rest/v3/videos/insert · https://pkg.go.dev/github.com/googleapis/google-api-go-client@v0.0.0-20181229000844-f26a60c56f14
- Meta/TikTok: https://postdom.com/blog/instagram-reels-api · https://www.postzen.dev/blog/instagram-api-integration-guide · https://www.postbase.so/blog/tiktok-content-posting-api · https://blog.lots.social/tiktok-content-posting-api · https://uplonic.com/blog/tiktok-video-specs
- Hosting $0: https://en.inithtml.com/resources/cloudflare-free-tier-everything-you-get-in-2026-dns-cdn-ddos-r2-workers-d1/ · https://stacksfree.com/blog/cloudflare-workers-free-tier-guide-2026/ · https://outplane.com/blog/railway-pricing · https://stacksfree.com/blog/railway-free-tier-guide-2026/ · https://toolfreebie.com/free-hosting-ai-agents/ · https://stacksfree.com/blog/free-cron-jobs-scheduled-tasks-2026/ · https://space-node.net/blog/best-free-hosting-side-projects-2026 · https://xcloud.host/best-free-docker-hosting/
- Concurrencia/colas: https://www.toolmingo.com/blog/golang-vs-python · https://medium.com/@sharadhimarpalli/python-3-14-vs-go-concurrency-benchmark-on-m1-mac-updated-with-go-1-25-3-9024d86e53ff · https://www.devtoolsguide.com/background-job-tools/ · https://www.inngest.com/blog/best-job-queue-alternatives · https://github.com/h0rv/buquet/blob/HEAD/docs/guides/task-queue-comparison.md
- Rust: https://lib.rs/keywords/ffmpeg · https://lib.rs/crates/whisper-subs · https://dev.to/yeauty/feed-audio-to-whisper-in-rust-without-ever-shelling-out-to-ffmpeg-4if5 · https://www.utak3r.pl/2026/09/08/running-whisper-on-gpu-in-rust-with-onnx-and-directml/
