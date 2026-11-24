# SPEC — Validadores anti falsos positivos (2026-11-23)

Pedido del usuario (20:54, iOS): crear validadores para todo lo que estamos
haciendo ahora, para que no haya falsos positivos.

## Hechos verificados que motivan esto (no suposiciones)

1. Panel "Worker: parado" fue falsa alarma el 2026-11-22: el Worker devolvía
   `worker_running` booleano y el panel hacía `Object.values(true).some(...)`
   = `[]` = siempre falso. Verificar D1 y /api/health, no el texto del panel.
2. `engagement_enabled` guardado como `1` en D1 se leía como apagado porque
   db.ts exigía `=== true` (arreglado commit cab9644).
3. Cuentas muestra el texto enlatado "(scope youtube.upload, renovado con
   refresh_token)" aunque la re-autorización 20:37-20:38 concedió también
   youtube.readonly + youtube.force-ssl. La sonda 20:40 sí dijo OK.
4. TikTok EN: el banner "Verificar ahora" se desvaneció sin leerse; el
   timestamp guardado "verificado 18-11" no se refresca con la verificación.
5. La sonda de engagement tiene caché de 1 hora (PROBE_CACHE_MS = 3600_000):
   tras un arreglo, el veredicto viejo sigue vigente hasta forzarla o esperar.
6. La sonda no da veredicto para TikTok (solo "vía zernio"); el estado real
   está en Cuentas / "Verificar ahora", no en la sonda.
7. La pasada de engagement 20:03 dio 0/0 sin explicar nada: el panel no
   cuenta "comentarios encontrados" por plataforma, así que un 0 no se puede
   distinguir de "no había comentarios" vs "la sonda falló" vs "no se buscó".
8. El caption público llevaba `[clip:168]` desde 2026-11-20 (arreglado commit
   51afff2 quitando la marca en 4 puntos de salida); ningún validador lo
   detectó en 3 días.
9. El validador de clips tiene falsos positivos conocidos: rechaza título
   presente como texto amarillo sobre negro (espera fondo amarillo) y
   subtítulos blancos presentes con palabras cortas no medidas (clips 170,
   174). Un "rechazado" no se guarda en D1 sin revisar los fotogramas.
10. Deploy sin TENANT silenció avc por completo (2026-11-22 14:41); deploy
    nocron borra los crons de franja. Ambos se detectan solo verificando
    /api/health y GET schedules después del deploy.

## Alcance: qué se valida (6 frentes, nada más)

- **V1. Sonda de engagement:** cada veredicto (OK / sin_permiso /
  sin_sonda / sin_conexion / zernio) debe probarse contra la evidencia real
  que lo produce, no contra el texto que muestra el panel.
  - OK de YouTube solo vale si tokeninfo (o la respuesta del probe)
    lista los 3 scopes; un OK con scopes incompletos es FALSO POSITIVO.
  - sin_permiso debe nombrar el scope exacto que falta.
  - sin_sonda solo vale si META_APP_ID/SECRET están ausentes o debug_token
    falla; si las credenciales existen y debug_token responde, sin_sonda es
    FALSO POSITIVO.
  - El probe debe indicar si el resultado viene de caché o es fresco, y su
    edad; un veredicto cacheado tras un cambio de credenciales no vale
    como confirmación.
  - TikTok: la sonda debe devolver un veredicto propio (o declarar
    explícitamente "sin veredicto, ver Cuentas"), nunca dejar la casilla
    vacía ni implicar OK.
- **V2. Estado de conexión en Cuentas:** "Conectada" solo vale si la última
  verificación real (verify_ok + verify_message + verified_at) es posterior
  al último cambio de credencial; un timestamp viejo con credencial nueva es
  FALSO POSITIVO. El banner de "Verificar ahora" debe persistir (no
  desvanecerse sin dejar registro) y quedar guardado en el evento.
- **V3. Pasada de engagement:** cada pasada registra por plataforma+idioma:
  comentarios encontrados, respuestas enviadas, likes dados, omitidos con
  motivo exacto. Un 0/0 con 0 comentarios encontrados es "sin trabajo", no
  "fallo"; un 0/0 con comentarios encontrados y 0 acciones es fallo y debe
  decir por qué. Sin ese conteo, el 0 no se puede interpretar.
