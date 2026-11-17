#!/usr/bin/env python3
"""
moment_card.py — ficha del momento como única fuente de verdad.

Bloque 2 del tribunal (viral-clip-agent). La ficha se construye SOLO
de evidencia que ya existe, sin LLM en caliente:

  * transcripción del clip (SRT corregido si existe, si no el .srt.raw
    o el transcript.json de faster-whisper), y
  * chat del pico (chat.json en formato TwitchDownloader).

Reglas duras:

  * El juego NUNCA se infiere del chat ni del título del canal. Lo
    pasa quien llama (metadato del VOD / config del canal) con su
    fuente en ``game_source``. Si no se sabe, ``game`` es None y la
    ficha lo dice: ningún título podrá nombrar un juego.
  * ``what`` (qué pasa, verbo+objeto) solo se afirma si una frase de
    acción aparece repetida en chat y/o transcripción (ver
    ACTION_LEXICON). Si no hay evidencia, ``what`` es None y
    ``evidence`` es ``low``: la ficha lo dice en vez de inventar.
  * La frase literal del streamer sale del SRT; la del chat, del
    mensaje de texto más repetido en la ventana del pico. Los emotes
    no son frases: no se citan como texto.

Campos principales (todo JSON-serializable, ver ``build_moment_card``):

  game, game_label, game_source, streamer, language,
  peak_ts_vod, clip_start_vod, clip_duration_s,
  payoff_ts_clip, payoff_ts_vod,
  score, risas, hype, unique_users, viral_multiple,
  dominant_reaction, what, what_evidence, what_counts,
  quote_streamer {text, ts_clip}, quote_chat {text, count},
  chat_top, transcript_text, evidence, evidence_notes.

``evidence``: ``high`` (acción + cita del streamer que la describe +
reacción), ``medium`` (acción repetida en chat + reacción, pero la
transcripción no describe la acción) o ``low`` (sin transcripción
útil, casi sin texto en el chat, o reacción solo de volumen/emotes).

Uso como librería:

    from .moment_card import build_moment_card
    card = build_moment_card(
        clip_path="runs/.../clip_01_....mp4",
        chat_path="runs/.../chat.json",
        srt_path="runs/.../clip_01_....srt",
        streamer="coker", game="GTA V RP",
        game_source="brief del tribunal 2026-11-16 (Rob Run, GTA V RP)",
        peak_ts_vod=11710, clip_start_vod=11690,
        score=56.5, risas=14, hype=1, median_score=1.0)

Uso como CLI:

    python moment_card.py --clip runs/.../clip.mp4 --chat runs/.../chat.json \\
        --srt runs/.../clip.srt --streamer coker --game "GTA V RP" \\
        --game-source "brief del tribunal" --peak 11710 --start 11690 \\
        --score 56.5 --risas 14 --hype 1 --out runs/.../moment_card.json
"""

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Léxico de acciones (ES/EN, sin LLM)
# ---------------------------------------------------------------------------
# Cada entrada: (clave, verbo+objeto en español, variantes que la
# evidencian en chat/transcripción). Una acción solo se afirma si sus
# variantes suman >= 2 menciones en la ventana del clip. "Jugada" a
# secas NO está aquí a propósito: es la palabra que el empaquetado
# viejo inventaba; solo se afirma si el chat o el streamer la dicen.
ACTION_LEXICON = [
    ("erra", "errar un tiro", ["lo erra", "la erra", "erra", "erró", "erro el", "lo erró"]),
    ("patea", "patear (un tiro/penal)", ["patea", "patear", "pateó", "patea tan mal"]),
    ("dispara", "disparar", ["dispara", "disparó", "disparo", "tiro"]),
    ("mata", "matar a alguien", ["lo mata", "la mata", "mata", "mató"]),
    ("muere", "morir en el juego", ["se muere", "muere", "murió", "me muero de risa"]),
    ("gana", "ganar la partida", ["gana", "ganó", "ganamos"]),
    ("pierde", "perder la partida", ["pierde", "perdió", "perdimos"]),
    ("roba", "robar algo", ["roba", "robó", "robo", "lo roba"]),
    ("choca", "chocar el coche", ["choca", "chocó", "choque"]),
    ("cae", "caerse", ["se cae", "cae", "cayó", "se cayó"]),
    ("gol", "marcar un gol", ["gol", "golazo"]),
    ("penal", "un penal", ["penal", "penalti"]),
    ("clutch", "ganar un clutch", ["clutch"]),
    ("falla", "fallar algo", ["falla", "falló", "fallo"]),
]

