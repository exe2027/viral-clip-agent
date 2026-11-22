# Crear tu cuenta en el panel de clips — guía para personas nuevas

Esta guía es para ti si quieres usar el panel para publicar tus
clips de GTA 6 en YouTube, TikTok, Instagram y Facebook sin tocar
código. El proceso tiene partes tediosas (permisos de Google y de
Meta), así que está escrito paso a paso, con qué hacer cuando algo
falla. Nada de lo que hagas aquí publica nada: el Worker queda
parado hasta que tú pulses Iniciar.

## Primero: pide tu cuenta

Tú no puedes crearte la cuenta solo: el administrador del panel
tiene que darte un usuario y una contraseña. Pídeselos y entra en
https://avc-em9.pages.dev. Al entrar, el asistente de onboarding
se abre solo y te guía por los 4 pasos de abajo. Si no se abre,
ve a la pestaña **Onboarding**.

## Antes de empezar: qué necesitas tener

| Para | Necesitas |
|---|---|
| YouTube | Un canal de YouTube (el tuyo) |
| TikTok | Una cuenta de TikTok + cuenta en https://zernio.com |
| Facebook + Instagram | Una **Página de Facebook** (no tu perfil personal) + una cuenta de Instagram **vinculada a esa Página** (ver "Meta: Página + Instagram" abajo) |
| Twitch y Gemini (opcional) | Cuenta en https://dev.twitch.tv/console/apps y en https://aistudio.google.com/apikey |

## Los 4 pasos del asistente

### Paso 1: configuración base

Pulsa copiar. El panel copia solo las REGLAS (duración de clips,
topes diarios, SEO, etc.) desde la configuración de referencia.
No se copia ningún secreto ni ninguna cuenta de nadie: cada persona
usa solo sus cuentas. Si ya tienes configuración, no se pisa.

### Paso 2: conectar tus cuentas (botones)

- **YouTube**: pulsa Conectar y autoriza con tu cuenta de Google.
  El panel guarda el acceso cifrado. Si el botón te dice que falta
  el cliente de Google, es cosa del administrador (ver
  "Bloqueadores" abajo): avísale y sigue con lo demás.
