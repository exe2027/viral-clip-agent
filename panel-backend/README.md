# Panel Clips GTA 6 — Backend independiente (Cloudflare)

Backend propio del panel, fuera de Muse: **Pages (front) + Worker
(TypeScript) + D1 (datos) + R2 (MP4s)**, una instalación completa por
persona. Sin cuentas de Cloudflare del usuario no se puede desplegar;
todo aquí está listo para desplegar con `scripts/deploy-all.sh`.

Fecha: 17 nov 2026, actualizado 18 nov 2026. Estado: **desplegado**
(Worker `avc` + Pages https://avc-em9.pages.dev, D1 `clips-avc`,
R2 `clips-avc`), **sin credenciales reales en el repo** (no hay
ninguna: ni tokens, ni claves, ni contraseñas — solo placeholders
y ejemplos con valores falsos).

## Crear tu cuenta (guía para personas nuevas)

Si quieres usar el panel para publicar tus clips de GTA 6 y no
has entrado nunca, lee **`ONBOARDING-2026-11-18.md`**: es la guía
paso a paso para crearse una cuenta sin tocar código. Resumen:

1. **Pide tu usuario y contraseña** al administrador del panel
   (no puedes crearte la cuenta solo) y entra en
   https://avc-em9.pages.dev: el asistente de onboarding se abre
   solo.
2. **Ten listo antes**: tu canal de YouTube, tu TikTok, y para
   Meta una **Página de Facebook** con tu Instagram **vinculado a
   esa Página** (no vale tu perfil personal).
3. **Conecta con los botones** (YouTube por Google, Meta por
   Facebook, TikTok por Zernio) y verifica todo. Si un botón te
   dice que falta algo, es cosa del administrador: la guía dice
   qué pedirle.
4. **Pulsa Iniciar** en la pestaña Worker. Nada se publica hasta
   que tú lo pulses.

Por defecto, cada clip se publica en tu Página de Facebook Y en
tu Instagram vinculado (ver "Meta por defecto" más abajo).

## Arquitectura (una instalación por persona)

```
Sebastián                          Colega
┌─────────────────────────┐        ┌─────────────────────────┐
│ Pages  panel-sebastian  │        │ Pages  panel-colega     │
│ Worker clips-worker-    │        │ Worker clips-worker-    │
│   sebastian             │        │   colega                │
│ D1     clips-sebastian  │        │ D1     clips-colega     │
│ R2     clips-sebastian  │        │ R2     clips-colega     │
│ Secrets propios         │        │ Secrets propios         │
└─────────────────────────┘        └─────────────────────────┘
        ▲ código base compartido (este repo, mismo commit)
        │ deploy-all.sh despliega el MISMO commit a los dos
```

Nada se comparte en runtime: el Worker de uno no tiene binding a la
D1 ni al R2 del otro, así que un bug no puede leer ni tocar los datos
del otro. Cada persona usa su propia cuenta Cloudflare (recomendado) y
tiene su cuota gratis completa.

## Qué hay en cada carpeta