- **V4. Caption público:** validador que, antes de publicar y en un chequeo
  de lo ya publicado, rechaza cualquier caption que contenga `[clip:<id>]`
  o cualquier marca de idempotencia. Prueba de regresión con el caso 168.
- **V5. Evolution dry-run:** el 409 "ya publicado en esa plataforma" debe
  probarse con un clip realmente publicado y con uno no publicado (el segundo
  no debe dar 409 = falso positivo de bloqueo). `isDistinctCut` debe
  probarse en el borde: cambio de 1s en inicio/fin = distinto; solo cambio
  de título/subtítulos = NO distinto.
- **V6. Deploy / salud:** tras cada deploy, verificación automática en vivo:
  /api/health devuelve tenant correcto y worker_running true; GET schedules
  devuelve los crons de franja; si falta TENANT o faltan crons, el deploy se
  marca fallido aunque wrangler diga éxito. (Ya es práctica manual; aquí se
  convierte en chequeo con veredicto, no en texto libre.)

## Fuera de alcance

- Cambiar caps, slots, reglas de publicación, ni la lógica de engagement o
  evolución. Solo validadores y sus tests.
- Tocar avc-sebastian.
- Publicar clips reales, correr una pasada real de engagement, ni re-autorizar
  cuentas (eso es trabajo del usuario / su navegador).
- No pedir contraseñas, tokens ni secretos en chat. Los secretos se leen
  desde el Worker / vault como hoy.

## Criterio de éxito (verificado en vivo, no solo tests en verde)

- Suite de validadores nueva en verde; suite completa sin nuevos fallos.
- Cada falso positivo de la lista de hechos (1-10) tiene al menos un test que
  lo reproduce ANTES del arreglo y lo atrapa DESPUÉS (test rojo → verde).
- La sonda 20:53 (IG ES OK con nota, FB ES sin_permiso pages_manage_engagement,
  YT ES+EN OK) se reproduce con los validadores y da los mismos veredictos
  con su evidencia citada, no con texto del panel.
- Un deploy de prueba (o el siguiente deploy real) pasa V6 o se marca fallido
  con el motivo exacto.

## Orden de trabajo

1. V1 + V2 primero (son lo que acabamos de conectar: YT, Meta, TikTok).
2. V3 (explica el 0/0 de la pasada).
3. V4 + V5 (regresiones de fixes ya desplegados).
4. V6 (chequeo de deploy).

## Resolución Goldfish (revisión 20:57, veredicto: NO aprueba la spec tal cual)

El pez dorado encontró errores reales de descripción y frentes que no son
"solos validadores". Se resuelve así (nada de esto se implementa hasta que el
usuario apruebe la spec corregida):

1. **V1 taxonomía Meta (hallazgo 1):** `sin_sonda` solo vale si APP_ID/SECRET
   ausentes (`!metaConfigured`); debug_token que falla o sin `scopes` es
   `sonda_fallida`, no `sin_sonda`. Test para cada uno.
2. **V1 OK de YouTube (hallazgo 2):** se adopta la definición del código:
   OK = `youtube.force-ssl` presente (`NATIVE_REQUIRED_SCOPES.youtube`).
   La spec original ("los 3 scopes") estaba mal. Cambiar a 3 scopes sería
   cambio de lógica, no validador; queda fuera.
3. **V1 asimetría IG (hallazgo 3):** OK = scope de respuesta
   (`instagram_manage_comments`) presente; el scope de likes afuera faltante
   es nota, no fallo. Inverso también: sin scope de respuesta = sin_permiso
   aunque el de likes esté.
4. **V1 hueco app_id/expiración (hallazgo 4):** el probe hoy no comprueba
   `debug_token.data.app_id` ni `expires_at`. El validador exige que el
   probe lo compruebe (endurecimiento pequeño del probe, incluido aquí) o
   se declara el hueco por escrito. Decisión: se comprueba.
