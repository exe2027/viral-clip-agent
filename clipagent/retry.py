#!/usr/bin/env python3
"""Frenos anti-recaída A4 (2026-11-20, encargo "que no vuelva a
pasar").

ÚNICA FUENTE de los umbrales de reintento del cerebro: backoff,
tope por día, error 7500 y circuito de cuota. Ningún otro módulo
del cerebro duplica estos literales (panel_bridge.py y watchdog.py
importan de aquí).

El problema que arregla: el 19-11 el auto-enqueue del panel
reintentó los mismos clips ~400 veces en un día (~15.000
escrituras D1) y el cerebro no tenía ningún freno propio ante
llamadas fallidas al panel: un panel caído o con la cuota agotada
se reintentaba sin límite. Por eso:

  - Toda llamada reintentable al panel usa backoff 1/5/15/60 min
    y tope 3 reintentos por operación y día (RetryBudget).
  - Ante un error 7500 (cuota D1 agotada) NO se reintenta nada y
    se abre el circuito del panel hasta el reset de cuota.

Estado del freno y qué pasa si el proceso reinicia: el presupuesto
de reintentos y el circuito viven EN MEMORIA del proceso (un run
de GitHub Actions es efímero: no hay disco durable entre corridas
y el state.json del vigilante tampoco sobrevive). Eso acota los
reintentos DENTRO de una corrida, que es donde vive la tormenta
de reintentos (el mismo proceso reintentando la misma llamada en
bucle). Entre corridas, el freno durable es el del lado del panel
(Worker: retry.ts — tope por job/día derivado de la fila del job
y circuito 7500 por lectura) y el del vigilante (watchdog.py: el
tope por día se cuenta en el historial de GitHub, que sí
persiste). Un proceso nuevo arranca con el presupuesto limpio y
el circuito cerrado: si el panel sigue con la cuota agotada, la
primera llamada vuelve a dar 7500 y el circuito se abre con ese
único intento (una llamada fallida no consume cuota D1).

Días: hay DOS "días" distintos y es a propósito.
  - Día de Santiago (America/Santiago): el tope de reintentos y
    de re-disparos. Es el mismo día que posted_day y la
    idempotency_key del panel.
  - Día de cuota D1: la cuota se libera a las 00:00 UTC = 21:00
    -03. El circuito 7500 se abre hasta ese reset, no hasta
    medianoche de Santiago: si se abriera hasta medianoche de
    Santiago, el cerebro seguiría parado 3 h después de liberarse
    la cuota (21:00 -> 00:00).
"""
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Backoff entre reintentos, en minutos: 1.er reintento a 1 min,
# 2.º a 5, 3.º a 15, 4.º a 60. Con el tope de 3 reintentos/día el
# 4.º peldaño (60 min) nunca se programa el mismo día: queda
# reservado en la tabla para que, si el tope se sube algún día,
# el backoff ya lo contemple sin tocar más código.
RETRY_BACKOFF_MINUTES = (1, 5, 15, 60)

# Tope de reintentos por operación y día (Santiago). Agotado, la
# operación queda parada hasta el día siguiente.
MAX_RETRIES_PER_DAY = 3

# Código de error de Cloudflare D1 cuando la cuota diaria de
# escrituras (100.000/cuenta) está agotada.
D1_QUOTA_ERROR_CODE = 7500

SANTIAGO_TZ = "America/Santiago"


def retry_delay_seconds(retry_number):
    """Segundos de espera antes del reintento N (1 = primer
    reintento): 60, 300, 900, 3600. None si N está fuera de la
    tabla."""
    if not isinstance(retry_number, int) or isinstance(retry_number,
                                                      bool):
        return None
    if retry_number < 1 or retry_number > len(RETRY_BACKOFF_MINUTES):
        return None
    return RETRY_BACKOFF_MINUTES[retry_number - 1] * 60


def can_retry_today(retries_today):
    """¿Cabe otro reintento hoy? ``retries_today`` = reintentos ya
    usados hoy por esa operación."""
    return retries_today < MAX_RETRIES_PER_DAY


def is_d1_quota_error(exc_or_text):
    """¿Es un error de cuota D1 agotada? Basta con que mencione el
    7500, o que sea un error D1 de cuota/límite. Acepta excepciones
    o texto. Falla cerrado a False. Nota: el texto exacto del error
    D1 no se pudo verificar sin escribir en D1 (cuota agotada al
    implementar esto): se casa por el código 7500 y por las
    palabras límite/cuota como respaldo."""
    text = str(exc_or_text or "")
    if re.search(r"\b7500\b", text):
        return True
    return bool(re.search(r"d1", text, re.I)
                and re.search(r"quota|cuota|l[ií]mite|limit|exceeded",
                              text, re.I))