- **Meta (Facebook + Instagram)**: pulsa Conectar y autoriza con
  tu cuenta de Facebook. Con ese solo paso quedan conectadas las
  dos: la Página y su Instagram vinculado (ver "Meta: Página +
  Instagram"). Si Meta rechaza tu login, casi seguro es porque la
  app está en Desarrollo y tu usuario no es Tester: pídele al
  administrador de la app que te añada como Tester en Roles.
- **TikTok (por Zernio)**: entra en
  https://zernio.com/dashboard/connections, conecta tu cuenta de
  TikTok, y pega en el panel la **API key** y el **account id** de
  Zernio. No hay botón de OAuth para TikTok: es así por diseño.

### Paso 3: Twitch y Gemini (pegado, opcional)

Si los usas, pega el Client ID + Client Secret de Twitch (se crean
en https://dev.twitch.tv/console/apps) y la API key de Gemini
(https://aistudio.google.com/apikey). Si no los usas, sáltate este
paso: no bloquea nada.

### Paso 4: verificar todo

Pulsa **Verificar todo**. El panel comprueba cada cuenta contra su
plataforma de verdad. Solo un éxito real marca "Conectada". Si
alguna falla, el panel te dice el motivo exacto en pantalla: sigue
el consejo de "Errores típicos" de abajo, no reintentes a ciegas.

Cuando los 4 pasos estén en verde, pulsa **Iniciar** en la pestaña
Worker. A partir de ahí el panel trabaja solo.

## Meta: Página + Instagram (lo que más confunde)

**Por defecto, cada clip se publica en tu Página de Facebook Y en
tu cuenta de Instagram, desde la misma cuenta Meta.** No hay que
elegir nada: es el comportamiento por defecto (ver DECISIONS.md
§8). El panel descubre solo los dos datos al conectar: la Página y
la cuenta de Instagram vinculada a ella. No se pegan a mano.

### Antes de conectar Meta, deja esto listo

1. **Crea tu Página de Facebook** (si no la tienes): en Facebook,
   crea una Página para tu canal. No sirve tu perfil personal.
2. **Vincula tu Instagram a esa Página**: en Facebook, entra en tu
   Página → Configuración → Cuentas vinculadas → Instagram →
   Conectar, e inicia sesión con tu cuenta de Instagram. Tu cuenta
   de Instagram tiene que ser profesional (Empresa o Creador), no
   personal.
3. **No confundas la Página con tu perfil**: tu perfil personal
   puede llamarse igual que tu canal (y si lo tienes en modo
   profesional, se ve parecido), pero NO es la Página. Los reels
   se publican en la Página, nunca en tu perfil.

### Cómo comprobar que el vínculo está bien

En el Explorador de Graph de Meta
(https://developers.facebook.com/tools/explorer), con el token de
tu Página, corre `me?fields=id,instagram_business_account`. Si te
devuelve la cuenta de Instagram, el vínculo está bien. Si no sale
ese campo, la Página no tiene ninguna cuenta vinculada: vuelve al
paso 2.

## Errores típicos (y qué hacer)

| Lo que ves | Qué significa | Qué hacer |
|---|---|---|
| Meta verifica en verde pero Instagram falla con `Object ... does not exist, cannot be loaded due to missing permissions` | La Página perdió su cuenta de Instagram vinculada (o la cuenta está suspendida) | Re-vincula tu Instagram a la Página (paso 2 de arriba). Si tu Instagram está suspendido o en apelación, no se puede publicar ahí hasta que Meta lo devuelva |
| El botón Conectar de Meta rechaza tu login | La app de Meta está en Desarrollo y tu usuario no es Tester | Pide al administrador de la app que te añada como Tester en Roles (o que pase la app a Live) |
| El botón Conectar de YouTube dice que falta el cliente | El administrador no ha creado el cliente OAuth web de Google | Avísale al administrador (ver "Bloqueadores" abajo) |
| `me/accounts` da error `(#100) Tried accessing nonexisting field` | Corriste la consulta con el token de la Página, y las Páginas no tienen "accounts" | Cambia a "Token del usuario" en el Explorador, genera el token y repite la consulta |
| Zernio dice que no hay cuenta TikTok conectada | La API key vale, pero no hay ninguna cuenta TikTok conectada en Zernio | Conecta tu TikTok en https://zernio.com/dashboard/connections y pega el account id en el panel |
| Verificación de TikTok/YouTube/Instagram falla tras un rato sin usar | El token caducó o se revocó | Vuelve a conectar esa cuenta (Paso 2); no pegues el token viejo |

## Bloqueadores: qué tiene que crear el administrador en cada consola

Esto NO lo haces tú: lo hace quien administra el panel. Está aquí
para que sepas qué pedirle si un botón te dice que falta algo.

### 1. Google — cliente OAuth tipo Aplicación web

- Consola: https://console.cloud.google.com/apis/credentials
- Crear credencial **ID de cliente de OAuth → Aplicación web**
  (un cliente de escritorio NO vale: Google rechaza `redirect_uri`
  web en clientes de escritorio).
- **URI de redirección autorizada (exacta):**
  `https://avc.vikingosfoodspa.workers.dev/api/oauth/google/callback`
- Scopes que pide el panel:
  `https://www.googleapis.com/auth/youtube.upload` y
  `https://www.googleapis.com/auth/youtube.readonly`.
- Guardar como secretos del Worker `avc` (nunca en el repo):
  `wrangler secret put GOOGLE_CLIENT_ID` y
  `wrangler secret put GOOGLE_CLIENT_SECRET` (desde
  `panel-backend/worker`, con el token de Cloudflare).

### 2. Meta — OAuth de la app del panel

- Consola: https://developers.facebook.com/apps (la app del panel)
- Producto **Facebook Login → Configuración → Valid OAuth Redirect URIs**,
  añadir (exacta):
  `https://avc.vikingosfoodspa.workers.dev/api/oauth/meta/callback`
- Guardar como secretos del Worker `avc`: App ID →
  `wrangler secret put META_APP_ID`; App Secret (Configuración →
  Datos básicos → Clave secreta de la app) →
  `wrangler secret put META_APP_SECRET`.
- **La app está en Desarrollo: solo entran usuarios con rol o tester.**
  Para que una persona nueva conecte, hay que añadirla como
  **Tester** en Roles (o pasar la app a Live con App Review). Sin
  ese paso, Meta rechaza su login aunque el cliente esté creado.
- Scopes que pide el panel: `public_profile, pages_show_list,
  pages_read_engagement, instagram_basic, instagram_content_publish,
  pages_manage_posts, business_management`.

### 3. Zernio / TikTok — sin OAuth al panel (POR DISEÑO)

Zernio no admite un OAuth con redirect al panel: la conexión de
TikTok se hace en su dashboard. Cada persona entra en
https://zernio.com/dashboard/connections, conecta su cuenta TikTok
y copia la **API key** y el **account id** en el paso 2 del
asistente (o en Cuentas). La verificación es real contra
`zernio.com/api/v1`.

### 4. Twitch y Gemini — pegado (sin bloqueador)

- Twitch: https://dev.twitch.tv/console/apps → Client ID + Client
  Secret de la app. Verificación real: token de app + Helix.
- Gemini: https://aistudio.google.com/apikey → API key.
  Verificación real: listar modelos con la key.

## Regla de tenants nuevos (solo administrador)

Un tenant nuevo NO funciona solo con la fila en D1 `panel_users`.
Hay que hacer DOS cosas:

1. Fila en `panel_users` (username, hash PBKDF2, role, status activo,
   `tenant_id` = el tenant nuevo).
2. Añadir el tenant a `TENANTS` en `worker/src/auth.ts`.

Si falta el paso 2, el login da 200
(`{"ok":true,"tenant":"<nuevo>"}`) pero el token no verifica:
`verifySessionToken` rechaza el tenant con `isTenant` y
`GET /api/auth/me` da 401 "Sin sesión.". El `/api/health` lista
los tenants desde `[...TENANTS]`, así que si el tenant nuevo
aparece ahí, el Worker desplegado ya lo acepta.

## Historial (no son instrucciones)

- 2026-11-18: verificación en vivo con el tenant colega. El
  asistente se abre solo al entrar, la base se copió (reglas sin
  secretos), los botones Google/Meta muestran el bloqueador exacto
  (sin cliente), Zernio/Twitch/Gemini aceptan el pegado y su
  verificación da error limpio con claves falsas, y el Worker
  quedó parado. Nada del tenant sebastian se tocó.
- 2026-11-18: lección del clip 13 (Meta verificaba en verde pero
  Instagram fallaba): la verificación ahora también comprueba la
  cuenta de Instagram, no solo el token (ver DECISIONS.md §8).
