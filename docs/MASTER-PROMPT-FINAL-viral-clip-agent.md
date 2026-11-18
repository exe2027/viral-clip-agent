# MÁSTER PROMPT FINAL — viral-clip-agent: "pegar links y presionar Iniciar"

## 0. Reconciliación con los prompts anteriores (LEE ESTO PRIMERO)
Ya recibiste dos máster prompts antes que este. ESTE los reemplaza y manda en
cualquier contradicción. En especial:
- Ya NO hay aprobación humana por clip (el primer prompt la mencionaba): la
  publicación es automática, con red de seguridad (Fase 6).
- El sistema de puntaje (viral_score, seo_score, priority) es intocable en
  sus fórmulas y se conecta como cerebro de la publicación (Fase 6 bis).

Antes de escribir código:
1. Revisa qué del trabajo anterior YA hiciste (git log / git diff / tests).
2. NO rehagas lo que ya está hecho y pasa sus tests. Verifícalo contra los
   criterios de aceptación de abajo y corrige solo lo que no los cumpla.
3. Si hiciste algo que contradice este prompt (p. ej. una cola de aprobación
   manual obligatoria), conviértelo a lo que pide este prompt y avísalo en el
   resumen.
4. Entrega primero una tabla corta: fase/punto -> HECHO / PARCIAL / PENDIENTE.
   Luego continúa desde la primera fase PARCIAL o PENDIENTE.

## 1. Objetivo final (manda sobre todo lo demás)
El usuario SOLO hace dos cosas: pega links de canales de streamers (Twitch o
Kick) y presiona "Iniciar". El sistema hace todo solo, de punta a punta:
detectar directos y VODs nuevos -> descargar solo lo necesario -> elegir los
momentos más virales -> cortar en 9:16 -> subtitular -> titular -> miniatura
-> publicar en las cuentas configuradas (YouTube, TikTok, Instagram,
Facebook) -> guardar métricas. Sin aprobación humana por clip.

## 2. Tu rol
Eres un ingeniero senior de Python. Trabajas sobre el repo `viral-clip-agent`
existente (≈20k líneas, Python 3.10+, ffmpeg, faster-whisper opcional,
SQLite, YAML). Trabaja por FASES en orden. No avances de fase si algún test
falla. Un commit lógico por punto numerado.

## 3. Reglas inamovibles
R1. Automatización SÍ, pero con red de seguridad obligatoria (ver Fase 6):
    filtros de calidad, circuit breaker por plataforma, topes diarios,
    espaciado, kill-switch y botón Detener que paran TODO (incluidos hijos).
    El sistema de puntaje (viral_score.py, seo_score.py, priority.py) es
    INTOCABLE en sus fórmulas, pesos y umbrales: solo se conecta y se prueba.
R2. "Estar en la lista de streamers = permiso dado" es regla del usuario.
    Se respeta tal cual. No agregues fichas de permiso obligatorias. Solo un
    campo OPCIONAL `evidencia_url` por streamer.
R3. Cero secretos en archivos versionados. Todo secreto va en `.env` o en el
    almacén de tokens (Fase 3), nunca en YAML ni en código.
R4. No inventes datos de plataformas (cuotas, límites, scopes, plazos de
    tokens). Verifica en la documentación oficial. Si no puedes verificar,
    deja el valor más conservador en `limits.yaml` con `reviewed: null`.
R5. Una plataforma sin credenciales o con error NUNCA rompe el resto: cae a
    estado "borrador" y el sistema sigue con las demás.
R6. No borres tests. Todo cambio de comportamiento lleva test.
R7. Dependencias nuevas: solo si hacen falta, justificadas en una línea y
    opcionales con fallback cuando se pueda.
R8. Mensajes al usuario y docstrings en español, como el repo.
R9. Si algo es ambiguo y el riesgo de equivocarse es bajo, decide, anótalo en
    el resumen de la fase y sigue. No pares a preguntar por cosas menores.

## 4. Contexto del repo (lo que ya existe)
- Pipeline: clips_chat_v2.py (scoring de chat), auto_clips.py (orquestación),
  layout.py (9:16 con facecam), subtitles.py (whisper + ASS karaoke),
  precise_cut.py, viral_score.py, seo_score.py, priority.py,
  viral_package.py (títulos/SEO), thumbnails.py, moment_card.py, audio.py,
  vision.py.
