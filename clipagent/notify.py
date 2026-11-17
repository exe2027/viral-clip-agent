"""Notificaciones opcionales por Telegram (Fase 6.6).

SOLO informativas ("publicado", "plataforma pausada", "falló X").
Nunca piden permiso ni esperan respuesta: el sistema no se para
por Telegram y un fallo de red no rompe la publicación.

Configuración (solo por entorno o .env, nunca en YAML):
  TELEGRAM_BOT_TOKEN: token del bot (creado con @BotFather).
  TELEGRAM_CHAT_ID: id del chat (el usuario, o su grupo).
Sin esas dos variables, notify() no hace nada (devuelve False).
"""
import json
import sys
import urllib.request

from . import credentials as _creds


def _config():
    token = _creds.get_secret("TELEGRAM_BOT_TOKEN")
    chat_id = _creds.get_secret("TELEGRAM_CHAT_ID")
    return token, chat_id


def send_telegram(message, request_fn=None):
    """Envía un mensaje. Devuelve (ok, detalle).

    Sin configurar -> (False, "Telegram sin configurar"). Un error
    de red -> (False, motivo), nunca una excepción.
    """
    token, chat_id = _config()
    if not token or not chat_id:
        return False, "Telegram sin configurar (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID en .env)"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {"chat_id": chat_id, "text": message}
    try:
        if request_fn is not None:
            status, payload = request_fn(
                "POST", url, {"Content-Type": "application/json"}, body)
        else:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                status, payload = resp.status, json.loads(
                    resp.read().decode("utf-8", "ignore") or "{}")
    except Exception as e:  # noqa: BLE001 - notificar nunca rompe nada
        return False, f"error enviando a Telegram: {e}"
    if status == 200 and (payload or {}).get("ok"):
        return True, "enviado"
    return False, f"Telegram respondió {status}: {payload}"


def notify(event, detail, request_fn=None):
    """notify_fn del bucle de publicación: "evento — detalle".

    Nunca levanta excepciones: si Telegram no está configurado o
    falla, avisa por stderr y sigue.
    """
    message = f"🎬 viral-clip-agent: {event} — {detail}"
    try:
        ok, why = send_telegram(message, request_fn=request_fn)
    except Exception as e:  # noqa: BLE001 - nunca romper el bucle
        ok, why = False, str(e)
    if not ok:
        print(f"aviso Telegram ({event}): {why}", file=sys.stderr)
    return ok
