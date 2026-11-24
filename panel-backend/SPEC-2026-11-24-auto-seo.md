# SPEC — SEO automático al cortar + revalidación de pendientes (2026-11-24)

Elefante (spec) + Pez dorado (revisión) según EGM. Orden del usuario
2026-11-24 00:55 ("empieza a programar lo que estabas haciendo tú"):
tras la revisión del pez dorado se implementa DIRECTO, sin otra ronda
de aprobación. El alcance es automatizar lo que el coordinador hacía a
mano con los clips: puntuar SEO al cortar, validar, y dejarlos
encolables/publicables solos por el ciclo.

Revisión Goldfish 2026-11-24 01:04: **NO APRUEBA la v1** (dos
bloqueantes). Esta v2 resuelve los 13 hallazgos punto por punto (ver
"Resolución Goldfish" al final). Cambios principales respecto a v1:
el SEO ya NO viaja en el POST (puerta SEO del POST, hallazgo 1) sino
en un PATCH posterior; el backfill reproduce el score exacto con
`seo_inputs` guardados (hallazgo 2); y se añade un cambio pequeño y
aditivo en el Worker (passthrough de `seo_inputs`/`seo_source`).

## 1. Hechos verificados (cada uno comprobado en archivos o en D1 antes de escribirlo)

### 1.1 El SEO se calcula al cortar, pero se tira en la puerta del panel

- `clipagent/workers.py:618` (`package_clip_step`): puntúa el paquete
  con `score_all_platforms` (línea ~640) y escribe
  `package["seo"] = {p: seo[p]["score"] for p in PLATFORMS}` más
  `seo_detail`; devuelve `{"package_path", "seo", ...}`.
- `clipagent/live_cutter.py:1040-1075` (paso 7 de `process_peak`,
  paquete) y paso 8 (~1075): `package = finalize_package(...)` y
  `seo = package.get("seo") or {}`; el record lleva `"seo": seo`
  (línea ~1125).
- `clipagent/live_cutter.py:1297` (`_enqueue_panel`): el payload a
  `POST /api/clips` lleva `seo_scores` **SOLO si el clip es rescate
  por gritos** (líneas 1376-1385):
  ```python
  if _is_rescue:
      payload["rescue"] = True
      payload["rescue_shouts"] = int(_shouts or 0)
      payload["viral_score"] = ...
      payload["seo_scores"] = record.get("seo") or {}
  ```
  El comentario lo dice explícito: "En clips normales el payload no
  cambia (la puerta SEO del POST no debe empezar a tirar clips que
  antes entraban a la Cola y se frenaban al publicar)". **Los clips
  normales NUNCA mandan `seo_scores`.**
- `clipagent/vod_fallback.py:598` (`burn_and_enqueue`): el payload a
  `POST /api/clips` (~733-775) NO lleva `seo_scores` ni `viral`
  (verificado por grep: cero ocurrencias en el archivo). El `seo` del
  manifiesto (`clip.get("seo_scores")`, línea 660) solo se usa para
  el texto del `reason` y para `panel_score`. Tampoco viaja el
  veredicto de calidad: solo se llama `preview_seen` si la decisión
  es "aprobado" (líneas ~769-771).
- `clipagent/review.py:254`: la revisión pre-corte también calcula
  `result["seo"]` vía `pre_corte_package` — el SEO existe en tres
  sitios del cerebro antes de cortar; en ninguno sale hacia el panel
  en el camino normal.

### 1.2 La puerta SEO del POST (hallazgo Goldfish 1 — lo que la v1 ignoró)

- `panel-backend/worker/src/index.ts:1388-1394` (`parseClipInput`):
  si el POST trae `seo_scores` y el **máximo** es `< min_seo` (80),
  el clip **no entra en la Cola** salvo `seo_override: true`. Sin
  `seo_scores` no se rechaza por SEO. El comentario del propio Worker
  lo dice: "Sin seo_scores (clip heredado sin medir) NO se rechaza
  por SEO: la puerta de viralidad de arriba decide, y el SEO se mide
  luego (PATCH) o lo frena el gate de publicación."
- El rescate SÍ necesita el SEO en el POST:
  `index.ts:1333-1344`: un rescate solo es válido con
  `rescueSeoMax >= eff.min_seo` (además de viralidad revisada
  45–79 y 2+ s de gritos). Por eso el rescate manda `seo_scores` en
  el POST hoy — y por eso la puerta no le afecta (un rescate con
  máximo SEO < 80 es inválido por definición).
- Consecuencia: mandar `seo_scores` en el POST de un clip NORMAL con
  máximo < 80 lo rechazaría con 400 y el clip se perdería
  (`_enqueue_panel` captura la excepción, lo mete en `skipped` y
  devuelve `None`, `live_cutter.py:1447-1456`). Hoy ese mismo clip
  entra a la Cola como borrador: el paso 9 solo *reporta* el
  descarte y acto seguido llama a `_enqueue_panel` igualmente
  (`live_cutter.py:1260-1290`).
- **El PATCH no tiene puerta SEO**: `buildPatchColumns`
  (`index.ts:1183-1190`) solo parsea y fusiona vía `mergeViralJson`.
  El camino correcto es POST sin SEO + PATCH con SEO (el patrón que
  el propio Worker anticipa en su comentario).

### 1.3 El scoring SEO es local: $0, sin Gemini, sin red

- `clipagent/seo_score.py`, docstring del módulo: "Sin dependencias
  externas, sin credenciales, sin red. Todo local." `score_all_platforms`
  es reglas puras sobre título/descripción/tags/miniatura/
  coherencia. Gemini solo GENERA el texto del paquete
  (`metadata.py:1578` `_gemini_first_package`); la MEDICIÓN del paquete
  ya hecho no llama a Gemini. Puntuar al cortar no añade llamadas,
  coste ni latencia de red: son milisegundos de CPU.
- `clipagent/metadata.py:1943` (`score_package_seo`): mapea el paquete
  a `score_all_platforms`; `metadata.py:1936` guarda `gemini_seo` solo
  cuando hubo paquete Gemini.