- Operación: queue.py (cola SQLite), worker.py (1 worker con flock),
  watcher.py (monitorea influencers), publisher.py (YouTube por API,
  resto draft-first), counter.py + AUTOPUBLISH.md (fases y circuit breaker),
  kick_downloader/.
- Config: config.yaml, accounts.yaml, influencers.yaml,
  allowed_streamers.yaml. Hay TRES listas de streamers: se unifican.
- Hallazgo importante: priority.py solo lo usa su propio test; ni worker,
  ni cola, ni publisher lo importan, y el `can_publish` de SEO solo imprime
  un aviso en auto_clips.py. El sistema de puntaje existe pero hoy NO manda
  sobre lo que se publica.

## FASE 1 — Bugs confirmados (máxima prioridad)
1.1 `queue.py` hace sombra al módulo `queue` de la stdlib (rompe requests,
    urllib3, multiprocessing). Renómbralo a `jobqueue.py`, actualiza todos
    los imports (worker, watcher, counter, tests, docs) y elimina los
    parches `_preload_stdlib_queue` de auto_clips.py y subtitles.py.
    Criterio: `pytest` recoge y pasa test_queue.py y test_watcher.py (hoy no
    se pueden recolectar) y `import requests` funciona desde la carpeta.
1.2 auto_clips.py no usa los arreglos de clips_chat_v2.py: borra su
    `sample_messages` propio (relee el JSON por clip) e importa el cacheado;
    escribe manifest.json tras CADA clip con escritura atómica (tmp +
    os.replace). Test: fallo en el clip 3 de 5 conserva los clips 1-2.
1.3 `viral_score.filter_passed` solo imprime. Los candidatos que no pasan
    (múltiplo < mínimo, solo emote-spam, sin mensajes) NO se cortan ni se
    publican; van a `descartados` en el manifiesto con motivo. `--top-n` es un
    MÁXIMO, no una cuota. Si ninguno pasa, el job termina OK con "sin momentos
    virales". Test: chat sintético de 2 h con 3 picos reales + ruido => 3
    clips, no 5.
1.4 Worker: lanzar auto_clips con `start_new_session=True` y matar con
    `os.killpg` (SIGTERM y luego SIGKILL) en timeout y kill-switch. Un timeout
    reintenta máximo 1 vez. No borrar worker.lock tras soltar el flock
    (carrera). Test: un hijo `sleep` no sobrevive al timeout.
1.5 Secretos: quita client_id/client_secret de config.yaml; ignora por nombre
    en .gitignore: client_secrets.json, token.json, publish_queue.json, .env,
    *.db, tokens/. Agrega `.env.example` sin valores.
1.6 `TWITCH_CLI_DEFAULT` no puede apuntar a `~/workspace/...`: léela de
    config/env (`TWITCH_DL_CLI`) con error claro si no existe.

## FASE 2 — Bugs probables (reproduce primero, corrige después)
Para cada uno escribe un test/script que lo reproduzca. Si no se reproduce,
explica por qué y sigue.
2.1 `ffmpeg -ss X -i ... -c copy` en la ventana temporal corta en keyframe y
    desfasa peak_rel, hook y subtítulos. Mide el desfase y corrige.
2.2 `faces_1fps` lanza un ffmpeg por segundo: extrae frames en UNA pasada.
2.3 `GTA6_LAUNCH` fija y comparada en UTC: muévela a config con zona
    America/Santiago. Generaliza `detect_game` con una lista `games:` en
    config (patrones de categoría); un juego fuera de la lista sigue en rojo.
2.4 Cuota de YouTube inconsistente (publisher.py dice 1.600/subida; el
    documento de auditoría dice 100). Crea `limits.yaml` (valor, source_url,
    reviewed) y haz que publisher.py y el planificador lean de ahí.