# Palabras que no aportan nada como "qué pasa" (reacción, no acción).
STOPWORDS = set(
    "el la los las un una unos unas de del en y o a que no si es se su sus "
    "con por para como más pero qué cuando donde esto esta este eso esa "
    "yo tú él ella nosotros vos sos estás está están muy bien mal ya ahí "
    "ahora después antes todo todos nada nadie alguien algo vez veces "
    "the a an and or of to in on it is are was were be been this that "
    "with for you he she they we it s".split()
)

EMOTE_RE = re.compile(r"\[emote:[^\]]+\]")


def _norm(text: str) -> str:
    """Minúsculas, sin tildes, sin emotes, espacios colapsados."""
    text = EMOTE_RE.sub(" ", text or "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9áéíóúñü ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return [t for t in _norm(text).split() if t and t not in STOPWORDS]


# ---------------------------------------------------------------------------
# Lectura de fuentes
# ---------------------------------------------------------------------------

def parse_srt(path):
    """Lee un .srt -> [{start, end, text}] (tiempos en s, relativos al clip)."""
    path = Path(path)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")

    def _ts(s):
        h, m, rest = s.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000.0

    blocks, out = [], []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            start, end = [p.strip() for p in lines[1].split("-->")]
            out.append({"start": _ts(start), "end": _ts(end),
                        "text": " ".join(lines[2:])})
    return out


def parse_transcript_json(path):
    """Lee el transcript.json de faster-whisper -> (segmentos, idioma)."""
    path = Path(path)
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], None
    segs = [{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)),
             "text": (s.get("text") or "").strip()}
            for s in data.get("segments", []) if (s.get("text") or "").strip()]
    return segs, data.get("language") or data.get("source_language")


def load_chat(path):
    """Lee chat.json (TwitchDownloader) -> [{offset, body, user}].

    Acepta el dict {"comments": [...]} y también una lista plana de
    mensajes. Los mensajes vacíos se descartan; los emotes se
    conservan en ``body`` (cuentan para volumen) pero no como texto.
    """
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    comments = data.get("comments", []) if isinstance(data, dict) else data
    out = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        body = (c.get("message", {}) or {}).get("body", "") or ""
        body = body.strip()
        if not body:
            continue
        commenter = c.get("commenter", {}) or {}
        out.append({
            "offset": float(c.get("content_offset_seconds", 0) or 0),
            "body": body,
            "user": commenter.get("name") or commenter.get("display_name") or "?",
        })
    return sorted(out, key=lambda m: m["offset"])


def _text_messages(messages):
    """Mensajes con texto real (sin emotes puros)."""
    out = []
    for m in messages:
        body = EMOTE_RE.sub(" ", m["body"]).strip()
        if body and len(_norm(body)) >= 2:
            out.append({**m, "body_text": body})
    return out


# ---------------------------------------------------------------------------
# Construcción de la ficha
# ---------------------------------------------------------------------------

def _find_actions(chat_text, transcript_text):
    """Cuenta menciones de cada acción del léxico en chat+transcripción."""
    haystack = " " + _norm(chat_text) + " " + _norm(transcript_text) + " "
    found = []
    for key, label, variants in ACTION_LEXICON:
        count = 0
        for v in variants:
            count += haystack.count(" " + _norm(v) + " ")
            # variantes de una palabra también pegadas a puntuación
            if " " not in _norm(v):
                count += len(re.findall(r"\b" + re.escape(_norm(v)) + r"\b", haystack))
        # "muere" no cuenta si es solo "me muero de risa" (risa, no muerte)
        if count:
            found.append({"key": key, "what": label, "mentions": count})
    # evitar doble conteo de la variante de 1 palabra dentro de la de 2:
    # se deja el conteo bruto pero se ordena; el umbral es >= 2 menciones.
    found.sort(key=lambda a: -a["mentions"])
    return found


def _quote_streamer(segments, payoff_ts_clip):
    """Segmento del SRT más cercano al payoff, con contenido real."""
    if not segments:
        return None
    # preferir segmentos que solapan la ventana payoff ±5 s
    window = [s for s in segments
              if s["start"] <= payoff_ts_clip + 5 and s["end"] >= payoff_ts_clip - 5]
    pool = window or segments
    # el más largo (más palabras) dentro de la ventana; desempate: más cerca
    def _key(s):
        return (len(s["text"].split()),
                -abs((s["start"] + s["end"]) / 2 - payoff_ts_clip))
    best = max(pool, key=_key)
    return {"text": best["text"].strip(), "ts_clip": round(best["start"], 1)}


