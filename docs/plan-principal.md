# Plan principal: Cloudflare + GitHub Actions — "Todo automático"

Fecha: 17 nov 2026. **Decisión del usuario: "Todo automático".**
No acepta cortar manual en el teléfono con CapCut. Quiere corte,
subtítulos, detección y publicación 100% automáticos tras pulsar
**Iniciar**. La vía principal pasa a ser **Cloudflare (panel / API /
cola) + GitHub Actions como compute principal $0** para Python +
ffmpeg + navegador.

Su miedo era que GitHub Actions cobrara o fuera lento; aclarado:
**no cobra dentro de los 2.000 min/mes gratis (repo privado)** y la
lentitud no importa porque el trabajo es programado (cron 09:00),
no interactivo. Solo Cloudflare + manual queda como **respaldo
degradado**, no como plan principal (ver
`docs/plan-cloudflare-solo.md`).

Experiencia que se mantiene: login → configurar cuentas / subtítulos
/ ajustes → botón **Iniciar** en el Worker → automatizado, sin pasos
manuales después, salvo lo imposible de automatizar (resolver un
captcha de TikTok, que se avisa). El usuario no toca GitHub Actions,
ni terminal, ni instala nada después de Iniciar.

## 1. Arquitectura $0 (una instalación por persona)

| Pieza | Recurso | Qué hace |
|---|---|---|
| Panel web | Cloudflare Pages `panel-sebastian` / `panel-colega` | Login, cuentas, subtítulos, ajustes, botón Iniciar, Cola, Tracking |
| API + cola + estados | Cloudflare Worker (TypeScript) por panel | Endpoints del panel, máquina de estados, cola |
| Datos | D1 por panel | Clips, estados, cuentas, `started`, registro de atascos |
| Videos | R2 por panel (10 GB gratis) | MP4 9:16 ya cortados, listos para publicar |
| Scheduler panel | Cron Triggers del Worker | Cola del panel, avisos |
| **Compute principal** | **GitHub Actions, un workflow por panel** (`.github/workflows/cerebro-sebastian.yml`, `cerebro-colega.yml`) | **Detección, corte, subtítulos, empaquetado y publicación** |
| Scheduler cerebro | Cron del workflow (09:00 America/Santiago) | Revisión diaria + cadena completa |
| TikTok por navegador | Runner autoalojado en la PC propia (solo esa pieza) | Sesión persistente, misma IP, captcha atendido por humano |

Compartido: código, esquema SQL, fórmula de prioridad, reglas.
Duplicado por persona: Pages, Worker, D1, R2, secretos, cron,
GitHub Environment + workflow.

## 2. La cadena completa (qué corre dónde)

El workflow del cerebro, tras pulsar Iniciar, hace en una pasada:

```
watch --once            → detecta VODs nuevos de IlloJuan (Twitch/Kick)
worker --once --top-n 5 → auto_clips: picos por chat → corte 9:16
                          (ffmpeg) → subtítulos faster-whisper →
                          empaquetado SEO → encola para publicar
publish loop --once --live → publica por API (política 6.3: gates,
                          topes diarios, breaker, anti-duplicado)
publish audit + status  → estado visible; atascos con motivo y hora
```

| Paso | Dónde | Estado |
|---|---|---|
| Login, cuentas, ajustes, Iniciar | Panel (Cloudflare) | ✅ Automático |
| Revisión diaria 09:00 (VOD nuevo) | GH Actions (`watch --once`) | ✅ Automático |
| Detección de picos por chat | GH Actions (auto_clips, `ingest.score_windows`) | ✅ Automático |
| Corte 9:16 (ffmpeg, layout split) | GH Actions (auto_clips, `render.py`) | ✅ Automático |
| Subtítulos (faster-whisper + quemado) | GH Actions (`transcribe.py`, extra `whisper`) | ✅ Automático |
| Títulos / SEO / hashtags | GH Actions (Gemini API gratis) + Worker | ✅ Automático |
| Cola con prioridad (viralidad^0,5 × SEO^0,3 × chat_norm^0,2) | Worker + D1 / `PublishQueue` | ✅ Automático |
| YouTube Shorts por API | GH Actions (`publish loop --live`, OAuth) | ✅ Automático |
| Instagram / Facebook por API | GH Actions (Graph API: contenedor / 3 fases) | ✅ Cuando App Review lo permita; si falta algo, draft con motivo |
| TikTok por API (Direct Post) | GH Actions (`TikTokAdapter`) | ✅ Cuando la auditoría/token lo permita; sin auditar, Direct Post queda privado → draft |
| TikTok por navegador | Runner autoalojado en su PC, NO en GH Actions | ⚠️ Pieza aparte (ver §4) |
| Atascos | Artefacto `cerebro-logs-<panel>` + D1 (`run-log`) | ✅ Registrados con motivo y hora, visibles en el panel |

**Nota sobre la partición en workers por paso:** el repo ya tiene
la partición (`clipagent/jobs.py`, `workers.py`, `step_jobs`,
`cortar.py`, `subtitular.py`, `detectar_picos.py`,
`empaquetar.py`; ver `docs/jobs-workers.md`). El workflow usa hoy
la cadena monolítica porque es la que funciona de punta a punta
sin cableado pendiente (auto_clips descarga chat+vídeo él mismo).
El cambio a `pipeline bridge` + `workers start --once` está
pendiente de (a) que el payload lleve `chat_path` y `video`, y
(b) que el supervisor cree los jobs de publicar solo tras
empaquetar (hoy se detiene ahí). No se cambia hasta que (a) y (b)
existan.