## FASE 3 — Enlace de APIs y credenciales (reescribe lo existente)
Objetivo: que conectar cada plataforma sea un proceso guiado, seguro,
verificable y que se renueve solo. Revisa lo que ya hay (accounts.yaml,
PUBLISHING.md, CONFIGURACION.md, publisher.py) y corrige lo que esté mal
puesto; no asumas que está bien.
3.1 Almacén de credenciales único: `.env` para secretos estáticos y carpeta
    `tokens/` (gitignored, permisos 600) para tokens OAuth. Un módulo
    `clipagent/credentials.py` es la ÚNICA puerta de acceso; ningún otro
    módulo lee archivos de credenciales directamente.
3.2 `accounts.yaml` guarda SOLO estado no secreto (cuenta creada, página
    vinculada, última verificación). Deja de ser fuente de verdad "a mano":
    el estado se calcula comprobando credenciales reales.
3.3 Comando `clipagent setup` (asistente interactivo, repetible):
    - Revisa por plataforma qué falta y muestra los pasos EXACTOS y los
      enlaces a las consolas oficiales. Marca claramente qué solo el usuario
      puede hacer a mano una vez (crear cuenta/página, aprobar app, aceptar
      permisos en el navegador).
    - Lanza el flujo OAuth local cuando corresponda (servidor en localhost
      para el callback) y guarda el token en tokens/.
    - Termina con una prueba real de cada conexión (ver 3.5).
3.4 Plataformas (verifica cada punto en la documentación oficial vigente
    antes de implementarlo y deja el enlace en el docstring):
    a) YouTube: OAuth 2.0 de escritorio, scope de subida, refresh token,
       thumbnails.set, cuota diaria (ver limits.yaml), canal verificado
       necesario para miniaturas personalizadas.
    b) TikTok: Content Posting API; sin auditoría de la app solo se puede
       publicar privado -> detectarlo y caer a borrador, no a error. Manejar
       expiración y refresco del token.
    c) Instagram Reels: Graph API, cuenta profesional vinculada a una Página
       de Facebook; flujo contenedor -> estado -> publicar; manejar el
       límite diario y el tiempo de procesamiento del video.
    d) Facebook Reels: solo a Página (nunca perfil personal), Page access
       token, requisitos de duración, resolución y fps del video.
    e) Twitch Helix (client credentials) para el watcher; si no hay
       credenciales, usa GQL como respaldo y avisa que es no oficial.
    f) Kick: sin API oficial de chat; documenta qué se usa y sus riesgos.
    g) LLM (para elegir clips y metadata): interfaz `LLMClient` con
       proveedor intercambiable por env var; sin clave, fallback al scoring
       actual.
3.5 `clipagent doctor`: comprueba TODAS las conexiones con llamadas de solo
    lectura (sin publicar nada) y muestra una tabla: plataforma, estado
    (OK / falta paso / token vencido / sin permisos), causa y cómo
    arreglarlo. Se ejecuta también al iniciar el servicio.
3.6 Renovación de tokens automática y antes de que venzan. Si un refresco
    falla, esa plataforma pasa a borrador y se notifica; nunca se pierde un
    clip por token vencido (queda en la cola de reintento).
3.7 Estado por plataforma visible en el panel: conectada, borrador o
    pausada, con el motivo.
3.8 Tests con APIs simuladas (mocks): token vencido, scope faltante, rechazo
    por copyright, límite diario alcanzado, error 5xx transitorio.

## FASE 4 — Una sola lista de streamers y entrada por links
4.1 Unifica config.yaml/allowlist, influencers.yaml y allowed_streamers.yaml
    en `streamers.yaml`. Migra los datos sin perder nada. Un solo lector.
4.2 Entrada única de links: acepta twitch.tv/<canal>, kick.com/<canal> y
    también links de VOD. Extrae plataforma y slug, valida y agrega a
    streamers.yaml. Error claro si no se reconoce. Quitar un streamer
    bloquea nuevas subidas aunque haya clips aprobados (comportamiento
    actual de publisher.py: mantenerlo).
4.3 El watcher usa solo esta lista. Límite de VODs nuevos por streamer por
    día (ya existe en config: respétalo).

## FASE 5 — Refactor de estructura
5.1 Paquete `clipagent/` con: ingest, detect, transcribe, render, metadata,
    publish, config, credentials, service. Mover código sin cambiar lógica
    en este paso (los tests deben pasar solo con cambios de import).
