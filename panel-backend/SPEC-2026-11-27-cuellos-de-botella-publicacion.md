# SPEC — Arreglo de cuellos de botella del sistema de publicación

- **Fecha:** 2026-11-27 (v2 — revisada por 2 Goldfish, hallazgos resueltos)
- **Estado:** BORRADOR v2 para el sí del usuario → implementar
- **Orden del usuario:** 2026-11-27 00:19 — «Arregla los cuello de botella para que no suceda eso, ya te lo dije»
- **Proceso:** Elephant-Goldfish SIEMPRE (orden permanente del usuario 2026-11-23).
  Revisión v1: Goldfish A (B2+B3, 22 hallazgos G1–G22) y Goldfish B
  (B1+B4+coherencia, 36 hallazgos H1–H36). Todas resueltas en el
  «Registro de revisión Goldfish» al final. Limitación honesta: en
  Muse el revisor hereda contexto; la revisión es por brief estricto,
  no por contexto limpio. La revisión no reemplaza tests ni
  verificación en vivo.
- **Árbol:** `~/workspace/gta6-clips/viral-clip-agent` (Worker TypeScript en `panel-backend/worker/`)
- **No implementado aún.** Nada de este documento es código hasta el sí del usuario.

## §1. Objetivo

El día 2026-11-26 cerró en **26/38** (seba 13, kaez 13) teniendo inventario de sobra.
Que un día no vuelva a cerrar corto teniendo clips validados para publicar.
El arreglo ataca las 4 causas verificadas en D1 y código (B1–B4), sin tocar
lo que ya funciona y sin cambiar ninguna regla vigente (sin franjas,
aleatorio 1:30–3 h, todos publicados en 24 h, topes diarios, calentamiento).

## §2. Evidencia verificada (D1 + código, 2026-11-27 00:00–01:30)

| # | Cuello | Evidencia |
|---|--------|-----------|
| B1 | Atasque de mañana (seba): 0 jobs nuevos hasta ~15:00; primer job nuevo publicó 15:06 (242 YT) | La causa era la siembra A4: el contador vivía solo en memoria y al reciclarse el isolate se re-sembraba en 1. La persistencia en D1 (migración 0045, commit `1e6ce78`) **SÍ está viva y desplegada**: `auto_enqueue_attempts` 26-nov = 41 clips / 137 intentos (seba), 13 / 39 (kaez); 27-nov ya 10 / 22 y 3 / 12. `seedAutoEnqueueStateFromReads` (`jobs.ts:1242-1260`) lee el valor REAL de D1, no 1. El atasque no puede repetirse por esa vía. Queda por blindar con test de regresión y corregir un comentario obsoleto (§4.1). |
| B2 | Quema de reintentos en el piso de 90 min | Jobs 159 (seba 211 YT, creado 19:55, muerto 23:11, attempts=3), 52/45 (kaez 57/35 YT) murieron en draft con «menos de 90 min» tras 3 reintentos. **Mecanismo verificado en código:** el gate frena con `lastPublishedAtMs = max(última subida real, reserva de cupo)` (`gate.ts:680-685`, lógica inline en `recheckPublishGate`, `gate.ts:618`), pero `retryAtForGateBlock` caso `min90` re-consulta SOLO subidas reales (`gate.ts:507-520`). Si el piso lo cerró una reserva (no una subida real), el `retryAtMs` cae en el pasado → el job vuelve a estar vencido en el siguiente ciclo (5 min) → se rechaza otra vez → attempts+1 → a los 3, draft terminal. Además el piso es un blanco móvil: cada subida nueva re-pone el reloj a 90 min. El piso es tiempo de espera, no un fallo: no debe consumir reintentos. |
| B3 | Tope rolling 24 h: jobs creados que luego rebotan | 249 FB (seba job 167), 84 IG (kaez job 71, rebotó 23:44, quedó para mañana 18:31), 5 FB (kaez job 21), 46/50/76 IG (kaez) bloqueados por rolling 3/3 en ejecución. **Mecanismo verificado:** en creación, `tryEnqueueClipPublish` (`jobs.ts:989-1005`) encadena el `next_run_at` tras la última subida + azar(90–180) y tras el máximo `next_run_at` pendiente, pero el conteo rolling de creación (`last24h`, `jobs.ts:885-893`) cuenta SOLO `state='posted'`. Los jobs ya programados que publicarán antes que el candidato no se cuentan. El candidato nace cuando el conteo es 2/3, otro job publica antes que él, y en ejecución el re-chequeo (que sí cuenta bien) lo frena. El planificador debe proyectar la ventana rolling a la hora del candidato. El mismo defecto existe para el tope diario (§4.3 B3.6). |
| B4 | Drafts viejos (seba, clips 77/83/39/62/169/171) | Jobs en draft/failed desde ~19-nov (39/62/77: drafts FB/IG/YT + failed TT; 83: drafts FB/IG, YT posted 19-nov; 169/171: draft TT, YT posted 21-nov). **Verificado:** 39/62/77/83 tienen veredicto de calidad `rechazado` en `viral.quality_gate`; 169/171 no tienen veredicto. Los 4 primeros son `origin='live'` (segmento LIVE de la Cola, que se escanea primero) y **SÍ se evalúan**: `clip_events` 26-nov muestra frenos para 62 (19:55), 39 (20:00) y 77 (22:11), 4 plataformas cada uno, motivo «sin subtítulos completos: necesita transcripción humana» (duro). Cada evaluación consume `considered`+`evaluated` hasta que A4 los aparca (3 intentos/día). La afirmación del borrador v1 («consumen ~0 presupuesto») era **falsa** y se retira: el consumo es real y es lo que B4.1 elimina. |
| B5 | Retención por bug de layout (clips 253-256) | **FUERA de este arreglo.** La está arreglando OTRO CHAT (formato sin-webcam, aprobado 22:42). No se toca aquí, no se despliega encima de su trabajo. |
| — | NO es cuello: «1 por plataforma y franja» | **Verificado en código y en vivo, se deja como está.** El chequeo de franja y el `1 por plataforma y franja` se eliminaron (`gate.ts:295-300`, comentario «SIN FRANJAS», SPEC quitar-franjas v2 opción A). El texto en `gate.ts:409` es SOLO la clasificación de reintento para jobs viejos frenados por la regla antigua (transitorio → `min90`); no aplica ninguna franja. En vivo: kaez publicó 78 YT 22:05 y 67 YT 23:45 en la misma ventana vieja. No se toca. |

Cierre del día verificado en D1 (26-nov, `posted_day`, ambos tenants):
seba 13, kaez 13 (67 YT sí publicó 23:45; 84 IG rebotó 23:44 por rolling).
Sin duplicados en el conteo. El hueco grande de la mañana fue B1 (seba sin
jobs nuevos hasta ~15:00); el de la noche, B2+B3 (jobs que rebotaron y
murieron en vez de esperar su hora). Detalle de filas en el Anexo A.