- Entradas exactas del scorer (`score_package_seo`, `metadata.py:1943-
  1990`): `best_title`, `description_first_2_lines`, `tags`,
  `thumbnail_text`, `hashtags` (de `pkg.platforms.youtube_shorts` o
  `pkg.hashtags`), `game_verified` (deducido: juego conocido y
  distinto de "desconocido"), `visible` (del paquete o `None`),
  `lang`, `keyword` ("GTA 6"). Los hashtags del paquete son los del
  juego (`metadata.py:1690`, `hashtags, tags = game_hashtags_tags(game)`;
  para GTA 6: `["#GTA6", "#GTA6Clips", "#GTA", "#RockstarGames"]`,
  `metadata.py:240-242`).

### 1.4 El Worker acepta, guarda y lee seo_scores (contrato ya testeado)

- `panel-backend/worker/src/db.ts:247-260` (`parseSeoScoresInput`:
  objeto {plataforma: 0–100}); `db.ts:324` (`parseViralInput`) acepta
  `body.seo_scores` y `body.viral.seo_scores`; `index.ts:1183-1186` lo
  fusiona en `clips.viral` en POST/PATCH.
- `panel-backend/worker/src/db.ts:419-460` (`mergeViralJson`): fusiona
  por campo sobre el JSON guardado (`seo_scores` por plataforma,
  `quality_gate` reemplaza al anterior, resto se preserva). El PATCH
  lee la fila actual del clip y fusiona sobre ella (el `clip` que
  recibe `buildPatchColumns` es la fila leída por el handler).
- `parseViralInput` construye la salida campo a campo: las claves
  desconocidas dentro de `viral` se DESCARTAN (no hay passthrough).
  Por eso `seo_inputs`/`seo_source` (§3.1, A5) exigen añadirlos
  explícitos al `ViralData` y al parser — no basta con mandarlos.
- `panel-backend/worker/src/gate.ts:362-368` (`seoForPlatform`): lee
  `viral.platforms[platform].score` (legacy) o
  `viral.seo_scores[platform]` (contrato IA 2026-11-18).
- `panel-backend/worker/src/gate.ts:248`: el gate bloquea SOLO si el
  SEO está medido Y es < `min_seo` (80).
  `gate.ts:50`: `seoScore: number | null; // null = sin medir
  (no bloquea por SEO, sí por resto)`.
- Test de contrato ya existente:
  `panel-backend/worker/test/clips-scoring.test.ts:193-214` (POST 201
  y GET devuelven `seo_scores`; 217+ rechaza fuera de 0–100 con 400).

### 1.5 El encolado automático existe, pero no llega a los clips nuevos

- `panel-backend/worker/src/jobs.ts:960-1064` (`autoEnqueueTopClips`):
  detrás del ajuste `auto_publish_top` (default `false`,
  `db.ts:494`); toma la Cola ordenada, máx. 3 clips por ciclo y 10
  evaluados, y pasa EXACTAMENTE el mismo `publishGate` que el botón
  Publicar (vía `tryEnqueueClipPublish`). Cada freno queda registrado
  con el motivo literal en `clip_events` + `worker_events`.
- El cerebro NO llama a publicar: `live_cutter.py:1462-1510`
  (`_enqueue_publish`): por defecto (`auto_publish=False`,
  `live_cutter.py:555,573`) el clip queda en la Cola como borrador;
  `quality_gate.py:272` (`publish_mode_for_quality`): "api" solo con
  veredicto que pasa Y `auto_publish` encendido. El workflow
  `.github/workflows/cerebro-live.yml:275` corre
  `python -m clipagent live ... --cut --minutes 10` SIN
  `--auto-publish` (el flag existe, `cli.py:93`, pero nadie lo pasa).
  **Vía única de encolado: el `autoEnqueueTopClips` del Worker.**
  El cerebro no debe llamar a `publish_clip` (evita carreras de
  doble encolado con el ciclo).
- Puerta de calidad B4 (`quality-gate.ts:150-180`,
  `qualityGateForClip`): si existe veredicto, `preview_seen` es
  IRRELEVANTE — `aprobado` pasa, `pendiente_revision`/`rechazado`
  bloquean con independencia de `preview_seen`. `preview_seen=1`
  solo sustituye a un veredicto AUSENTE. Y `gate.ts:235`: el
  `require_preview` solo frena si el ajuste está activado (en ambos
  tenants está en `false`).

### 1.6 Estado vivo en D1 (verificado 2026-11-24 00:34-01:10, Chile)

Ajustes:

| tenant | `auto_publish_top` | `require_preview` |
|---|---|---|
| sebastian | `true` | `false` |
| kaez | **`false`** | `false` |

Clips de seba 177-185 (D1 `clips-avc`):

| clip | origen | `quality_gate` | `preview_seen` | `seo_scores` |
|---|---|---|---|---|
| 177, 178 | vod | ninguno | 0 | no |
| 179 (Noni) | vod | aprobado | 0 | **sí** (a mano) |
| 180, 181, 182 | vod | ninguno | 0 | no |
| 183 | live | rechazado | 0 | no |
| 184 | live | pendiente_revision | 0 | no |
| 185 | live | aprobado | 1 | no |

Lectura (corregida tras Goldfish): el 179 (aprobado) **también** pasa
la puerta de calidad aunque tenga `preview_seen=0` — el veredicto
manda. El 184 (`pendiente_revision`) seguiría bloqueado aunque
tuviera `preview_seen=1`. Los VOD 180-182 no tienen veredicto en D1
(el camino VOD no lo manda, §1.1) ni `preview_seen`, así que el gate
los frena ("sin veredicto"). El 185 (aprobado + `preview_seen=1`) es
encolable; su SEO es `null` (no bloquea, §1.4), así que publicaría
con SEO sin medir — la regla del usuario (SEO >= 80, orden
2026-11-19) quedaría sin verificar en ese clip.

Cola de kaez (D1 `clips-avc-kaez`, verificado 2026-11-24 01:10): en
`en_carpeta` sin ningún job publish vigente: clips 1, 2, 3, 5, 6, 7,
9, 10, 11 (9 y 10 con `preview_seen=1`, 11 en inglés). Solo los clips
4 y 8 tienen jobs (todos `posted`).

