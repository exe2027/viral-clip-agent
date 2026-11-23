# Engagement automático — riesgo de ban/shadowban (investigación 2026-11-23)

Fecha: 2026-11-23. Autor: investigación previa al arreglo del flag
`engagement_enabled` (bug de tipo: D1 guardaba `1`, `db.ts:714`
exigía `=== true`). Regla del usuario: investigar antes de hacer
cualquier cosa; no ban, no shadowban; queremos hacer plata.

## 1. Qué hace realmente cada pasada (código, no memoria)

- Orquestador `runEngagement` (native-engagement.ts): nativa
  YT/IG/FB + Zernio solo TikTok. 1 pasada/hora máximo (throttle),
  al final del ciclo tras analíticas, nunca tumba el ciclo.
- Adentro: lee comentarios en TUS posts (Zernio inbox o API nativa),
  máximo 10 posts y 10 comentarios por post por pasada.
- Respuestas: 8 plantillas ES + 8 EN, elegidas por hash del
  commentId (determinista, variada entre comentarios, nunca repite
  la anterior en la pasada), con el título del clip cuando se
  conoce. Nunca responde 2 veces al mismo comentario
  (`engagement_replied`), nunca a comentarios propios, nunca a
  comentarios vacíos, con enlace o de más de 500 caracteres.
- Pausa 8-15 s aleatoria entre escrituras. Topes por
  plataforma+idioma+día (Santiago): respuestas 10, likes a
  comentarios 10, likes afuera 5, comentarios afuera 1.
- Afuera: SOLO objetivos aportados por el usuario con su URL
  (POST /api/engagement/targets, streamer activo con permiso, texto
  específico de 10-280 caracteres, nada genérico). Sin objetivos,
  afuera no hace nada. Hoy: 0 objetivos en seba y kaez.
- Errores 401/403 cortan la plataforma en esa pasada; todo queda
  en `engagement_log` / `worker_events` con reason.
- Sin follow en ninguna vía. Sin TikTok hacia afuera (Like post
  devuelve 400 en Zernio: se marca skipped sin llamar). Sin
  navegador, sin scraping, sin contraseñas.
- Nativa: NUNCA llama a comentar/likear sin sonda previa de
  scopes. Sin permiso → `sin_permiso` con el scope exacto, sin
  llamar. Sin META_APP_ID/SECRET → `sin_sonda`, sin llamar.

## 2. Riesgo por vía (fuentes web, nov 2026)

Fuentes: CommentShark (YouTube, oct 2026), ActivePieces/Blabla
(YouTube, nov 2026), SmartReply/Maedix/ReachLee (Meta, 2026),
InstantDM/OpenHosst (TikTok, ago 2026), docs.zernio.com
(Comments API, Platforms/TikTok). Son guías de terceros sobre las
políticas oficiales, no las páginas de políticas mismas: los
veredictos de abajo distinguen hecho verificado en código de
inferencia de esas fuentes.

### Adentro (responder comentarios en TUS posts) — RIESGO BAJO
- YouTube: responder en tu propio canal por la API oficial es
  gestión de comunidad permitida; lo sancionado es comentar en
  vídeos ajenos a volumen, repetitivo o engañoso (spam policy +
  fake engagement; 3 strikes en 90 días pueden cerrar el canal).
  Cuota: 50 unidades por respuesta, 10.000/día por proyecto →
  10 respuestas/día son 500 unidades, despreciable.
- Meta (IG/FB): la Graph API está hecha para responder a quien
  actúa primero; nunca iniciar, 1 private reply por comentario
  en 7 días, rate limits documentados. Lo sancionado es la
  automatización NO oficial (bots de navegador, contraseña
  compartida, DMs en frío): action block y shadowban (alcance
  reducido sin aviso).
- TikTok: la vía menos verificada. Zernio documenta que su inbox
  de TikTok exige conexión por la app de TikTok for Business y
  que las automatizaciones de comentarios son solo public reply.
  NO consta que Zernio sea TikTok Marketing Partner, y varias
  fuentes señalan que TikTok no documenta webhook oficial de
  auto-respuesta a comentarios públicos (el auto-mensaje nativo
  es para DMs). Los términos de TikTok (actualizados jul 2026)
  prohíben scripts/bots no aprobados por escrito y la fake
  engagement. Por eso TikTok queda en riesgo MEDIO y es el que
  más protege el modo seguro.

### Afuera (likes/comentarios en posts de terceros) — RIESGO ALTO
- Las tres plataformas lo tratan como spam / fake engagement
  cuando es templado, repetitivo o a volumen: comentarios
  eliminados, restricción de funciones, shadowban y, en YouTube,
  strikes. Es el único riesgo que NO se puede mitigar solo con
  topes: un comentario automático en un post ajeno es spam por
  diseño aunque sea 1 al día.
- Mitigación aplicada: en modo seguro afuera = 0. Después, los
  topes (5 likes, 1 comentario) siguen, pero solo con objetivo
  aportado a mano y texto específico. Recomendación: mantener
  afuera en manual (solo con URL y texto del usuario), nunca
  automático por descubrimiento.

### Vía navegador + extensión — NO SE HACE
- La extensión AVC v1.3.0 no hace engagement (content_scripts
  [], solo storage+alarms). Construir engagement por navegador
  sería automatización por sesión (la categoría que todas las
  plataformas prohíben explícitamente) y es más riesgoso que la
  API. No se construye en este trabajo.

## 3. Modo seguro (implementado por defecto, sin configurar nada)

Primeras 24 h tras la primera pasada (`engagement_first_run_at`
en settings, estampada por el runner): 2 respuestas y 2 likes a
comentarios por plataforma+idioma+día, 0 hacia afuera. Pasadas
las 24 h, mandan los topes configurados (10/10/5/1). El modo
sale solo; el panel lo muestra en `GET /api/engagement/status`
(`safe_mode`, `safe_caps`). Implementado en
`engagementCaps()` (zernio-engagement.ts), usado por la pasada
Zernio y por la nativa. Tests en
`test/zernio-engagement.test.ts` (flag 1/true/0/false + modo
seguro en la primera pasada y tras 24 h).

## 4. Qué NO es un bloqueador (y qué sigue pendiente)

- No hay bloqueador para adentro a bajo volumen con dedupe,
  plantillas variadas, pausa 8-15 s y throttle 1 h.
- Pendiente del usuario: re-autorizar YouTube (force-ssl) y Meta
  (instagram_manage_comments, instagram_manage_engagement,
  pages_manage_engagement) para que la sonda nativa dé `ok`;
  configurar META_APP_ID/SECRET en el Worker (sin eso, Meta da
  `sin_sonda` y no se llama a nada); cuentas EN de kaez (sin
  ellas no hay engagement EN ahí). Sin eso, tras el arreglo del
  flag solo TikTok vía Zernio puede actuar.
- Primera pasada: vigilar `engagement_log` y `worker_events`. Si
  aparece un 403/429 o un action block en algún panel, el
  kill-switch es apagar el toggle en Ajustes (o `engagement_enabled`
  a false) y avisar; no se reintenta a ciegas.