5. **V2 hueco "último cambio de credencial" (hallazgos 5-6):** `api_accounts`
   no tiene ese campo. V2 exige primero migración `credential_changed_at`
   (se actualiza al pegar credencial y en el callback OAuth) + invalidar la
   caché de sonda al cambiar credenciales. Y se corrige el hecho 4: que
   `verified_at` no se refresque cuando nada cambió es DISEÑO (`db.ts:2206`,
   "sin cambio no hay UPDATE"), no falso positivo; el falso positivo es solo
   "verificado hace tiempo con credencial cambiada".
6. **V2 banner (hallazgo 7):** fuera de esta spec. La persistencia del banner
   "Verificar ahora" es frontend (panel), no Worker; va a una spec de panel
   aparte, con su archivo y su tabla de eventos nombrados.
7. **V3 es instrumentación nueva (hallazgos 8-9):** el conteo "comentarios
   encontrados" no existe en ningún lado; crearlo es cambio de código, no
   "solo validadores", y se reconoce como tal. El conteo vive agregado en el
   summary / `engagement_last_status` por plataforma+idioma (no una fila por
   comentario, para no multiplicar escrituras D1). Además V3 exige posts
   consultados vs posts con error de listado, para distinguir "sin
   comentarios" de "no se pudo listar".
8. **V4 enganche y alcance (hallazgos 10-11):** el chequeo se engancha en un
   helper compartido usado por los 4 puntos de salida (no existe hoy: solo
   hay comentarios). El patrón es solo el id propio: `[clip:<id-del-clip>]`;
   un `[clip:123]` de otro clip o citado por el streamer no es la marca del
   sistema. El test de regresión del caso 168 ya existe
   (`wal-idempotency.test.ts:285`); V4 añade el helper en los 4 puntos +
   escaneo report-only de lo ya publicado (sin acción sobre posts viejos:
   solo reportar, editarlos es trabajo del usuario en la app).
9. **V5 fuente de verdad del 409 (hallazgo 12):** la fuente es D1
   `publish_states[platform].state === "publicado"` (no la plataforma ni
   los jobs). Se añade el caso "publicado en D1 pero borrado en plataforma"
   (hoy daría 409 y bloquearía una republicación legítima) y se verifica
   antes de afirmar si el 409 distingue ES/EN (`publish_states` es espejo
   sin idioma según `db.ts:3480` — por confirmar contra el código).
10. **V5 duplicados (hallazgo 13):** `isDistinctCut` en el borde y el 409 ya
    están probados (`evolution.test.ts:184` y `:380`). V5 aporta solo lo
    nuevo: negativo del 409, caso borrado-en-plataforma y dimensión ES/EN.
11. **V6 ejecutor (hallazgo 14):** paso post-deploy en el wrapper `wr_v2.py`
    (corre /api/health + GET schedules y da veredicto; si falta TENANT o
    faltan crons, el deploy se marca fallido). El hecho 1 (panel mostrando
    "parado" con el Worker sano) queda explícitamente FUERA: es render del
    panel, no del Worker; V6 no lo cubre.
12. **V7 nuevo — validador de clips (hallazgo 15):** el hecho 9 no tenía
    frente. V7: casos de regresión 170/174 — fotograma con título amarillo
    sobre negro y subtítulos blancos de palabras cortas deben PASAR.
    Y test de regresión para `engagement_enabled: 1` (hecho 2, hoy sin test
    que lo siembre como `1`).
13. **Criterio rojo→verde (hallazgo 16):** reformulado — para hechos ya
    arreglados (2, 3, 8) el test es de regresión: debe FALLAR si se revierte
    el arreglo (prueba con el fix revertido / mutación). Rojo→verde solo
    para frentes con arreglo pendiente (V3, V6).
14. **Modo por frente (hallazgo 17):** cada frente indica unit con mocks o
    vivo contra el Worker desplegado, y de dónde salen las credenciales
    (CI sin credenciales no puede llamar a tokeninfo/debug_token; lo vivo
    corre con sesión del panel).
15. **Reglas del usuario (hallazgo 18):** sin contradicciones directas. V3
    añade un dato visible en el panel (el conteo) pero es solo lectura, sin
    ajustes nuevos. V5 usa D1 sembrada, nunca una publicación real.

**Veredicto Goldfish tras la resolución: la spec corregida queda lista para
aprobación del usuario. Implementación bloqueada hasta su "sí".**