### 1.7 No existe re-validación automática (pero sí el batch)

- `clipagent/validate_all.py` (`--from-panel`) valida TODOS los clips
  del panel y clasifica acciones (`reintentar` para pendiente,
  `re-render`, `avisar-humano`, `nada`). Verificado por grep: el
  archivo NO contiene `update_clip`, `preview_seen` ni `PATCH` — solo
  escribe un JSON de trabajo local (`validate_all.py:522-526,610`).
- El workflow `.github/workflows/validate-all.yml:120` SÍ ejecuta
  `validate_all` (disparadores `repository_dispatch` /
  `workflow_dispatch`, sin cron, sin escritura de vuelta; sube el JSON
  como artefacto). Lo que NO existe es un loop que reintente: ningún
  cron lo ejecuta y nada aplica sus veredictos a D1.
- `clipagent/panel_bridge.py:409` (`update_clip`, PATCH
  `/api/clips/:id`) acepta `viral` y `seo_scores` (Worker,
  `index.ts:1186`); `panel_bridge.py:421` (`preview_seen`, POST
  `/api/clips/:id/preview-seen`); `panel_bridge.py:373`
  (`download_video`, `GET /api/clips/:id/video`). Las piezas para
  escribir de vuelta existen; nadie las llama en un loop.
- El workflow del loop NO puede tener cron propio en GitHub: un run
  por cron no tendría token del cerebro para autenticarse (el
  bootstrap `panel_credentials` canjea el token de un solo uso del
  dispatch). El disparo lo hace el Worker: `dispatchCerebro`
  (`index.ts:467`) emite el token y dispara `panel-revalidate`, con
  la cadencia marcada en settings (`last_revalidate_at_ms`) y la
  puerta `revalidateDue` evaluada por `cycleGate` con solo lecturas
  (mismo patrón que `panel-live` y `panel-daily`).

### 1.8 Trabajo manual en curso (contexto, no duplicar)

Un subagente del coordinador está puntuando SEO y validando a mano los
clips 180-185 AHORA (lanzado 2026-11-24 00:35). Escribe los mismos
campos que esta spec (`seo_scores`, `quality_gate`, `preview_seen`).
El loop de §3.2 NO se estrena hasta que ese trabajo termine: en el
deploy se escribe la marca inicial `last_revalidate_at_ms = <ahora>`
en ambas D1, así que el primer dispatch automático cae 6 h después
del deploy (y las reglas disjuntas de §3.2 paso 1 hacen que, aunque
coincidieran, cada escritor solo escriba lo que el otro no escribió).

## 2. El problema exacto

Cuatro huecos encadenados entre el corte y la publicación; cada uno
solo pedía una mano, pero juntos obligaban a hacer TODO a mano:

1. **SEO calculado pero no enviado** (§1.1): el cerebro mide el SEO de
   cada clip al cortar y lo guarda en el `record`, pero el payload a
   `POST /api/clips` solo lo incluye en rescates por gritos (live) o
   nunca (VOD). Los clips llegan a D1 sin `viral.seo_scores`, así que
   la regla SEO >= 80 no se puede verificar sola: o se puntúa a mano,
   o el clip publica con SEO sin medir (el gate no bloquea el `null`,
   §1.4). Y NO basta con meterlo en el POST: la puerta SEO del POST
   (§1.2) rechazaría los clips con máximo < 80, que hoy entran como
   borrador.
2. **El cerebro no encola** (§1.5): `auto_publish` apagado en el
   workflow y `publish_mode_for_quality` a "draft". Los clips quedan
   en la Cola sin jobs.
3. **Kaez no auto-encola** (§1.6): `auto_publish_top=false` en su D1.
   Aunque un clip de kaez llegue perfecto (SEO + veredicto + preview),
   su ciclo nunca lo encola. Seba lo tiene en `true` desde antes.
4. **Sin re-validación ni backfill** (§1.7): un clip en
   `pendiente_revision` (Gemini caído al validar) o sin veredicto
   (VOD, que no manda el gate) o sin SEO (scorer fallido) se queda
   bloqueado para siempre hasta que un humano actúe. No hay loop que
   lo reintente ni que rellene el SEO que faltó.

## 3. Propuesta

Tres frentes. Ninguno toca el validador (Fix 3, otro chat, spec
`SPEC-2026-11-24-fix3-una-palabra.md`), ni el gate, ni caps, ni slots,
ni crons, ni `avc-sebastian`.

### 3.1 Frente A — mandar el SEO (y el veredicto VOD) al cortar

Regla general (por la puerta del POST, §1.2): **el POST crea el clip
sin SEO; el SEO viaja en un PATCH inmediatamente posterior** (el
patrón que el propio Worker anticipa: "el SEO se mide luego (PATCH)").
El PATCH no tiene puerta SEO. Excepción: el rescate, que necesita el
SEO en el POST para que el Worker verifique la marca (§1.2).

**A1. Live** (`clipagent/live_cutter.py`, `_enqueue_panel`, ~1376-1385):
- Rescate (sin cambio): el payload lleva `seo_scores` en el POST,
  como hoy (la verificación del rescate lo exige).
- Normal: el POST NO lleva `seo_scores`. Tras `create_clip` con éxito
  (y antes o después de `upload_video`, da igual: el PATCH es
  independiente), llamar a `bridge.update_clip(panel_id,
  {"seo_scores": record["seo"], "viral": {"seo_inputs": ...,
  "seo_source": "corte"}})` (ver A5). Si `record["seo"]` está vacío,
  no se hace el PATCH de SEO (el loop de §3.2 lo rellena después).
  Si el PATCH falla, se registra el motivo y se sigue: el clip ya
  está en la Cola (nunca se pierde por el SEO).
- La ventana POST→PATCH dura segundos; aunque el ciclo auto-encolara
  el clip en esa ventana, el gate vería SEO `null` (no bloquea) y el
  job se ejecutaría en su slot, horas después, con el SEO ya puesto.
  Riesgo residual despreciable, documentado en §7.

