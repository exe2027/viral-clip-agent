# SPEC — Quitar franjas, publicar aleatorio 1:30–3 h (v2)

- **Estado: BORRADOR.** v2 = v1 + correcciones de la revisión Goldfish (2026-11-26 15:33). Pendiente: aprobación del usuario → implementar → desplegar. **Nada de esto está implementado ni desplegado.**
- **Orden del usuario (2026-11-26 13:57–13:58, sus palabras):** "No es necesario tener hora asignada! Se tienen que subir cada 3 horas o algo así, habíamos puesto unas reglas antiban" / "No tenemos franjas de publicaciones se publica aleatoriamente nunca 1:30 después de cada uno" / "Uno hora y media" / "Y nunca más de 3 horas" / "Debe ser aleatorio" / "Pero en 24 horas se tienen que publicar todos".
- **Regla final:** NO hay franjas ni hora asignada. La publicación es ALEATORIA: nunca menos de 1:30 h ni más de 3 h entre publicaciones (por cuenta y plataforma), y todo lo del día se publica dentro de 24 h.
- **Motivo (medido 2026-11-26):** con el fix A4 desplegado y verificado (5 clips aparcados 3/3 en ambas D1, 14:48), el escaneo ya no se atasca, pero el código sigue sin crear jobs fuera de franja: 0 jobs nuevos entre las 14:48 y las 15:27, siguiente franja 18:00 Chile. Las franjas son ahora el freno. El respaldo desde el chat publicó 3 (242 YT, 215 TT, 202 FB) pero no sustituye al ciclo.

## 0. Qué se quita (código actual, verificado en vivo 2026-11-26 15:30–15:33)

Repo `viral-clip-agent`, `panel-backend/worker/src/`:

| Pieza | Ubicación | Uso actual |
|---|---|---|
| `PUBLISH_SLOT_WINDOWS` (mañana 12–14, tarde 18–20, noche 21–23, madrugada 00–01, hora Chile) | `db.ts:1113` | Define las franjas |
| `slotWindowContaining` / `isWithinPublishSlot` | `db.ts:1151–1160` | El gate bloquea fuera de franja (solo al encolar; el re-chequeo en ejecución NUNCA comprueba franja — `gate.ts:131`, `checkSlot` solo en `gate.ts:131/319-322` y `jobs.ts:945`) |
| `slotWindowBoundsMs` | `db.ts:1168` | Límites de la franja actual; lo usan `gate.ts:538` (retry `slotEnd`), `gate.ts:728` (contexto del re-chequeo), `gate.ts:921` (`reservePublishSlot`), `jobs.ts:888` (contexto de encolado) |
| `slotWindowsForCount`, `slotTimeFor` (hora determinista por hash FNV-1a cuenta+día ±10 min) | `db.ts:1180–1210` | Asignan la hora de cada publicación del día; `slotTimeFor` también la usa el plan diario (`daily-plan.ts:186`) |
| `nextSlotStartMs` | `db.ts:1239` | Programa `next_run_at` al inicio de la próxima franja |
| `PLATFORM_SCHEDULE` (YT 00/12/14/18/20/22; TT 13/19/22; IG 12/18/21; FB 13/19/22) | `db.ts:1264` | Horas fijas por plataforma; también la usa el retry `tomorrow` (`gate.ts:556-560`) |
| `nextPlatformHourMs` | `db.ts:1283` | El auto-enqueue programa `next_run_at` a la próxima hora de SU plataforma; también base del escalonado F1.3 (`jobs.ts:981-998`) |
| Chequeo de franja en `publishGate` (`checkSlot`, "fuera de franja…") | `gate.ts:319–324` | Frena el encolado fuera de franja |
| "1 por plataforma y franja" (`publishedThisSlot >= 1`) | `gate.ts:327–331` y `reservePublishSlot` (`gate.ts:921-940`, mismo mensaje, mapeado a `slotEnd`) | Como mucho 1 por plataforma en cada franja; la valla de la reserva debe quitarse también, si no seguirá frenando por franja aunque el gate ya no lo haga |
| Retry kinds `slotEnd` / `nextSlot` | `gate.ts:401, ~501–560` | Reprogramación de jobs frenados por franja |
| Validación `scheduled_for` (manual programado rechaza 400 si no cae en franja) | `index.ts:3416`; `scheduledForMs` fluye a `jobs.ts:998` como `nextRunAt` directo; `jobs.ts:889-908` cuenta los scheduled dentro de la franja para `publishedThisSlot` | Hay que cambiarla (ver §2, manual programado) |
| API `publish_slots` / `publish_window` | `index.ts:3341-3346`, `index.ts:959` | El endpoint expone las franjas. **Verificado 2026-11-26: el panel NO lo consume** (grep en `frontend/src` y `frontend/dist`: 0 referencias) → se elimina el campo (o se deja deprecado) |
| `PUBLISH_WINDOW` (string de display) y comentarios que citan franjas | `index.ts:959`, `db.ts:996`, `db.ts:1109` | Limpieza |

