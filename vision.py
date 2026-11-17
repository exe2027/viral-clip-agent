#!/usr/bin/env python3
"""
vision.py — Gemini elige el mejor fotograma (miniatura).

Una sola llamada a Gemini Flash-Lite con los 3 fotogramas que saca
``thumbnails.py`` (antes del payoff, payoff, después del payoff).
Gemini NO genera una miniatura nueva y NO elige por el humano: solo
ordena los 3 frames reales del clip y dice cuál va mejor con el
título. La aprobación final sigue siendo humana.

Reglas:
  * Sin ``GEMINI_API_KEY`` (variable de entorno o ``.env`` en la raíz
    del repo) la visión queda apagada (``vision_skipped``) y se usa
    el fotograma del payoff, como siempre. La app funciona igual.
  * Si la API falla, da timeout o devuelve algo inválido, también
    se cae al fotograma del payoff, nunca se rompe el pipeline.
  * 1 llamada por clip, 3 imágenes, con gemini-3.5-flash-lite
    (los modelos 2.5 ya no están disponibles para usuarios nuevos:
    la API devuelve 404). Capa gratuita: 500 peticiones/día.
  * La key nunca se escribe en un archivo del repo ni se imprime en
    logs: va en ``.env`` (en ``.gitignore``) o como variable de
    entorno.

Uso como librería:

    from vision import choose_best_frame
    result = choose_best_frame(frames, title="GTA V RP: Errar un tiro y el chat explotó")
    # result = {"vision": "gemini", "best_index": 1, "best_path": ...,
    #           "ranking": [2, 1, 3], "reason": "...", "model": "gemini-3.5-flash-lite"}
    # Sin key: {"vision": "skipped", "reason": "sin GEMINI_API_KEY", "best_index": 1, ...}

Uso como CLI:

    python vision.py --title "GTA V RP: Errar un tiro y el chat explotó" \\
        thumb_01.jpg thumb_02.jpg thumb_03.jpg
"""

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

MODEL_DEFAULT = "gemini-3.5-flash-lite"
API_HOST = "generativelanguage.googleapis.com"
TIMEOUT_S = 30


def load_dotenv(path=None):
    """Carga ``.env`` (raíz del repo o cwd) sin pisar el entorno real."""
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.append(Path(__file__).resolve().parent / ".env")
    candidates.append(Path.cwd() / ".env")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return candidate
    return None


def get_api_key():
    """Key de Gemini, del entorno o de ``.env``. None si no hay."""
    load_dotenv()
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None


def _build_prompt(title, game=None):
    title_line = f'Título del clip: "{title}"\n' if title else ""
    game_line = f"Juego: {game}\n" if game else ""
    return (
        "Eres el editor de miniaturas de un canal de clips de directos. "
        "Te paso 3 fotogramas reales del mismo clip, en orden: "
        "1 = antes del momento, 2 = el momento (payoff), 3 = después del momento.\n"
        + title_line
        + game_line
        + "Ordénalos de mejor a peor como miniatura/portada vertical (1080x1920) con estos criterios, en este orden:\n"
        "1) Que se vea lo que promete el título (si el título promete una jugada, tiene que verse gameplay; si promete una reacción, tienen que verse caras riendo).\n"
        "2) Cara o emoción visible y nítida.\n"
        "3) Imagen nítida y con luz, sin pantalla negra, sin menú de pausa ni barra de carga.\n"
        "Devuelve SOLO este JSON, sin más texto:\n"
        '{"best": 2, "ranking": [2, 1, 3], "reason": "una frase corta en español"}'
    )


