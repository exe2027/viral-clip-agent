# Auto-publicación — contador "funciona", 4 fases y circuit breaker

Bloque 10 del tribunal (2026-11-16). Implementado en `counter.py`,
configurado en `config.yaml` (sección `autopublish`) y
`config.py` (`get_autopublish_config()`).

**Regla madre: nada se publica solo sin pasar por las fases, y cada
cambio de fase es manual, con confirmación. Este módulo NO publica
nada: solo registra eventos y dice si la cola PUEDE publicar.**

## 1. Contador "funciona"

El contador mide si el pipeline funciona de punta a punta: un clip
suma **solo si se aprobó sin editar título/caption Y se publicó sin
fallo técnico**. La racha vive en `state/counter.json` (JSON en disco;
ese archivo es estado de ejecución, no se commitea).

### Umbral (propuesto, a validar por el usuario)

- **15 seguidos** sin edición y sin fallos (`autopublish.threshold`, default 15).
- **0 rechazados por contenido en los últimos 15** clips decididos.
- Además: circuit breaker sin disparar y kill-switch inactivo.

### Qué reinicia el contador a cero

| Evento | Efecto |
|---|---|
| `approved_edited` (aprobado con edición) | racha → 0 |
| `rejected_content` (rechazado por contenido) | racha → 0 |
| `published_fail` (fallo de subida) | racha → 0 |
| `red_flag` / `copyright` / `strike` | racha → 0 + breaker |

Un clip aprobado limpio queda en `pending_clean` hasta que se
publica: si se publica ok, suma 1; si falla, la racha se pierde. Un
clip publicado sin haber pasado por `approved_clean` no suma ni
resta (no hay evidencia de que pasara la revisión sin editar).

### CLI del contador

```bash
python3 counter.py status
python3 counter.py record approved_clean --clip c1
python3 counter.py record published_ok   --clip c1 --platform youtube
python3 counter.py record published_fail --clip c1 --platform youtube
python3 counter.py record approved_edited --clip c1
python3 counter.py record rejected_content --clip c1
python3 counter.py record red_flag --clip c1
python3 counter.py record copyright --clip c1
python3 counter.py record strike --clip c1
python3 counter.py record lang_low_conf --clip c1   # idioma conf. < 0,5
python3 counter.py record below_median --clip c1   # muy por debajo de la mediana
```

`counter.py status` muestra racha, rechazados en los últimos 15,
totales, fase, breaker, kill-switch y rachas de problemas.

## 2. Las 4 fases (cambio MANUAL, nunca automático)

| Fase | Nombre | Qué pasa |
|---|---|---|
| 1 | manual (19 nov) | Nada automático. El usuario publica a mano. |
| 2 | semi-auto | El lote se propone; **si no responde, no se publica**. |
| 3 | auto con ventana de veto | Se programa la publicación tras la ventana de veto; si nadie veta en ese tiempo, se publica. |
| 4 | auto total | Publica solo, **solo** con semáforo verde + lista blanca + métricas en verde. |

Ventana de veto (fase 3, configurable, a validar por el usuario):
**30 min en YouTube, 2 h en el resto**
(`autopublish.veto_window_minutes: {youtube: 30, default: 120}`).

### Mínimos por fase

Cada fase dura **mínimo 3-5 días y 10 clips**
(`min_days_per_phase: 3`, `min_clips_per_phase: 10`). `set-phase`
se niega a avanzar si no se cumplen, salvo `--force` explícito
(queda registrado como "(forzado)" en el historial de fases).

### Cambiar de fase

```bash
python3 counter.py phase                 # fase actual
python3 counter.py set-phase 2 --yes     # pide confirmación si no hay --yes
python3 counter.py set-phase 2 --force --yes --reason "motivo"
```

Reglas de `set_phase()`:

