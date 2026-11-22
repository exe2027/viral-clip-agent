# Escalado automático de topes por antigüedad/actividad (2026-11-18)

Orden del usuario: cuando una cuenta DEJE DE SER "cuenta nueva", los
topes tienen que cambiar AUTOMÁTICAMENTE, sin que él lo cambie a mano.

## Criterio (en código, `worker/src/db.ts` `computeMaturity`)

Una cuenta pasa de **NUEVA** a **ESTABLECIDA** cuando cumple TODO a la vez:

- más de **30 días** desde su primera publicación (`first_published_at`), y
- al menos **10 publicaciones** acumuladas (`published_count`), y
- **0 strikes** (sanciones).

Al pasar, sus topes suben solos de **1/día** a los normales:

| Cuenta | Plan/día | Máx/día |
|---|---|---|
| YouTube establecida | 3 | 5 |
| Resto establecida (TikTok, Instagram, Facebook) | 2 | 3 |
| Nueva (cualquier plataforma) | 1 | 1 |

Los valores de plan/máx son los de Ajustes (`youtube_plan`,
`youtube_max`, `resto_plan`, `resto_max`, `new_account_daily`); la
tabla muestra los defaults.

## Si hay sanción (strike)

`strikes > 0` devuelve la cuenta a **NUEVA (1/día)** aunque ya fuera
establecida, hasta que los strikes se pongan a 0 Y se vuelva a cumplir
el criterio (>30 días y >=10 publicaciones). El campo `strikes` lo
pone el usuario o su agente con `PATCH /api/accounts/:id {strikes}`:
es el registro de una sanción real de la plataforma. **El Worker
nunca inventa una sanción**: sin strikes puestos a mano, el contador
es 0.

## Datos en D1 (migración 0017, tabla `api_accounts`)

| Columna | Qué es |
|---|---|
| `first_published_at` | ms epoch de la primera publicación real. Lo pone el Worker en `markPosted` (jobs.ts); NULL = aún no ha publicado. |
| `published_count` | Publicaciones reales acumuladas (jobs en `posted`). Lo suma `markPosted`. |
| `strikes` | Sanciones (ver arriba). |
| `maturity` | Estado vigente guardado: `"nueva"` \| `"establecida"`. Es lo que aplica el gate. |
| `maturity_checked_day` | Día America/Santiago (YYYY-MM-DD) del último recálculo. |

## Recálculo: solo una vez al día, sin sesión

`refreshAccountMaturity` (db.ts) recorre las cuentas del tenant y
solo recalcula las que no se revisaron hoy (`maturity_checked_day !=
hoy`). Lo llaman:

- el **cron */5** del Worker (vía `runWorkerCycle`, solo si el tenant
  inició su Worker),
- `POST /api/clips/:id/publish`,
- `GET /api/accounts` (para mostrar el estado).

En los tres casos el recálculo es un no-op si ya se hizo hoy. El
gate (`publishGate`, jobs.ts) usa **solo el estado guardado**
(`accountDailyLimit` / `accountDailyPlan` que le pasa el handler de
publicar), nunca un cálculo en caliente por sesión. El cambio de
estado queda en `worker_events` (kind `madurez`).

## Dónde se ve

- **Ajustes → "Escalado automático por cuenta"**: por cuenta, estado
  actual (nueva/establecida), días desde la primera publicación,
  publicaciones acumuladas, strikes y su tope vigente hoy
  (p. ej. "YouTube · establecida · 3/día (máx 5)").
- **Cuentas**: cada tarjeta muestra su línea de escalado.
- **Canales** (`GET /api/channels`): `daily_cap` / `daily_plan` ya
  son el tope vigente por madurez, y `maturity` trae el estado.
- **API** (`GET /api/accounts`): cada cuenta trae `maturity: {status,
  days_since_first_published, published_count, strikes,
  first_published_at, effective_daily_limit, effective_daily_plan,
  effective_daily_max}`.

## Verificación (2026-11-18)

Cuenta de prueba en D1 (tenant sebastian, luego borrada): con
`first_published_at` hace 31 días + `published_count` 10 + `strikes`
0, el recálculo la marcó `establecida` (YouTube 3/día, máx 5); con
`strikes` 1 volvió a `nueva` (1/día). Ver el informe del despliegue
en `DEPLOYED-2026-11-18.md` (tanda de Ajustes + escalado).