**A2. VOD** (`clipagent/vod_fallback.py`, `burn_and_enqueue`, ~733):
igual que A1 normal: el payload NO lleva `seo_scores`; tras
`create_clip`, un PATCH con `seo_scores` mapeados desde el manifiesto
(solo plataformas con número medido — nunca 0 por una sin medir, que
es un bloque medido, no "sin medir"). El manifiesto trae las entradas
como mapas `{score}` (o, por defensa, como número plano); el bucle
explícito es la forma correcta (`_seo_score_value` devuelve 0 cuando
falta el número y NO distingue "sin medir" de 0, así que no sirve
para decidir la inclusión):
```python
scores = {}
for p in PLATFORMS:
    entry = (seo or {}).get(p)
    if isinstance(entry, (int, float)):
        value = entry
    elif isinstance(entry, dict):
        value = entry.get("score")
    else:
        value = None
    if isinstance(value, (int, float)):
        scores[p] = value
```
(El camino `run()` ya descarta antes de quemar si falta SEO o el
mínimo < 80, `vod_fallback.py:240-262`; el caso parcial solo existe
por llamada directa a `burn_and_enqueue`, y A2 lo maneja como
defensa.)

**A3. Veredicto VOD a D1** (mismo `burn_and_enqueue`): construir el
veredicto con `build_quality_gate(final_review, thumbnail_text=...,
no_speech=...)` — la misma llamada que el paso 8c del live
(`live_cutter.py:1217`) — **DESPUÉS** de `_rerender_vod_clip` (el
`final_review` puede ser reemplazado por el re-render) y mandarlo en
el mismo PATCH de A2 como `viral: {quality_gate: quality_gate}`.
El camino live YA manda el veredicto en el POST
(`live_cutter.py:1390-1392`) y no se toca. Un `rechazado` nunca llega
al payload: el camino VOD retorna antes, igual que el live (paso 9).
El `preview_seen` en aprobado se mantiene tal cual (no se quita).

**A4. Sin cambios en la puerta SEO del POST ni en el gate.** El
contrato de almacenamiento ya existe y está testeado (§1.4).

**A5. `seo_inputs` + `seo_source` (único cambio en el Worker, aditivo).**
Para que el backfill (§3.2) reproduzca el score exacto del corte, el
cerebro manda en el PATCH (A1/A2) `viral.seo_inputs = {hashtags,
visible, game_verified, keyword, lang}` — las entradas exactas de
`score_all_platforms` (§1.3) — y `viral.seo_source = "corte"`.
El Worker hoy DESCARTA las claves desconocidas de `viral`
(`parseViralInput` construye la salida campo a campo, §1.4), así que
hay que añadirlas: `ViralData.seo_inputs?` y `ViralData.seo_source?`
en `db.ts`, validación en `parseViralInput` (seo_inputs: objeto con
`hashtags` lista de strings, `visible` string|null,
`game_verified` booleano, `keyword` string, `lang` string;
seo_source: uno de `"corte" | "backfill_exacto" |
"backfill_aproximado"`), y passthrough en `mergeViralJson`
(`if (patch.seo_inputs !== undefined) next.seo_inputs =
patch.seo_inputs;` y lo mismo para `seo_source`). Sin ningún otro
cambio de comportamiento: el gate no los lee, el panel no los
muestra. Test en `clips-scoring.test.ts` (round-trip POST/PATCH).

### 3.2 Frente B — loop de revalidación + backfill de SEO

Nuevo módulo `clipagent/revalidate_pending.py` + workflow
`.github/workflows/cerebro-revalidate.yml` (disparadores:
`repository_dispatch` tipo `panel-revalidate` +
`workflow_dispatch`). El disparo lo hace el Worker, no un cron de
GitHub (§1.7): `maybeDispatchRevalidate` (`index.ts`, espejo de
`maybeDispatchDaily`) dispara `panel-revalidate` cuando toca (cada
6 h, marca `last_revalidate_at_ms` en settings) y la puerta
`revalidateDue` se evalúa en `cycleGate` con solo lecturas. Un
`workflow_dispatch` manual sin token falla en el bootstrap con
mensaje claro (igual que validate-all): la vía manual es el
dispatch del panel. Reutiliza, sin duplicar: `validate_final_clip`
+ `build_quality_gate` (validación), `score_all_platforms` (SEO),
`transcribe_clip_to_srt` (de validate_all, para el SRT),
`PanelBridge.list_clips / get_clip / update_clip / download_video /
list_clip_events / tracking_step / report_event` (panel). El workflow
usa el mismo bootstrap de `panel_credentials` que el live cutter, y
ese paso es OBLIGATORIO: exporta `GEMINI_API_KEY`
(`panel_credentials.py:130-132`); sin él, cada clip quedaría
`pendiente_revision` y el loop quemaría descargas sin efecto.
`validate_final_clip` se importa a nivel de módulo (o se recibe
inyectada) para que los tests puedan hacer stub (seam fijado).

Por corrida (tope: 10 clips, los más antiguos primero):
1. Lista la Cola del panel y elige candidatos: clips con
   `quality_gate` en (`None`, `pendiente_revision`) O sin
   `seo_scores` en alguna plataforma. Los `rechazado` NO se tocan.
   Además, un clip con `seo_scores` ya puesto NO se re-puntúa, y un
   clip con veredicto ya puesto NO se re-valida: cada escritor solo
   escribe lo que el otro no escribió (guarda de no-solapamiento con
   el trabajo manual, §1.8).
2. Anti-atasco: antes de procesar un clip, escribe un evento de
   tracking `revalidado` (vía `bridge.tracking_step`, el mismo canal
   que usa el live cutter) y SALTA los candidatos con un evento
   `revalidado` en las últimas 24 h. Sin esto, con Gemini caído
   varios días, los mismos 10 más antiguos se reprocesarían en cada
   corrida y el clip 11+ no se tocaría nunca.
