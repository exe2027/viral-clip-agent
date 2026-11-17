# Cerebro partido en workers + tabla jobs (Opción A, todo Python)

Fecha: 17 nov 2026. Estado: implementado en el repo, sin desplegar.
Referencia intocable: `docs/MASTER-PROMPT-FINAL-viral-clip-agent.md`
(copia verbatim del MÁSTER PROMPT FINAL; no se reescribe, solo se
referencia). Fórmula de prioridad intocable: se IMPORTA de
`clipagent/priority.py` (`viralidad^0,5 × SEO^0,3 × chat_norm^0,2`);
ningún worker la reimplementa.

## 1. Dos tablas de jobs (por qué no hay duplicado)

`queue.db` tiene DOS tablas con distinto grano:

| Tabla | Grano | Quién la usa |
|---|---|---|
| `jobs` (legacy, `jobqueue.py`) | un VOD entero (`url`, `vod_id`, `out_dir`) | watcher, worker monolítico, panel. Estados `queued → processing → ready_for_review → approved …`. **No se toca.** |
| `step_jobs` (nueva, `clipagent/jobs.py`) | un paso de un clip en una plataforma (`clip_id`, `plataforma`, `paso`) | los workers de esta partición. Estados `pending → running → done/failed`, `scheduled → processing → posted` (publicar) y `esperando_humano`. |

Se llama `step_jobs` y no `jobs` porque la tabla `jobs` ya existía:
ponerle el mismo nombre habría roto el watcher, el worker y sus
tests. El DDL es el mismo en local y en D1
(`migrations/0001_step_jobs.sql`).