5.2 Un solo CLI `clipagent`: start, stop, status, setup, doctor, add
    <link>, queue, worker, watch, publish. Los scripts viejos quedan como
    wrappers con aviso de deprecación.
5.3 `pyproject.toml` (dependencias, extras `whisper` y `vision`, ruff,
    pytest).
5.4 Validación de config al arrancar (pydantic o jsonschema) con errores que
    nombren la clave exacta.
5.5 Etapas idempotentes con checkpoint por job (runs/<vod>/state.json):
    reanuda desde la última etapa completa.

## FASE 6 — Automatización total y red de seguridad
6.1 Orquestador `clipagent start`: levanta watcher + worker + publicador como
    un servicio, reinicia procesos caídos, recupera jobs colgados al
    arrancar, respeta el kill-switch. `clipagent stop` y el botón Detener
    paran todo el árbol de procesos.
6.2 Panel web local mínimo (solo localhost): cuadro para pegar links, lista de
    streamers activos con interruptor, botón Iniciar/Detener, estado de las
    plataformas (3.7), cola, últimos clips publicados, clips retenidos con
    motivo y errores recientes.
6.3 Aprobación automática por política, sin humano por clip. Config
    `auto_approve:` (puntaje mínimo, banda mínima, sin rojos del semáforo,
    sin música/copyright sospechada, juego permitido, sin duplicados).
    Con rojos: NO se publica, queda "retenido" con motivo visible. La
    política usa los puntajes de la Fase 6 bis.
6.4 Circuit breaker por plataforma: N fallos seguidos, rechazo por
    copyright/contenido o strike => pausa SOLO esa plataforma, notifica y no
    reintenta a ciegas. Reanudar es una acción explícita en el panel.
6.5 Ritmo: topes diarios y espaciado (SIN franja horaria desde el 2026-11-18: da lo mismo la hora) desde
    limits.yaml; cuentas nuevas con ritmo reducido la primera semana.
6.6 Notificaciones opcionales por Telegram, SOLO informativas ("publicado",
    "plataforma pausada", "falló X"). Nunca piden permiso.
6.7 Compatibilidad con counter.py/AUTOPUBLISH.md: las fases y el contador
    "funciona" se mantienen como mecanismo de arranque gradual opcional
    (`autopublish.mode: gradual | full`). `full` publica según 6.3 sin
    pasar por las fases; `gradual` conserva el comportamiento actual.

## FASE 6 BIS — El sistema de puntaje MANDA sobre qué se publica y cuándo
Contexto: el repo ya tiene viral_score.py (viralidad 0-100), seo_score.py
(SEO 0-100 por plataforma) y priority.py (prioridad = viralidad^0,5 x SEO^0,3
x chat_norm^0,2, con filtros duros, penalización por edad y recalibración).
Hoy priority.py NO está conectado a worker, cola ni publisher, y
can_publish de SEO solo imprime. Esta fase los convierte en el cerebro de la
publicación automática. NO cambies sus fórmulas, pesos ni umbrales; solo
conéctalos y agrega tests.

6B.1 Conectar priority.order_queue como único orden de publicación:
     la cola de publicación NUNCA es FIFO. Cada plataforma ordena sus clips
     por su propia prioridad (el SEO es por plataforma). publisher.due_items
     usa ese orden.
6B.2 Aplicar los filtros duros de verdad, antes de publicar:
     - viralidad < 50: descartado (queda en `descartados` con motivo).
     - SEO de la plataforma < 90: NO se publica salvo override registrado
       (register_override); SEO < 70 es bloqueo duro sin override.
     - penalización por edad, archivado por clip viejo de prioridad baja
       (should_auto_archive), y no dos clips del mismo momento el mismo día en
       la misma plataforma (moment_key).
     El umbral SEO_GATE (90) y el mínimo de viralidad (50) se leen de
     config.yaml (`scoring.seo_gate`, `scoring.viral_min`) con esos valores
     por defecto. Si el usuario los baja, queda registrado en el log con
     fecha. No se cambian solos.