3. Si falta SEO: puntúa y hace `PATCH /api/clips/:id` con
   `seo_scores` + `seo_source`. Dos casos:
   - **Con `seo_inputs` guardados** (clips cortados tras A5):
     reproduce el score EXACTO con esas entradas;
     `seo_source = "backfill_exacto"`.
   - **Sin `seo_inputs`** (clips anteriores al cambio): aproximado
     con entradas documentadas — hashtags = los del juego
     (`game_hashtags_tags("GTA 6")[0]`, exactos para la parte del
     juego, §1.3), `visible = None` (coherencia 80, conservador),
     `game_verified = True` (los clips del cerebro pasaron el chequeo
     de juego GTA VI), `keyword = "GTA 6"`, `lang` de D1;
     `seo_source = "backfill_aproximado"`. Sesgo conocido: ~2-3
     puntos por debajo del exacto (solo por coherencia 80 vs 100);
     nunca por encima en el caso normal (gameplay visible). Límite
     declarado: un clip solo-cams quedaría sobre-puntuado por este
     camino — esos clips son raros y el camino exacto (A5) los cubre
     en adelante.
   En ambos casos, sin MP4 y sin Gemini.
4. Si falta veredicto o es `pendiente_revision`: descarga el MP4
   (`GET /api/clips/:id/video`), corre `validate_final_clip` +
   `build_quality_gate` y hace `PATCH` con
   `viral: {quality_gate: <veredicto>}`. Si el veredicto nuevo es
   `aprobado`, no se llama a `preview_seen`: el gate pasa por el
   veredicto solo (§1.5) y `require_preview` está en `false` en ambos
   tenants. Si sigue `pendiente_revision` (Gemini sigue caído), no
   se escribe nada: la próxima corrida lo reintenta (con el anti-
   atasco del paso 2). Si es `rechazado`, se escribe (bloqueo honesto;
   su re-render es trabajo aparte, §6).
5. Registra cada acción en `worker_events` (vía `bridge.report_event`,
   como el live cutter) con el motivo literal; nunca levanta por un
   clip (un clip que falla es un dato, se salta y se sigue — igual que
   `validate_all.py`).

**Orden con Fix 3 (obligatorio, YA CUMPLIDO):** el loop se estrena
DESPUÉS de que Fix 3 esté desplegado y verificado en `main`. Fix 3
está en main desde el commit `fda26b8` (2026-11-24 ~01:16, otro
chat), así que la precondición se cumple al desplegar este cambio.
La guarda de no-solapamiento con el trabajo manual es la marca
inicial `last_revalidate_at_ms` puesta en el deploy (§1.8): el
primer dispatch automático cae 6 h después. Si el loop corriera
con el validador pre-Fix 3, podría grabar `rechazado` por el falso
positivo de `subtitulos_una_palabra` y esos clips quedarían fuera
del alcance de Fix 3 (el loop nunca revisita `rechazado`).

Cadencia 6 h: las caídas de Gemini suelen ser cortas; reintentar cada
6 h recupera los pendientes dentro del día sin gastar llamadas de más.
El tope de 10 acota el coste (10 descargas + 10 llamadas Gemini por
corrida, como mucho).

### 3.3 Frente C — encender el auto-enqueue de kaez

`UPDATE settings SET data = json_set(data, '$.auto_publish_top', true)`
en la D1 `clips-avc-kaez` (vía `wr_kaez.py`, mismo camino que los
ajustes de YouTube del 2026-11-23), con lectura de vuelta que lo
confirme. Seba ya lo tiene en `true` y no se toca. Efecto esperado
(con datos reales, §1.6): los primeros ciclos evaluarán los clips
sin job publish vigente (1, 2, 3, 5, 6, 7, 9, 10, 11) y encolarán
hasta 3 por ciclo los que pasen el gate; los que frenen quedarán con
el motivo literal registrado (slot, caps, espaciamiento o calidad).
No se promete que un clip concreto salga en el primer ciclo.

### 3.4 Qué NO cambia (decisiones de diseño fijadas)

- `auto_publish` del cerebro sigue OFF en `cerebro-live.yml` (sin
  `--auto-publish`). Vía única de encolado: `autoEnqueueTopClips`
  del Worker. El cerebro nunca llama a `publish_clip`.
- El gate NO cambia: `null` SEO sigue sin bloquear (`gate.ts:248`).
  Con A1/A2 el `null` pasa a ser la excepción (solo cuando el scorer
  falló o el clip es anterior a este cambio), no la regla. El hueco
  residual (un clip que llega al auto-enqueue con SEO `null` puede
  publicar sin medir) queda documentado en §7.3 y como decisión
  abierta en §8.1.
- La puerta SEO del POST NO cambia: el rescate la necesita (§1.2).
  El camino normal la rodea con el PATCH, que es el patrón que el
  propio Worker documenta.
- El `preview_seen` automático en aprobado se mantiene en ambos
  caminos de corte (live y VOD). El loop no lo llama (redundante,
  §1.5).

## 4. Casos de regresión concretos (obligatorios)

Todos en el repo, antes de desplegar. Nombres de test orientativos;
el implementador los fija en los archivos que correspondan.