## 2. Esquema `step_jobs`

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
clip_id TEXT NOT NULL,            -- clip o VOD (picos fan-out: "vod:p1234")
plataforma TEXT,                  -- youtube_shorts|tiktok|instagram_reels|facebook_reels (solo publicar)
paso TEXT NOT NULL,               -- vod|picos|corte|subtitulos|empaquetar|publicar
estado TEXT NOT NULL DEFAULT 'pending',
intentos INTEGER NOT NULL DEFAULT 0,
max_intentos INTEGER NOT NULL DEFAULT 3,
proximo_reintento TEXT,           -- ISO UTC, backoff exponencial (60 s…1 h)
clave_anti_duplicado TEXT NOT NULL,  -- UNIQUE (ver §3)
payload_json TEXT NOT NULL DEFAULT '{}',
result_json TEXT,
error TEXT,                       -- motivo visible, con hora (updated_at)
error_kind TEXT,                  -- transient|permanent|timeout|human
vod_job_id INTEGER,               -- job legacy de jobqueue, si lo hay
prioridad REAL NOT NULL DEFAULT 0,    -- orden de la cola (no FIFO)
created_at TEXT NOT NULL, updated_at TEXT NOT NULL
-- UNIQUE INDEX idx_step_jobs_idempotency (clave_anti_duplicado)
-- INDEX idx_step_jobs_due (estado, paso, prioridad, created_at)
-- INDEX idx_step_jobs_clip (clip_id, paso)
```

`step_job_events` guarda el historial por job (`enqueued`, `claimed`,
`retry_scheduled`, `failed`, `esperando_humano`, `pipeline_advance`,
`requeued_stale`…), con hora. Es lo que el panel muestra en Tracking.

Transiciones permitidas (`clipagent/jobs.py:TRANSITIONS`):

```
pending     -> running | scheduled | failed | esperando_humano
scheduled   -> processing | pending | failed | esperando_humano
running     -> done | failed | pending | esperando_humano | scheduled
processing  -> posted | failed | pending | esperando_humano | scheduled
esperando_humano -> pending        (solo resume humano)
failed      -> pending             (solo resume humano, intentos a 0)
done, posted: terminales
```

## 3. Idempotencia

`clave_anti_duplicado = sha256(...)`, UNIQUE. Repetir el mismo
encolado devuelve el job existente, nunca crea otro.

- Paso normal: `sha256(paso|clip_id|plataforma|payload canónico)`.
  Mismos datos = mismo job; datos distintos (otro inicio de corte) =
  job nuevo.
- Publicar: `sha256(publicar|clip_id|plataforma)` SIN payload: un clip
  no se publica dos veces en la misma plataforma aunque cambie el
  título (plan §5). Además el worker de publicar comprueba la cola de
  publicación (`PublishQueue`) antes de subir: si ya está `published`,
  termina en `already_posted` sin subir nada.

## 4. Workers por paso (sin reescribir la lógica)

Cada handler delega en el módulo que ya lo hacía:

| Paso | Reutiliza | CLI estable (Fase 2, Go lo llama tal cual) |
|---|---|---|
| `vod` | `jobqueue.parse_url` + lista blanca | — (validación, sin vídeo) |
| `picos` | `ingest.score_windows` + `select_top_peaks` + `detect.score_clip` + filtro previo de `auto_clips` | `detectar_picos.py chat.json --top-n 5` |
| `corte` | `render.cut_clip_with_layout` (ffmpeg, 9:16) | `cortar.py --in v.mp4 --start 11710 --duration 60 --out clip.mp4` |
| `subtitulos` | `transcribe.transcribe_audio` + `generate_srt` + `burn_subtitles` (faster-whisper) | `subtitular.py clip.mp4 [--srt …] [--burn …]` |
| `empaquetar` | `seo_score.score_all_platforms` + `priority.priority` (importada) | `empaquetar.py clip.mp4 --title "…"` |
| `publicar` | `publish.PublishQueue` + `publish_one` (política 6.3) | `clipagent pipeline publish …` |

Idempotencia por paso: `corte` no re-corta si la salida ya existe;
`subtitulos` reutiliza el SRT si ya existe; `picos` y `empaquetar`
son puros (mismo input = mismo output, escritura atómica del
paquete). Si el clip no tiene voz, `subtitulos` termina OK marcado
`no_speech` (no es un fallo).

## 5. Un solo punto de encendido (experiencia final)

El usuario no maneja un comando por paso. Hay UN supervisor:

```
python -m clipagent workers start [--db queue.db] [--once] [--max-jobs N]
```

Es lo que enciende el botón **Iniciar** del panel. Bucle: recuperar
jobs de un worker muerto (`requeue_stale`, sin gastar intento) →
tomar el siguiente job debido (cualquier paso, por prioridad) →
ejecutarlo → encolar el paso siguiente → repetir. Con `--once`
procesa lo debido y sale (para cron/tests). Con `queue.kill`
(kill-switch) se pausa de forma segura y sale con 1, sin perder nada.

La cadena avanza sola: `vod → picos → corte (uno por pico) →
subtitulos → empaquetar`. Se detiene sola en `empaquetar`:
**publicar nunca se crea automáticamente**; solo la aprobación humana
crea un job de publicar por clip y plataforma
(`clipagent pipeline publish --clip … --plataforma tiktok --mode draft|dry-run|api`).

Si un paso se atasca, queda visible con motivo y hora
(`esperando_humano` para captcha/credencial/aprobación —sin reintento
automático—; `failed` con motivo tras agotar los reintentos), el
resto de la cola sigue, y nada reintenta a ciegas hasta el infinito
(máx. 3 intentos, backoff 60 s → 1 h). Reanudar: `clipagent jobs
resume --job-id N`. Ver atascados: `clipagent workers status`.

La configuración que el usuario toca en el panel (cuentas,
subtítulos, ajustes) alimenta a los workers sin pasos técnicos: cada
job relee `config.yaml` al empezar (`load_worker_config`), así el
siguiente job ya usa la config nueva sin reiniciar el supervisor.
Sin secretos en el código: credenciales en `tokens/` / entorno.

Depuración por paso (no es la vía del usuario):
`python -m clipagent workers step --paso corte --once`.

## 6. Tests

`test_step_jobs.py` (tabla: esquema, idempotencia, reclamo atómico,
transiciones, reintentos con backoff, stale, orden por prioridad,
DDL de la migración) y `test_step_workers.py` (handlers con fakes,
pipeline y avance, publicar draft/dry-run, CLI `cortar.py` /
`subtitular.py`). Suite completa: ver el reporte de la entrega
(nº de tests pasados/fallidos al final de esta implementación).

## 7. Dónde corre esto — vía principal: Cloudflare + GitHub Actions

**Decisión del usuario (17 nov 2026, "Todo automático"): NO acepta
el paso manual de cortar en el teléfono con CapCut. Quiere corte,
subtítulos, detección y publicación 100% automáticos tras Iniciar.
Eso implica GitHub Actions como cómputo principal $0 para Python +
ffmpeg + navegador. Su miedo anterior era que cobrara o fuera lento;
ya se aclaró que no cobra dentro de los 2.000 min/mes gratis y que
la lentitud no importa por ser programado.**

(Historial: el 17 nov por la mañana dijo que GitHub Actions le
complicaba y se dejó como alternativa; esa misma tarde decidió "Todo
automático" y pasó a ser la vía principal. Lo anterior queda
anulado.)

### 7.1 Reparto principal (Cloudflare + GitHub Actions, $0)

| Pieza | Dónde | Por qué ahí |
|---|---|---|
| Panel web, login, cuentas, ajustes | Cloudflare Pages + Workers | Es web; no necesita ffmpeg |
| Cola + estados (`step_jobs`) | D1 (mismo esquema, `migrations/0001_step_jobs.sql`) | El Worker la lee/escribe; el runner de GitHub también |
| Vigilar VODs 09:00, cron | GitHub Actions (`schedule`, workflow cerebro-*.yml) + Cron Trigger del Worker como respaldo | El cron de GitHub dispara el trabajo; el panel solo es el interruptor |
| Cerebro: detección, corte, subtítulos, empaquetar, publicar | **GitHub Actions, runner `ubuntu-latest`** | Python + ffmpeg + faster-whisper + Playwright corren ahí; repo ya en GitHub (`exe2027/viral-clip-agent`, privado) |
| Botón Iniciar / Detener | Panel → `repository_dispatch` al workflow (con token guardado como secreto del Worker, nunca en el repo) | El usuario no toca GitHub, ni terminal, ni instala nada después de Iniciar |
| TikTok navegador (sesión + captcha) | Runner autoalojado en su PC, solo para esa pieza | Necesita sesión persistente en disco, misma IP y humano para el captcha; no corre en el runner de GitHub |

Coste: repo privado 2.000 min/mes gratis (público ilimitado). Para
1 clip/día alcanza de sobra: una corrida (vigilar + picos + corte +
subtítulos + publicar) son minutos, no horas. No hay que comprar
nada ni dejar nada prendido: GitHub corre el trabajo cuando toca
(cron o dispatch) y se apaga solo. La PC solo se necesitaría para el
login inicial una vez (sesión TikTok), no 24/7.

Qué ejecuta el workflow por corrida (cadena que funciona hoy, sin
cableado pendiente):

```
python -m clipagent watch --once               # VODs nuevos -> cola legacy
python -m clipagent worker --once --top-n 5    # cadena monolítica: picos -> corte -> subtítulos -> empaquetar
python -m clipagent publish loop --once --live # publicación por API (política 6.3)
python -m clipagent publish audit              # estado de publicación
```

El cambio futuro a la partición (`pipeline bridge --limit 5` +
`workers start --once`) está pendiente del cableado del §7.4 y de
que el supervisor cree los jobs de publicar solo (hoy se detiene en
`empaquetar`); hasta entonces, la cadena monolítica es la que da el
100% automático.

Requisitos del runner (ya casi todos en el workflow): `ffmpeg`
(apt), `pip install -e .[dev,youtube,whisper]` (el extra `whisper`
es para faster-whisper; sin él, subtítulos falla permanente con
motivo claro), y —cuando se cablee la descarga de chat—,
TwitchDownloaderCLI.

### 7.2 Qué cabe en solo Cloudflare $0 (y qué no)

Si algún día GitHub Actions no estuviera, solo con Workers + D1 +
R2 + Cron Triggers + Queues cabe: cola/estados, vigilar VODs,
picos por chat (aritmética pura, portable a JS), SEO + prioridad
(puro), publicar YouTube/Meta por API oficial (solo HTTP) y TikTok
a borrador por API (`video.upload`, 1 toque en la app). NO cabe:
corte con ffmpeg (sin binario ni `subprocess`, 10 ms de CPU, 128
MB), subtítulos con faster-whisper (modelo nativo), ni navegador
TikTok. **Sin ningún cómputo externo, el 100% automático no es
posible** — por eso solo Cloudflare + manual queda como respaldo
degradado (§7.3), no como plan principal.

### 7.3 Respaldo degradado (solo Cloudflare + manual)

Si GitHub Actions cae o se descarta: el Worker sigue vigilando,
puntuando y publicando por API; el corte y los subtítulos quedan
manuales (CapCut o similar) o por Gemini API gratis desde el Worker
(audio → transcripción por HTTP, sin Whisper) y ffmpeg.wasm en el
panel (corta en el navegador cuando está abierto; lento en el
teléfono). Es un modo degradado: la cola no se para en silencio,
pero deja de ser 100% automática. No es el plan principal.

### 7.4 Qué falta para que el supervisor corra en GitHub Actions

- **Puente legacy → steps (HECHO en esta entrega).**
  `clipagent.workers.bridge_legacy_queue` + `clipagent pipeline
  bridge`: convierte los VODs que deja `watch --once` en pipelines
  de pasos. El job legacy queda en `processing` (reclamado).
- **Cableado chat/vídeo en el payload (PENDIENTE, trabajo por
  asignar).** El pipeline espera `chat_path` y `video` en el
  payload y el watcher legacy hoy no los escribe (el chat/vídeo los
  descarga el worker legacy dentro de `auto_clips`). Hasta que eso
  exista, `picos` falla permanente con motivo claro ("falta
  chat_path") y la cadena se detiene visible, nunca en silencio.
  Dos vías: (a) el watcher escribe `chat.json` y el segmento de
  vídeo en `out_dir` al encolar; (b) el paso `vod` descarga el chat
  con `auto_clips.download_chat` (exige TwitchDownloaderCLI en el
  runner). Sin esto, el supervisor en GH Actions valida VODs pero
  no produce clips.
- **Cambiar el paso del cerebro en los workflows (PENDIENTE,
  coordinar con el agente de workflows).** Los archivos
  `.github/workflows/cerebro-sebastian.yml` y `cerebro-colega.yml`
  hoy llaman al worker monolítico legacy (`clipagent worker
  --once`) + `publish loop --once --live`: esa es la cadena que
  funciona hoy (auto_clips descarga chat+vídeo él mismo). Su
  cabecera ya se corrigió en la reconciliación del 17 nov (decía
  "ALTERNATIVA, NO LA VÍA PRINCIPAL"; ahora dice vía principal).
  El cambio a `pipeline bridge` + `workers start --once` sigue
  pendiente de (a) el cableado chat/vídeo de arriba y (b) que el
  supervisor cree los jobs de publicar solo tras empaquetar — hoy
  se detiene ahí y publicar desde esa partición exige aprobación
  explícita (`clipagent pipeline publish`), lo que rompería el
  "Todo automático". No se cambia el workflow hasta que (a) y (b)
  existan; el cambio exacto queda aquí registrado para que el
  coordinador lo dirija.
- Probar subtítulos por Gemini API gratis (audio → SRT) con 1 clip
  (respaldo de subtítulos, no principal).
- Pedir el scope `video.upload` de TikTok (borrador por API).
- Resolver el push 403 a GitHub (permiso Contents: Write de la
  app): sin push no hay despliegue por Git.

## 8. Pregunta pendiente para el usuario

**Resuelta el 17 nov 2026: "Todo automático".** Cómputo aceptado:
GitHub Actions como principal ($0, 2.000 min/mes, repo ya
existente). Ya no se pregunta por CapCut/manual ni por otras vías:
el manual queda solo como respaldo degradado (§7.3). La única
pregunta que queda es de trabajo, no del usuario: el cableado
chat/vídeo del §7.4.

## 9. Qué falta (después de esta entrega)

- Cableado chat/vídeo en el payload del pipeline (§7.4) y cambio
  del paso del cerebro en los workflows a `workers start --once`.
- Portar `picos` y `empaquetar` a TypeScript en el Worker (son
  cómputo puro; el Python queda como referencia y tests).
- Probar ffmpeg.wasm con 1 clip real en su teléfono (respaldo).
- Probar subtítulos por Gemini API gratis (audio → SRT) con 1 clip.
- Pedir el scope `video.upload` de TikTok (borrador por API).
- Agente navegador TikTok solo en runner autoalojado (no en GH).
- Resolver el push 403 a GitHub (permiso Contents: Write de la
  app): sin push no hay despliegue por Git.