**Valores vivos que se reemplazan:** settings en D1 leídos 2026-11-26: `spacing_hours: 2`, `youtube_spacing_hours: 3`, `min_spacing_minutes: 5`, `cross_platform_spacing_minutes: 15`. Los defaults del código son los inversos (`db.ts:646-647`: `spacing_hours: 3`, `youtube_spacing_hours: 2`) — la D1 manda. En la práctica hoy: YT frena 3 h (job 152 a las 15:06 → siguiente YT no antes de las 18:06) y TT 2 h (job 151 a las 15:10 → 17:10).

**Supersede:** esta regla revoca las órdenes viejas citadas en los comentarios del código ("orden usuario 2026-11-22 01:11" horario por plataforma; "REVOCA la decisión 2026-11-18 'sin franja'" en `gate.ts:319`). Se deja constancia en los comentarios nuevos, no se borra la historia.

## 1. Qué queda igual (no se toca)

- Topes diarios por plataforma: YouTube 6, TikTok/Instagram/Facebook 3 (por cuenta e idioma).
- Rolling 24 h: 3 por cuenta (6 en YouTube).
- Madurez/calentamiento: cuentas nuevas 1/día, semana 3 plan, FB/IG en warmup 2/día (hoy FB e IG se cerraron en 2/2).
- Piso absoluto 5 min entre publicaciones de la misma plataforma (`min_spacing_minutes`).
- Cross-platform 15 min entre plataformas del mismo clip (se dispara más con azar independiente — ver §2).
- Puertas de calidad (B4, SEO ≥80, permiso del influencer, MP4 en R2), deduplicación, presupuesto D1 (3.500 eventos/día por tenant), A4 (contador persistente, migración 0045 — NO se toca).
- La reserva de cupo en sí (`reservePublishSlot`, tabla `publish_slots` por tenant/plataforma/idioma/día) sobrevive: es por DÍA, no por franja. Solo se quita su valla "1 por plataforma y franja".
- Día libre semanal por cuenta (regla 7) sigue.

## 2. La regla nueva (cómo se programa y cómo se hace cumplir)

- **El azar vive en `next_run_at`, y el mínimo de 90 min también se hace cumplir en ejecución (doble control).** Al encolar (auto-enqueue y reprogramación de transitorios), el próximo intento se programa en `última_publicación_de_esa_cuenta_y_plataforma + aleatorio(90–180 min)`. El programador además nunca crea dos jobs pendientes para la misma cuenta+plataforma con `next_run_at` a menos de 90 min entre sí. Y el re-chequeo en ejecución aplica el piso de 90 min contra la última publicación de esa cuenta+plataforma+idioma: si un job vencido (p. ej. dos jobs vencidos durante un hueco, o uno recogido tarde) publicaría a menos de 90 min de la anterior, se frena con motivo propio y se reprograma a `última_publicación + aleatorio(90–180)`. Sin este segundo control, "nunca menos de 1:30 h" sería solo intención de programación, no invariante. El piso de 90 min aplica en el re-chequeo igual que el viejo espaciamiento: también a la manual (el espaciamiento viejo también frenaba a mano desde la orden 09:25). "Publicar ahora" sigue saltándose la franja (como hoy), pero no el piso de 90 min (como hoy no se saltaba el espaciamiento).
- **El gate en ejecución guarda solo los pisos:** topes diarios, rolling, warmup, 5 min, 15 min cross-platform, 90 min. Ya NO comprueba franja ni "1 por plataforma y franja".
- **Fuente de azar:** `Math.random()` en el Worker (es jitter de programación, no un secreto), inyectable en tests para poder pasar un RNG falso y probar determinísticamente. Los tests de distribución sortean N veces y afirman cotas (todos en [90,180], media ≈135), nunca igualdad entre corridas.
- **Reprogramación de transitorios:** el motivo de 90 min tiene su propio mensaje ("menos de 90 min desde la última subida en esta plataforma") y su retry es `última_publicación + aleatorio(90–180)`. El regex de minutos del viejo espaciamiento (piso de 5 min) sigue con su retry actual (última + 5 min, NO +azar). Los regexes de horas del viejo espaciamiento mueren con el espaciamiento fijo. **Motivos viejos de franja:** los regexes de franja ("fuera de franja:", "ya se publicó 1 en … en esta franja") se MANTIENEN una versión como transitorios, mapeados al retry nuevo (azar 90–180). Si se borraran, `GATE_BLOCKS` es fail-closed y un job viejo frenado por franja pasaría a DURO (a draft, no se reprograma) — lo contrario de lo prometido. En la versión siguiente se retiran.
- **Retry `tomorrow`:** sin `PLATFORM_SCHEDULE`, "mañana" es mañana 00:00 Chile + azar(90–180) desde la última publicación (se escribe así).
- **Escalonado entre plataformas del mismo clip (reemplazo de F1.3):** cada plataforma sortea independiente, así que al crear los jobs de un mismo clip se escalonan sus `next_run_at` con ≥15 min entre sí (reemplaza el escalonado F1.3 de `jobs.ts:981-998`, que era sobre `nextPlatformHourMs`). Aun así el cross-platform de 15 min se disparará más que antes; es esperable y se acepta.
- **Plan diario (`daily-plan.ts:186, 196-200`):** deja de asignar horas con `slotTimeFor` y deja de separar con `spacing_hours` fijos. El plan asigna horas con la regla nueva (aleatorio 90–180 desde la última) o deja de asignar horas fijas (a decidir en implementación; el plan es una vista, no el programador — el programador es el auto-enqueue).
- **Manual programado (`scheduled_for`):** se acepta cualquier hora futura (se quita la validación 400 de franja). Al programar se aplica el piso de 90 min contra la última publicación y contra otros scheduled de esa cuenta+plataforma: si la hora elegida viola el piso, se mueve hacia adelante al primer instante válido y la respuesta informa la hora ajustada (no se rechaza en silencio).
- **Tope 24 h:** el auto-enqueue sigue programando hasta cubrir el plan del día. Con el máximo de 3 h: YouTube 6 publicaciones × 3 h = 18 h < 24 h. Ver §3 para el caso por tenant.

