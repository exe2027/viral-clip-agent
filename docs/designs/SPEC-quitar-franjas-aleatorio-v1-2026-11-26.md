# SPEC — Quitar franjas, publicar aleatorio 1:30–3 h (v1 borrador)

- **Estado: BORRADOR.** Escrita por el principal 2026-11-26 15:30 como preparación. Pendiente: revisión Goldfish (EGM) → aprobación del usuario → implementar → desplegar. **Nada de esto está implementado ni desplegado.**
- **Orden del usuario (2026-11-26 13:57–13:58, sus palabras):** "No es necesario tener hora asignada! Se tienen que subir cada 3 horas o algo así, habíamos puesto unas reglas antiban" / "No tenemos franjas de publicaciones se publica aleatoriamente nunca 1:30 después de cada uno" / "Uno hora y media" / "Y nunca más de 3 horas" / "Debe ser aleatorio" / "Pero en 24 horas se tienen que publicar todos".
- **Regla final:** NO hay franjas ni hora asignada. La publicación es ALEATORIA: nunca menos de 1:30 h ni más de 3 h entre publicaciones (por cuenta y plataforma), y todo lo del día se publica dentro de 24 h.
- **Motivo (medido 2026-11-26):** con el fix A4 desplegado y verificado (5 clips aparcados 3/3 en ambas D1, 14:48), el escaneo ya no se atasca, pero el código sigue sin crear jobs fuera de franja: 0 jobs nuevos entre las 14:48 y las 15:27, siguiente franja 18:00 Chile. Las franjas son ahora el freno. El respaldo desde el chat publicó 3 (242 YT, 215 TT, 202 FB) pero no sustituye al ciclo.

## 0. Qué se quita (código actual, verificado en vivo 2026-11-26 15:30)

Repo `viral-clip-agent`, `panel-backend/worker/src/`:

| Pieza | Ubicación | Uso actual |
|---|---|---|
| `PUBLISH_SLOT_WINDOWS` (mañana 12–14, tarde 18–20, noche 21–23, madrugada 00–01, hora Chile) | `db.ts:1113` | Define las franjas |
| `slotWindowContaining` / `isWithinPublishSlot` | `db.ts:1153–1160` | El gate bloquea fuera de franja |
| `slotWindowsForCount`, `slotTimeFor` (hora determinista por hash FNV-1a cuenta+día ±10 min) | `db.ts:1180–1210` | Asigna la hora de cada publicación del día |
| `nextSlotStartMs` | `db.ts:1241` | Programa `next_run_at` al inicio de la próxima franja |
| `PLATFORM_SCHEDULE` (YT 00/12/14/18/20/22; TT 13/19/22; IG 12/18/21; FB 13/19/22) | `db.ts:1264` | Horas fijas por plataforma |
| `nextPlatformHourMs` | `db.ts:1284` | El auto-enqueue programa `next_run_at` a la próxima hora de SU plataforma |
| Chequeo de franja en `publishGate` (`checkSlot`, "fuera de franja…") | `gate.ts:319–324` | Frena encolar y re-chequeo fuera de franja |
| "1 por plataforma y franja" (`publishedThisSlot >= 1`) | `gate.ts:327–331` | Como mucho 1 por plataforma en cada franja |
| Retry kinds `slotEnd` / `nextSlot` | `gate.ts:401, ~501–540` | Reprogramación de jobs frenados por franja |

**Valores vivos que se reemplazan (settings en D1, leídos 2026-11-26):** `spacing_hours: 2`, `youtube_spacing_hours: 3`, `min_spacing_minutes: 5`, `cross_platform_spacing_minutes: 15`. En la práctica hoy: YT frena 3 h (job 152 a las 15:06 → siguiente YT no antes de las 18:06) y TT 2 h (job 151 a las 15:10 → 17:10).

**Supersede:** esta regla revoca las órdenes viejas citadas en los comentarios del código ("orden usuario 2026-11-22 01:11" horario por plataforma; "REVOCA la decisión 2026-11-18 'sin franja'" en `gate.ts:319`). Se deja constancia en los comentarios nuevos, no se borra la historia.

## 1. Qué queda igual (no se toca)

- Topes diarios por plataforma: YouTube 6, TikTok/Instagram/Facebook 3 (por cuenta e idioma).
- Rolling 24 h: 3 por cuenta (6 en YouTube).
- Madurez/calentamiento: cuentas nuevas 1/día, semana 3 plan, FB/IG en warmup 2/día (hoy FB e IG se cerraron en 2/2).
- Piso absoluto 5 min entre publicaciones de la misma plataforma (`min_spacing_minutes`).
- Cross-platform 15 min entre plataformas del mismo clip.
- Puertas de calidad (B4, SEO ≥80, permiso del influencer, MP4 en R2), deduplicación, presupuesto D1 (3.500 eventos/día por tenant), A4 (contador persistente, migración 0045 — NO se toca).
- Publicación manual ("Publicar ahora", `force_now`) sigue inmediata.
- Día libre semanal por cuenta (regla 7) sigue.