| # | Caso | Fuente | Resultado exigido |
|---|---|---|---|
| R1 | `_enqueue_panel` con clip normal (no rescate) | `test_live_cutter.py` (nuevo) | el POST a `POST /api/clips` NO lleva `seo_scores`; tras el POST se hace un PATCH con `seo_scores` == `record["seo"]` + `viral.seo_inputs` + `seo_source="corte"` |
| R2 | `_enqueue_panel` con `record["seo"]` vacío | mismo | ni POST ni PATCH llevan `seo_scores` |
| R3 | `_enqueue_panel` con rescate por gritos | mismo | el POST lleva `seo_scores` + `viral_score` + `rescue_shouts` (sin regresión); el PATCH no duplica el SEO |
| R4 | `burn_and_enqueue` VOD con SEO del manifiesto en 4 plataformas | `test_vod_fallback.py` (nuevo) | el POST no lleva `seo_scores`; el PATCH lleva los 4 números + `viral.quality_gate` con el veredicto construido DESPUÉS del re-render |
| R5 | `burn_and_enqueue` VOD con SEO solo en 2 plataformas | mismo (llamada directa, no vía `run()`: `run()` descarta antes de quemar si falta SEO) | el PATCH lleva solo esas 2; las otras NO van como 0 |
| R6 | `burn_and_enqueue` VOD con veredicto aprobado / rechazado | mismo | aprobado: se llama `preview_seen` (sin regresión). rechazado: retorna antes del payload, no se POSTea nada |
| R7 | revalidate: clip sin SEO, CON `seo_inputs` | `test_revalidate_pending.py` (nuevo) | el score reproducido == `score_all_platforms` con esas entradas (exacto); PATCH con los 4 scores + `seo_source="backfill_exacto"`; sin descarga de MP4 ni Gemini |
| R7b | revalidate: clip sin SEO, SIN `seo_inputs` (aproximado) | mismo | PATCH con scores + `seo_source="backfill_aproximado"`; en un fixture con gameplay visible, el aproximado <= exacto |
| R8 | revalidate: clip `pendiente_revision` → aprobado | mismo (stub de `validate_final_clip` a nivel de módulo) | PATCH con el veredicto nuevo; NO se llama a `preview_seen` |
| R9 | revalidate: clip `pendiente_revision` → pendiente otra vez | mismo | NO se escribe nada; queda para la próxima corrida (con el anti-atasco, no se reintenta antes de 24 h) |
| R10 | revalidate: clip `rechazado` | mismo | NO se toca (ni PATCH ni preview) |
| R11 | revalidate: tope 10 + anti-atasco | mismo | con 12 candidatos, solo se procesan 10; un candidato con evento `revalidado` de hace < 24 h se salta |
| R12 | Worker sin cambios de comportamiento | suite existente | `clips-scoring.test.ts` en verde (contrato SEO intacto); suite completa sin nuevos fallos; `tsc` limpio |
| R13 | Worker: passthrough `seo_inputs`/`seo_source` | `clips-scoring.test.ts` (nuevo) | POST con `viral.seo_inputs` + `seo_source` y GET los devuelve; PATCH con uno solo preserva el otro y el resto de `viral` |
| R14 | Puerta SEO del POST intacta | `clips-scoring.test.ts` (nuevo o existente) | POST normal con máximo SEO < 80 → 400 (sin regresión); POST sin SEO → entra (sin regresión); POST rescate con máximo < 80 → 400 |

- R1/R4 son rojos antes de verde (hoy el payload no lleva SEO en
  el POST normal / VOD; el PATCH no existe).
- R14 fija la puerta que la v1 ignoró: el cambio no la toca.

## 5. Criterio de éxito

- Tests: R1-R14 en verde. `ruff` en verde. Suite del cerebro sin
  nuevos fallos.
- Deploy: cerebro por push a `main` (GitHub Actions lo toma solo, como
  siempre); Worker con el cambio aditivo A5 en avc y avc-kaez
  (despliegue con la config CON cron en seba y `wrangler.kaez.toml`
  sin `[triggers]` en kaez — nunca `nocron`); `/api/health` ok en
  ambos tras el deploy y crons de franja verificados intactos (GET de
  schedules). Ajuste kaez aplicado con lectura de vuelta. El workflow
  `cerebro-revalidate.yml` se despliega solo con `workflow_dispatch`
  al principio (sin cron hasta §3.2).
- Verificación en vivo (obligatoria antes de darlo por hecho):
  1. El próximo clip cortado en vivo (seba o kaez) llega a D1 con
     `viral.seo_scores` en las 4 plataformas, `viral.seo_inputs` y
     `quality_gate` (verificado por lectura D1, no por el panel).
  2. El próximo clip VOD igual (SEO + veredicto en D1).
  3. `auto_publish_top=true` leído en la D1 de kaez; en el siguiente
     ciclo de kaez en franja, un clip listo se auto-encola (o queda
     registrado el motivo literal del gate si frena).
  4. El loop corre en seco local (`--dry-run` contra la D1 real, sin
     escribir) y lista sus candidatos con el SEO que rellenaría; el
     primer dispatch automático del Worker cae 6 h después del
     deploy (marca inicial) y deja su log en `worker_events`
     (`github_dispatch` "Dispatch panel-revalidate enviado") y en el
     run de GitHub (resumen JSON impreso por el módulo).
  5. Un clip que antes habría quedado bloqueado por
     `pendiente_revision` aparece con veredicto nuevo en D1 sin
     intervención manual.
- Si (1) o (2) no se cumplen en el primer run vivo tras el deploy, el
  trabajo NO está terminado: se revisa el log del run antes de
  declarar éxito.

## 6. Fuera de alcance

- No tocar el validador (`final_review.py`, `quality_gate.py`,
  `render_gate.py`, `policy.py`): el Fix 3 vive en otro chat y otra
  spec. Esta spec solo USA `validate_final_clip` y
  `build_quality_gate`, no los cambia. El loop se estrena después de
  Fix 3 (§3.2).
- No cambiar el gate del Worker (ni el `null` sin bloquear, ni
  `min_seo`, ni caps, ni slots, ni espaciamiento) ni la puerta SEO
  del POST (ni su umbral, ni el rescate).
- No automatizar el re-render de rechazados (`remake_all.py`): el loop
  los clasifica y los deja; el re-render programado queda pendiente
  explícito (§8.2).
- No encender `auto_publish` del cerebro en `cerebro-live.yml`.
- No backfill histórico masivo: el loop rellena lo que encuentra en
  la Cola; los clips 177/178/180-184 concretos los está puntuando a
  mano el subagente en curso (§1.8).
- No tocar `avc-sebastian` (ciclo parado a propósito, otros canales).
- No tocar Content Rewards, la extensión, ni el engagement.

## 7. Riesgos

1. **El gate ahora SÍ frena por SEO.** Hoy un clip sin SEO pasa el
   gate (`null` no bloquea); con A1/A2 el SEO medido < 80 en una
   plataforma frena esa plataforma (`gate.ts:248`). Es la regla del
   usuario funcionando como se pidió, pero el volumen auto-publicable
   puede bajar si los paquetes del cerebro puntúan < 80: el paso 9 del
   live cutter ya descarta esos clips del `publish_status`, así que el
   Worker solo refleja lo que el cerebro ya midió. Si el volumen cae,
   el arreglo es mejorar el paquete, no quitar el SEO.