6B.3 "En qué momento se hace el clip" = por score: el worker procesa y corta
     primero los candidatos de mayor prioridad. Los de score bajo no se cortan
     (ahorra descarga, CPU y disco) o quedan al final. La cola de jobs pasa de
     FIFO a ordenada por score cuando hay varios VODs pendientes, sin dejar
     morir de hambre a los viejos (envejecimiento).
6B.4 Auto-mejora del SEO antes de descartar: si el SEO queda entre 70 y 89,
     el sistema corre seo_score.retry_best (máx. 3 reintentos, cambiando
     solo el bloque más débil) con el LLM como make_variant. Si llega a >= 90
     se publica; si no, se retiene con motivo. Sin LLM: se retiene.
6B.5 La auto-aprobación de 6.3 usa estos puntajes como política:
     publish_status(virality, seo) decide publicar / retener / descartar.
     Cualquier "override" automático está PROHIBIDO; los overrides solo los
     registra el usuario desde el panel.
6B.6 Panel: por clip y plataforma mostrar viralidad, SEO, prioridad, desglose
     de la fórmula, motivo de retención y posición en la cola.
6B.7 Aprendizaje: conectar el flujo de métricas (9.1) a priority.recalibrate.
     Se mantiene el comportamiento actual: pesos fijos 0,5/0,3/0,2 y marca
     BETA hasta 30 clips con datos a 7 días; después ajusta solo exponentes,
     máximo ±25% por ciclo, con registro de fecha y motivo. Nunca toca los
     filtros duros.
6B.8 Tests: (a) con 2 clips, el de mayor prioridad se publica primero aunque
     llegó después; (b) viralidad 49 no se publica; (c) SEO 89 sin override
     no se publica; (d) SEO por plataforma distinto produce orden distinto
     en TikTok y YouTube; (e) dos clips del mismo momento no se publican el
     mismo día en la misma plataforma; (f) recalibrate no pasa de ±25%.
6B.9 Los ejemplos de referencia de priority.py (Coker ~86, IlloJuan ~61 queda
     atrás) deben seguir dando los mismos resultados: agrégalos como tests de
     regresión.

## FASE 7 — Calidad de los clips
7.1 Selección con LLM (opcional, con fallback): entrada = transcripción por
    ventana + 15-20 candidatos del chat; salida JSON validada
    [{start, end, hook, por_que_viral, score}]; cortes alineados a
    frase/silencio con precise_cut.py. El score del LLM es una señal más
    del score de viralidad existente, no lo reemplaza.
7.2 Audio como señal del score (picos de energía, risas/gritos), peso
    configurable y mayor en Kick.
7.3 Dedup: sin clips solapados del mismo VOD ni el mismo momento de dos VODs
    (hash perceptual de 3 frames + ventana temporal).
7.4 Facecam detectada UNA vez por VOD y guardada en runs/<vod>/layout.json;
    `facecam_box` de config manda siempre.
7.5 Metadata única por clip y plataforma: título, descripción, hashtags y
    hook desde la transcripción real; longitudes validadas; sin títulos
    repetidos; juego y streamer salen de config y metadatos del VOD, nunca
    hardcodeados; crédito al streamer en la descripción (configurable).
    Debe poder alcanzar SEO >= 90 por plataforma (ver 6B.4).

## FASE 9 — Métricas y directos
(La numeración salta la 8 a propósito: no la busques.)
9.1 Métricas a 24 y 48 h por clip (donde la API lo permita) en SQLite y un
    reporte simple en el panel; alimentan priority.recalibrate (6B.7).
9.2 (Solo si las fases 1-7 y 6 bis están verdes) Twitch en vivo con IRC y
    grabación por segmentos de 5-10 min; Kick por websocket de chat;
    cooldown y tope de clips por directo y por streamer.

## Pruebas y calidad (todas las fases)
- `pytest -q` completo en verde (incluidos test_queue y test_watcher).
- `ruff check .` limpio en lo que toques.
- Test de humo end-to-end sin red: video sintético de 10 s
  (`ffmpeg -f lavfi -i testsrc`) + chat sintético => detect -> render ->
  manifiesto -> publicación simulada, pasando por los filtros de puntaje.
- GitHub Actions: pytest + ruff en cada push.

