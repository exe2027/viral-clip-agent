# Vigilante de publicación

> 2026-11-21, orden del usuario: hace automáticamente lo que hoy
> hace Muse por el chat cuando el Worker o las subidas fallan —
> revisa D1, reintenta lo atascado, verifica las URLs publicadas
> y avisa si no puede arreglarlo. Es el respaldo del plan
> "revísalo tras cada franja": la web intenta primero; el
> vigilante revisa tras cada franja.

No es el vigilante de `clipagent/watchdog.py` (ese vigila el
SUMINISTRO de clips: token de Twitch, despachos, escaneo en
vivo, VOD). Este vigila la PUBLICACIÓN: los jobs de hoy en D1,
base vieja `clips-avc`, ambos tenants (sebastian y kaez).

## Archivos

| Archivo | Qué es |
|---|---|
| `clipagent/publish_watchdog.py` | El módulo (todo: reglas, clasificación, verificación, republicaciones, informe, CLI). |
| `clipagent/d1_client.py` | Cliente D1 mínimo por HTTP (urllib, sin dependencias). Reutilizado aquí. |
| `clipagent/panel_bridge.py` | `PanelBridge.run_once()` (nuevo): un ciclo del Worker por API. |
| `clipagent/validate.py` | `"publish_watchdog"` en `KNOWN_TOP_KEYS`. |
| `config.yaml` | Sección `publish_watchdog:` (única fuente de sus valores). |
| `.github/workflows/cerebro-publish-watchdog.yml` | Programación tras cada franja. **APAGADO hasta 21:00.** |
| `test_publish_watchdog.py` | 23 tests (puros + orquestación con fakes). |
| `docs/publish-watchdog.md` | Este documento. |

## Cómo se programa

El workflow corre a las **14:30, 20:30 y 23:30 hora Chile**
(17:30, 23:30 y 02:30 UTC), tras las franjas 12:00–14:00 /
18:00–20:00 / 21:00–23:00, más `workflow_dispatch` manual
(input `live`, por defecto false). Concurrencia: una corrida a
la vez. Timeout 15 min. El informe queda como artefacto
(retención 7 días) y en el log.

## Qué hace en cada corrida (en orden)

1. **Lee D1** (base vieja `clips-avc`, ambos tenants): settings
   de ambos tenants, jobs de hoy, cuentas API y `worker_state`.
   Las reglas se resuelven desde los settings VIVOS de D1
   (YT 6/día, TT/IG/FB 3/día, espaciamiento 2 h / 4 h YT,
   calentamiento, rolling 3/24 h); si D1 no se puede leer, usa
   `limits.yaml` como fallback y lo dice en el informe
   (ojo: `limits.yaml` tiene YT plan 1/máx 2, DESACTUALIZADO
   frente a D1 6/6).
   Sin token de Cloudflare degrada a modo solo-panel: solo
   sebastian, sin republicaciones, y lo dice.
2. **Clasifica cada job** con el mismo espejo del gate del
   Worker (nunca lo sustituye: el Worker decide al ejecutar):
   `verify` (posted con URL), `ready` (listo para el ciclo),
   `held_*` (calentamiento / tope diario / rolling /
   espaciamiento / mismo clip hoy / sin cuenta), `draft`,
   `failed_terminal`, `failed_retryable`, `captcha`,
   `posted_no_url`, `processing`, `waiting_*`.
3. **Verifica en vivo cada URL publicada.** Un `posted` en D1
   NO es prueba: el reel FB del 168 estaba `posted` y estaba
   caído. YouTube/TikTok por oEmbed (FUERTE: 200 = vivo,
   404/401 = caído). Instagram/Facebook por GET anónimo es
   DÉBIL: un 200 no prueba nada (el muro de login devuelve
   200) — nunca basta para republicar; solo genera sospecha
   ("revisar a mano") y aviso. Sin red o con error, el
   veredicto es `unknown`, nunca "caído".
4. **Republicaciones (solo evidencia FUERTE de caída).** Si una
   URL YT/TT está realmente caída, crea un job de
   republicación para **mañana 12:00** (la regla de mismo clip
   hoy lo impide hoy) con idempotency key `|repubN` (la clave
   canónica colisiona por UNIQUE — lección del job 118),
   `state=scheduled`, `next_run_at` = mañana 12:00 Santiago,
   `slot_day` = mañana, `manual=1`, título/caption/r2_key
   copiados del job original. Si ya hay una republicación en
   curso, no crea otra. Si el clip está bloqueado o sin vídeo,
   no republica.
5. **Ciclo run-once (solo en ventana de reparación).** Solo
   dentro de los 60 min tras la franja que acaba de terminar,
   solo si hay jobs `ready`, como mucho **1 ciclo por corrida**,
   y solo si el Worker está en marcha. El ciclo es el del
   Worker (`POST /api/worker/run-once`, que corre sebastian Y
   kaez); su gate frena lo que no debe salir. Tras un ciclo en
   vivo, relee D1 y verifica lo que se publicó nuevo.