## §3. Supuestos

1. La regla vigente se mantiene: **sin franjas, aleatorio 1:30–3 h entre
   publicaciones, todos publicados en 24 h**. Topes diarios (YT 6,
   TT/IG/FB 3 por cuenta), rolling 24 h (YT 6, resto 3), calentamiento
   y plan del día se mantienen. Nada de esto se relaja ni se sube.
2. El ciclo corre cada 5 min. Crons (UTC, hora Chile = UTC−3 en
   noviembre): avc 4 — `*/5 15-18` (ciclo 12:00–15:00), `*/5 21-23,0-4`
   (ciclo 18:00–01:00), `0 12,13` (despacho diario 09:00 y 10:00, que
   crea jobs por la mañana), `0 6,7` (caducidad 03:00 y 04:00);
   avc-kaez 2 (los dos de ciclo); avc-sebastian ninguno. Los 5 min
   son el latido: cualquier espera menor que eso es invisible para
   el ciclo.
3. `MIN_PUBLISH_SPACING_MS` = 90 min, `MAX_PUBLISH_SPACING_MS` = 180 min
   (`db.ts:1146-1156`, `randomPublishDelayMs`). El azar 90–180 min es el
   programador; el piso de 90 min es el segundo control en el gate.
4. Un rechazo del gate por esperar (piso, espaciamiento, 15 min entre
   plataformas, rolling, plan/tope de mañana) NO es un fallo del job:
   es el sistema diciendo «todavía no». Los fallos reales (error de
   subida, API, atascado en processing >30 min) siguen su camino
   actual y sí consumen reintentos.
5. `auto_enqueue_attempts` (migración 0045) es la única fuente del
   contador A4. La siembra por `clip_events` murió con 0045.
6. Hay DOS topes «3» distintos y no se mezclan: **A4 por clip** (3
   intentos fallidos de auto-enqueue por clip y día, `retry.ts:323`)
   y **`retriesToday` por job** (3 reintentos por job y día,
   `retry.ts:164-166`, derivado de `attempts` + `updated_at`).
   B2 cambia qué cuenta para el segundo; el primero no cambia.
7. `slot.updated_at` (`publish_slots`) es la **última mutación del
   cupo** (reserva o liberación), no necesariamente la hora de la
   reserva. Se usa como cota conservadora para el piso (§4.2), no
   como hora exacta de reserva.
8. «Programado = publicará» es una aproximación declarada (§4.3):
   la proyección cuenta un job `scheduled` como una publicación
   futura en su `next_run_at`. El error es el retraso entre
   programación y publicación (típicamente minutos; acotado por el
   ciclo de 5 min + gate). La dirección del error es segura
   (sobre-conteo → empuje o no-creación; nunca rebote), y B2.2
   absorbe el caso contrario (si el programado falla, el candidato
   no rebota con coste).
9. $0. Nada que el usuario tenga que configurar. Sin nuevas cuentas,
   sin nuevos crons, sin nuevas tablas (0045 ya desplegada; B2–B4
   no crean tablas).

## §4. Cambios

### §4.1 B1 — Atasque A4: verificación + test de regresión (sin cambio de mecanismo)

El mecanismo ya es correcto y está desplegado. No se cambia el
comportamiento; se blinda para que no pueda regresar en silencio.

- **B1.1 (verificación, ya hecha):** 0045 viva en ambos Workers
  (filas en ambas D1, §2). Antes del despliegue de este arreglo se
  re-verifica en vivo: `SELECT COUNT(*) FROM auto_enqueue_attempts
  WHERE day = <hoy>` > 0 en ambas D1 y el Worker desplegado contiene
  la lectura de D1 en la siembra (ver §8).
- **B1.2 (test de regresión, `test/retry-a4.test.ts`):** solo la mitad
  que falta. El archivo YA contiene «siembra por lectura: lee el
  valor REAL» (inserta `attempts=2`, siembra, espera 2) y
  «persistencia entre reinicios» — no se duplican. Lo nuevo es:
  tras limpiar el contador por éxito
  (`clearAutoEnqueueAttemptPersisted`), la siembra posterior no
  resucita intentos viejos (intentos 0 tras limpiar + reinicio
  simulado). Fail-before: con el código actual (sin limpieza
  persistida) ese test falla; con B1.2 pasa.
- **B1.3 (doc):** el comentario de cabecera de `retry.ts` (líneas
  ~15–30) todavía describe la siembra vieja por `clip_events
  (MAX(at))`. Actualizarlo a la siembra por `auto_enqueue_attempts`
  (0045). Sin cambio de código funcional.
- **Riesgo residual aceptado (se documenta, no se arregla):** si la
  escritura D1 del upsert falla (cuota 7500), el freno en memoria
  vale ese ciclo pero se pierde en el siguiente reinicio del isolate:
  el clip gana hasta 3 intentos extra ese día. Acotado y ya
  documentado en el comentario de `recordAutoEnqueueAttempt`. No se
  añade reintento de escritura: con la cuota agotada, escribir más
  empeora el problema.

### §4.2 B2 — El piso de 90 min no quema reintentos

**Problema exacto:** el gate frena con `max(subida real, reserva de
cupo)` y el reintento se programa con solo la subida real. Dos
relojes distintos → `retryAtMs` en el pasado → re-rechazo en 5 min
→ 3 intentos quemados → draft terminal.

**Por qué el valor antes llamado «contaminado» es el reloj correcto
para el piso (y solo para el piso).** El docstring vigente de
`retryAtForGateBlock` (`gate.ts:454-463`) dice: «Solo lee subidas
`posted` reales, NUNCA reservas de cupo: `lastPublishedAtMs` del
re-chequeo está contaminado… y una reserva que nunca publicó no es
una subida». Esa decisión era correcta para CONTAR publicaciones
(rolling y tope diario cuentan solo subidas reales — y siguen
contando solo subidas reales). Pero el piso no cuenta publicaciones:
mide separación entre publicaciones, y una reserva significa que
una publicación es inminente (otro job tiene el cupo y está
subiendo o a punto de subir). Usar la reserva como referencia del
piso es correcto; usarla como publicación contada sería incorrecto.
Y si la reserva nunca publica (el job falla o va a draft), el cupo
se libera, `reservedOthers` vuelve a 0 y el reloj cae de nuevo a
la última subida real en la siguiente evaluación: una reserva
liberada no envenena el reloj más allá de un ciclo. El sobre-espera
por usar la hora de última mutación del cupo (§3.7) está acotado
por el detector de atascados (30 min) y, en el caso típico
(reserva en vuelo), es de minutos. Si el reintento cae antes de
que se libere el piso por una subida hermana posterior, se
re-rechaza UNA vez más (sin consumir intento, B2.2) y se
reprograma desde la subida real: el sistema se corrige solo en un
ciclo, no muere.