- Avanzar es secuencial (1→2→3→4); saltarse una fase exige `--force`.
- Retroceder a una fase menor siempre se permite: es el camino seguro.
- Avanzar a la **fase 4** exige además: umbral "funciona" en verde +
  lista blanca no vacía + `metrics_green: true` (salvo `--force`).
- Todo cambio queda en `state.phase.history` con fecha y motivo.

La lista blanca son los streamers de la lista que sube el usuario.
**Vacía = fase 4 bloqueada.** La fuente canónica es
**`influencers.yaml`** (REGLA 2026-11-16: estar en la lista =
permiso ya dado, sin ficha) **+ `allowed_streamers.yaml`**, leídas por
`publisher.load_whitelist()` (bloque 7); `jobqueue.py` (bloque 1) usa
además la clave `allowlist` de nivel superior en `config.yaml`.
`phase4_ready()` consulta esas fuentes en ese orden. Hoy está
IlloJuan. `metrics_green` es una declaración manual: poner `true`
solo tras revisar las métricas de los últimos clips.

## 3. Circuit breaker

Auto-pausa la publicación (la cola debe dejar de publicar) cuando:

1. **2 fallos de subida seguidos en una misma plataforma.**
2. **Copyright o strike.**
3. **1 rojo que intentó colarse** (un clip marcado rojo que llegó al paso de publicar).
4. **Idioma conf. < 0,5 en 2 clips seguidos.**
5. **3 clips seguidos muy por debajo de la mediana.**

El breaker se dispara desde `record_event()` en el momento en que se
cumple la condición; el motivo, la plataforma y la hora quedan en
`state.circuit_breaker`. **Reanudación solo manual:**

```bash
python3 counter.py resume --yes        # limpia el breaker y quita queue.kill
python3 counter.py breaker-reset --yes # limpia solo el breaker
```

`resume` también pone a cero las rachas de problemas (fallos por
plataforma, idioma, mediana).

## 4. Kill-switch (compartido con la cola)

El flag es el archivo **`queue.kill` en la raíz del repo** — el mismo
flag del bloque 1 (la cola). Si existe, nada se publica, en ninguna
fase, ni siquiera en fase 4.

```bash
python3 counter.py kill --reason "motivo"   # crea queue.kill
python3 counter.py resume --yes             # lo quita (y limpia el breaker)
```

`queue.kill` NO se commitea ni se deja creado por defecto: su
presencia significa "parado". Confirmado: `jobqueue.py` (bloque 1) usa
este mismo archivo (`KILL_SWITCH_PATH` en la raíz del repo) y su
worker se pausa si existe. Un solo flag para los dos bloques.

## 5. Interfaz con la cola (`jobqueue.py`, bloque 1) y el publicador (`publisher.py`, bloque 7)

La cola es `jobqueue.py` (SQLite en `queue.db`, bloque 1). Sus estados
reales:

```
queued → processing → ready_for_review → approved → published
                                  \-> rejected
processing → queued (reintento transitorio) / failed / dead
```

El semáforo de `jobqueue.py` (`check_semaphore`) marca en rojo un job
con juego ≠ GTA 6, VOD anterior al 19 nov 2026, música
con copyright sospechada, o manifiesto/layout/subtítulos
incompletos; con rojos no se aprueba sin `--force-confirm`
explícito. (2026-11-16: el permiso YA NO es un rojo.) **Si un job con rojos llega a publicarse con
`--force-confirm`, eso es "un rojo que intentó colarse": el
publicador debe registrar `counter.py record red_flag` y el breaker
se dispara.**

La cola y el publicador deben:

