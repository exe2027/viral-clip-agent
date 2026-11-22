# publisher-bridge — mecanismo ALTERNATIVO dirigido por el Worker (GitHub Actions)

La vía principal de cómputo es la cadena del cerebro en GitHub
Actions (`.github/workflows/cerebro.yml`, el cerebro genérico; ver
`viral-clip-agent/docs/plan-principal.md`). Este puente es un
mecanismo alternativo: el Worker pide jobs y el puente ejecuta la
subida, por si hace falta disparar subidas fuera del cron del
cerebro. Solo Cloudflare + manual queda como respaldo degradado
(ver `COMPUTE.md`).

## Qué hace (si se activa)

`bridge.py` toma jobs del Worker y ejecuta la subida con el código ya
probado de `viral-clip-agent` (`clipagent/platforms.py`,
`DirectPublisher`, `YouTubePublisher`, 503 tests). `posted` solo con
`evidence_url`; si falta algo, devuelve `draft` con el motivo, nunca
finge una publicación.

## Activación (mecanismo alternativo, no vía principal)

1. Copiar `bridge.py` a la raíz de `exe2027/viral-clip-agent`.
2. Copiar `github-actions/publish-<tenant>.yml` a
   `.github/workflows/` de ese repo.
3. En GitHub: repo → Settings → Environments → crear `sebastian`
   (y `kaez`) con los secretos `WORKER_URL`, `BRIDGE_TOKEN` y
   `BRIDGE_TENANT` (= `sebastian` o `kaez`; el puente solo toma
   jobs de ese tenant). `WORKER_URL` es ahora el Worker único:
   `https://avc.<cuenta>.workers.dev`. `BRIDGE_TOKEN` es el secreto
   único del Worker avc (el mismo para los dos tenants).
4. Los tokens de plataforma del runner van como secretos del
   Environment (`YOUTUBE_REFRESH_TOKEN`, `TIKTOK_ACCESS_TOKEN`,
   `META_PAGE_TOKEN`, `IG_USER_ID`, `FACEBOOK_PAGE_ID`).

El workflow corre cada 30 min en franja 18:00–24:00 Chile
(21:00–03:00 UTC) y también a mano (`workflow_dispatch`). El runner
se apaga solo: nada queda prendido.