**B2.1 — Mismo reloj (solo donde corresponde).** `recheckPublishGate`
(`gate.ts:618`) devuelve, además del motivo, el instante que el
gate usó como última subida: `{ reason: string | null,
lastPublishedAtMs: number | null }`. `retryAtForGateBlock` recibe
ese instante como parámetro opcional y lo usa para los casos
`min90` y `spacing` (el reloj de la plataforma del job), en vez
de re-consultar solo subidas reales. `cross15` NO usa ese
instante: su reloj es la última subida del MISMO CLIP en OTRA
plataforma (`state IN ('posted','processing')`, caso `cross15`
actual) y se mantiene exactamente como hoy. El caso `rolling` no
cambia: su reloj ya es el mismo en ambos lados (solo subidas
reales). Si el instante no viene (llamadores viejos, tests), se
mantiene la re-consulta actual como fallback. El segundo llamador,
`publish/index.ts:226` (fallo de `reservePublishSlot`, que devuelve
solo `{ ok, reason }` sin instante), llama a `retryAtForGateBlock`
SIN el parámetro nuevo (fallback actual + invariante B2.3).

**B2.2 — La espera no consume intentos.** En el caso `"retry"` de
`applyPublishResult` (`jobs.ts:2176`, caso en `~2203-2227`), el
kind se deriva con `classifyGateBlock(r.reason).retry` (el
`PublishResult` no trae kind; `publish/index.ts:65-80`). Si el
kind es de espera — `min90`, `spacing`, `cross15`, `rolling`,
`tomorrow` — el job se reprograma a `retryAtMs` **sin consumir
intento**: el `UPDATE` escribe `attempts = retriesToday(job,
todayKey)` (normalización al conteo de hoy, SIN +1; escribir el
valor actual evita que intentos de ayer resuciten como de hoy al
cambiar `updated_at` — ver §6). Siguen consumiendo intentos: kind
`backoff` (cupo no reservado / reserva perdida — `attempts =
retriesToday + 1`, como hoy), los atascados en processing >30 min
(`detectStalledJobs`, como hoy) y los fallos reales de ejecución
(casos `error`/`failed`). Si `classifyGateBlock` devuelve
`retry: null` en el caso `"retry"` (motivo desconocido que llegó
a reintento), se trata como `backoff` (consume; fail-closed) y se
deja un evento en el Worker. La rama que manda a draft por agotar
el tope en el caso `"retry"` se conserva como válvula SOLO para
intentos consumidos por fallos reales: como las esperas ya no
incrementan, `retriesToday >= 3` ahí significa 3 fallos reales o
atascados previos. El mensaje de esa válvula cambia para no culpar
a la espera: `gate de publicación: ${r.reason} (tope de reintentos
por hoy agotado por fallos previos: 3/día)`. Un job que espera su
hora no puede morir por esperar; un job que ya falló 3 veces de
verdad sí va a draft aunque su último choque sea una espera.
`tomorrow` está en la lista de no-consumo por uniformidad, aunque
su efecto práctico es casi nulo: al reprogramarse a mañana 00:00,
`retriesToday` se resetea con el cambio de día (`retry.ts:164-166`).

**B2.3 — Invariante y tabla de fallback por kind.**
`retryAtForGateBlock` garantiza `retryAtMs > now` para todos los
kinds. El valor de fallback cuando el cálculo da ≤ now (o no hay
desde qué contar) es POR KIND, no único:

| kind | fallback si el cálculo da ≤ now |
|---|---|
| `min90` | `now + randomPublishDelayMs(rng)` (90–180 min frescos) |
| `spacing` | `now + min_spacing_minutes` (piso de 5 min, de Ajustes) |
| `cross15` | `now + cross_platform_spacing_minutes` (15 min, de Ajustes) |
| `rolling` | `now + 60 min` (re-chequeo en una hora; la ventana libre es `más viejo + 24 h`, que por construcción es futura) |
| `tomorrow` | mañana 00:00 Chile (ya es futuro; si diera ≤ now, +24 h) |
| `backoff` | `now + nextRetryDelayMs(used)` (escalera 1/5/15 min, sin cambio) |

El invariante ya se cumplía hoy para `rolling`, `tomorrow` y
`backoff`, y para `spacing`/`cross15` con motivo fresco; el único
caso que lo violaba era `min90` con reserva. La tabla no cambia
esos casos: solo pone el suelo donde faltaba, con la duración
correcta para cada espera (un fallback de 90–180 min para una
espera de 5 o 15 min sería un sobre-retraso).

**B2.4 — Tests (`test/retry-a4.test.ts`, con su harness SQLite
ampliado; `test/gate-blocks.test.ts` solo para lo puro).**
`applyPublishResult` pasa a exportarse para tests (ver §6). El
harness de `retry-a4.test.ts` se amplía con las tablas que B2
necesita (`publish_slots`, `settings`, `api_accounts`); sin ese
harness los tests no son escribibles ahí. Casos:
- Reserva sin subir: última subida real hace 5 h, reserva de cupo
  hace 10 min → el gate frena por piso; `retryAtMs` es reserva +
  90–180 min (nunca en el pasado). Fail-before: con el código
  actual da subida real + 90–180 (en el pasado). Nota: el test
  fabrica la fila de `publish_slots`; prueba la aritmética, no el
  reloj vivo (el vivo lo cubre §8.4).
- Espera no consume: job con attempts=0, 3 rechazos seguidos por
  `min90` → sigue en `scheduled` con attempts=0 y `next_run_at`
  en el futuro; nunca draft. Fail-before: el 3.º lo manda a draft.
- Invariante: `retryAtMs > now` para todos los kinds de la tabla
  B2.3, incluso sin subida previa.
- Sin regresión: `backoff` y atascado en processing siguen
  consumiendo y llegando a draft a los 3.
- `gate-blocks.test.ts` (sin D1, solo puro): el literal del motivo
  de bloqueo B3 (§4.3) casa con el regex de `GATE_BLOCKS`.

**B2.5 — Observabilidad de esperas largas (sin matar jobs).** Un
job bloqueado por `tomorrow` puede reprogramarse día tras día si
el plan está cubierto a diario — eso es presión de cola legítima,
no un bug, y `dueJobs` ordena por `created_at`, así que los jobs
viejos tienen prioridad al día siguiente: no se les pone un tope
de muerte. Lo que se añade es visibilidad: cuando un job se
reprograma por una espera y su `created_at` tiene más de 24 h, se
deja UN evento en el Worker por job y día (`espera larga: job #id
lleva >24 h esperando por <motivo>`, best-effort, con lectura
previa para no repetirlo ese día). Las revisiones de cada franja
ya reportan los atascados; este evento es el aviso para que no
pasen desapercibidos.

### §4.3 B3 — El planificador proyecta la ventana rolling (y el tope diario) al crear

**Problema exacto:** al crear, el rolling cuenta solo lo ya publicado;
los jobs programados que publicarán antes que el candidato no se
cuentan. El candidato nace condenado y rebota en ejecución.