1. **Antes de publicar cada clip**, consultar la puerta:
   ```python
   from counter import gate_publish, load_state
   decision = gate_publish(load_state(), platform="youtube", clip="c1")
   # {"allow": bool, "mode": "manual|propose|veto|auto|deny",
   #  "reason": str, "veto_minutes": int | None}
   ```
   O por CLI (útil si la cola es un script de shell):
   ```bash
   python3 counter.py gate --platform youtube --clip c1   # exit 0 = permitido
   ```
   Modos: `manual` (fase 1, nunca publica solo), `propose` (fase 2,
   sin respuesta humana no se publica), `veto` (fase 3, publicar
   tras `veto_minutes` si nadie veta), `auto` (fase 4, publica ya),
   `deny` (kill-switch, breaker, o fase 4 sin semáforo verde).
2. **Registrar cada revisión humana** (`record approved_clean` /
   `approved_edited` / `rejected_content`).
3. **Registrar cada intento de subida** (`record published_ok` /
   `published_fail --platform <plataforma>`).
4. **Registrar las señales del breaker**: `lang_low_conf` cuando la
   confianza de idioma sea < 0,5, `below_median` cuando el clip esté
   muy por debajo de la mediana, `red_flag` si un rojo llega a
   publicar, `copyright` / `strike` si llegan.
5. **Antes de cada lote**, comprobar que no existe `queue.kill`
   (`counter.kill_switch_active()`).

Funciones importables desde `counter.py`: `load_state`,
`save_state`, `record_event`, `meets_threshold`, `current_phase`,
`set_phase`, `phase_can_advance`, `phase4_ready`,
`whitelist_nonempty`, `gate_publish`, `trip_breaker`,
`reset_breaker`, `breaker_tripped`, `kill_switch_active`,
`set_kill_switch`, `clear_kill_switch`, `content_rejections_last_n`,
`status_text`.

`publisher.py` (bloque 7) es el único módulo que publica y el que
debe llamar a `gate_publish()` / `record published_ok|fail` y a
`record red_flag|copyright|strike` cuando corresponda. `jobqueue.py`
(bloque 1) usa el **mismo archivo `queue.kill`** como kill-switch:
su `kill_switch_active()` y el de este módulo miran el mismo flag.

**Arranque gradual (6.7, 2026-11-17):** `autopublish.mode` en
config.yaml elige entre `gradual` (por defecto) y `full`.
En `gradual`, la política (`clipagent/policy.py`) consulta el gate
de este contador antes de publicar, y el bucle de publicación
(`clipagent publish loop`) registra cada resultado con
`published_ok` / `published_fail` en `state/counter.json`, así las
fases y las rachas avanzan solas con la publicación real. En `full`
se publica según la política (6.3) sin pasar por las fases y el
contador no se toca.

## 6. Estado en disco (`state/counter.json`)

```json
{
  "version": 1,
  "streak": 0,
  "total_approved_clean": 0,
  "total_published_ok": 0,
  "total_rejected_content": 0,
  "pending_clean": {"clip-42": {"at": "..."}},
  "recent": [{"event": "published_ok", "clip": "clip-42", "at": "..."}],
  "history": [{"event": "...", "clip": "...", "platform": "...",
               "detail": "...", "at": "..."}],
  "fail_streak_by_platform": {"youtube": 0},
  "lang_low_conf_streak": 0,
  "below_median_streak": 0,
  "circuit_breaker": {"tripped": false, "reason": null,
                      "platform": null, "tripped_at": null},
  "phase": {"current": 1, "entered_at": "...", "clips_in_phase": 0,
            "history": [{"phase": 1, "at": "...", "note": "fase inicial"}]}
}
```

- `recent`: últimas 30 decisiones (el umbral mira las últimas 15).
- `history`: log de auditoría, últimos 200 eventos.
- Escritura atómica (`.tmp` + `os.replace`).

## 7. Simulación

`python3 counter.py simulate` corre en un estado temporal (no toca
`state/counter.json`): 10 clips aprobados sin editar y publicados
sin fallo → 1 rechazo por contenido → 2 fallos de subida seguidos
en YouTube (demostración del breaker).

Salida real (2026-11-16):