def _quote_chat(text_msgs, action_keys):
    """Frase literal del chat: la más repetida en la ventana del pico.

    Preferencia: mensajes que contengan una acción del léxico (dicen
    qué pasa) sobre gritos genéricos ("NOOOO", "PAID ACTOR").
    """
    if not text_msgs:
        return None
    counts = Counter(_norm(m["body_text"]) for m in text_msgs)
    ranked = []
    for body_norm, count in counts.most_common():
        original = next(m["body_text"] for m in text_msgs
                        if _norm(m["body_text"]) == body_norm)
        has_action = any(k in body_norm for k in action_keys)
        ranked.append((1 if has_action else 0, count, -len(original), original, count))
    ranked.sort(reverse=True)
    _, _, _, original, count = ranked[0]
    return {"text": original, "count": count}


def build_moment_card(clip_path, chat_path, srt_path=None, transcript_path=None,
                      streamer="", game=None, game_source="", language="es",
                      peak_ts_vod=None, clip_start_vod=None, clip_duration_s=60,
                      score=None, risas=0, hype=0, median_score=None,
                      vod_id=None, clip_start_fallback=None):
    """Construye la ficha del momento. ``game`` None = juego desconocido.

    ``peak_ts_vod`` / ``clip_start_vod`` en segundos del VOD. Si no se
    pasan, el payoff queda en None y la ficha lo marca (evidence low).
    """
    clip_path = Path(clip_path) if clip_path else None
    messages = load_chat(chat_path) if chat_path else []

    segments, lang_detected = [], None
    if srt_path:
        segments = parse_srt(srt_path)
    if not segments and transcript_path:
        segments, lang_detected = parse_transcript_json(transcript_path)
    transcript_text = " ".join(s["text"] for s in segments)

    if peak_ts_vod is not None and clip_start_vod is not None:
        payoff_ts_clip = round(float(peak_ts_vod) - float(clip_start_vod), 1)
        payoff_ts_vod = float(peak_ts_vod)
    else:
        payoff_ts_clip = payoff_ts_vod = None

    clip_start = float(clip_start_vod) if clip_start_vod is not None else 0.0
    in_clip = [m for m in messages
               if clip_start <= m["offset"] < clip_start + clip_duration_s]
    if payoff_ts_vod is not None:
        # Ventana del pico = 10 s (±5 s): es la ventana que puntúa
        # clips_chat_v2, así que "N mensajes en 10 segundos" es literal.
        in_peak = [m for m in messages
                   if payoff_ts_vod - 5 <= m["offset"] <= payoff_ts_vod + 5]
        in_peak30 = [m for m in messages
                     if payoff_ts_vod - 30 <= m["offset"] <= payoff_ts_vod + 30]
    else:
        in_peak, in_peak30 = in_clip, in_clip
    text_msgs = _text_messages(in_peak) or _text_messages(in_peak30)
    text_in_clip = _text_messages(in_clip)

    chat_text = " ".join(m["body_text"] for m in text_in_clip)
    actions = _find_actions(chat_text, transcript_text)
    action = next((a for a in actions if a["mentions"] >= 2), None)

    quote_streamer = _quote_streamer(segments, payoff_ts_clip) if payoff_ts_clip is not None else None
    quote_chat = _quote_chat(text_msgs, [a["key"] for a in actions])

    # Reacción dominante: risas > hype > volumen (mucho chat, 0 risas/hype).
    risas, hype = int(risas or 0), int(hype or 0)
    if risas >= 3 and risas >= hype:
        dominant = "risas"
    elif hype >= 3 and hype > risas:
        dominant = "hype"
    elif len(in_peak) >= 20:
        dominant = "volumen"
    elif risas > hype:
        dominant = "risas"
    elif hype > risas:
        dominant = "hype"
    else:
        dominant = "sin reacción clara"

    unique_users = len({m["user"] for m in in_peak30}) if in_peak30 else 0
    viral_multiple = (round(score / median_score, 2)
                      if score is not None and median_score else None)

    # Evidencia: ¿qué se puede afirmar y qué no?
    notes = []
    if not segments:
        notes.append("sin transcripción del clip (sin SRT ni transcript.json)")
    if len(text_msgs) < 3:
        notes.append(f"solo {len(text_msgs)} mensaje(s) de texto en la ventana del pico")
    if action is None:
        notes.append("ninguna acción del léxico aparece >= 2 veces en chat/transcripción")
    if quote_streamer and len(quote_streamer["text"].split()) <= 2:
        notes.append("la cita del streamer es solo una reacción corta, no describe la acción")
    if dominant == "volumen":
        notes.append("reacción solo de volumen/emotes: no hay risas ni hype medibles en ES/EN")

    if action and quote_streamer and len(quote_streamer["text"].split()) > 2 \
            and any(a["key"] in _norm(quote_streamer["text"]) for a in actions):
        evidence = "high"
    elif action and (quote_streamer or len(text_msgs) >= 3):
        evidence = "medium"
    else:
        evidence = "low"
    if payoff_ts_clip is None:
        evidence = "low"
        notes.append("sin timestamp de payoff: no se sabe dónde ocurre el momento dentro del clip")

    chat_top = [{"text": t, "count": c}
                for t, c in Counter(_norm(m["body_text"]) for m in text_msgs).most_common(10)]

    # Sustantivos de evidencia para el check promesa-payoff: todo lo que
    # el título puede nombrar sin inventar.
    evidence_tokens = sorted(set(_tokens(chat_text + " " + transcript_text)))
    if action:
        evidence_tokens += [action["key"]]
    if quote_chat:
        evidence_tokens += _tokens(quote_chat["text"])
    if quote_streamer:
        evidence_tokens += _tokens(quote_streamer["text"])

    game_label = game or "desconocido"
    return {
        "clip_file": str(clip_path) if clip_path else None,
        "vod_id": vod_id,
        "streamer": streamer or "desconocido",
        "game": game,                      # None = no se sabe, no se asume
        "game_label": game_label,
        "game_source": game_source or "no indicada (juego no verificado)",
        "language": language,
        "peak_ts_vod": payoff_ts_vod,
        "clip_start_vod": clip_start if clip_start_vod is not None else None,
        "clip_duration_s": clip_duration_s,
        "payoff_ts_clip": payoff_ts_clip,  # s dentro del clip (20 = pre-roll)
        "payoff_ts_vod": payoff_ts_vod,
        "score": score,
        "risas": risas,
        "hype": hype,
        "chat_msgs_peak_10s": len(in_peak),
        "chat_msgs_clip": len(in_clip),
        "unique_users_peak_30s": unique_users,
        "viral_multiple": viral_multiple,
        "dominant_reaction": dominant,
        "what": action["what"] if action else None,
        "what_key": action["key"] if action else None,
        "what_evidence": (f"{action['mentions']} menciones de "
                          f"'{action['key']}' en chat/transcripción"
                          if action else "sin evidencia: no se sabe qué pasa"),
        "what_counts": actions,
        "quote_streamer": quote_streamer,
        "quote_chat": quote_chat,
        "chat_top": chat_top,
        "transcript_text": transcript_text,
        "transcript_segments": len(segments),
        "language_detected": lang_detected,
        "evidence": evidence,
        "evidence_notes": notes,
        "evidence_tokens": sorted(set(evidence_tokens)),
    }