## 2. La regla nueva (cómo se programa)

- **El azar vive en `next_run_at`, no en el gate.** Al encolar (auto-enqueue y reprogramación de transitorios), el próximo intento se programa en `última_publicación_de_esa_cuenta_y_plataforma + aleatorio(90–180 min)`, con aleatoriedad real por decisión (no hash determinista por día: el viejo jitter ±10 min daba siempre la misma hora para el mismo día y cuenta, y eso es justo lo que el usuario revocó).
- **El gate en ejecución solo guarda los pisos:** topes diarios, rolling, warmup, 5 min, 15 min cross-platform. Ya NO comprueba franja ni "1 por plataforma y franja". `recheckPublishGate` ya no comprobaba franja (comentario `gate.ts:319`); ahora tampoco `publishGate`.
- **Tope 24 h:** el auto-enqueue sigue programando hasta cubrir el plan del día. Con el máximo de 3 h: YouTube 6 publicaciones × 3 h = 18 h < 24 h; resto 3 × 3 h = 9 h. El día se cubre dentro de 24 h siempre que el ciclo corra (ver §3, huecos de cron).
- **Reprogramación de transitorios:** el retry `spacing` recalcula un aleatorio nuevo de 90–180 min desde la última publicación (no el viejo espaciamiento fijo). Los kinds `slotEnd`/`nextSlot` se eliminan de la tabla de clasificación (`gate.ts:401`); un motivo de franja que aparezca de un job viejo se trata como transitorio y se reprograma con la regla nueva (nunca duro por franja).
- **Limpieza:** se eliminan los usos de `PUBLISH_SLOT_WINDOWS`, `slotWindowsForCount`, `slotTimeFor`, `nextSlotStartMs`, `PLATFORM_SCHEDULE`, `nextPlatformHourMs` en el camino de encolado/gate. Si algún helper queda sin uso y sin test que lo cubra, se borra con sus tests; si un test lo cubre, se actualiza o se borra con justificación en el commit. No se deja código muerto de franjas que otro lector pueda reactivar por error.

## 3. Hueco de cron — decisión abierta (hay que resolverla antes de implementar)

El Worker solo despierta con sus crons actuales (`*/5 15-18` y `*/5 21-23,0-4` UTC = 12:00–15:00 y 18:00–01:00 hora Chile). Huecos sin ciclo: **15:00–18:00 (3 h) y 01:00–12:00 (11 h)**. Con esos huecos, "nunca más de 3 h entre publicaciones" es imposible de noche aunque se quiten las franjas del código.

Opciones (a decidir por el usuario en la aprobación):
- **A (recomendada):** crons 24/7 (`*/5 * * * *`, una sola expresión por Worker) y medir el consumo D1 la primera semana. Riesgo: más ciclos = más lecturas D1; el guardián de presupuesto (3.500/día) sigue vigente y frenaría antes de romper el tope.
- **B:** mantener los crons y aceptar la pausa nocturna: las publicaciones se concentran en 12:00–01:00, todas dentro de 24 h, pero con una pausa de 11 h de noche (la regla "nunca más de 3 h" regiría solo dentro del horario activo).

Sin resolver esto, quitar las franjas del código deja el hueco de 15:00–18:00 (3 h justas) y el nocturno (11 h).

## 4. Tests y verificación

- Tests Worker: `gate` (sin franja, sin "1 por franja", retry sin slotEnd/nextSlot), `auto-enqueue` (`next_run_at` en [90, 180] min desde la última publicación de esa cuenta+plataforma; nunca < 90 ni > 180; aleatorio entre dos corridas con la misma semilla de entrada), `jobs`/`cycle-gate` sin regresiones. `tsc` en verde antes de desplegar.
- Despliegue: ambos Workers vía `wr_v2.py seba|kaez` (kaez SIEMPRE con `wrangler.kaez.toml` sin triggers); verificar `/api/health` (tenant correcto, `worker_running` true) y crons intactos tras cada deploy (GET schedules; si se tocan, restaurar con PUT array JSON). Reglas AGENTS.md: nunca desplegar avc sin `TENANT` en el config.
- Verificación en vivo: tras el deploy, el siguiente ciclo debe crear jobs fuera de las viejas franjas (p. ej. entre 15:00–18:00 Chile) y un job debe publicarse 90–180 min después de la última publicación de su cuenta+plataforma. Medir en D1 los `next_run_at` creados y contrastar con la regla. Si la decisión del §3 es A, verificar además que el ciclo corre de noche.
- Rollback: si algo se rompe, revertir el commit del Worker y redesplegar la versión anterior (`0a1b3018` seba / `8083d341` kaez, con fix A4); las franjas volverían, el A4 seguiría.

## 5. Fuera de alcance

- No se toca el panel (UI), ni `clipagent/`, ni `config.yaml`, ni la recopilación, ni los crons de GitHub (viral-source 10:00), ni los topes diarios.
- No se cambia la regla de un vídeo por tenant ni el dedupe de momentos.