| Carpeta / archivo | Qué es |
|---|---|
| `worker/` | El Worker TypeScript: API REST, login, cifrado de credenciales, cola de publicación (jobs), verificación real de cuentas, subida a R2 y cron. |
| `migrations/` | Esquema D1: migraciones 0001–0010 del panel adaptadas a D1 + 0011 nueva (jobs de publicación, sesiones, credenciales cifradas) + `schema.sql` completo. |
| `scripts/migrate-appdb-to-d1.py` | Migra `app.db` (SQLite local del artifact) a D1 por tenant. **Redacta los secretos**: nunca escribe una credencial real en la salida. |
| `wrangler.toml` | Proyecto único `avc` (Worker + D1 `clips-avc` + R2 `clips-avc`). |
| `scripts/deploy.sh` | Despliega la instalación única avc (typecheck + tests + deploy + health). |
| `scripts/health-check.sh` | Health check de https://avc.pages.dev. |
| `scripts/seed-users.sql` | Usuarios de login (hashes con `gen-password-hash.mjs`, nunca por chat). |
| `tenants/sebastian/`, `tenants/colega/` | LEGADO (instalación por persona): no usar. Ver `DEPLOY-avc.md`. |
| `publisher-bridge/` | Mecanismo ALTERNATIVO dirigido por el Worker (GitHub Actions; ver `COMPUTE.md`): puente Python que ejecuta las subidas pesadas reutilizando el código ya probado de `viral-clip-agent` y devuelve la evidencia al Worker. La vía principal de cómputo es la cadena del cerebro (`cerebro-*.yml`, cron 09:00). |
| `COMPUTE.md` | Cómputo: plan principal Cloudflare + GitHub Actions ("Todo automático", decisión 17 nov 2026), qué se automatiza y qué queda manual, otras opciones gratis evaluadas (Colab, Apps Script, teléfono, ffmpeg.wasm) y el respaldo degradado solo-Cloudflare. |
| `frontend-notes.md` | Qué cambia en el front (Pages): página de login y llamadas con cookie. |
| `ONBOARDING-2026-11-18.md` | **Guía para personas nuevas**: crear tu cuenta paso a paso, qué tener listo antes, errores típicos y qué pedirle al administrador. |
| `DECISIONS.md` | Decisiones con pros/contras: login, cifrado, por qué el Worker no sube el video entero él solo. |

## Límites honestos del Worker (del plan §3)

Un Worker **no** corre Python/ffmpeg, **no** procesa video y tiene
~10 ms de CPU por invocación y 128 MB de memoria. Por eso el diseño
divide el trabajo en dos:

1. **Worker = cerebro de estado.** API, colas, gates (SEO ≥ 90 por
   plataforma, cupo diario, espaciamiento 2 h, franja 18:00–24:00
   Chile, permiso del streamer, preview visto), máquina de estados
   por plataforma y evidencia. Nunca marca `posted` sin URL.
2. **Publisher-bridge = manos (ALTERNATIVA dirigida por el
   Worker).** La vía principal de cómputo es GitHub Actions con la
   cadena del cerebro (`.github/workflows/cerebro-*.yml`, cron
   09:00; ver `COMPUTE.md`): detección, corte, subtítulos y
   publicación automáticos tras Iniciar. El puente queda en
   `publisher-bridge/` como mecanismo alternativo para que el
   Worker dispare subidas fuera de ese cron. El Worker también
   implementa los pasos de API por plataforma
   (`worker/src/publish/`) para que el cron pueda avanzar jobs
   pequeños (init, consultas de estado, finish) sin ningún puente.

TikTok por navegador (Playwright, captcha atendido) sigue siendo
trabajo aparte (plan §5, ítem 6) y **no** está en este backend: el
job de TikTok por API queda en `draft` con el motivo hasta que haya
auditoría aprobada o agente navegador.

## Flujo de publicación (por plataforma, con evidencia)

```
panel (Publicar) → POST /api/clips/:id/publish
  → gates (preview, SEO, cupo, permiso, franja)
  → jobs: scheduled, idempotency_key = sha256(clip|platform|cuenta|fecha)
  → cron del Worker cada 5 min (o el bridge) toma el job → processing
  → adaptador por plataforma (YouTube / TikTok / IG / FB)
  → posted SOLO con evidence_url (URL pública de la plataforma)
    o failed (con motivo + retryable) o draft (falta credencial/auditoría/URL pública)
  → clips.publish_states se actualiza; el clip solo se cierra cuando
    TODAS sus plataformas están en estado terminal (publicado/fallido/
    expirado/cancelado), como ya hacía el panel v17 (§9).
```

Estados del job: `scheduled → processing → posted | failed`, más
`draft` (no se puede subir por API: falta algo, con motivo) y
`awaiting_captcha` (solo TikTok por navegador, futuro). Un job en
`processing` o `posted` con la misma `idempotency_key` nunca se
vuelve a subir: anti-duplicado.

## Meta por defecto: Página + Instagram vinculado (18 nov 2026)