## 3. Despertares del sistema — corregido (la v1 estaba mal aquí)

**Verificado en vivo 2026-11-26 15:33 (API de Cloudflare, no el repo):**

- **seba (`avc`): 4 crons** — `*/5 15-18`, `*/5 21-23,0-4`, `0 12,13`, `0 6,7` UTC (los dos horarios se crearon hoy 10:55 UTC). En hora Chile: cada 5 min de 12:00–15:00 y 18:00–01:00, más despertares a las 03:00, 04:00, 09:00 y 10:00. Huecos sin despertar: **01:00–03:00 (2 h), 04:00–09:00 (5 h), 10:00–12:00 (2 h), 15:00–18:00 (3 h)**. El único que rompe "nunca más de 3 h" es **04:00–09:00 (5 h)**.
- **kaez (`avc-kaez`): 2 crons** — solo `*/5 15-18` y `*/5 21-23,0-4` UTC (por API, su `wrangler.kaez.toml` no tiene triggers). En hora Chile: cada 5 min de 12:00–15:00 y 18:00–01:00. Huecos: **15:00–18:00 (3 h) y 01:00–12:00 (11 h)**. El hueco nocturno de 11 h SÍ existe para kaez.
- **El ciclo no tiene puerta horaria** (`cycle-gate.ts:292`): corre si hay job vencido (ventana 60 s), revisión pendiente (12 min) o dispatch diario pendiente. En cada despertar se procesan los jobs vencidos. La v1 asumía que fuera de las ventanas `*/5` "no hay ciclo" sin haberlo comprobado: falso.

Opciones (a decidir por el usuario en la aprobación):
- **A (recomendada):** crons 24/7 (`*/5 * * * *`, una sola expresión por Worker) y medir el consumo D1 la primera semana. El guardián de presupuesto (3.500/día) sigue vigente. Cupo del plan Free (5 triggers por cuenta): seba pasaría de 4 a 1 expresión en `avc`; kaez de 2 a 1 en `avc-kaez`. Sin problema de cupo. Con A, YouTube 6/día cabe en 24 h en ambos (18 h < 24 h).
- **B:** mantener los crons. Seba: YouTube 6 cabe en el peor caso justo (primera 12:00 + 5 intervalos × 3 h = última 03:00, y hay despertar a las 03:00). Kaez: YouTube 6 NO cabe en el peor caso (última 03:00, sin despertar hasta las 12:00) — algunos días kaez publicaría 5 en YouTube en vez de 6; en el caso medio (intervalos ~2 h 15 min) sí caben 6. Con B, "todo en 24 h" se cumple en el caso medio pero no en el peor caso aleatorio para kaez.

## 4. Tests y verificación