## Formato de entrega al terminar cada fase
1. Archivos cambiados.
2. Qué resuelve cada cambio (referencia al punto numerado).
3. Salida resumida de pytest y ruff.
4. Decisiones que tomaste y por qué.
5. Qué NO hiciste y por qué.
6. Pasos que SOLO el usuario puede hacer (cuentas, aprobaciones, permisos).

Empieza por la sección 0 (tabla HECHO / PARCIAL / PENDIENTE) y luego sigue
desde la primera fase incompleta.

## Reglas del usuario 2026-11-18 (añadido; manda sobre lo anterior si contradice)

Fuente canónica: `docs/reglas-usuario.md` (tabla completa con
archivo y test por regla). Resumen:

- Clips **40–60 s** (nunca < 40 ni > 60, nunca 30 exacto, 25–35
  prohibido). El clip p2010 (38 s, viralidad 44,5) NO tiene override.
- **Rubius prioritario, IlloJuan PAUSADO** (no publicar, no encolar
  clip_02). Clip p3220 (48 s, viralidad 53,8, SEO 92/94/94/94) a la
  Cola web, sin publicar en público.
- **Hype por ráfaga v2**: velocidad msgs/10 s y /60 s contra baseline
  rodante (mediana 5 min); risas, emotes hype y repetición por
  usuarios distintos dentro de la ráfaga.
- **Categoría GTA VI obligatoria** (Helix/Kick por API oficial +
  capítulos del VOD); el gate de publicación la exige.
- **SIN franja horaria**: se publica a cualquier hora; los topes
  diarios se respetan igual.
- **Layout RDJavi personalizado GTA 6 / Vice City** (default para
  GTA 6; DJ Mariio aparcado): streamer ARRIBA, gameplay ABAJO; fondo
  desenfocado 0–730, webcam centrada 580×590 en (250,190), gameplay
  730–1770, banda inferior 1770–1920 con barra atardecer
  rosa/violeta/naranja + handle @ELCLIPDEGTA6 | GTA VI. Subtítulos
  estilo RDJavi total (una línea, 32 px, MarginV 230, mínimo 200;
  la regla vieja de 680 queda reemplazada). Miniaturas estilo GTA 6.
- **Configuración desde la web**: Ajustes del panel (D1 por tenant,
  leídos por Worker/brain); nada de editar código para cambiar
  layout, subtítulos, duración, viralidad/SEO, topes, categoría,
  miniatura, hype o el loop.
- **REGLA DE ORO — automatizado 100%**: Iniciar en el panel arranca
  el loop (Cerebro + Worker cron */5) y el usuario puede
  desloguearse; nada depende de su sesión, navegador ni teléfono.
- **Topes como REGLAS** (`limits.yaml`): YT plan 3/máx 5, resto plan
  2/máx 3, nuevas 1/día, Zernio 15/día + 5 borradores, 2 h entre
  publicaciones; escalado automático nueva → establecida (30 días
  desde la 1ª publicación + 10 publicaciones sin sanciones).
- **Puerta de render PRE-CORTE**: el clip SOLO se corta/renderiza si
  ya pasa las puertas con el paquete provisional (viralidad estimada
  >= 45 pre-corte — calibrado: p3220 estima 46,3 vs real 53,8;
  p2010 44,5 — y SEO >= 90 en al menos una plataforma). Si no pasa,
  se descarta SIN descargar el segmento ni renderizar. El 50 REAL
  sigue vigente en la puerta de publicación.
- Revisión automática de los mejores picos (APROBADO 2026-11-18): en el
  loop en vivo, los top 3 picos por directo con viralidad auto 40-49,9
  pasan por revisión automática (3 fotogramas + transcripción Whisper +
  juez Gemini con los 5 componentes humanos) y se re-calcula su
  viralidad; si tras la revisión pasan >= 50 y SEO >= 90, se renderizan
  y se encolan en LIVE; si no, se descartan SIN render. >= 50 directo a
  render; < 40 se descarta sin revisión. Si Gemini no está disponible,
  fallback a la puerta pre-corte (>= 45), registrado como tal. La puerta
  de publicación sigue en 50, NO se baja.