6. **Telegram solo si algo DE HOY no se pudo arreglar** (URL
   caída sin republicación, failed terminal de hoy, Worker
   parado, captcha, sospecha IG/FB de hoy). Lo viejo queda en
   el informe pero no avisa: si no, los 19 borradores TikTok
   cancelados anoche avisarían 3 veces al día, para siempre.
   Si Telegram falla, el mensaje queda como borrador en el log.

## Reglas que NUNCA se salta

- Franjas 12:00–14:00 / 18:00–20:00 / 21:00–23:00 (Chile).
  Fuera de la ventana de reparación (60 min tras la franja)
  solo verifica e informa; no dispara ciclos.
- Espaciamiento 2 h (4 h YouTube), topes diarios YT 6 /
  TT/IG/FB 3, calentamiento 1/día por plataforma en cuentas
  nuevas (ni a mano se supera), máx 3 por cuenta en 24 h,
  1 por plataforma y franja como mucho.
- Los jobs `draft` no se tocan (el gate los puso ahí por un
  motivo): se reportan con su motivo y cuándo salen.
- Los `failed` terminales no se resucitan el mismo día.
- Un Worker parado (`running=0`) NO se auto-arranca: se
  reporta ("pulsar Iniciar en el panel").
- El error 7500 de cuota D1 detiene todo de inmediato
  (circuito de `clipagent/retry.py`).

## Cómo se enciende a las 21:00 (2026-11-21)

1. **Añadir el secreto `CLOUDFLARE_API_TOKEN` al repo.**
   Es el token de la cuenta de Cloudflare (el mismo del
   conector `custom.cloudflare`); necesita lectura/escritura
   D1 sobre la base `clips-avc`. Sin él, el vigilante degrada
   a modo solo-panel (solo sebastian, sin republicaciones).
   ```bash
   gh secret set CLOUDFLARE_API_TOKEN --body "$TOKEN"
   ```
   El valor del token NUNCA va en el chat, en archivos ni en
   logs. `PANEL_USERNAME` / `PANEL_PASSWORD` ya existen como
   secretos (login de servicio de sebastian).
2. **Poner `WATCHDOG_ENABLED` en `'true'`** en
   `.github/workflows/cerebro-publish-watchdog.yml` y pushear.
   Con `'false'`, las corridas programadas corren en dry-run.
3. **Opcional, prueba de una vez:** dispatch manual del
   workflow con `live=true` (una sola corrida en vivo).

## Cómo se verifica en seco (sin escribir en D1)

```bash
# Simulación offline (fakes): con y sin caída
python -m clipagent.publish_watchdog --simulate
python -m clipagent.publish_watchdog --simulate-down
# Dry-run real de solo lectura contra D1 vivo
# (las lecturas no gastan cuota de escritura)
python -m clipagent.publish_watchdog --dry-run
# Tests
python -m pytest test_publish_watchdog.py test_no_secrets_in_repo.py -q
```

## Qué prueba falta (no se puede hacer hoy sin escribir en D1)

- Una corrida **en vivo tras una franja real** con un job
  atascado de verdad (que el ciclo lo avance) y con una URL
  caída de verdad (que cree la republicación `|repubN` y
  salga al día siguiente a las 12:00). La primera corrida
  en vivo tras las 21:00 es esa prueba: revisar su artefacto
  y confirmar que el ciclo corrió y que la republicación, si
  hubo, quedó en `scheduled` para mañana 12:00.
- El aviso por Telegram en vivo (solo salta si hay algo de
  hoy sin arreglar; en la simulación no se envía nada).

## Verificado el 2026-11-21 (dry-run real, solo lectura)

- 23/23 tests pasan (`test_publish_watchdog.py` + secreto).
- Simulación `--simulate`: 899/900 VIVA, 901 CAÍDA →
  republicación `|repub1` mañana 12:00 (dry-run), 902 listo
  → 1 ciclo (dry-run), 903 frenado por calentamiento →
  mañana, 904 failed terminal → sin arreglar. 0 ciclos, 0
  escrituras D1, 0 telegramas.
- Dry-run contra D1 vivo (~10:00, fuera de ventana): 95 jobs
  clasificados. Las 10 URLs publicadas hoy verificadas:
  169/171/170 YT VIVA, 168 TT VIVA, 168 YT VIVA, 167 TT VIVA,
  102 YT+TT EN VIVA; 168 FB SIN VERIFICAR (GET 400, muro de
  login — correctamente "unknown", no "caído"); 168 IG
  sospecha débil. Jobs 111/113/114/115/117/118 frenados por
  calentamiento → mañana. Nada que reparar (fuera de ventana).