## 3. Cómo encaja el botón Iniciar

1. El usuario pulsa **Iniciar** en el panel.
2. El Worker pone `started=true` en D1 **y** dispara el workflow
   con `repository_dispatch` (`panel-start-sebastian` /
   `panel-start-colega`) usando un token de GitHub guardado como
   secreto del Worker (`GITHUB_TOKEN`, nunca en el repo).
3. El dispatch deja `started=true` en `state/panel-state.json`
   (persistido como artefacto entre días).
4. El cron diario (09:00 America/Santiago) solo trabaja si
   `started=true`; si no, termina registrando "panel no iniciado",
   sin hacer nada. El botón **Detener** manda `panel-stop-<panel>`
   y el cron vuelve a quedar en espera.
5. Nunca un paro silencioso: cada fallo queda en
   `state/run-log.jsonl` (artefacto `cerebro-logs-<panel>`, 90 días)
   y el panel lo muestra.

## 4. Lo que sigue sin ser 100% automático (honesto)

- **TikTok por navegador:** no puede vivir en GitHub Actions (IP
  de centro de datos → mucho captcha; sesión persistente en disco
  local; captcha lo resuelve un humano). Corre en el runner
  autoalojado de su PC, solo en la ventana de publicación, no
  24/7. Sus clips quedan en draft en la cola hasta que el agente
  los publica o se suben a mano. La solución a largo plazo para no
  tener captcha es la API oficial.
- **Captcha (cualquier vía):** el agente se detiene, marca
  `waiting_captcha`, guarda captura y avisa; el humano lo resuelve
  en esa misma sesión. Nunca auto-solver.
- **App Review de Meta** (Reels por API) y **auditoría de TikTok**:
  pendientes; mientras tanto, IG/FB/TikTok caen a draft con motivo.
- **Directo en vivo:** TwitchDownloaderCLI no tiene comando live;
  el corte en vivo sigue sin existir (Fase 0 sombra → Fase 1
  semi-manual 19-nov). La cadena automática cubre VODs.

## 5. Coste $0 (por qué no cobra)

- Repo **privado**: 2.000 min/mes gratis de GitHub Actions.
- Una corrida diaria: instalar dependencias (Python + ffmpeg +
  faster-whisper + TwitchDownloaderCLI) + 1 VOD + 1 clip + publicar.
  **Medir los minutos reales en la primera corrida** y anotarlos
  aquí; si el instalador domina el tiempo, la mejora es cachear
  dependencias (`actions/cache` para pip), no pagar.
- Cloudflare: todo dentro del tier gratis (Workers, D1, R2 10 GB,
  Pages). Gemini: tier gratis (~500 pet./día).
- Nada queda prendido: el runner de GitHub se apaga solo al terminar.

## 6. Secretos (solo por nombre, nunca valores en el repo)

Por Environment de GitHub (`sebastian` / `colega`):

| Secreto | Para qué |
|---|---|
| `GEMINI_API_KEY` | Títulos/SEO, visión |
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | Listar VODs (Helix) |
| `YOUTUBE_CLIENT_SECRETS_JSON_B64`, `YOUTUBE_TOKEN_JSON_B64` | Subida real a YouTube (OAuth, base64) |
| `IG_USER_ID`, `FACEBOOK_PAGE_ID` | IG contenedor / FB 3 fases |
| `CLIP_PUBLIC_BASE_URL` | URL pública HTTPS del clip (IG la exige) |

Secreto del Worker (Cloudflare, `wrangler secret put`):

| Secreto | Para qué |
|---|---|
| `GITHUB_TOKEN` | El botón Iniciar dispara el workflow (`repository_dispatch`); necesita permiso `actions:write` / `repo` sobre `exe2027/viral-clip-agent` |

## 7. Puesta en marcha (checklist)

1. Resolver el push 403 a GitHub (instalación de la app,
   permiso Contents: Write): sin push, los workflows no llegan al
   repo y nada de esto corre.
2. Crear los Environments `sebastian` y `colega` con los secretos
   de §6 (los introduce él en su cuenta; nada por chat).
3. Configurar `GITHUB_TOKEN` como secreto del Worker por panel.
4. Desplegar el panel (`./deploy-all.sh`) y pulsar Iniciar.
5. Primera corrida real: medir minutos de GH Actions, verificar
   que el VOD de Twitch se descarga (TwitchDownloaderCLI 1.56.5),
   que faster-whisper subtitula y que YouTube publica.
6. TikTok: runner autoalojado + sesión (`clipagent tiktok-browser
   login`) solo para esa pieza.

## 8. Respaldo degradado

Si GitHub Actions dejara de estar disponible: **solo Cloudflare +
manual** (el usuario corta el clip en el teléfono y sube el MP4
9:16 al panel; el resto — cola, SEO, publicación por API — sigue
solo tras Iniciar). Detalle en `docs/plan-cloudflare-solo.md`.
Es un respaldo, no el plan: se pierde el corte, los subtítulos y
la detección automáticos.
