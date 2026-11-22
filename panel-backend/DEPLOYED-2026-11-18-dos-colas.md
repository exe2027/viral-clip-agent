# Despliegue 2026-11-18 — DOS COLAS SEPARADAS (español / inglés)

Panel: https://avc-em9.pages.dev · Worker: https://avc.vikingosfoodspa.workers.dev
D1 `clips-avc` (5cf19675-f905-4e49-b8f2-96ae0810fafd) · R2 `clips-avc`.
Cuenta Cloudflare vikingosfoodspa@gmail.com.

| Qué | Versión |
|---|---|
| Migración 0019 | `clips.language`, `jobs.language` + índice `jobs_posted_day_lang` |
| Worker | 0f96011e-565e-45da-aad4-1e2821041594 |
| Pages | 8a5963ea.avc-em9.pages.dev |

## Regla

Los vídeos en inglés se suben a los canales en inglés y los de
español a los de español: son colas diferentes. Un clip EN **nunca**
se publica en un canal ES y viceversa. La puerta de idioma frena
antes que el resto de puertas (duración, SEO, topes): ni siquiera
se evalúa un clip que no puede salir por ese canal.

## Contrato para el cerebro (`enqueue_clip.py`)

POST /api/clips acepta (además de todo lo del contrato de
puntuación IA 2026-11-18):

| Campo | Tipo | Obligatorio | Regla |
|---|---|---|---|
| `language` | `"es" \| "en"` | No (default `"es"`) | Idioma del clip. Decide la cola y las cuentas. Otro valor → **400** `"Idioma inválido: usa es o en."` |

- GET /api/clips y GET /api/clips/:id devuelven `language` en cada clip.
- PATCH /api/clips/:id acepta `language` (`"es" | "en"`, otro valor → 400): corrige el idioma y mueve el clip de cola. Cambiar el idioma NO re-publica nada: los jobs ya encolados guardan su propio idioma y el Worker los frena si su cuenta ya no coincide.
- Clips heredados sin idioma son `"es"` (default de la migración 0019 y de POST sin `language`): nunca se inventa un `"en"`.
- Sin `language`, el cerebro sigue encolando en español como hasta ahora (nada se rompe).

## Cómo funciona (código)

- **Migración 0019** (`migrations/0019_clip_language.sql`): `clips.language TEXT NOT NULL DEFAULT 'es'`, `jobs.language TEXT NOT NULL DEFAULT 'es'` (copia del idioma del clip al encolar), índice `(platform, language, state, posted_day)`. `schema.sql` NO se tocó: está obsoleto desde 0016 (solo llega a 0015) y el `migrate` de `deploy_with_token.py all` sigue fallando por él ("table settings already exists", preexistente; la 0019 se aplicó ✅ antes del fallo y Worker/Pages se desplegaron por separado con `deploy-worker` + `deploy-pages`).
- **Puerta de idioma** (`worker/src/jobs.ts`, `publishGate`): `GateContext` lleva `accountLanguage` (`"es" | "en" | null`) y `accountReady`. Motivos exactos:
  - sin cuenta en ese idioma → `sin cuenta YouTube en inglés: crea la cuenta en Cuentas y pega su credencial`
  - cuenta del otro idioma → `idioma: el clip es en inglés pero la cuenta YouTube es en español: nunca se publica en el canal del otro idioma`
  - cuenta en el idioma pero sin credencial verificada → `cuenta YouTube en inglés sin credencial verificada: pega la credencial en Cuentas y verifícala`
- **POST /api/clips/:id/publish** (`worker/src/index.ts`): resuelve la cuenta por (plataforma, idioma del clip) con `getApiAccountByPlatform`; si falta o no está verificada, la plataforma queda frenada con el motivo y **no se crea ningún job**. NUNCA auto-crea cuentas. `upsertAccountCredential` (OAuth/onboarding) ahora recibe el idioma como parámetro (default `"es"`): ese flujo no lleva clip, así que conecta la cuenta principal en español; las cuentas EN se crean en Cuentas.
- **Topes por plataforma+idioma** (`POST /publish`): `publishedToday` cuenta solo jobs `posted` de hoy con `jobs.language = idioma del clip`; el espaciamiento usa la última subida `posted` en ese idioma (antes `lastPublishedAtMs` era siempre `null`: el espaciamiento no se aplicaba en este endpoint; ahora sí, por idioma). Plan/techo/madurez: la madurez ya era por cuenta y la cuenta ya es por plataforma+idioma, así que YouTube ES 3/día (tope 5) es independiente de YouTube EN 3/día (tope 5). Documentado en Ajustes (nota en el fieldset "Plan diario y cuentas nuevas") y en Canales ("por idioma: ES y EN tienen su propio tope").
- **Guarda del Worker** (`worker/src/publish/index.ts`, `dispatchPublishStep` + `resolveCreds`): la credencial se resuelve por (plataforma, idioma del clip, `api_account_id` del job). Si el job trae una cuenta de otro idioma (o no hay cuenta en ese idioma), queda en **draft** con el motivo de idioma. El fallback a la cuenta del otro idioma está prohibido.
- **GET /api/channels**: 8 entradas (4 plataformas × es/en), cada una con SU cuenta, SU cuota de hoy y SUS publicados (filtrados por `clips.language`).
- **Front** (`frontend/dist/app.js`, `index.html`, `styles.css`): Cola en dos secciones — "Cola en Español (N)" y "Cola en Inglés (N)" — cada una con sus segmentos LIVE/VOD y contadores. Cada tarjeta/fila lleva píldora ES (verde) / EN (violeta). El Editor muestra `Idioma: Español (ES)` / `Inglés (EN)` + píldora. Cuentas muestra `youtube · Inglés (en)` y el estado; Canales muestra 8 tarjetas (`YouTube · Español`, `YouTube · Inglés`, …).