- Tests Worker (alcance real: **19 archivos** citan slot/franja; los directamente afectados son `test/auto-enqueue.test.ts`, `test/publish-rules-2026-11-19.test.ts`, `test/gate-blocks.test.ts` (tabla `GATE_BLOCKS`), `test/slot-reservation.test.ts` (~700 líneas), `test/manual-publish.test.ts` (`checkSlot`/`publishedThisSlot`, `scheduled_for`), `test/campaign-priority.test.ts` + `test/endpoints-nuevos-2.test.ts` (daily-plan)): todos los tests que mencionen franja/slot pasan o se actualizan con justificación. `tsc` en verde antes de desplegar. Grep final: **0 referencias** a los símbolos eliminados (`PUBLISH_SLOT_WINDOWS`, `slotWindowContaining`, `isWithinPublishSlot`, `slotWindowBoundsMs`, `slotWindowsForCount`, `slotTimeFor`, `nextSlotStartMs`, `PLATFORM_SCHEDULE`, `nextPlatformHourMs`, `publishedThisSlot`, `checkSlot`, `PUBLISH_WINDOW`).
- Casos: (a) dos jobs vencidos a la vez, misma cuenta+plataforma (el segundo se frena por el piso de 90 min y se reprograma); (b) job vencido horas, recogida tardía (se re-escalona, no publica de inmediato si viola el piso); (c) manual programado fuera de vieja franja (debe pasar, con ajuste si viola 90 min); (d) YT 6/día en 24 h con azar máximo, y con opción B en la ventana de cada tenant; (e) retry de motivo viejo "fuera de franja:" (transitorio, se reprograma); (f) `cross15` disparado por azar; (g) distribución del azar: N sorteos, todos en [90,180], media ≈135 (o RNG inyectado); (h) reserva de cupo sin la valla 1-por-franja; (i) la API ya no expone `publish_slots` (o lo expone deprecado).
- Despliegue: ambos Workers vía `wr_v2.py seba|kaez` (kaez SIEMPRE con `wrangler.kaez.toml` sin triggers; **los crons de kaez se cambian por API** — PUT array JSON al endpoint schedules — no por el toml). Verificar `/api/health` (tenant correcto, `worker_running` true) y crons intactos tras cada deploy (GET schedules). Reglas AGENTS.md: nunca desplegar avc sin `TENANT` en el config.
- Verificación en vivo: tras el deploy, el siguiente ciclo debe crear jobs fuera de las viejas franjas (p. ej. entre 15:00–18:00 Chile) y un job debe publicarse 90–180 min después de la última publicación de su cuenta+plataforma. Medir en D1 los `next_run_at` creados y contrastar con la regla. Si la decisión del §3 es A, verificar además que el ciclo corre de noche (job debido a las 03:00–04:00 publica).
- Rollback: si algo se rompe, revertir el commit del Worker y redesplegar la versión anterior (`0a1b3018` seba / `8083d341` kaez, con fix A4); las franjas volverían, el A4 seguiría.

## 5. Fuera de alcance

- No se toca el panel (UI), ni `clipagent/`, ni `config.yaml`, ni la recopilación, ni los crons de GitHub (viral-source 10:00), ni los topes diarios.
- No se cambia la regla de un vídeo por tenant ni el dedupe de momentos.

## Apéndice — qué corrigió la revisión Goldfish respecto a v1

1. §3 reescrito: la v1 decía 2 crons y hueco nocturno de 11 h para todos; lo real es 4 crons en seba (hueco mayor 04:00–09:00, 5 h) y 2 en kaez (hueco 01:00–12:00, 11 h), verificado por API. Además `cycleGate` no tiene puerta horaria.
2. §0 corregido: el re-chequeo nunca comprobó franja (la v1 lo decía mal en §0 y bien en §2). Líneas corregidas (1151, 1239, 1283).
3. §0/§2 completados con 8 call sites que la v1 no cubría: `slotWindowBoundsMs`, plan diario, endpoint `publish_slots`, validación `scheduled_for`, valla en `reservePublishSlot`, retry `tomorrow`, escalonado F1.3, `PUBLISH_WINDOW`.
4. §2 añade el doble control del mínimo de 90 min (programador + re-chequeo), la fuente de azar (`Math.random()`, inyectable), la decisión sobre motivos viejos de franja (transitorios una versión), el manual programado y el reemplazo del escalonado F1.3.
5. §3 cuantifica la opción B por tenant (seba cabe justo, kaez no en el peor caso).
6. §4 lista el alcance real de tests (19 archivos, los 7 afectados nombrados) y los 9 casos (a)–(i), más el grep final de 0 referencias.