def _parse_choice(text, n_frames):
    """Extrae y valida el JSON de Gemini. Devuelve dict o None."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    best = data.get("best")
    ranking = data.get("ranking")
    if not isinstance(best, int) or not 1 <= best <= n_frames:
        return None
    if not isinstance(ranking, list) or sorted(ranking) != list(range(1, n_frames + 1)):
        ranking = [best] + [i for i in range(1, n_frames + 1) if i != best]
    return {
        "best": best,
        "ranking": ranking,
        "reason": str(data.get("reason") or "").strip(),
    }


def _call_gemini(parts, model=MODEL_DEFAULT, api_key=None, timeout=TIMEOUT_S):
    """Una llamada generateContent. Devuelve el texto de la respuesta."""
    key = api_key or get_api_key()
    if not key:
        raise RuntimeError("sin GEMINI_API_KEY")
    url = f"https://{API_HOST}/v1beta/models/{model}:generateContent"
    body = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("x-goog-api-key", key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"Gemini HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini no responde: {exc.reason}") from exc
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini devolvió una respuesta vacía")
    parts_out = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts_out)
    if not text.strip():
        raise RuntimeError("Gemini devolvió texto vacío")
    return text


def _payoff_index(frames):
    """Índice del fotograma del payoff, o el del medio si no hay."""
    for i, frame in enumerate(frames):
        if frame.get("role") == "payoff":
            return i
    return len(frames) // 2 if frames else 0


def choose_best_frame(frames, title=None, game=None, model=MODEL_DEFAULT,
                      api_key=None, timeout=TIMEOUT_S):
    """Elige el mejor frame con Gemini. Nunca lanza excepción.

    ``frames`` es la lista de ``thumbnails.extract_thumbnail_frames``:
    [{"path": ..., "role": "payoff"|..., "ts_clip": ...}, ...].
    Devuelve siempre un dict con ``best_index`` y ``best_path`` (el
    frame a usar como portada) y ``vision`` en "gemini" | "skipped" |
    "error".
    """
    if not frames:
        return {"vision": "error", "reason": "sin fotogramas",
                "best_index": 0, "best_path": None, "ranking": [],
                "model": model}
    fallback = _payoff_index(frames)
    key = api_key or get_api_key()
    if not key:
        return {"vision": "skipped", "reason": "sin GEMINI_API_KEY",
                "best_index": fallback,
                "best_path": frames[fallback].get("path"),
                "ranking": [fallback + 1], "model": model}
    parts = [{"text": _build_prompt(title, game)}]
    for frame in frames:
        path = Path(frame.get("path") or "")
        if not path.is_file():
            return {"vision": "error", "reason": f"fotograma no encontrado: {path}",
                    "best_index": fallback,
                    "best_path": frames[fallback].get("path"),
                    "ranking": [fallback + 1], "model": model}
        parts.append({
            "inlineData": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(path.read_bytes()).decode(),
            }
        })
    try:
        text = _call_gemini(parts, model=model, api_key=key, timeout=timeout)
        choice = _parse_choice(text, len(frames))
    except RuntimeError as exc:
        return {"vision": "error", "reason": str(exc),
                "best_index": fallback,
                "best_path": frames[fallback].get("path"),
                "ranking": [fallback + 1], "model": model}
    if choice is None:
        return {"vision": "error", "reason": "respuesta de Gemini sin JSON válido",
                "best_index": fallback,
                "best_path": frames[fallback].get("path"),
                "ranking": [fallback + 1], "model": model}
    best_index = choice["best"] - 1
    return {"vision": "gemini", "reason": choice["reason"],
            "best_index": best_index,
            "best_path": frames[best_index].get("path"),
            "ranking": choice["ranking"], "model": model}


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Gemini elige el mejor fotograma de un clip (miniatura).")
    parser.add_argument("frames", nargs="+", help="Fotogramas .jpg en orden (antes, payoff, después)")
    parser.add_argument("--title", default=None, help="Título del clip")
    parser.add_argument("--game", default=None, help="Juego del clip")
    parser.add_argument("--model", default=MODEL_DEFAULT)
    args = parser.parse_args()
    frames = [{"path": p, "role": "payoff" if i == 1 else ""} for i, p in enumerate(args.frames)]
    result = choose_best_frame(frames, title=args.title, game=args.game, model=args.model)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["vision"] in ("gemini", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