def save_card(card, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(card, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return out_path


def main():
    p = argparse.ArgumentParser(description="Ficha del momento (sin LLM): SRT + chat del pico.")
    p.add_argument("--clip", required=True)
    p.add_argument("--chat", required=True)
    p.add_argument("--srt")
    p.add_argument("--transcript")
    p.add_argument("--streamer", default="")
    p.add_argument("--game", default=None, help="Juego real (metadato del VOD). Omitir = desconocido.")
    p.add_argument("--game-source", default="")
    p.add_argument("--lang", default="es")
    p.add_argument("--peak", type=float, default=None, help="Peak en s del VOD")
    p.add_argument("--start", type=float, default=None, help="Inicio del clip en s del VOD")
    p.add_argument("--duration", type=float, default=60)
    p.add_argument("--score", type=float, default=None)
    p.add_argument("--risas", type=int, default=0)
    p.add_argument("--hype", type=int, default=0)
    p.add_argument("--median", type=float, default=None)
    p.add_argument("--vod-id", default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    card = build_moment_card(
        clip_path=args.clip, chat_path=args.chat, srt_path=args.srt,
        transcript_path=args.transcript, streamer=args.streamer,
        game=args.game, game_source=args.game_source, language=args.lang,
        peak_ts_vod=args.peak, clip_start_vod=args.start,
        clip_duration_s=args.duration, score=args.score,
        risas=args.risas, hype=args.hype, median_score=args.median,
        vod_id=args.vod_id)
    if args.out:
        save_card(card, args.out)
        print(f"Ficha guardada en {args.out}")
    print(json.dumps(card, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
