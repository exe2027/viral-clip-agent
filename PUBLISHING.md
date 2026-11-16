# Publicación — Bloque 7 del tribunal

`publisher.py` es el único módulo que publica. Nada se publica solo:
todo clip pasa por cola → revisión → aprobación → publicación, y la
lista blanca se verifica EN EL MOMENTO de publicar.

## El mapa: qué se publica cómo

| Plataforma | Modo | ¿Directo por API? |
|---|---|---|
| YouTube Shorts | API oficial (Data API v3, `videos.insert`) | **Sí, la única** |
| TikTok | Draft-first (paquete manual) | No, hasta app aprobada + auditoría en verde |
| Instagram Reels | Draft-first (paquete manual) | No, hasta cuenta Profesional + Page vinculada |
| Facebook Reels | Draft-first (paquete manual) | No, hasta Page + rol + token |

Interfaz común por plataforma: `publish_draft()` / `publish()` / `status()`.
`publish()` sin `--live` es dry-run: valida y no sube nada.

## YouTube Shorts por API

### OAuth 2.0 (pasos, sin credenciales creadas aquí)

1. Proyecto en Google Cloud con **YouTube Data API v3** habilitada.
2. Credenciales OAuth → **ID de cliente de escritorio** → descargar
   como `client_secrets.json` en la raíz del repo (está en `.gitignore`,
   nunca se sube a GitHub).
3. Primera ejecución: flujo de consentimiento con el scope
   `https://www.googleapis.com/auth/youtube.upload` → se guarda
   `token.json` (también ignorado por git).
4. Subida: `videos.insert`, partes `snippet` + `status`, video
   1080x1920, título ≤60 car., descripción, tags ≤15, categoría 20
   (Gaming), `privacyStatus` `public` (o `unlisted` para revisar
   primero y luego pasar a público en YouTube Studio).

Librería: `google-api-python-client` + `google-auth-oauthlib`
(`pip install google-api-python-client google-auth-oauthlib`).
Sin ella, `publish()` se niega; sin `client_secrets.json` /
`token.json`, también.

### Cuota (lo que manda el tope diario)

- Cuota diaria por proyecto: **~10.000 unidades**.
- Una subida (`videos.insert`): **~1.600 unidades**.
- Máximo por cuota: **~6 Shorts/día** (10.000 / 1.600).
- Plan del proyecto: **3-5/día** (3 planeados = 4.800 unidades,
  con margen para errores y reintentos).
- La cuota se resetea a medianoche hora del Pacífico (EE. UU.).
  Si se agota, la API devuelve 403 `quotaExceeded`: esperar al día
  siguiente, no insistir.

## Draft-first: TikTok, Instagram, Facebook

`publish_draft()` genera en `drafts/<clip>_<plataforma>/`:

- `draft.json` — paquete completo (archivo, caption, hashtags,
  portada, reglas de duración y subtítulos, estado de lista blanca).
- `UPLOAD.txt` — instrucciones de subida manual, con el caption
  listo para copiar.

Nunca hay publicación directa sin auditoría aprobada: `publish()`
en estas plataformas devuelve el draft y se niega mientras la
auditoría no esté en verde. El cliente de subida directa por API
(TikTok Direct Post, Graph API) NO está implementado todavía: es
trabajo futuro, después de que cada cuenta pase su auditoría.

## Auditoría de cuentas — `audit_accounts()`

Checklist pasa/no-pasa por cuenta. Sin fila en verde, esa cuenta
queda en draft-first (o bloqueada). Estado real a 2026-11-16
(`python publisher.py audit`):

| Cuenta | Estado | Lo que falta |
|---|---|---|
| YouTube ES @elclipdegta6 | 🟡 AMBER | Verificar canal + OAuth (client_secrets.json / token.json) |
| TikTok ES @elclipdegta6____bits | 🟡 AMBER | App aprobada en TikTok for Developers + scopes `video.upload`, `user.info.basic` (1-4 sem.) |
| Instagram ES @elclipdegta6 | 🔴 RED | Cuenta NO creada (Instagram rechazó los códigos el 2026-11-16); luego pasar a Profesional + vincular Page |
| Facebook Page El Clip de GTA 6 | 🔴 RED | Page NO creada; luego rol admin/editor + Page access token |

Checklist por cuenta:

- **YouTube:** canal creado, canal verificado, OAuth configurado,
  cuota suficiente, sin strikes.
- **TikTok:** cuenta creada, app aprobada, scopes `video.upload` +
  `user.info.basic`.
- **Instagram:** cuenta creada, cuenta Profesional (Creator/Business),
  Página de Facebook vinculada.
- **Facebook:** Página creada, rol admin/editor, Page access token.

El estado vive en `accounts.yaml` (editable); si no existe, se usan
los valores por defecto, que son el estado real conocido.

## Cola de publicación

Estados (diseño de `queue.py`, creado en paralelo por otro agente;
este módulo es compatible con él):

```
queued → ready_for_review → approved → published / failed
```

Más dos estados propios de este módulo:

- `draft` — paquete manual generado, sin publicar (draft-first).
- `quarantined` — terminal, rechazo por copyright/contenido.

La cola persiste en `publish_queue.json`. Flujo:

1. `enqueue(clip, plataforma)` — el clip entra en `queued`.
2. Revisión humana en el panel → `ready_for_review`.
3. Aprobación del usuario → `approved` (con `scheduled_for` opcional).
4. `publish()` → `published` (con `platform_id`) o `failed`.