2. **VOD con SEO parcial.** El manifiesto VOD puede traer SEO en 2
   plataformas y nada en las otras: esas plataformas quedan "sin
   medir" y el gate no las frena por SEO. El loop de §3.2 las rellena
   (con `seo_inputs` si el clip es posterior a A5, aproximado si no).
   Riesgo aceptado: parcial hoy, completo tras el primer loop.
3. **Hueco residual: `null` SEO publica.** Un clip que llega al
   auto-enqueue antes de que el loop lo rellene (o cuyo scorer falló)
   sigue publicando con SEO sin medir, porque el gate no bloquea el
   `null`. Mitigación: el scorer es local y casi no falla; el loop
   corre cada 6 h; la ventana POST→PATCH dura segundos (y el job se
   ejecuta en su slot, horas después). Cierre total: hacer que el
   `null` bloquee (decisión del usuario, §8.1, fuera de esta
   implementación).
4. **Kaez drena Cola vieja.** Al encender `auto_publish_top`, sus
   primeros ciclos evaluarán los clips 1, 2, 3, 5, 6, 7, 9, 10, 11
   (§1.6) y encolarán hasta 3 por ciclo los que pasen el gate. Es lo
   buscado, pero el usuario debe saberlo: los primeros días de kaez
   pueden publicar clips antiguos además de los nuevos.
5. **Coste del loop.** Cada clip re-validado descarga un MP4 y gasta
   una llamada Gemini de revisión. Tope 10/corrida, cada 6 h: como
   mucho 40 llamadas/día, solo mientras haya pendientes. El
   anti-atasco (§3.2 paso 2) evita quemarlas siempre en los mismos
   10 si Gemini cae varios días.
6. **Orden con Fix 3.** Si el loop corriera con el validador pre-Fix 3,
   podría grabar `rechazado` por el falso positivo de
   `subtitulos_una_palabra` y esos clips quedarían fuera del alcance
   de Fix 3 (el loop nunca revisita `rechazado`). Mitigación: orden
   de estreno (§3.2) — loop después de Fix 3, primera corrida manual.
7. **Carrera loop vs ciclo del Worker.** `tryEnqueueClipPublish`
   evalúa el gate sobre el snapshot de `listQueueClips` (sin relectura
   fresca): un PATCH del loop a mitad de ciclo es invisible hasta el
   ciclo siguiente (retraso de un ciclo, benigno). El sentido
   peligroso (ciclo que encola un clip mientras el loop lo valida, y
   el loop escribe `rechazado` después) queda contenido en ejecución:
   `recheckPublishGate` relee el clip (`gate.ts:402-405`) y el dispatch
   lo invoca antes del adaptador (`publish/index.ts:202`): el
   resultado serían jobs fallidos con motivo, no una publicación
   indebida.
8. **Carrera con el trabajo manual (§1.8).** El PATCH es
   read-modify-write sobre `viral`, pero `mergeViralJson` fusiona por
   campo sobre la fila que lee el handler, y los dos escritores son
   disjuntos por construcción (§3.2 paso 1: el loop no re-escribe lo
   que ya está puesto). Además la marca inicial
   `last_revalidate_at_ms` retrasa el primer dispatch 6 h (el
   trabajo manual termina antes). Riesgo residual: dos PATCHes al
   mismo clip en la misma ventana de milisegundos; despreciable con
   esas dos guardas.
9. **Aproximado sobre-puntúa un solo-cams.** El backfill aproximado
   usa `visible=None` (coherencia 80); un clip solo-cams real
   puntuaría 20 + bloqueo con el scorer exacto. Los clips del cerebro
   salen de footage de gameplay (el chequeo de juego GTA VI es
   puerta de corte), así que el caso es raro; queda como límite
   declarado y el camino exacto (A5) lo cubre en adelante.

## 8. Decisiones

