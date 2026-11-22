# Medición automática de analíticas + auto-boost + auto-enqueue (2026-11-19)

> Nota de trabajo para la auditoría final. NO es versionado: sin tags
> ni cambios de versión (orden del usuario 08:53).

## Qué se implementó

- **Medición automática** (`worker/src/publish/stats.ts`, nuevo): `collectAnalytics`
  corre dentro de `runWorkerCycle`, como mucho 1 vez cada 6 h por tenant y
  1 medición cada 24 h por (clip, plataforma). Lee los `platform_id` de los
  jobs `posted`, resuelve credenciales cifradas con `resolveCreds` y guarda
  cifras reales en `clip_stats` (lo que una API no devuelve queda NULL).
  Si una plataforma falla, queda en `worker_events` + `analytics_status`
  y las demás siguen; medir nunca tumba la cola. Reintentos solo
  transitorios (429/5xx, máx 3 con backoff); 401/403-permiso no se reintentan.
- **YouTube**: Data API v3 `videos?part=statistics`, hasta 50 ids, 1 unidad
  de cuota. Guarda views + comments. Retención/shares/saves/new_subs NULL.
  `likes` se pide pero NO se guarda (sin columna).
- **Instagram**: `/{media_id}/insights` (sin crossposted/facebook_views).
  Retención derivada `avg_watch_ms / duración_clip_ms`, tope 100.
  `new_subs` NULL. Requiere scope nuevo `instagram_manage_insights`.
- **Facebook**: `/{video_id}/video_insights?period=lifetime`; si el conjunto
  completo falla, reintenta con el mínimo (vistas + completas). Retención
  derivada `complete_views/views`. Comments/shares por el nodo del vídeo;
  `saves` NULL. Requiere scopes nuevos `read_insights`,
  `pages_read_user_content`.
- **TikTok**: `POST /v2/video/query/` (Display API). BLOQUEADO POR PERMISOS:
  el token actual solo tiene `video.publish`; exige `video.list` + Login
  Kit + aprobación Display API. Se clasifica `sin_permiso`, sin inventar
  cifras. El id real se extrae de `jobs.evidence_url` (`.../video/<id>`).
- **Estado por plataforma**: tabla nueva `analytics_status` (migración
  0028). `GET /api/analytics` expone `auto_status` por plataforma
  (`ok | sin_credencial | sin_permiso | error` + motivo + última medición)
  SIN tocar los números manuales.
- **window_label**: las métricas son LIFETIME (acumulado total). Se guardan
  con `window_label "7d"`, la etiqueta por defecto del formulario manual:
  NO es una ventana de 7 días.
- **Auto-boost** (`worker/src/db.ts`): `boostBonusByClip` — ratio =
  mediana_p(views_clip,p / median_views_p) con última medición por
  (clip, plataforma) de los últimos 7 días; bonus 0 si no hay medición o
  ratio < 1; si ratio >= 1, `min(10, round(5*log2(ratio)))`; prioridad
  efectiva `min(100, base + bonus)`, base = `viral.priority ?? clips.score`.
  Solo reordena DENTRO de cada segmento; `manual_rank` sigue mandando;
  sin medición = orden de antes. Aplicado en `listQueueClips` y en
  `dueJobs` (pide 3x y recorta tras ordenar). Si leer `clip_stats`
  falla, el boost es vacío y la Cola queda como antes.
- **Auto-enqueue** (`worker/src/jobs.ts`): `tryEnqueueClipPublish` es AHORA
  el camino único del botón Publicar (POST `/api/clips/:id/publish`
  refactorizado a usarlo) y del auto-enqueue. `autoEnqueueTopClips`
  corre en `runWorkerCycle` DESPUÉS del chequeo `isWorkerRunning`
  (único punto donde /parar lo detiene), máx. 3 clips por ciclo sin job
  publish vigente, con el mismo `publishGate` (plan/techo/espaciamiento
  por plataforma+idioma, SEO, preview, duración, idioma). Cada freno se
  registra con el motivo LITERAL del gate en `clip_events` +
  `worker_events`. Detrás del ajuste `auto_publish_top` (default OFF),
  con interruptor en Ajustes del panel. Umbrales 50/90 intactos.
- **Scopes**: `META_SCOPES` suma `instagram_manage_insights,read_insights,
  pages_read_user_content`. `GOOGLE_SCOPES` ya incluía `youtube.readonly`.

## Estado

- `tsc` limpio; `bun test test/` → **382 pass, 0 fail** (364 previos + 18
  nuevos en `worker/test/analytics-auto.test.ts`).
- **SIN DESPLEGAR** (Worker ni Pages): sin auth de Cloudflare en este
  entorno (`wrangler` no autenticado, `CLOUDFLARE_API_TOKEN` vacío;
  `wrangler d1 execute --remote` y `wrangler deploy` lo exigen).
  La migración 0028 NO está aplicada en la D1 remota.
- **NO verificado en vivo**: `/api/analytics` ni `/resumen` de Telegram
  (sin despliegue). `https://avc.pages.dev/api/health` devolvió
  `curl: (52) Empty reply from server` en 2 intentos (11:55 -03).
- `require_preview` en vivo NO verificado (sin acceso a la D1 remota);
  el default del código es `false` (`db.ts:375`).
- Tokeninfo de YouTube (`youtube.readonly` en el refresh token concedido)
  NO verificado: el refresh token está cifrado en D1 y no hay acceso a
  la clave ni a la D1 remota.
- Dato actualizado 08:54: job 17 (clip 23, Facebook) PUBLICADO 11:47 UTC,
  reel https://www.facebook.com/reel/4706559349610438/
  (`platform_id` 4706559349610438). Clip 23: 3/4 (YT + TikTok + FB;
  IG en borrador por tope 1/1). Job 4 (clip 7 FB) en borrador por el
  mismo tope. El conteo de publicados por plataforma NO se re-consultó
  en vivo (sin acceso remoto): no asumir Facebook = 1.

## Pendiente del usuario

1. Desplegar (con auth de Cloudflare): `scripts/deploy.sh` (tsc + tests +
   `wrangler deploy` + health-check) y aplicar la migración 0028.
2. Re-conectar Meta en Cuentas (nuevos scopes de insights) para que
   Instagram/Facebook midan; hasta entonces quedan en `sin_permiso`.
3. Reautorizar TikTok con `video.list` (+ Login Kit + aprobación Display
   API) para medir TikTok; hasta entonces queda en `sin_permiso`.
4. Confirmar el encendido del auto-enqueue (Ajustes → "Publicar solos
   los tops de la Cola" → Sí). Antes, revisar `require_preview` en vivo:
   con el default `false`, un clip sin preview visto publica igual.