```
SIMULACIÓN (estado temporal, no toca state/counter.json)

— 10 clips aprobados sin editar y publicados sin fallo —
Contador «funciona» — estado
  racha (aprobados sin editar + publicados sin fallo): 10 / 15
  rechazados por contenido en los últimos 15: 0
  totales: 10 aprobados limpios, 10 publicados ok, 0 rechazados
  aprobados limpios pendientes de publicar: 0
  umbral «funciona»: ROJO — racha 10 < umbral 15
  fase actual: 1 (manual), 0.0 días en la fase, 10 clips en la fase
  circuit breaker: ok
  kill-switch (queue.kill): inactivo
  rachas de problemas: fallos subida {'youtube': 0}, idioma<0,5 seguidos: 0, bajo mediana seguidos: 0
  umbral (15): ROJO (racha 10 < 15, faltan 5)

— 1 rechazo por contenido (clip sim-clip-11) —
Contador «funciona» — estado
  racha (aprobados sin editar + publicados sin fallo): 0 / 15
  rechazados por contenido en los últimos 15: 1
  totales: 10 aprobados limpios, 10 publicados ok, 1 rechazados
  aprobados limpios pendientes de publicar: 0
  umbral «funciona»: ROJO — racha 0 < umbral 15; 1 rechazo(s) por contenido en los últimos 15
  fase actual: 1 (manual), 0.0 días en la fase, 10 clips en la fase
  circuit breaker: ok
  kill-switch (queue.kill): inactivo
  rachas de problemas: fallos subida {'youtube': 0}, idioma<0,5 seguidos: 0, bajo mediana seguidos: 0
  → el contador vuelve a CERO (racha 10 → 0).

— extra: 2 fallos de subida seguidos en YouTube —
  breaker disparado: 2 fallos de subida seguidos en youtube
Contador «funciona» — estado
  racha (aprobados sin editar + publicados sin fallo): 0 / 15
  rechazados por contenido en los últimos 15: 1
  totales: 10 aprobados limpios, 10 publicados ok, 1 rechazados
  aprobados limpios pendientes de publicar: 0
  umbral «funciona»: ROJO — racha 0 < umbral 15; 1 rechazo(s) por contenido en los últimos 15; circuit breaker disparado: 2 fallos de subida seguidos en youtube
  fase actual: 1 (manual), 0.0 días en la fase, 10 clips en la fase
  circuit breaker: DISPARADO — 2 fallos de subida seguidos en youtube
  kill-switch (queue.kill): inactivo
  rachas de problemas: fallos subida {'youtube': 2}, idioma<0,5 seguidos: 0, bajo mediana seguidos: 0

Reanudación: solo manual → `counter.py resume --yes` (limpia el breaker y quita queue.kill si existe).
```

## 8. Pendiente de validar por el usuario

- Umbral de **15** seguidos sin edición y sin fallos (¿15 o menos?).
- Ventana de veto: **30 min YouTube / 2 h resto**.
- Mínimo por fase: **3 días** (rango 3-5) y **10 clips**.
- La fase 1 arranca el **19 nov** (lanzamiento de GTA 6).

## 9. Notas de implementación

- `counter.py` no importa nada de `clips_chat_v2.py`, `subtitles.py`,
  `layout.py`, `viral_package.py` ni `auto_clips.py`: solo `config.py`,
  y de forma perezosa (dentro de la función, con try/except)
  `publisher.load_whitelist()` y `queue.load_allowlist()` para la
  fase 4. Si esos módulos no están, el fallback es la config.
- La lista blanca NO tiene clave propia en `autopublish`: usa las
  fuentes canónicas (influencers.yaml / allowed_streamers.yaml /
  allowlist). Así no hay tres listas que mantener sincronizadas.
- `--state <ruta>` permite usar otro JSON de estado (pruebas, otro canal).
- `state/counter.json` es estado de ejecución y está ignorado por
  git (`*.json` en `.gitignore`): persiste en disco, no en el repo.