**Por defecto, cada clip se publica en la Página de Facebook Y en
la cuenta de Instagram vinculada a esa Página, desde la misma
cuenta Meta.** No hay que elegir: `instagram_reels` y
`facebook_reels` resuelven la misma cuenta `meta` del tenant
(`POST /api/clips/:id/publish` mapea ambas a `meta`), y el front
crea los clips con las 4 plataformas por defecto
(`youtube_shorts`, `tiktok`, `instagram_reels`, `facebook_reels`).

Instalación de Sebastián (tenant `sebastian`):
- Página: la Página del canal (su id queda en Extra de la cuenta
  Meta, nunca en este repo).
- Instagram: la cuenta de Instagram del canal, vinculada a esa
  Página (su `ig_user_id` queda en Extra, nunca en este repo;
  suspendida; apelación enviada el 18 nov 2026, en revisión).
- El OAuth de Meta descubre `page_id` e `ig_user_id` solo: toma
  la primera Página de `/me/accounts` y lee su
  `instagram_business_account`; ambos quedan en Extra de la
  cuenta Meta. No se pegan a mano.

Condición del vínculo (sin esto Instagram falla aunque Meta
verifique en verde):
- La Página TIENE que tener una cuenta de Instagram vinculada
  (`instagram_business_account`). Si la Página la pierde (cuenta
  suspendida, desvinculada), el job de Instagram falla con
  `Object with ID '<ig_user_id>' does not exist, cannot be loaded
  due to missing permissions`, mientras el token sigue verificando
  bien — la verificación vieja solo comprobaba `/me` con el token.
  Desde el 18 nov 2026 `verify.ts` también comprueba la cuenta de
  Instagram (`GET /{ig_user_id}?fields=username`): si no es
  accesible, la cuenta queda No conectada con el motivo
  (pendiente de desplegar).
- Cómo comprobarlo a mano (Explorador de Graph, app Clipdelgta6):
  con token de usuario, `me/accounts?fields=id,name` lista las
  Páginas; con token de Página,
  `me?fields=id,instagram_business_account` muestra la cuenta de
  Instagram vinculada (si no sale el campo, no hay ninguna).
- NO confundir la Página con el perfil personal: el perfil
  personal de Sebastián en modo profesional también se llama igual
  que el canal y NO es la Página. Los reels se publican en la
  Página, nunca en el perfil. (Los ids reales viven solo en Extra
  de la cuenta Meta, cifrados en D1; nunca en este repo.)

## Login

Un usuario por persona (ver `DECISIONS.md` §6): username +
contraseña contra `panel_users` en D1 (hash PBKDF2, generado con
`scripts/gen-password-hash.mjs`, sembrado con
`scripts/seed-users.sql`). Sesión en cookie httpOnly firmada con
HMAC (30 días) con el tenant dentro. Sin Google/Firebase por
defecto: menos piezas, menos superficie, $0. El esqueleto de Google
OAuth queda documentado en `DECISIONS.md` como mejora opcional.
El secreto `PANEL_PASSWORD_HASH` por despliegue YA NO EXISTE.

## Credenciales cifradas

Hoy las credenciales están en texto en `api_accounts.credential`.
Aquí se cifran con **AES-256-GCM** (WebCrypto) con una clave
derivada por tenant (HKDF sobre `CREDENTIAL_ENCRYPTION_KEY`,
secreto único de Cloudflare, 32 bytes base64). La API nunca devuelve una credencial: solo `****últimos 4`
+ estado de verificación. La migración redacta los valores viejos:
hay que volver a pegar cada credencial en el panel nuevo (es lo
seguro: el texto plano viejo no debe viajar a D1).

## Cómputo (sin VPS, sin PC prendida 24/7; GitHub Actions como compute principal)