def is_transient_panel_error(exc_or_text):
    """¿Es un fallo transitorio del panel (reintentable)? 429, 5xx
    o fallo de red/conexión. Un 400/401/403/404 NO es transitorio:
    reintentarlo no lo arregla. Un 7500 tampoco: lo cubre el
    circuito (ver is_d1_quota_error)."""
    if is_d1_quota_error(exc_or_text):
        return False
    text = str(exc_or_text or "")
    if re.search(r"HTTP (429|5\d\d)\b", text):
        return True
    return bool(re.search(
        r"URLError|urlopen|timed out|timeout|Connection reset|"
        r"Connection refused|Temporary failure|Network is unreachable",
        text, re.I))


def santiago_day_key(now_ms=None):
    """Día YYYY-MM-DD en America/Santiago del instante dado."""
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    dt = datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(SANTIAGO_TZ))
    return dt.strftime("%Y-%m-%d")


def santiago_day_start_ms(now_ms=None):
    """Inicio del día de Santiago (ms epoch) que contiene now_ms."""
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    dt = datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(SANTIAGO_TZ))
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


def next_d1_reset_ms(now_ms=None):
    """Próxima liberación de la cuota D1: 00:00 UTC tras now_ms
    (= 21:00 -03 del día anterior en Santiago)."""
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    reset = dt.replace(hour=0, minute=0, second=0,
                       microsecond=0) + timedelta(days=1)
    return int(reset.timestamp() * 1000)


def redispatch_allowed(dispatch_times_today_ms, now_ms=None):
    """¿Se puede re-disparar el cerebro ahora? (vigilante).

    ``dispatch_times_today_ms``: created_at (ms) de los
    re-disparos por workflow_dispatch de HOY (día de Santiago),
    leídos del historial durable de GitHub. Devuelve
    (permitido, motivo): tope 3 re-disparos/día; entre ellos, el
    backoff de la tabla (tras 1 re-disparo espera 1 min, tras 2
    espera 5 min). Sin re-disparos hoy, siempre se puede.
    """
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    times = sorted(dispatch_times_today_ms or [])
    n = len(times)
    if n >= MAX_RETRIES_PER_DAY:
        return False, (f"tope de {MAX_RETRIES_PER_DAY} re-disparos "
                       f"por día alcanzado ({n} hoy)")
    if n == 0:
        return True, ""
    last = times[-1]
    delay_ms = RETRY_BACKOFF_MINUTES[n - 1] * 60000
    waited_ms = now - last
    if waited_ms < delay_ms:
        missing_min = (delay_ms - waited_ms) / 60000
        return False, (f"backoff: faltan {missing_min:.0f} min para "
                       f"el siguiente re-disparo")
    return True, ""


# ---------------------------------------------------------------------------
# Presupuesto de reintentos por operación y día (en memoria)
# ---------------------------------------------------------------------------

class RetryBudget:
    """Cuántas veces se reintentó cada operación hoy.

    La clave es "MÉTODO ruta" normalizada por el llamador (p. ej.
    "POST /api/clips/102/publish": por clip, para que el tope sea
    por clip y no global). El día es el de Santiago. Solo memoria:
    ver el encabezado para qué pasa si el proceso reinicia.
    ``now_fn`` es inyectable (tests): devuelve ms epoch.
    """

    def __init__(self, now_fn=None):
        self._counts = {}
        self._now_fn = now_fn or (lambda: int(time.time() * 1000))

    def _day(self):
        return santiago_day_key(self._now_fn())

    def retries_used(self, key):
        return self._counts.get((key, self._day()), 0)

    def can_retry(self, key):
        return can_retry_today(self.retries_used(key))

    def record_retry(self, key):
        day = self._day()
        self._counts[(key, day)] = self._counts.get((key, day), 0) + 1
        return self._counts[(key, day)]

    def reset(self):
        self._counts.clear()


# ---------------------------------------------------------------------------
# Circuito 7500 del panel (en memoria, por proceso)
# ---------------------------------------------------------------------------

_panel_quota_tripped_at_ms = None


def trip_panel_quota_circuit(now_ms=None):
    """Abre el circuito: ninguna llamada al panel se intenta hasta
    el reset de cuota. Solo memoria (no escribe nada)."""
    global _panel_quota_tripped_at_ms
    _panel_quota_tripped_at_ms = int(
        now_ms if now_ms is not None else time.time() * 1000)


def panel_quota_circuit_open(now_ms=None):
    """¿Está abierto el circuito? Se cierra solo al llegar el reset
    de cuota (00:00 UTC); al cerrarse se olvida el disparo."""
    global _panel_quota_tripped_at_ms
    if _panel_quota_tripped_at_ms is None:
        return False
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    if now >= next_d1_reset_ms(_panel_quota_tripped_at_ms):
        _panel_quota_tripped_at_ms = None
        return False
    return True


def reset_panel_quota_circuit():
    """Solo para tests: vacía el estado en memoria del circuito."""
    global _panel_quota_tripped_at_ms
    _panel_quota_tripped_at_ms = None