Cubiertas por la orden del usuario 2026-11-24 00:55 ("empieza a
programar lo que estabas haciendo tú"): implementar A1-A5, B y C sin
otra ronda de aprobación. Quedan abiertas (no bloquean esta
implementación):

1. **¿El `null` SEO debe bloquear en el gate?** Cerrar el hueco de
   §7.3 exigiendo SEO medido para auto-encolar (cambio en
   `gate.ts:248`). Fuera de esta implementación; si se decide, es
   otra spec (cambia el comportamiento de los clips creados a mano
   en el panel, que hoy publican sin SEO).
2. **Re-render automático de rechazados.** El loop los deja
   clasificados; `remake_all.py` existe pero nadie lo programa.
   ¿Se programa un workflow de re-render, o sigue a mano?
3. **Cadencia del loop (6 h) y tope (10/corrida).** Propuestos en
   §3.2; el usuario puede cambiarlos sin tocar el diseño.
4. **¿El loop también re-encola, o solo deja listo?** Propuesta: solo
   deja listo (SEO + veredicto); el encolado lo hace el ciclo del
   Worker con `auto_publish_top`. Si el usuario quiere que el loop
   llame a `publish_clip` directamente, cambia el diseño (doble vía
   de encolado) y hay que revisarlo.

## Resolución Goldfish (revisión 2026-11-24 01:04, veredicto v1: NO APRUEBA)

El pez dorado verificó los 8 puntos del encargo: (a)-(f) ciertos;
(g) cierto en sustancia pero con evidencia falsa (ver hallazgo 4);
(h) parcialmente falso (ver hallazgo 3). Sus 13 hallazgos se
resuelven así; los cambios ya están aplicados en el cuerpo de la
spec donde se indica:

1. **Puerta SEO del POST — RESUELTO (bloqueante).** Verificado por el
   Elefante: `index.ts:1388-1394` rechaza el POST con máximo SEO <
   80; el rescate lo necesita (`index.ts:1333-1344`, `rescueSeoMax >=
   min_seo`). La v1 lo ignoraba y A1 habría destruido clips. Diseño
   cambiado: POST sin SEO + PATCH posterior (el patrón que el propio
   Worker documenta en su comentario); el rescate mantiene el SEO en
   el POST. Nuevo caso R14 fija la puerta intacta. §7.1 de v1 ("el
   Worker solo refleja lo que el cerebro ya midió") retirado por
   falso para este caso.
2. **Backfill sin entradas — RESUELTO (bloqueante).** Verificado:
   D1 solo guarda `seo_title`, `seo_description`, `seo_tags`,
   `thumb_text` (no `hashtags`/`visible`/`game_verified`). Solución
   en dos partes: (a) A5 — el cerebro manda `viral.seo_inputs` con
   las entradas exactas y el Worker las preserva (cambio aditivo en
   `db.ts`: `ViralData` + `parseViralInput` + `mergeViralJson` +
   test R13); el backfill con `seo_inputs` reproduce el score
   exacto. (b) Sin `seo_inputs` (clips pre-cambio): aproximado con
   hashtags del juego (exactos), `visible=None`, `game_verified=True`,
   marcado `seo_source="backfill_aproximado"`, con invariante
   testeable (R7b: aproximado <= exacto en fixture con gameplay).
   R7 reescrita: ya no compara la función consigo misma.
3. **`preview_seen` mal leído — RESUELTO.** Verificado por el
   Elefante: `quality-gate.ts:150-180` (veredicto manda;
   `preview_seen` solo sustituye a veredicto ausente) y `gate.ts:235`
   (`require_preview` solo frena si está activado). §1.5 corregida:
   el 179 también pasa; el 184 seguiría bloqueado con
   `preview_seen=1`. El loop ya NO llama a `preview_seen` (era
   redundante).
4. **`validate-all.yml` sí existe — RESUELTO.** Verificado:
   `.github/workflows/validate-all.yml:120` ejecuta `validate_all`
   (dispatch, sin cron, sin escritura de vuelta). §1.6 corregida; la
   conclusión (no hay loop) sobrevive.
5. **Loop se atasca — RESUELTO.** Añadido anti-atasco (§3.2 paso 2):
   evento de tracking `revalidado` por intento + salto de candidatos
   con evento en las últimas 24 h. §7.5 de v1 retirado por falso.
6. **Orden con Fix 3 — RESUELTO.** §3.2 declara el orden: el loop se
   estrena DESPUÉS de Fix 3 desplegado y verificado; primera corrida
   manual. §7.6 documenta el riesgo.
7. **Carrera con el trabajo manual — RESUELTO.** Mecanismo concreto
   (§3.2 + §1.8): el workflow nace solo con `workflow_dispatch`; el
   cron se activa cuando el coordinador confirme que el trabajo manual
   terminó. Además, reglas disjuntas (§3.2 paso 1): el loop no
   re-escribe lo que ya está puesto. La fusión del PATCH es por campo
   sobre la fila leída por el handler (`db.ts:419-460`,
   `index.ts:1186-1190`); el residuo es una ventana de
   milisegundos (§7.8).
8. **"Clips 5/6/7" sin evidencia — RESUELTO.** Verificado por el
   Elefante en D1 kaez (2026-11-24 01:10): en `en_carpeta` sin job
   vigente están 1, 2, 3, 5, 6, 7, 9, 10, 11; solo 4 y 8 tienen jobs
   (todos `posted`). §1.6 y §3.3 usan esa lista real, sin prometer
   qué clip sale en el primer ciclo.
9. **R5 inalcanzable vía `run()` — RESUELTO.** §3.1 A2 y R5 aclaran
   que el caso parcial solo existe por llamada directa (`run()`
   descarta antes de quemar, `vod_fallback.py:240-262`). La guarda de
   A2 usa `_seo_score_value` (acepta número o mapa, como el código
   VOD existente) en vez de la cadena `.get` en línea.
10. **A3 precisiones — RESUELTO.** La spec exige construir el
    veredicto DESPUÉS de `_rerender_vod_clip` y anota que el
    `rechazado` retorna antes del payload (R4/R6 lo dicen).
11. **Seam R8/R9 + Gemini — RESUELTO.** §3.2 fija el seam (import a
    nivel de módulo o inyección) y exige el paso de bootstrap de
    `panel_credentials` en el workflow (exporta `GEMINI_API_KEY`,
    `panel_credentials.py:130-132`).
12. **Carrera loop vs ciclo — RESUELTO (documentado).** §7.7: benigna
    (retraso de un ciclo; `recheckPublishGate` relee antes del
    adaptador).
13. **Nits — RESUELTO.** §1.1 cita el paso 8 (~1075) para
    `seo = package.get("seo")`; resto de citas verificadas correctas
    por el pez dorado (incluido el test de contrato
    `clips-scoring.test.ts:193-214` y que el PATCH fusiona
    `seo_scores` por plataforma y reemplaza `quality_gate`).

14. **Hallazgo de implementación (no del Goldfish): el workflow
    necesita una fuente de token.** La v2 decía "cada 6 h +
    `workflow_dispatch`" sin especificar el disparo, pero un run por
    cron de GitHub no tiene token del cerebro (el bootstrap canjea
    el token del dispatch). Resuelto en la implementación: el Worker
    dispara `panel-revalidate` cuando toca (`maybeDispatchRevalidate`
    + puerta `revalidateDue` en `cycleGate`, mismo patrón que
    `panel-live`/`panel-daily`; marca `last_revalidate_at_ms` en
    settings, sin migración). §1.7 y §3.2 actualizados con el
    mecanismo. El cambio en el Worker es solo este disparo + el
    passthrough aditivo A5; la puerta SEO del POST y el gate no se
    tocan.

**Veredicto Goldfish tras la resolución: la v2 incorpora los 13
hallazgos; la implementación añade el hallazgo 14 (disparo del
workflow) y la corrección del snippet A2 (`_seo_score_value`
devuelve 0 y no distingue "sin medir"). El despliegue se hace con
la orden del usuario (2026-11-24 00:55); la primera corrida del
loop queda para 6 h después del deploy (marca inicial, §1.8).**