## Tests

`bun test test/` — **187 pass, 0 fail** (11 archivos, 746 expects; 3 pasadas seguidas en verde). `tsc --noEmit` OK. `node --check frontend/dist/app.js` OK.

- Nuevo `worker/test/clip-language.test.ts` (15 tests): POST EN → 201 con `language: "en"`; `fr` → 400; sin idioma → `"es"`; PATCH en→es y `fr` → 400; `publishGate` con cuenta ES/cuenta ausente/cuenta EN/sin credencial/clip heredado; POST /publish EN con solo cuenta ES → 400 sin jobs; EN con cuenta EN → 200 con job `language: "en"`; tope EN 3/día frena el EN pero el ES publica; clip heredado publica por ES; `dispatchPublishStep` con cuenta ES para clip EN → draft con motivo.
- `worker/test/clip02.test.ts`: publicar exige ahora la cuenta en el idioma del clip, así que los tests de publish siembran cuentas ES verificadas (el de `publish_blocked` sigue frenando por subtítulos con el motivo exacto).
- `worker/test/endpoints-nuevos-2.test.ts`: `/api/channels` devuelve 8 entradas; los tests comprueban por `plataforma|idioma` y que los publicados ES no gastan la cuota EN.
- Flake preexistente arreglado en el test (no en producción): `endpoints-nuevos.test.ts` "revocar un token no afecta a un token nuevo" fallaba 1 de cada 2-3 pasadas porque `createSessionToken` solo lleva `exp` en ms y dos tokens del mismo milisegundo son idénticos; el test espera 2 ms antes de crear el segundo token.

## Verificación en vivo (login muse-servicio2, tenant sebastian, 2026-11-18 ~19:11 UTC)

| # | Comprobación | Resultado | Evidencia |
|---|---|---|---|
| 1 | Front desplegado con las dos colas | ✅ Pasa | `curl https://avc-em9.pages.dev/app.js` contiene `Cola en Inglés` y `queue-lang`; front 200 |
| 2 | Worker desplegado | ✅ Pasa | `/api/health` → `tenants: ["sebastian","colega","kaez"]`, `worker_running.sebastian: true` |
| 3 | Cuentas: YouTube EN pendiente sin credencial | ✅ Pasa | GET /api/accounts → `(3, 'youtube', 'en', 'YouTube', 'pendiente', False, None)`; meta/es y youtube/es `conectada` |
| 4 | Canales: 8 entradas por idioma | ✅ Pasa | GET /api/channels → 8: youtube es/en, tiktok es/en, instagram es/en, facebook es/en; youtube en `sin_credencial`, resto es/en según su cuenta |
| 5 | Clip EN de prueba (48 s, GTA VI, viralidad 55, SEO 92, `language: "en"`) | ✅ Pasa | POST /api/clips → 201, id 5, `language: "en"`; GET /api/clips lo lista con `language: "en"` |
| 6 | Clip ES de prueba (48 s, GTA VI, viralidad 55, SEO 92) | ✅ Pasa | POST /api/clips → 201, id 6, `language: "es"` |
| 7 | Publicar clip EN → bloqueado con motivo de idioma | ✅ Pasa | POST /api/clips/5/publish (tras preview-seen) → 400: `Ninguna plataforma pudo encolarse: youtube_shorts: cuenta YouTube en inglés sin credencial verificada: pega la credencial en Cuentas y verifícala`. Repetido 2 veces, mismo bloqueo |
| 8 | Ningún job creado por el intento EN | ✅ Pasa | GET /api/jobs → `jobs: []` |
| 9 | `language: "fr"` → 400 en vivo | ✅ Pasa | POST /api/clips → `{"ok":false,"error":"Idioma inválido: usa es o en."}` |
| 10 | PATCH idioma en vivo | ✅ Pasa | PATCH /api/clips/6 `{language:"en"}` → 200 `en`; PATCH `{language:"es"}` → 200 `es` |
| 11 | Clips de prueba borrados | ✅ Pasa | `wrangler d1 execute` remoto: 5 `clip_events` + 2 `clips` borrados; SELECT posterior id 5/6 → 0 filas; GET /api/clips → `[]`; GET /api/clips/5 → `Clip no encontrado.` |
| 12 | Nada publicado en público | ✅ Pasa | Ningún job creado en toda la verificación; la Cola quedó vacía |

**Pendiente de confirmación visual (sin navegador en este agente):** que el usuario (o el agente principal con navegador) abra la Cola en https://avc-em9.pages.dev y vea las secciones "Cola en Español" y "Cola en Inglés" con sus contadores, y en Cuentas la tarjeta "YouTube · Inglés (en) — pendiente, sin credencial" con su campo para pegar la credencial. La separación está verificada por API y el bundle desplegado contiene el código de las dos colas; falta solo verlo renderizado.

## Pendientes conocidos (FUERA de esta tarea, no tocados)

Cambio en Ajustes + restaurar; 2 TEST-WEB en D1 que GET no lista (revisar `archived`); Iniciar/Parar + logout; test de madurez; onboarding colega en vivo; TikTok dummy-key; `migrate` de `deploy_with_token.py all` roto por `schema.sql` obsoleto (usar `deploy-worker` + `deploy-pages` por separado hasta arreglarlo).