Ver `COMPUTE.md`. Resumen: la vía principal es **Cloudflare $0**
(Workers + D1 + R2 + Cron Triggers + Queues) **+ GitHub Actions**
como compute principal (detección, corte 9:16 con ffmpeg,
subtítulos faster-whisper y publicación; cron 09:00; $0 dentro de
los 2.000 min/mes). El botón Iniciar del panel dispara el workflow
con `repository_dispatch`. Solo Cloudflare + manual (cortar el clip
en el teléfono) queda como respaldo degradado.

## Despliegue (resumen)

Ver el runbook completo en `DEPLOY-avc.md`. Resumen:

```bash
# 1. Sebastián crea su cuenta Cloudflare (gratis) y hace wrangler login.
# 2. Crear los recursos (UNA vez):
#      wrangler d1 create clips-avc
#      wrangler r2 bucket create clips-avc
#      wrangler pages project create avc
# 3. Aplicar migraciones: npm run d1:migrate (desde worker/)
# 4. Sembrar usuarios: rellenar scripts/seed-users.sql y
#      wrangler d1 execute clips-avc --file scripts/seed-users.sql --remote
# 5. Migrar datos (opcional): python3 scripts/migrate-appdb-to-d1.py
#      --db app.db --tenant sebastian --out /tmp/migracion-sebastian.sql
#    (revisar la salida: las credenciales salen como REDACTED)
# 6. Crear los secretos (wrangler secret put ...) — ver DEPLOY-avc.md
# 7. ./scripts/deploy.sh
# 8. ./scripts/health-check.sh https://avc.pages.dev
# 9. Abrir https://avc.pages.dev → login → Cuentas → Worker → Iniciar.
```

El front de Pages (build del panel) NO está en este repo: hay que
exportar el build del artifact y hacer
`wrangler pages deploy <dist> --project-name avc` (ver DEPLOY-avc.md
§5). Sin ese paso, avc.pages.dev existe pero sin front.

## Qué quedó implementado / qué falta

Implementado (código real, typecheck + tests en `worker/test/`):
- Worker completo: router, auth por contraseña, sesiones firmadas,
  CRUD de lectura de clips/cola, verificación real de cuentas
  (YouTube/TikTok/Meta, port de `verifyApiCredential` del panel),
  cifrado AES-GCM de credenciales, subida/descarga R2, jobs con
  idempotencia y gates, cron que avanza jobs.
- Adaptadores de subida por plataforma en TypeScript
  (`worker/src/publish/`): YouTube (resumable upload por pasos),
  TikTok Direct Post (FILE_UPLOAD por chunks + status), Instagram
  (contenedor → FINISHED → publicar), Facebook (start → rupload por
  chunks → finish). Son la vía principal de subida (solo
  Cloudflare). Con contrato claro por endpoint.
- Migraciones D1 0001–0011 + `schema.sql`.
- Script de migración con redacción de secretos.
- `deploy-all.sh` + `health-check.sh`.
- Puente Python (ALTERNATIVA dirigida por el Worker, no vía
  principal de cómputo) que reutiliza `clipagent` para las subidas
  pesadas + workflows por tenant en
  `publisher-bridge/github-actions/` (para que el Worker dispare
  subidas fuera del cron del cerebro).

Falta / no se puede hacer sin el usuario (ver `DECISIONS.md` §6 y §7):
- Cuenta Cloudflare de Sebastián + `wrangler login` + secretos reales.
- Usuarios de login sembrados en D1 (seed-users.sql con hashes reales).
- OAuth de YouTube (client_secrets + refresh token) por tenant.
- App de TikTok auditada (`video.publish` público) o agente
  navegador; hoy TikTok por API solo llega a privado sin auditoría.
- Token largo de Página para IG (lo renueva el OAuth de Meta;
  `ig_user_id` y `page_id` los descubre el OAuth solo, ya no se
  pegan a mano) y App Review de Meta para publicar Reels por API
  en producción.
- Front de Pages (build del cliente actual + login) — notas en
  `frontend-notes.md`, no está en este repo: hay que exportarlo del
  artifact y desplegarlo con `wrangler pages deploy`.
- Prueba end-to-end con 1 clip real por tenant (solo con credenciales
  reales y decisión del usuario).
