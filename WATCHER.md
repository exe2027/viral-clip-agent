# Watcher — monitor de influencers

`watcher.py` vigila la lista blanca de `influencers.yaml` y detecta
directos en vivo y VODs nuevos, para no tener que pegar la URL a mano.
El encolado manual por URL (`jobqueue.py add`) queda como opción
secundaria.

**Hoy la lista blanca es SOLO IlloJuan** (https://www.twitch.tv/illojuan,
Twitch, español, GTA). Es el único que hace contenido de GTA por
ahora y el caso de prueba del monitor. No hay otros 6: los demás se
agregarían después SOLO si hacen GTA (el usuario los sube a la
lista, y eso ya es permiso — ver [Lista blanca](#lista-blanca)).

## Lo que hace y lo que NO hace

Hace:

- Cada pasada, por cada influencer: ¿está en vivo? (título, juego,
  espectadores, desde cuándo) y ¿qué VODs nuevos hay desde la última
  pasada?
- Encola en `jobqueue.py` los VODs terminados que pasan TODOS los frenos.
- Guarda lo visto en `seen_vods.json` (gitignored) para no duplicar.

NO hace:

- **No publica nada.** El job queda en `queued` → el worker lo
  procesa → `ready_for_review` → un humano aprueba o rechaza.
  Publicar es otro bloque, con su propia aprobación.
- **No sube nada en el momento.** Regla de diseño (usuario,
  2026-11-16): si varios influencers están en vivo a la vez, la
  detección en vivo SÍ es para todos, pero los clips se hacen solo
  cuando termina el directo y sale el VOD. El watcher detecta en
  vivo y REGISTRA; encola SOLO VODs terminados. (El pre-análisis de
  un VOD en curso, sin publicar, queda como opción futura, no
  implementada.)
- **No pide permiso en nombre del usuario.** Solo lee datos públicos
  (directos y VODs). REGLA 2026-11-16: estar en la lista que sube
  el usuario YA es permiso; no hay ficha que registrar.
- **No contacta streamers ni usa cuentas del usuario.** Kick y
  Twitch se leen sin login.

## Lista blanca

La canónica es `influencers.yaml`. REGLA DEL USUARIO (2026-11-16):
estar en la lista que sube el usuario YA es permiso. Hoy IlloJuan
está en `concedido`, así que el watcher lo monitorea y **encola
sus VODs de GTA 6**. Solo `denegado` / `revocado` excluyen.
`jobqueue.py` usa además `allowlist` en `config.yaml` para encolar:
IlloJuan ya está en ambas.

## Frenos (todos, antes de encolar)

Si falla uno, el VOD no se encola y el motivo queda registrado en
`seen_vods.json` y en la salida:

1. **Kill-switch:** si existe `queue.kill`, no se encola NADA.
2. **Lista blanca:** influencer en `influencers.yaml` (solo
   `denegado` / `revocado` excluyen).
3. **Solo GTA 6 verificado:** la categoría/juego del VOD (o del
   directo) tiene que ser GTA 6. GTA V, GTA V RP u otro juego NO se
   encola. (Se usa `queue.detect_game`: `gta6` pasa; `other` o sin
   verificar, no.)
4. **Solo VOD terminado:** duración > 0 y directo ya cerrado. Un VOD
   en curso se registra como `skipped:vod_en_curso` y se re-revisa en
   la siguiente pasada.
5. **Dedup:** `seen_vods.json` guarda cada VOD visto con su
   resultado; un VOD ya visto no se vuelve a proponer. La cola
   además rechaza un VOD + idioma con job activo.
6. **Tope diario:** máx. 5 VODs nuevos por influencer y día
   (configurable: `watcher.max_new_vods_per_streamer_per_day`).
7. **Regla nº 1 (no-ban):** nunca más de 1 job por influencer a la
   vez en `processing`. Si el worker ya procesa un VOD suyo, el
   siguiente espera.
8. **Aprobación humana:** el job pasa por `ready_for_review`; nada se
   publica solo.

Además `jobqueue.py add` aplica sus propias validaciones (disco ≥ 2 GB
libres, dedup por VOD + idioma).

## Fuentes de datos (todo gratis, $0)

### Kick — API pública, sin key

La que usa la propia web de Kick. Reutiliza
`kick_downloader/core.py: http_json` (reintentos + fallback curl,
porque el API de Kick se cae a veces a mitad de respuesta).

- En vivo: `GET https://kick.com/api/v2/channels/{slug}` → campo
  `livestream` (`is_live`, título, categorías, espectadores).
- VODs: `GET https://kick.com/api/v2/channels/{slug}/videos` (con
  `source`, `start_time`, `duration` en ms). El primer ítem con
  `duration: 0` es el directo en curso.

### Twitch — Helix (oficial) o GQL público (sin credenciales)

**Helix (vía estable, recomendada):** API oficial, gratis, sin
tarjeta. Requiere `client_id` + `client_secret` de una app (ver
[Credenciales de Twitch](#credenciales-de-twitch-helix-gratis)).
Endpoints: `GET /helix/streams?user_login=` (en vivo o no, juego,
título) y `GET /helix/videos?user_id=&type=archive` (VODs nuevos).
Token de app por client credentials (dura ~60 días; el watcher lo
renueva solo si recibe un 401).

**GQL público (sin credenciales, lo que usa hoy):** `POST
https://gql.twitch.tv/gql` con el Client-ID público y estático de la
web de Twitch (`kimne78kx3ncx6brgo4mv6wki5h1ko`, el mismo que envía
cualquier navegador al abrir twitch.tv; no es una key personal ni un
secreto). Devuelve el stream en vivo y los últimos 20 VODs. Vía **no
oficial**: funciona hoy (probado 2026-11-16 con illojuan), pero
Twitch puede cambiarla o bloquearla. Si falla, el watcher lo dice y
Twitch queda en "no configurado" hasta poner credenciales Helix.
Detalle técnico: en GQL el id del stream NO es el id del VOD (en
Helix sí), así que el VOD en curso se detecta porque su `createdAt`
está a ≤ 120 s del inicio del directo.

**Sin credenciales, Twitch queda en modo GQL público** (lo reporta en
cada pasada, no falla, no encola nada distinto). Con credenciales,
usa Helix automáticamente.

## Credenciales de Twitch (Helix, gratis, sin tarjeta)

Opcional por ahora (el modo GQL público ya monitorea). Pasos:

1. Entrar a https://dev.twitch.tv/console con la cuenta de Twitch
   del usuario (o una cuenta cualquiera; no tiene que ser la del
   canal).
2. **Applications → Register Your Application.**
3. Nombre: p. ej. `viral-clip-agent-watcher`. OAuth Redirect URL:
   `http://localhost` (el flujo client credentials no usa
   redirección, pero el formulario la pide). Categoría: la que sea.
4. **Create.** Copiar el **Client ID**.
5. **New Secret** → copiar el **Client Secret** (solo se muestra una
   vez; guardarlo en el Secure Vault, nunca en chat ni en el repo).
6. Pasarlas al watcher por variables de entorno (mandan sobre
   `config.yaml`):
   ```bash
   export TWITCH_CLIENT_ID="..."
   export TWITCH_CLIENT_SECRET="..."
   ```
   o en `config.yaml` → `watcher.twitch.client_id` /
   `watcher.twitch.client_secret` (el archivo NO se sube con
   secretos: no commitear credenciales reales ahí).

## Uso

```bash
python3 watcher.py --once --dry-run     # una pasada, sin encolar (DEFECTO)
python3 watcher.py --once --live        # una pasada, encola lo que pase los frenos
python3 watcher.py --loop --live        # bucle cada poll_minutes (config.yaml)
python3 watcher.py --once --dry-run --streamer illojuan   # solo un influencer
python3 watcher.py --once --dry-run --state /tmp/seen-test.json  # estado aparte
```

- `--dry-run` es el defecto: hay que pedir `--live` explícito para
  encolar. El dry-run **sí** guarda lo visto en `seen_vods.json`
  (outcome `would_enqueue` o `skipped:<motivo>`), para que una
  segunda pasada no vuelva a proponer lo mismo; pero un VOD marcado
  `would_enqueue` **no queda gastado**: en `--live` se re-evalúa y se
  encola de verdad. Un VOD `skipped:juego_no_gta6` no se re-evalúa
  (el juego de un VOD terminado no cambia).
- Códigos de salida: `0` = pasada OK; `2` = algún influencer falló
  al consultarse (red/API); `1` = error de configuración.
- `--loop` duerme `poll_minutes` entre pasadas (defecto 15, en
  `config.yaml` → `watcher.poll_minutes`). Ctrl-C para parar.

### Config (`config.yaml` → `watcher`)

```yaml
watcher:
  poll_minutes: 15
  max_new_vods_per_streamer_per_day: 5
  state_file: "seen_vods.json"
  twitch:
    client_id: ""       # o TWITCH_CLIENT_ID
    client_secret: ""   # o TWITCH_CLIENT_SECRET
```

### Estado (`seen_vods.json`, gitignored)

```json
{
  "vods": { "<vod_id>": {
      "streamer": "illojuan", "platform": "twitch",
      "title": "...", "category": "Just Chatting", "game": "other",
      "start_time": "2026-11-15T18:25:21+00:00", "duration_s": 26750,
      "url": "https://www.twitch.tv/videos/2929822269",
      "first_seen": "...", "last_seen": "...", "live": false,
      "outcome": "skipped:juego_no_gta6", "job_id": 12 } },
  "live": { "<streamer>": { "stream_id": "...", "title": "...",
      "game": "...", "viewers": 60371, "started_at": "...",
      "last_seen": "..." } },
  "daily": { "<streamer>|2026-11-19": 2 }
}
```

`outcome` vale: `pending`, `would_enqueue`, `enqueued`,
`skipped:<motivo>` (`juego_no_gta6`, `kill_switch`,
`tope_diario`, `en_processing`, `vod_en_curso`, `ya_en_cola`).
Borrar el archivo es seguro: solo se pierde la memoria de lo visto
(la siguiente pasada volvería a revisar los últimos 20 VODs; la cola
seguiría deduplicando por VOD + idioma).

## Prueba de aceptación (2026-11-16, datos reales)

Caso de prueba: **IlloJuan, solo él** (Twitch, español). Tres pasadas
seguidas, sin encolar nada real:

**Pasada 1 — `--once --dry-run`, 22:19 (fuente: twitch-gql-publico,
sin credenciales):**

- **EN VIVO:** sí. `2 DÍAS 📅 ADIÓS GTA 5 👋🏻 QUE ALGUIEN ME
  PELLIZQUE 🥺 BUENOS AIRES ENDING 🥰 - Grand Theft Auto V #7
  [FINAL]`, juego **Grand Theft Auto V**, **60.371 espectadores**,
  desde las 18:41 UTC (15:41 hora de Chile). Solo registro: no se
  encola nada hasta que termine el directo.
- **Último VOD (2930700035):** el del directo en curso (empezó
  18:41 UTC, 3h38m y creciendo entre pasadas: 12.926 s → 13.120 s).
  **No se encola: directo en curso** (además es GTA V, no GTA 6).
- **Último VOD terminado (2929822269):** `3 DÍAS 📅 ÚLTIMOS MOMENTOS
  DICIENDO HAIGA 🥀 SECUNDARIAS DAY 🎉 - Grand Theft Auto V #6`,
  2026-11-15 18:25 UTC, 7h25m, categoría Just Chatting. **No se
  encola: es GTA V, no GTA 6** (el freno de juego).
- **Total:** 20 VODs revisados (19 terminados + 1 en curso), 0
  encolados, 0 que se encolarían, 20 descartados (todos por
  `juego_no_gta6`), 0 errores. Los VODs revisados van del GTA V #1
  (2026-11-08) al #7 (hoy), más San Andreas, CoD y DayZ: ninguno es
  GTA 6, como era de esperar antes del lanzamiento del 19 nov.

**Pasada 2 — `--once --dry-run` (dedup):** 19 VODs "ya vistos", 0
propuestas nuevas, 0 encolados. El dedup funciona: la segunda pasada
no propone nada nuevo.

**Pasada 3 — `--once --live` (los frenos en modo real):** 0
encolados; la cola sigue con su único job (`ready_for_review`, el de
antes). Los frenos aguantan también con `--live`.

**Tests offline:** `python3 test_watcher.py` — 11 tests, sin red
(fixtures de las respuestas reales de illojuan y coker): GQL
(live + VOD en curso), Kick (live + VODs), frenos (fuera de lista,
GTA V en lista, kill-switch, tope diario, 1 job en processing),
dedup, encolado end-to-end en BD temporal y dry-run sin tocar la
cola. `python3 test_queue.py` sigue en 32/32.

**Nota:** el VOD 2930700035 ya tiene trabajo manual en el repo
(`runs/2930700035/`, hecho a mano hoy): el watcher no lo toca, solo
lo registra como en curso.

## Qué queda listo antes del 19 nov, y qué es fase 2

**Listo hoy (2026-11-16):**

- Monitor (`watcher.py`) + docs (`WATCHER.md`, sección en README).
- Detección en vivo + VODs nuevos en Kick (API pública) y Twitch
  (Helix con credenciales, o GQL público sin ellas — probado con
  IlloJuan).
- Encolado con todos los frenos (lista blanca, solo GTA 6, solo VOD
  terminado, dedup, tope diario, 1 job en processing, kill-switch,
  aprobación humana), en `--dry-run` por defecto.
- Probado en vivo con IlloJuan (en vivo, GTA V) + tests offline
  (11/11) + cola sin tocar.

**Fase 2 (después del 19 nov / cuando el usuario lo active):**

- **Cron cada 15 min en producción** (hoy el watcher se corre a mano;
  la línea sería, por ejemplo):
  ```cron
  */15 * * * * cd /home/hatch/workspace/gta6-clips/viral-clip-agent && python3 watcher.py --once --live >> runs/watcher.log 2>&1
  ```
  Con `--live` solo cuando el usuario lo autorice.
- **Twitch con credenciales Helix** (cuando el usuario las configure;
  hoy funciona por GQL público, pero Helix es la vía estable).
- **Encolado automático continuo** (hoy: pasadas manuales en dry-run;
  el primer VOD de GTA 6 de IlloJuan se encolaría solo con
  el cron activo).
- **Pre-análisis de VOD en curso** (opción futura, sin publicar):
  hoy el watcher solo registra el directo en curso.

## Pendientes para el usuario

1. **Activar el cron** (fase 2) cuando quiera monitoreo continuo
   cada 15 min con `--live`. (IlloJuan ya está concedido en la
   lista: estar en la lista = permiso ya dado, 2026-11-16. No hay
   que contactarlo ni registrar ficha.)
2. **Credenciales Twitch (opcional):** crear la app gratis en
   dev.twitch.tv y pasar `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET`
   (ver arriba). Sin ellas, Twitch sigue por GQL público.
3. **Otros streamers:** solo si hacen GTA; el usuario los agrega
   a `influencers.yaml` (subirlos a la lista ya es permiso).