**B3.1 — Proyección en `tryEnqueueClipPublish` (`jobs.ts:702-1010`),
por plataforma+idioma (regla 0019) y tenant.** Para un candidato
con hora C, el conteo proyectado es:

```
P(C) = #{posted con updated_at en (C-24h, C]}
     + #{scheduled o processing con next_run_at en (C-24h, C]}
       (next_run_at NULL = vencido ahora: cuenta)
```

El tope es el mismo que usa el gate (`rolling_24h_max`, YT
`rolling_24h_max_youtube`). Diferencias con el borrador v1
(corregidas por la revisión): el intervalo tiene cota inferior
(`C-24h`, no solo `≤ C`) — sin ella un job programado contaría
para siempre y el empuje no liberaría la ventana; los
`scheduled` vencidos (con `next_run_at` en el pasado, el caso
común en un ciclo de 5 min) SÍ cuentan; los `processing` SÍ
cuentan (publicarán de forma inminente y su `next_run_at` es
~ahora; además cubren las reservas de cupo sin doble conteo: el
que tiene la reserva ES el job en `processing`).

**B3.2 — Empuje acotado.** Si `P(C) >= tope`: sea `V` el instante
más viejo de la ventana proyectada (el `updated_at` más viejo de
los posted contados, o el `next_run_at` más viejo de los
scheduled/processing contados, el menor de ambos; si solo hay
scheduled, `V` es su `next_run_at` más viejo — aproximación
declarada en §3.8). Nuevo candidato `C' = V + 24 h`. Se
re-proyecta en `C'` (máximo 3 empujes). Convergencia: cada empuje
saca de la ventana al menos al instante más viejo (por la cota
inferior `C-24h`), así que `P` baja al menos en 1 por empuje
salvo que entren jobs programados nuevos entre C y C' — por eso
el tope de 3. Si tras 3 empujes `P` sigue >= tope y `C'` sigue
dentro de hoy, **no se crea el job** (normativo aquí, no solo en
tests). Empujar solo mueve el candidato hacia adelante, así que
no rompe las invariantes del programador: el encadenado (tras
pendientes +90 min) y el escalonado (+15 min entre plataformas
del mismo clip) se verifican de nuevo tras cada empuje con
`max()` y solo pueden mantenerse o alejarse.

**B3.3 — No-derrame (misma regla que el pase del piso §2).** El
no-derrame aplica SOLO al candidato empujado por B3.2: si el
candidato final cae fuera del día de Santiago en curso, **no se
crea el job** para esa plataforma; se devuelve bloqueado con el
motivo rolling (literal exacto en B3.4). El auto-enqueue de
mañana lo crea a su hora. El candidato INICIAL puede caer pasada
la medianoche (última subida + 90–180), como hoy (p. ej. el job
73 de kaez, creado 21:30 para mañana 14:30): eso no cambia. La
diferencia es que el inicial es su hora natural y el empujado es
un «no cabe hoy».

**B3.4 — El gate de creación usa el conteo proyectado (con
reordenamiento).** Hoy el gate se evalúa ANTES de calcular el
candidato (`jobs.ts:885-1005`). Para que reciba `P(C)` hay que
reordenar, por plataforma: (1) calcular el candidato C con la
lógica actual (última subida + azar, encadenado, escalonado);
(2) proyectar y empujar (B3.1–B3.2) hasta C'; (3) si C' cae fuera
de hoy, bloqueado rolling sin evaluar el gate; (4) si no, evaluar
el gate con `refMs = C'`: los inputs de día (`publishedToday`,
`lastPosted`, calentamiento, día libre) son los de C' — como C'
está dentro de hoy (paso 3), coinciden con los de ahora salvo el
rolling, que es `P(C')`. El literal del motivo de bloqueo rolling
es exacto (para que case con el regex de `GATE_BLOCKS`):
`tope rolling 24 h alcanzado: {P}/{tope} en {platform} (últimas 24 h)`,
con `P` = conteo proyectado. El evento de Tracking que lo
acompaña anota el desglose `(proyectado: X posted + Y programados)`
en texto libre (el motivo en sí no puede llevar sufijo: el regex
es anclado). El re-chequeo de ejecución (`recheckPublishGate`,
lógica inline `gate.ts:618`) no cambia: en ejecución solo hay
subidas reales y el conteo posted es el correcto ahí. Manual
inmediato: `C = ahora`, la proyección aplica igual. Manual
programado (`scheduledForMs` dado): `C = effectiveScheduledForMs`,
la proyección aplica igual; si empuja, la hora pedida solo se
mueve hacia adelante (patrón ya existente en `jobs.ts:820-840`)
y el `scheduledForMs` devuelto refleja la hora final.

**B3.5 — Tests (`test/auto-enqueue.test.ts`, con su FakeD1
ampliado).** El mock debe soportar las consultas nuevas de la
proyección (conteo por rango de `next_run_at` con `state IN
('scheduled','processing')`, `MIN(updated_at)`/`MIN(next_run_at)`
sobre conjuntos filtrados, y el NULL como «ahora»). Casos:
- Ventana llena por programados: 2 posted + 1 scheduled antes
  de C, tope 3 → el candidato se empuja a `V+24h` o no se crea si
  cae fuera de hoy. Fail-before: se crea para C (y rebotaría en
  ejecución).
- 3 empujes sin liberar dentro de hoy → no se crea (normativo
  §4.3 B3.2, no solo test).
- Sin regresión: con ventana libre, el candidato y el encadenado
  son idénticos a hoy.
- El re-chequeo de ejecución no cambia (tests existentes).

**B3.6 — El mismo defecto, en el tope diario.** `publishedToday`
en creación también cuenta solo lo ya publicado. Se proyecta
igual: `proyectadoHoy(C') = #{posted con posted_day = día(C')}
+ #{scheduled/processing con next_run_at en (ahora, C']}`. Si
`proyectadoHoy >= max` (techo duro) o `>= plan` (plan cubierto),
no se crea el job para esa plataforma, con el literal exacto del
gate (`tope diario alcanzado: {P}/{max} en {platform}` o `plan
del día cubierto: {P}/{plan} en {platform} (techo {max})`). El
tope diario se libera mañana, no en 24 h: no hay empuje, solo
no-creación. Misma maquinaria de proyección que B3.1 (una sola
lectura puede devolver ambos conteos).

**Residual conocido (no se arregla aquí, se documenta):** en
ejecución, un hermano en `processing` que aún no marcó posted no
cuenta para el rolling del re-chequeo (solo posted). Si ese
hermano tarda y el candidato publica antes, el tope rolling se
puede pasar en 1 ese día. Acotado por el detector de atascados
(30 min) y raro (subidas multi-paso largas). Candidato a
follow-up, no a este arreglo. (La proyección de creación SÍ
cuenta los `processing`, §4.3 B3.1: el hueco es solo en el
re-chequeo.)

### §4.4 B4 — Drafts viejos: salto en el escaneo + limpieza única