## Topes y franja

| Plataforma | Plan/día | Techo/día |
|---|---|---|
| YouTube Shorts | 3 | 5 (cuota: 6) |
| TikTok | 2 | 3 |
| Instagram Reels | 2 | 3 |
| Facebook Reels | 2 | 3 |

- **Espaciamiento:** mínimo 2 h entre subidas en la misma plataforma
  (ideal 2-3 h).
- **Franja:** 18:00–23:59 hora de Chile (tarde/noche). Fuera de
  franja, `publish()` se niega.
- **Cuentas nuevas:** 1 publicación/día por plataforma la primera
  semana (ver README, "Supervivencia por plataforma"). Los topes de
  arriba son el techo una vez calentada la cuenta.
- **No a la misma hora en las 4 plataformas:** escalonar (YT tarde,
  TikTok noche, etc.) para que no parezca una subida coordinada.

## Lista blanca — verificación AL PUBLICAR

`influencers.yaml` (Bloques 8+9, fuente canónica) es la lista de
streamers con permiso vigente: solo vale el estado `concedido` con
ficha completa en `PERMISOS.md`. `allowed_streamers.yaml` es un atajo
con el mismo fin (formato simple `streamers: [{name, ...}]`) y también
se lee. `check_whitelist()` relee ambas **en cada publicación**: si
el streamer revocó el permiso entre la detección y la subida, la
subida se bloquea aunque el clip ya estuviera aprobado.

- **Hoy (2026-11-16): la lista está VACÍA.** Nadie ha dado permiso.
  Coker, Locones y peereira7/NoraExplorer tienen clips de prueba,
  pero NO se publican hasta tener permiso.
- La evidencia del permiso (captura, mensaje, correo) va en
  `PERMISOS.md`; aquí solo van los nombres con permiso vigente.
- Sin streamer identificado en el manifiesto → bloqueado.

## Rechazos y cuarentena — `handle_rejection()`

| Rechazo | Acción |
|---|---|
| Error de formato (400, archivo, título, duración) | Corregir y **1 reintento**; el 2º fallo va a cuarentena |
| Copyright / contenido / reutilización (403, claim, strike, Content ID) | **Cuarentena inmediata**, revisión humana, **nunca reintento ciego** |

La cuarentena es un estado terminal en la cola (`quarantined`, con
el motivo guardado). Sacar un clip de cuarentena es decisión humana
explícita, nunca automática.

## Uso

```bash
# Auditoría de las 4 cuentas ES
python publisher.py audit

# Validar una subida sin subir nada (dry-run, modo por defecto)
python publisher.py dry-run --clip runs/kick-coker-01a1dffd/clips/clip_01_t03h14m50s_s56.5_r14h1_v2.mp4 --platform youtube_shorts

# Generar paquete manual (TikTok / IG / FB)
python publisher.py draft --clip <clip.mp4> --platform tiktok

# Subida real a YouTube (exige OAuth + dry-run en verde + --live)
python publisher.py publish --clip <clip.mp4> --platform youtube_shorts --live
python publisher.py publish --clip <clip.mp4> --platform youtube_shorts --live --privacy unlisted

# Estado de una plataforma
python publisher.py status --platform youtube_shorts

# Mandar un ítem a cuarentena
python publisher.py quarantine --clip <clip.mp4> --platform tiktok --reason "copyright"
```

## Prueba 2026-11-16 (dry-run, sin subir nada)

Clip: Coker, `runs/kick-coker-01a1dffd/clips/clip_01_t03h14m50s_s56.5_r14h1_v2.mp4`
(1080x1920, 60,0 s, 17,7 MB, con subtítulos quemados y layout apilado).

Dry-run YouTube (`python publisher.py dry-run ... --platform youtube_shorts`):
exit 1, `would_publish: false`.

- ✅ Paquete: archivo, resolución, duración, título (32 car.),
  10 tags, caption — todo pasa. Topes: 0/3 hoy. Cuota: plan 3/día =
  4.800 / 10.000 unidades.
- ❌ **Lista blanca: `coker` NO está en la lista blanca**
  (`influencers.yaml` y `allowed_streamers.yaml` vacíos). Bloqueo duro.
- ❌ **Auditoría YT: amber** — canal sin verificar, sin OAuth.
- ❌ **OAuth: sin `client_secrets.json` / `token.json`.**

Auditoría (`python publisher.py audit`): YT 🟡 AMBER, TikTok 🟡 AMBER,
Instagram 🔴 RED, Facebook 🔴 RED. Ninguna cuenta en verde: hoy
TODO es draft-first y nada se puede publicar por API.

Draft TikTok del mismo clip: generado en
`drafts/clip_01_t03h14m50s_s56.5_r14h1_v2_tiktok/` con advertencia
de lista blanca — el paquete existe, pero **no se sube** hasta tener
el permiso de Coker.

## Lo que falta para publicar de verdad

1. Permiso de Coker (o del streamer que sea) → `influencers.yaml`
   (estado `concedido`) + evidencia en `PERMISOS.md`.
2. YouTube: verificar el canal + OAuth (`client_secrets.json`, `token.json`).
3. TikTok: crear y aprobar la app en TikTok for Developers (1-4 sem.).
4. Instagram: crear @elclipdegta6 (reintentar códigos) → Profesional →
   vincular Page.
5. Facebook: crear la Page El Clip de GTA 6 → rol + token.
6. Aprobación del usuario por clip (cola `approved`) antes de cada subida.