**B4.1 — Salto en el escaneo (código).** Al inicio de `attemptClip`
y de `attemptClipForChannel` (`jobs.ts:1295-1333`, `1464-1494`),
ANTES de la siembra A4, del chequeo A4 y de calcular `need`: si
`qualityGateForClip(clip).hard === true` (veredicto `rechazado`,
`quality-gate.ts:152-160`), se devuelve `false` **sin consumir
`considered`/`evaluated`, sin registrar intento A4 y sin dejar
evento** (el motivo ya quedó en Tracking cuando se validó). Es un
estado decidido, no un fallo reintentable — por eso deja de
contar para A4 (antes cada intento fallido duro sí registraba
intento; ahora no hay intento que registrar). Los `hard === false`
(sin veredicto, pendiente) NO se saltan: siguen su camino actual.
`scanned` NO se ahorra en el bucle principal (el `scanned++`
ocurre antes de llamar a `attemptClip`, `jobs.ts:1336`); en el
pase del piso sí se ahorra cuando el salto evita calcular `need`.
El salto ocurre antes de la contabilidad de `qualityBlocked`, así
que los rechazados dejan de contar para la alerta del 30% de
descartes por calidad: la alerta pasa a medir solo clips
evaluados de nuevo (su propósito — cazar una tanda mala de
validación — se conserva). Si el veredicto cambia (re-render),
el clip re-entra solo: el salto se evalúa en cada ciclo, no se
marca nada persistente. El mecanismo existe: `enqueuePublishJobs`
(`jobs.ts:589-597`) re-encola un job en `draft`/`failed` en vez
de crear duplicados.

**B4.2 — Limpieza única (D1, paso del despliegue, no código).**
Solo tenant sebastian, solo jobs en `draft` (los ya `failed` son
terminales y se dejan como están), solo de clips con veredicto
rechazado (39, 62, 77, 83). `UPDATE` directo (no `markFailed`:
no hay notificación ni cambio de estado del clip que hacer por
filas de hace 7 días; el clip queda intacto en `en_carpeta`):

```sql
-- Previa obligatoria (ver el qué antes del cambio):
SELECT id, clip_id, platform, state FROM jobs
WHERE tenant_id = 'sebastian' AND clip_id IN (39,62,77,83) AND state = 'draft';
-- Cambio:
UPDATE jobs SET state = 'failed',
  last_error = 'aparcado 2026-11-27: clip con veredicto rechazado; sin reintento automático',
  retryable = 0, updated_at = <ahora_ms>
WHERE tenant_id = 'sebastian' AND clip_id IN (39,62,77,83) AND state = 'draft';
-- Verificación: re-consultar; 0 drafts de esos clips.
```

**No se tocan** los drafts de 169/171 (sin veredicto; trabajo
pendiente legítimo de TikTok) ni ningún clip sin veredicto. No se
borra ningún clip ni se archiva ninguno.

**B4.3 — Test (`test/auto-enqueue.test.ts`):** clip con veredicto
rechazado en la Cola → el ciclo no consume `considered` ni
`evaluated` en él y no deja evento de freno; clip sin veredicto
sigue igual que hoy. Fail-before (corregido): hoy el rechazado
consume 1 `considered` por ciclo en los primeros ciclos del día
hasta que A4 lo aparca (3 intentos/día); con el arreglo, 0 desde
el primer ciclo.

## §5. Qué NO cambia (normativo)

- **B5 (layout / sin-webcam, clips 253-256):** fuera. Otro chat. Ni
  una línea, ni un despliegue encima.
- **Reglas vigentes:** sin franjas; aleatorio 1:30–3 h; todos
  publicados en 24 h. Topes diarios (YT 6, TT/IG/FB 3), rolling 24 h
  (YT 6, resto 3), calentamiento, plan del día y piso 3/día (SPEC
  v2, desplegado 2026-11-26 21:38) **no se tocan ni se relajan**.
- **`gate.ts:409`:** la clasificación del viejo «1 por plataforma y
  franja» se deja como está (transitorio → `min90`). No aplica
  ninguna franja; solo deja revivir jobs viejos frenados por ella.
- **F0–F6 (SPEC 2026-11-24):** no se reescriben como sistema. B2
  ajusta el conteo de intentos DENTRO del camino de reintento F2 y
  la firma que usa el barrido F6 (con su adaptación especificada
  en §6); B3 ajusta el programador; B4 ajusta el presupuesto F4.
  Los invariantes se mantienen: reintento transitorio en vez de
  draft, barrido de drafts de hoy, freno A4 por clip, presupuestos
  5/10/60.
- **Topes:** el freno A4 por clip (3 intentos fallidos/día) se
  mantiene. El tope `retriesToday` por job (3/día) se mantiene
  como válvula para fallos reales; lo que cambia es qué cuenta
  para él (las esperas ya no cuentan, §4.2).
- **Clips 11, 91, 93, 102, 117:** no se tocan salvo verificar
  (§8.8: sus jobs no cambian por este arreglo).
- **Sin nuevas tablas, sin nuevos crons, sin nuevos ajustes.**
  El usuario no configura nada. $0.
- **Sin cambios en el panel, en el cerebro (GitHub Actions) ni en
  los adaptadores de publicación.** Todo el cambio vive en el
  Worker (`panel-backend/worker/src/`).

## §6. Cambios de interfaz (resumen para implementar)

| Función | Cambio |
|---|---|
| `recheckPublishGate(env, job)` (`gate.ts:618`) | Devuelve `{ reason: string \| null, lastPublishedAtMs: number \| null }` en vez de `string \| null`. Llamadores: `publish/index.ts:211` (desestructura ambos; `gateReason` sigue siendo string para `classifyGateBlock` y el `mode: "retry"`); `jobs.ts:1701` (barrido F6: `const r = await recheckPublishGate(env, job).catch(() => ({ reason: "sweep: re-chequeo no disponible", lastPublishedAtMs: null })); if (r.reason) continue;` — SIN esta adaptación el barrido se rompe (un objeto siempre es truthy). |
| `retryAtForGateBlock(env, job, reason, now, rng?, lastPublishedAtMs?)` (`gate.ts`) | Parámetro opcional nuevo. Casos `min90`/`spacing` lo usan (reloj del gate); `cross15` mantiene su consulta propia (mismo clip, otra plataforma); `rolling`/`tomorrow`/`backoff` sin cambio de reloj. Garantiza `retryAtMs > now` con la tabla B2.3. El llamador `publish/index.ts:226` (reserva) lo llama SIN el parámetro (fallback actual + invariante). Actualizar el docstring (`gate.ts:454-463`) con la distinción piso/contaje de §4.2. |
| `applyPublishResult(env, db, job, r)` (`jobs.ts:2176`, caso `"retry"` ~2203-2227; **exportar para tests**) | El kind se deriva con `classifyGateBlock(r.reason).retry`; `null` → `backoff` (consume + evento). Para kinds de espera se escribe `attempts = retriesToday(job, todayKey)` (SIN +1); para `backoff`, `attempts = retriesToday + 1` (como hoy). La válvula a draft se conserva con el mensaje nuevo (§4.2). |
| `tryEnqueueClipPublish` (`jobs.ts:702-1010`) | Proyección B3.1–B3.6 antes del gate de creación; reordenamiento candidato-antes-que-gate (§4.3 B3.4); el gate recibe el conteo proyectado. |
| `attemptClip` / `attemptClipForChannel` (`jobs.ts:1295-1333`, `1464-1494`) | Salto B4.1 al inicio (antes de siembra A4, chequeo A4 y `need`). |
| `retry.ts` (comentario) | Actualizar la cabecera: la siembra A4 es por `auto_enqueue_attempts` (0045), no por `clip_events`. Sin cambio funcional. |

## §7. Tests (antes de desplegar)

Runner: **`bun test test/`** (según `package.json`; NO vitest).
Cada test nuevo se escribe **fail-before / pass-after**. El
fail-before se corre en un **worktree limpio en HEAD** (`git
worktree add /tmp/fb-<fecha> HEAD`), NUNCA con `git stash` sobre
el árbol sucio (hay trabajo ajeno sin commitear; stashearlo es el
riesgo que §9 prohíbe). El worktree se elimina después.

- **Antes de implementar:** correr la suite Worker en HEAD limpio
  y registrar la lista de fallos preexistentes POR NOMBRE. Tras el
  cambio, esa lista debe ser idéntica; cualquier fallo nuevo
  bloquea el despliegue. (La cifra «9 fallos» de deploys
  anteriores no se cita como número: lo que manda es la lista.)
- `test/retry-a4.test.ts` (harness SQLite ampliado con
  `publish_slots`, `settings`, `api_accounts`): B1.2 (solo la mitad
  nueva), B2.4 (espera no consume; backoff/atascado sí; invariante
  por kind), B2.5 (evento de espera larga, una vez por job y día).
- `test/gate-blocks.test.ts` (solo puro, sin D1): el literal del
  motivo B3 casa con el regex de `GATE_BLOCKS`; clasificación de
  `tomorrow`/`backoff` sin cambio.
- `test/auto-enqueue.test.ts` (FakeD1 ampliado con las consultas
  de proyección: rango de `next_run_at`, `MIN` filtrados, NULL
  como «ahora»): B3.5 (proyección, empuje, no-derrame, no-creación
  tras 3 empujes), B3.6 (tope diario proyectado), B4.3 (rechazado
  no consume; sin veredicto igual que hoy).
- Existentes sin tocar: `test/publish-rules-2026-11-19.test.ts`,
  `test/quality-gate.test.ts`, `test/cycle-gate.test.ts`,
  `test/manual-publish.test.ts`. `tsc` limpio.
- Python: suite completa; este arreglo no toca Python, se corre y
  se registra igual (o idéntica a la base).

## §8. Verificación en vivo (después de desplegar, antes de darlo por hecho)

1. `/api/health` ok en ambos Workers; `worker_running=true`.
2. Schedules por API: avc 4 crons (`*/5 15-18`, `*/5 21-23,0-4`,
   `0 12,13`, `0 6,7`), avc-kaez 2 (`*/5 15-18`, `*/5 21-23,0-4`),
   avc-sebastian ninguno. Un deploy nocron los borra: verificar
   SIEMPRE tras desplegar.
3. B1: `auto_enqueue_attempts` con filas del día en ambas D1. A
   las 14:30 (primera revisión) hay jobs con `created_at` de hoy
   en seba (el despacho 09:00/10:00 y el ciclo 12:00–15:00 ya
   corrieron): el atasque de ayer (0 jobs nuevos hasta las 15:00)
   no se repite.
4. B2: un job frenado por piso de 90 min sigue en `scheduled` con
   `attempts` sin subir tras 3+ ciclos. Su `next_run_at` es
   posterior a la última subida + 90 min (caso `min90`) o a la
   última subida del clip en otra plataforma + 15 min (caso
   `cross15`, distinto reloj). Ningún job muere en draft por
   «menos de 90 min» con tope de reintentos ese día, SALVO que sus
   intentos ya estuvieran agotados por fallos reales (el motivo
   del draft lo indica: «agotado por fallos previos»).
5. B3: ningún job nacido ese día rebota en ejecución por rolling,
   SALVO el residual documentado (hermano en `processing` que
   publica tarde: como mucho 1 de más, y el rebote lleva
   `next_run_at` = más viejo + 24 h, sin consumir intento). Un job
   programado para una plataforma con ventana llena no se crea
   para hoy (o nace con `next_run_at` dentro de hoy tras el
   empuje).
6. B4: tras el deploy, no hay eventos nuevos de freno para los
   clips 39/62/77/83 en `clip_events` ni `worker_events` (antes
   los había a diario, §2/Anexo A); sus jobs viejos quedan en
   `failed` con el motivo de aparcado (re-consulta §4.4); 169/171
   siguen pendientes de TikTok sin tocar.
7. Cierre del día siguiente: 38 (23 seba + 15 kaez, meta del día)
   o déficit explicado por inventario real (clips retenidos por
   layout u otro motivo documentado), no por B1–B4. Si vuelve a
   cerrar corto con inventario, esta spec no cumplió y se reabre.
8. Clips 11, 91, 93, 102, 117: sus jobs no cambian por este
   arreglo (comparar `SELECT` antes/después del deploy).

## §9. Despliegue y commit

1. Solo tras el sí del usuario a esta spec v2.
2. Implementar en `panel-backend/worker/src/` (+ tests, §7).
   Antes de commitear: `git status` + `git diff` por archivo;
   commitear SOLO los archivos de este arreglo. El árbol tiene
   trabajo sin commitear de OTROS chats (layout, sin-webcam,
   compilación, dispatch CircleCI): nunca incluir hunks ajenos.
   Si el arreglo toca un archivo que otro chat también toca,
   separar por hunk, no por archivo.
3. Desplegar SOLO con `~/workspace/gta6-clips/wr_v2.py seba|kaez`,
   primero seba y luego kaez (verificar §8.1–8.2 tras cada uno
   antes de seguir). Nunca desplegar avc sin `TENANT` en el
   config. Kaez SIEMPRE con `wrangler.kaez.toml` SIN `[triggers]`
   (preserva sus crons). Nunca desplegar desde `independencia/`;
   sincronizar los archivos cambiados a `independencia/` después
   (`cp` + `diff -q`).
4. Ejecutar la limpieza B4.2 (con su SELECT previo y su
   re-consulta), tras el deploy.
5. Verificación §8.3–8.8. Si algún punto falla: rollback
   redeplegando las versiones anteriores conocidas (seba avc
   `7f938de1`, kaez avc-kaez `f13dbaf3`, commit `3d0104a` v5.7.0,
   desplegadas 2026-11-26 23:00 — fuente: registro de despliegue
   de esa noche) y se reabre la spec.
6. Commit + push a `origin/main`. Mensaje: `fix(publicación):
   cuellos de botella B1-B4 (spec 2026-11-27)`.

## §10. Decisiones tomadas (resueltas en la spec; el usuario puede revocarlas al dar el sí)

1. **Q1 — ¿la espera `tomorrow` consume intentos?** Resuelto: NO
   consume, por uniformidad con el resto de esperas. Efecto
   práctico casi nulo: al reprogramarse a mañana 00:00,
   `retriesToday` se resetea con el cambio de día. Si el usuario
   prefiere que el tope diario sí cuente, se cambia una línea
   (`tomorrow` pasa al grupo que consume).
2. **Q2 — ¿el empuje B3 puede crear el job para mañana?**
   Resuelto: NO (no-derrame, igual que el pase del piso). Si el
   candidato empujado cae fuera de hoy, no se crea; mañana el
   ciclo lo crea. Si el usuario prefiere que nazca ya programado
   para mañana, se cambia B3.3.
3. **Q3 — ¿aparcar también los drafts de 169/171?** Resuelto: NO
   (sin veredicto; trabajo pendiente legítimo de TikTok).

## §11. Fuera de alcance (aparcado, no se hace aquí)

- B5 (layout / sin-webcam, clips 253-256): otro chat.
- El residual de B3 (hermano en `processing` que no cuenta para el
  rolling en ejecución): documentado en §4.3, candidato a follow-up.
- Subir topes, relajar el piso de 90 min, cambiar el azar 90–180 min
  o el ciclo de 5 min: no se propone; las reglas vigentes se
  mantienen.
- Quitar el tope de 3 reintentos/día para fallos reales: no se toca.
- Cambios en el panel, el cerebro o los adaptadores de publicación.

## Anexo A — Evidencia D1 (consultas 2026-11-27 00:00–01:30, hora Chile)

- Cierre 26-nov: `SELECT … WHERE state='posted' AND posted_day='2026-11-26'` →
  seba 13 (238 TT 00:51, 238 IG 12:21, 238 FB 13:11, 237 IG 14:30,
  242 YT 15:06, 215 TT 15:10, 202 FB 15:25, 238 YT 18:27, 190 YT
  20:05, 215 YT 20:12, 249 YT 22:11, 249 TT 22:56, 209 FB 23:17);
  kaez 13 (33 FB 13:11, 9 TT 13:15, 62 YT 14:31, 75 YT 18:06,
  51 FB 18:11, 67 TT 18:21, 51 IG 18:31, 35 IG 20:11, 66 YT 20:15,
  78 YT 22:05, 78 IG 22:30, 75 TT 22:51, 67 YT 23:45).
- B1: `SELECT day, COUNT(*), SUM(attempts) FROM auto_enqueue_attempts
  GROUP BY day` → seba 26-nov 41/137, 27-nov 10/22; kaez 26-nov
  13/39, 27-nov 3/12.
- B2: job 159 seba (211 YT): creado 19:55:48, actualizado 23:11:02,
  attempts=3, `last_error` «menos de 90 min… (tope de reintentos por
  hoy: 3/día)», `next_run_at` 23:16:02. Jobs 52/45 kaez (57/35 YT):
  attempts=3, mismo motivo. YT posted seba 26-nov: 242 15:06, 238
  18:27, 190 20:05, 215 20:12, 249 22:11.
- B3: job 167 seba (249 FB) draft por rolling 3/3; job 71 kaez
  (84 IG) rebotó 23:44 por rolling, programado mañana 18:31; job 21
  kaez (5 FB) draft por rolling; jobs 46/50/76 IG kaez draft por
  rolling.
- B4: jobs 39/62/77/83/169/171 (ver §2); `clip_events` 26-nov:
  clip 62 frenado 19:55 (4 plataformas), 39 frenado 20:00 (4),
  77 frenado 22:11 (4), motivo «sin subtítulos completos: necesita
  transcripción humana». Cola seba: 234 clips
  (`archived=0 AND status IN ('en_carpeta','aprobado','programado')`),
  214 LIVE o con `manual_rank`.

---

## Registro de revisión Goldfish (v1 → v2)

Dos revisiones con solo la spec v1 + archivos referenciados. Cada
hallazgo: **Aplicado** (la spec cambió), **Rechazado** (con motivo y
evidencia) o **Aclarado**. Los duplicados entre ambas revisiones se
resuelven una vez.

### Goldfish B (B1+B4 y coherencia; H1–H36)

- **H1 (D1 sin fuente en los documentos) — Aplicado.** Anexo A con
  las filas y consultas exactas.
- **H2 (crons `0 12,13`/`0 6,7` sin definir) — Aplicado.** §3.2
  define los 4 crons de avc y los 2 de kaez.
- **H3 (reloj de `cross15`) — Aplicado.** §4.2 B2.1 separa
  `cross15` (reloj propio, sin cambio).
- **H4 (`slot.updated_at` = hora de liberación, no de reserva) —
  Aplicado.** §3.7 lo declara (última mutación) y §4.2 lo trata
  como cota conservadora con auto-corrección en un ciclo.
- **H5 (empuje sin demostrar convergencia) — Aplicado.** §4.3 B3.2
  con argumento de convergencia (cada empuje saca al menos al más
  viejo) y tope de 3.
- **H6 (mecanismo de re-render tras aparcar) — Aplicado.** §4.4
  cita `enqueuePublishJobs` (`jobs.ts:589-597`): re-encola un
  `draft`/`failed` en vez de duplicar.
- **H7 (espera sin cota) — Aplicado como B2.5** (alerta, no muerte:
  la espera indefinida bajo presión de cola es legítima y `dueJobs`
  da prioridad FIFO a los viejos).
- **H8 (segundo llamador `retryAtForGateBlock`) — Aplicado.**
  `publish/index.ts:226` llama sin el parámetro nuevo (§6).
- **H9 (cambio de firma rompe F6) — Aplicado.** Adaptación exacta
  del barrido en §6.
- **H10 (nombre `applyDispatchResult` inexistente) — Aplicado.**
  Es `applyPublishResult` (`jobs.ts:2176`), no exportada; §6 la
  exporta para tests.
- **H11 (escritura de `attempts` ambigua) — Aplicado.** Se escribe
  `attempts = retriesToday(job, todayKey)` (normalización, §6).
- **H12 (válvula vs «no puede morir por esperar») — Aplicado.**
  Válvula solo para intentos consumidos por fallos reales, con
  mensaje nuevo que lo dice; §8.4 con su excepción.
- **H13 (fallback único 90–180) — Aplicado.** Tabla por kind B2.3.
- **H14 (fórmula P(C) defectuosa) — Aplicado.** Fórmula corregida
  con cota inferior `C-24h`, vencidos y `processing` (§4.3 B3.1).
- **H15 (gate antes que candidato) — Aplicado.** Reordenamiento
  especificado en §4.3 B3.4.
- **H16 (ámbito del no-derrame) — Aplicado.** Solo al candidato
  empujado; el inicial puede caer mañana como hoy (§4.3 B3.3).
- **H17 (tope diario no proyectado) — Aplicado.** Nueva B3.6.
- **H18 (`scanned` no se ahorra) — Aplicado.** §4.4 lo dice
  exacto (ahorra `considered`/`evaluated`, no `scanned` del bucle
  principal) y fija la posición del salto.
- **H19 (fail-before B4.3 inexacto) — Aplicado.** Reescrito: consume
  hasta que A4 lo aparca (3/día), no indefinidamente.
- **H20 (B4.2 sin SQL) — Aplicado.** SQL exacto, ámbito seba,
  SELECT previo, solo `draft`→`failed`, UPDATE directo justificado.
- **H21 («no se reescribe F0–F6» falso) — Aplicado.** §5 reescrito
  (ajustes dentro de F2/F4/F6, no reescritura) y §3.6 distingue los
  dos topes «3».
- **H22 (citas de líneas erróneas) — RECHAZADO con evidencia.**
  Verificado por `grep` 2026-11-27: el comentario «SIN FRANJAS»
  está en `gate.ts:295-297` (la spec decía 296-300, correcto) y el
  regex «1 por plataforma y franja» está en `gate.ts:409` (la spec
  decía 409, correcto; el «tope de reintentos por hoy» es la
  línea 395). La afirmación del revisor era incorrecta en ambos
  puntos.
- **H23 (runner «vitest» mal) — Aplicado.** Es `bun test test/`.
- **H24 (`git stash` contra árbol sucio) — Aplicado.** Fail-before
  en worktree limpio en HEAD (§7).
- **H25 (clips 11/91/93/102/117, «verificar» sin definir) —
  Aplicado.** §8.8: comparar sus jobs antes/después.
- **H26 (Q1/Q2 abiertas) — Aplicado.** Resueltas en §10 (el
  usuario puede revocarlas al dar el sí).
- **H27 (F6 roto) — Aplicado** (igual que H9).
- **H28 (sobre-espera por `slot.updated_at`) — Aplicado.** §4.2:
  cota conservadora, auto-corrección en un ciclo, liberación
  devuelve el reloj a la subida real.
- **H29 (B3 que nunca crea) — Aplicado.** Con la fórmula
  corregida cada empuje libera ≥1 plaza; no-crear solo cuando la
  ventana está genuinamente llena.
- **H30 (jobs esperando para siempre) — Aplicado** (igual que H7,
  B2.5).
- **H31 (B1.2 duplica tests existentes) — Aplicado.** Solo la mitad
  nueva (siembra tras limpieza por éxito).
- **H32 (B4.2 manual sin SQL) — Aplicado** (igual que H20).
- **H33 (§9 ambiguo) — Aplicado.** Pasos numerados, orden
  seba→kaez, sync a `independencia/`, criterio de rollback y
  fuente de las versiones (registro de despliegue 2026-11-26 23:00).
- **H34 (§8 no observable) — Aplicado.** §8.3–8.7 reescritos con
  criterios comprobables.
- **H35 (B4 «~0 presupuesto» sin sostén) — Aplicado.** Afirmación
  retirada: los clips 39/62/77/83 son `origin='live'` y SÍ se
  evalúan (eventos 26-nov, Anexo A). B4.1 cobra más valor, no menos.
- **H36 (cerrar Q1/Q2, citas, fail-before) — Aplicado.**

### Goldfish A (B2+B3; G1–G22)

- **G1 (nombre inexistente + no exportada) — Aplicado** (igual que
  H10).
- **G2 (firma rompe F6) — Aplicado** (igual que H9).
- **G3 (`buildGateContext` no existe) — Aplicado.** Citas
  corregidas a la lógica inline en `recheckPublishGate`
  (`gate.ts:618`).
- **G4 (`slot.updated_at` no es hora de reserva) — Aplicado**
  (igual que H4).
- **G5 (docstring dice lo contrario) — Aplicado.** §4.2 rebate
  la decisión deliberada (piso vs contaje) y §6 actualiza el
  docstring.
- **G6 (`cross15` con reloj de plataforma) — Aplicado** (igual que
  H3).
- **G7 (camino de reserva sin instante) — Aplicado** (igual que H8).
- **G8 (origen del kind sin especificar) — Aplicado.** Se deriva
  con `classifyGateBlock(r.reason).retry`; `null` → `backoff` +
  evento.
- **G9 («sin tocar attempts» ambiguo) — Aplicado** (igual que H11).
- **G10 (válvula contradice el titular) — Aplicado** (igual que H12).
- **G11 (fallback único incorrecto por kind) — Aplicado** (igual
  que H13; tabla B2.3).
- **G12 (`tomorrow` casi inocua) — Aplicado.** Notado en §4.2 y
  Q1 resuelta en §10.
- **G13 (esperas infinitas) — Aplicado** (igual que H7; B2.5).
- **G14 (tests B2.4 no escribibles como asignados) — Aplicado.**
  Harness ampliado en `retry-a4.test.ts`, exportación de
  `applyPublishResult`, y nota de que el test de reserva prueba la
  aritmética (el vivo lo cubre §8.4).
- **G15 (gate antes que candidato) — Aplicado** (igual que H15).
- **G16 (bordes/estados de P(C)) — Aplicado.** Intervalo con cota
  inferior, vencidos, NULL, `processing`, filtro por tenant
  (§4.3 B3.1).
- **G17 («programado = publicará») — Aplicado.** Supuesto
  declarado en §3.8; `processing` contado; residual documentado.
- **G18 (3 empujes agotados sin norma; literal del motivo) —
  Aplicado.** No-creación normativa en §4.3 B3.2 y literales
  exactos en §4.3 B3.4/B3.6.
- **G19 (`V` usa `next_run_at` como hora de publicación) —
  Aplicado.** Aproximación declarada (§3.8) con su error acotado.
- **G20 (manual/programado sin norma) — Aplicado.** §4.3 B3.4
  cubre los tres caminos (automático, manual inmediato, manual
  programado).
- **G21 (invariantes sin demostrar; mensaje del gate) — Aplicado.**
  Demostración por `max()` tras cada empuje; desglose
  posted/programados en el evento de Tracking, no en el motivo.
- **G22 (tests B3 vs harnesses) — Aplicado.** B3 en
  `auto-enqueue.test.ts` con FakeD1 ampliado (consultas listadas);
  `gate-blocks.test.ts` solo para lo puro (literal del motivo).

### Hallazgos sin resolver

Ninguno. Los 58 hallazgos (36 H + 22 G, con duplicados) están
aplicados o rechazados con motivo arriba. Queda pendiente el sí
del usuario a esta v2; si revoca alguna decisión de §10, se
registra aquí y se saca una v3 solo de esa parte.
