#!/usr/bin/env python3
"""Mezclador de SFX para los clips de GTA 6 (2026-11-18).

Implementa las reglas de ``SFX-AUDIT.md`` §3-4 como un paso de audio
APARTE, sobre el MP4 ya renderizado. No toca la plantilla de render
(``rdjavi.py``: geometría, fuentes, subtítulos) ni el scoring.

Reglas (auditoría):
  * Payoff + dip de silencio -> vine boom en el segundo del payoff.
  * Escalada antes del desenlace -> riser en [desenlace-2, desenlace]
    + boom en el desenlace (regla 8). La escalada aquí es el propio
    grito del streamer que precede al silencio del payoff.
  * Fail visual sin grito del streamer en ±2 s -> sad trombone / bruh.
  * NUNCA SFX sobre gritos del streamer, frases de asombro, risas,
    s=0-3 (hook) ni los últimos 2 s (loop). Máximo 2 SFX por clip.
  * Ducking -6 dB del audio original durante el efecto, con fade de
    100 ms. Techo post-mezcla: RMS del segundo del SFX <= -10 dBFS.

Objetivo de nivel (2026-11-19, cambio respecto a la auditoría):
  * La auditoría pedía "pico del SFX -11/-13 dBFS", pero lo que el
    oído escucha es el RMS: el primer A/B (boom sintetizado con pico
    -11 dBFS y RMS -22,6 dBFS) NO se oyó en el celular. Los booms de
    referencia miden RMS -10,4/-11,1 dBFS por segundo. El objetivo
    es ahora el RMS del segundo mezclado: ~-11 dBFS para el boom.
  * Cada efecto tiene su ``target_rms_dbfs`` sobre su ventana audible
    (``loud_offset`` + ``loud_duration``: la parte del archivo que de
    verdad suena; la cola de un boom o el inicio casi mudo de un
    riser no cuentan). El mezclador calcula la ganancia para llegar
    al objetivo, con techo de pico -1 dBFS (si el archivo tiene mucho
    crest, la ganancia se recorta por el techo y el efecto queda por
    debajo del objetivo; nunca se clipea).
  * Los archivos del pack ya vienen cerca de su objetivo (boom y
    riser normalizados por RMS; bruh/airhorn/trombone siguen
    normalizados por pico -11 dBFS como pedía la auditoría y el
    mezclador compensa la diferencia con ganancia).

DESVIACIONES DOCUMENTADAS:
  * El dip se mide contra el PICO LOCAL de los 6 s anteriores al
    payoff (el grito del streamer), no contra la media del clip
    (en p3220 el dip vs media es solo ~4 dB; vs pico local 10,5 dB).
  * Riser: su objetivo es -13 dBFS, no -11. El segundo mezclado del
    payoff es silencio + boom (-11), pero el segundo del riser trae
    la voz del streamer (-19,2 dBFS RMS, banda de voz -21,2): un
    riser a -11 dejaría su banda de voz 3,6 dB POR ENCIMA de la voz
    y taparía la frase. A -13 la banda de voz del riser (-20,2)
    queda en paridad con la voz y la frase sigue inteligible; el
    segundo mezclado queda en ~-13,6 dBFS. Además el riser NO lleva
    ducking (duck_db 0): es un bed BAJO la voz, y un duck de -6 dB
    enterraría la frase. Su ventana audible son los últimos 0,5 s
    (la frase ya se apaga ahí: -21,2/-25,0 dBFS en t=19,5-20,0).
  * Boom real: desde 2026-11-19 ``vine_boom.wav`` es el Mixkit 2908
    ("Movie trailer epic impact"), no el sintetizado. Es un impacto
    de tráiler con ataque brillante (36% de energía >500 Hz), no un
    seno puro: su fracción ≤120 Hz es 0,46, no >0,7 como el seno
    sintetizado. Ese ataque es justo lo que lo hace audible en el
    parlante del celular (tramo de 100 ms a -15,7 dBFS tras highpass
    250 Hz, mejor que el 1143 con -20,7). El click añadido no
    aportaba nada medible y se descartó.

Volumen general (2026-11-19, pedido del usuario: "suena muy
despacio"): el clip ya pica cerca de 0 dBFS, así que el lift es
compresión ascendente, no ganancia. Un ``acompressor`` NO sirve:
su detector responde a los picos (la voz tiene ~16 dB de crest)
y con umbral -24 dB + makeup +8 dB la voz salía 1-3 dB MÁS BAJA
(medido). En su lugar, un ``compand`` con curva ascendente suave
(-20 -> -13, -30 -> -24, -40 -> -36, picos 0 -> -1): sube la voz
tranquila ~+4 dB y la fuerte ~+1,5 dB, los silencios ~+6 dB, y
no toca los picos. Medido en p7775: LUFS integrado -16,5 -> -13,8,
LRA 10,5 -> 6,8, sin bombeo (los tramos de 100 ms varían suave
con la envolvente del habla). El limitador final es 0,63 (-4 dBFS
en PCM): con 0,71 el sobreimpulso del AAC llevaba el pico de
muestra a 0,0 dBFS; con 0,63 queda en -2,5 dBFS y el true peak
en ≈ -2 dB. El lift se puede desactivar con ``lift=False``.

El fail visual NO se detecta automáticamente en esta versión: el
llamador pasa ``fail_s`` (segundo del fail) cuando lo conoce; si no
se pasa, no hay efecto de fail.

Densidad estilo nixuj (2026-11-19, pedido del usuario): el tope de
2 SFX por clip lo pone el usuario, no la auditoría. Para p7775 el
mezclador denso usa 6 efectos en beats reales de los 48 s
(``mix_sfx`` con eventos explícitos, no ``decide_sfx``): whoosh en
el lanzamiento (s=5,5), ding al aparecer el prompt del cromo
(s=11,5), riser + boom en el payoff (s=18-20), segundo boom con
la carta en primer plano (s=27) y ding en el beat del dinero
(s=38). Todos caen en huecos sin voz; cada segundo mezclado de
efecto queda en RMS ~-11/-13 dBFS. ``decide_sfx`` sigue con el
tope de 2 para el camino automático.
"""
import functools
import json
import math
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipagent.audio import decode_audio, rms_per_second, streamer_shout_spikes

SFX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sfx")
MANIFEST = os.path.join(SFX_DIR, "MANIFEST.json")

# Objetivo de nivel: RMS del segundo mezclado (ver docstring).
PEAK_CEILING_DBFS = -1.0
GAIN_CLAMP_DB = 12.0
DUCK_FADE_S = 0.1
MAX_SFX_PER_CLIP = 2
PAYOFF_DIP_DB = 6.0
PAYOFF_DIP_WINDOW_S = 6
HOOK_END_S = 3.0
LOOP_TAIL_S = 2.0

# Lift general del clip (compresión ascendente suave, ver docstring).
LIFT_POINTS = "-90/-90|-40/-36|-30/-24|-20/-13|-12/-7|-6/-4|0/-1"
LIFT_ATTACK_S = 0.005
LIFT_DECAY_S = 0.25
LIMITER_LIMIT = 0.63  # -4 dBFS en PCM -> pico muestra AAC ≈ -2,5 dBFS, true peak ≈ -2 dB

EFFECTS = {
    # "loud_offset"/"loud_duration": ventana audible del efecto, la
    # que cuenta para el guard de "nunca sobre un grito", el ducking
    # y el objetivo RMS. El vine boom concentra su energía en el
    # primer segundo (a t=1,0 s ya está 11 dB bajo el primer
    # segundo); su cola de 1,0-2,0 s es un decay bajo voz normal.
    # El riser solo suena de verdad en sus últimos 0,5 s.
    "vine_boom": {"file": "vine_boom.wav", "duration": 2.0,
                  "loud_offset": 0.0, "loud_duration": 1.0,
                  "target_rms_dbfs": -11.0, "duck_db": -6.0},
    "bruh": {"file": "bruh.wav", "duration": 2.02,
             "loud_offset": 0.0, "loud_duration": 2.02,
             "target_rms_dbfs": -20.0, "duck_db": -6.0},
    "airhorn": {"file": "airhorn.wav", "duration": 1.61,
                "loud_offset": 0.0, "loud_duration": 1.61,
                "target_rms_dbfs": -13.0, "duck_db": -6.0},
    "sad_trombone": {"file": "sad_trombone.wav", "duration": 2.5,
                     "loud_offset": 0.0, "loud_duration": 2.5,
                     "target_rms_dbfs": -17.0, "duck_db": -6.0},
    "riser": {"file": "riser.wav", "duration": 2.0,
              "loud_offset": 1.5, "loud_duration": 0.5,
              "target_rms_dbfs": -13.0, "duck_db": 0.0},
    # Densidad estilo nixuj (2026-11-19, pedido del usuario: el
    # _v2_sfx solo se oía un boom). El "ding" es un brillo de
    # reveal (Mixkit 2058, "bonus earned in video game": campana
    # corta y brillante, centroide 3354 Hz, ventana audible 0,5 s)
    # y el "whoosh" es un barrido que crece (Mixkit 2919, recortado
    # a 1,5-3,5 s: sube -32 -> -15 dBFS y pega en su último 0,5 s).
    # Objetivo -11,5 dBFS desde 2026-11-19 (pedido del usuario:
    # "sube los dings y el whoosh para que se noten"): a -13 el
    # usuario solo oía los booms. Son efectos cortos (0,5 s) y el
    # techo de pico -1 dBFS ya no dejaba subirles ganancia (crest
    # 12,4-12,8 dB), así que se procesaron por archivo con tanh
    # soft-clip (T=0,7; el ding además con compresión ascendente
    # p=0,9) para bajar el crest a ~10 dB y subir su RMS a igual
    # pico, como se hizo con el boom (crest 13,1 -> 7,4 dB).
    # El pico medido por decode_audio (16 kHz) sale ~0,8 dB por
    # encima del pico real a 44,1 kHz (sobreimpulso de remuestreo),
    # así que el mezclador les aplica -0,4/-0,9 dB y la ventana
    # audible cae en -11,5 dBFS.
    "ding": {"file": "ding.wav", "duration": 1.0,
             "loud_offset": 0.0, "loud_duration": 0.5,
             "target_rms_dbfs": -11.5, "duck_db": -6.0},
    "whoosh": {"file": "whoosh.wav", "duration": 2.0,
               "loud_offset": 1.5, "loud_duration": 0.5,
               "target_rms_dbfs": -11.5, "duck_db": -6.0},
}


def sfx_path(effect):
    """Ruta del archivo WAV de un efecto. KeyError si no existe."""
    info = EFFECTS[effect]
    path = os.path.join(SFX_DIR, info["file"])
    if not os.path.isfile(path):
        raise FileNotFoundError(f"SFX no encontrado: {path}")
    return path


def sfx_duration(effect):
    """Duración real del archivo (ffprobe), en segundos."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", sfx_path(effect)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


@functools.lru_cache(maxsize=None)
def _sfx_levels(effect):
    """(RMS de la ventana audible, pico) del archivo, en dBFS.

    Medido con ``decode_audio`` (ffmpeg a WAV 16 kHz + módulo wave):
    la vía pipe f32le da valores imposibles en esta VM (pico > 0 dBFS
    en un archivo 16-bit), así que no se usa para medir.
    """
    audio, sr = decode_audio(sfx_path(effect))
    info = EFFECTS[effect]
    a = int(round(info["loud_offset"] * sr))
    b = int(round((info["loud_offset"] + info["loud_duration"]) * sr))
    import numpy as np
    seg = audio[a:b].astype(np.float64)
    rms = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
    peak = float(np.max(np.abs(audio.astype(np.float64)))) if len(audio) else 0.0
    loud_rms = 20 * math.log10(rms) if rms > 1e-9 else -90.0
    peak_db = 20 * math.log10(peak) if peak > 1e-9 else -90.0
    return loud_rms, peak_db


def sfx_gain_db(effect):
    """Ganancia (dB) para que la ventana audible del efecto quede en
    su objetivo RMS, sin que el pico del archivo supere -1 dBFS.

    Si el archivo tiene mucho crest (voz, trombón), el techo de pico
    recorta la ganancia y el efecto queda por debajo del objetivo:
    nunca se clipea para llegar al objetivo.
    """
    info = EFFECTS[effect]
    loud_rms, peak_db = _sfx_levels(effect)
    gain = info["target_rms_dbfs"] - loud_rms
    gain = max(-GAIN_CLAMP_DB, min(GAIN_CLAMP_DB, gain))
    gain = min(gain, PEAK_CEILING_DBFS - peak_db)
    return gain


def _duck_gain_expr(start, dur, duck_db):
    """Expresión ffmpeg para el ducking de UN evento.

    Audio original a 1,0 fuera del evento, a 10**(duck_db/20)
    durante la ventana audible del efecto, con rampa lineal de
    100 ms a la entrada y a la salida. ``t`` es el tiempo en
    segundos (volume eval=frame).
    """
    lin = 10 ** (duck_db / 20.0)
    a = start
    b = start + dur
    f = DUCK_FADE_S
    return (
        f"if(lt(t,{a:.3f}),"
        f"1-{1 - lin:.3f}*min(max((t-({a:.3f}-{f:.3f}))/{f:.3f},0),1),"
        f"if(lt(t,{b:.3f}),{lin:.3f},"
        f"{lin:.3f}+{1 - lin:.3f}*min(max((t-{b:.3f})/{f:.3f},0),1)))"
    )


def _overlaps_shout(start, dur, shouts):
    """True si algún segundo de grito del streamer cae dentro de [start, start+dur)."""
    return any(start <= s < start + dur for s in shouts)


def decide_sfx(clip_path, transcript=None, payoff_s=20.0, fail_s=None,
               fail_effect="sad_trombone", max_sfx=MAX_SFX_PER_CLIP):
    """Decide qué SFX poner y dónde, según SFX-AUDIT.md §3.

    Devuelve una lista de eventos {"effect", "start", "duration",
    "reason"} en orden de prioridad (boom, riser, fail): con el
    presupuesto de 2 efectos por clip, el par del payoff gana al
    fail. Vacía si no hay nada que poner (reglas 2, 4, 5, 6, 9,
    10, 11). ``transcript`` es el dict Whisper del clip (para los
    gritos solo del streamer); sin transcripción no se puede
    comprobar el grito y el fail no se pone (mejor no poner un
    efecto que tapar voz que no vemos).
    """
    rms = rms_per_second(clip_path)
    if not rms:
        return []
    duration = len(rms)
    vals = {int(s): db for s, db in rms}
    shouts = streamer_shout_spikes(rms, transcript) if transcript else []
    events = []

    def _guard_ok(start, dur, loud_offset=0.0, loud_dur=None):
        # Nunca en el hook (s=0-3) ni en los últimos 2 s (loop).
        if start < HOOK_END_S:
            return False
        if start + dur > duration - LOOP_TAIL_S:
            return False
        # Nunca sobre un grito del streamer: solo cuenta la ventana
        # AUDIBLE del efecto. El detector de gritos compara contra la
        # mediana de los 10 s anteriores, así que el retorno a la voz
        # normal justo después del silencio del payoff (s=21 en
        # p7775) se marca como "grito" aunque no lo es; la cola del
        # boom (1,0-2,0 s, 11 dB bajo el primer segundo) no tapa nada
        # ahí.
        loud = loud_dur if loud_dur is not None else dur
        if _overlaps_shout(start + loud_offset, loud, shouts):
            return False
        return True

    # Regla 1/2: payoff + dip de silencio -> vine boom en el payoff.
    boom_decided = False
    if payoff_s is not None and int(payoff_s) in vals:
        dip_s = int(payoff_s)
        window = [vals[s] for s in range(max(0, dip_s - PAYOFF_DIP_WINDOW_S), dip_s)
                  if s in vals]
        if window:
            dip = max(window) - vals[dip_s]
            if dip >= PAYOFF_DIP_DB:
                info = EFFECTS["vine_boom"]
                if _guard_ok(payoff_s, info["duration"],
                             info["loud_offset"], info["loud_duration"]):
                    events.append({"effect": "vine_boom",
                                   "start": float(payoff_s),
                                   "duration": info["duration"],
                                   "reason": f"payoff s={dip_s}: dip "
                                             f"{dip:.1f} dB bajo el pico "
                                             f"local (grito)"})
                    boom_decided = True
    # Regla 8: si hay boom en el payoff, riser en [payoff-2, payoff].
    # La escalada es el grito del streamer que precede al silencio.
    if boom_decided:
        info = EFFECTS["riser"]
        r_start = payoff_s - info["duration"]
        if _guard_ok(r_start, info["duration"],
                     info["loud_offset"], info["loud_duration"]):
            events.append({"effect": "riser",
                           "start": float(r_start),
                           "duration": info["duration"],
                           "reason": "escalada antes del payoff "
                                     f"(grito s={int(payoff_s) - 4}-"
                                     f"{int(payoff_s) - 1} -> silencio "
                                     f"s={int(payoff_s)})"})
    # Regla 3: fail visual sin grito del streamer en ±2 s.
    if fail_s is not None and fail_effect in EFFECTS:
        info = EFFECTS[fail_effect]
        near_shout = any(abs(s - fail_s) <= 2 for s in shouts)
        if not near_shout and _guard_ok(
                fail_s, info["duration"],
                info["loud_offset"], info["loud_duration"]):
            events.append({"effect": fail_effect,
                           "start": float(fail_s),
                           "duration": info["duration"],
                           "reason": f"fail s={fail_s}: sin grito del "
                                     f"streamer en ±2 s"})
    return events[:max_sfx]


def _lift_filter():
    """Compresión ascendente suave para subir el volumen general
    sin tocar los picos ni bombear (ver docstring del módulo)."""
    return (
        "compand=attacks="
        f"{LIFT_ATTACK_S:g}:decays={LIFT_DECAY_S:g}:"
        f"points={LIFT_POINTS}"
    )


def mix_sfx(clip_path, events, out_path, lift=True):
    """Mezcla los eventos SFX sobre el MP4 ya renderizado.

    Cadena: original -> lift (compand ascendente, si ``lift``) ->
    ducking -6 dB sobre la ventana audible de cada efecto (fade
    100 ms; el riser no lleva ducking, es un bed bajo la voz) ->
    SFX con ganancia RMS (cada efecto a su objetivo, pico ≤ -1 dBFS)
    -> amix -> limitador de seguridad a -4 dBFS en PCM (0.63
    lineal: el AAC final queda con pico de muestra ≈ -2,5 dBFS y
    true peak ≈ -2 dB; con 0,71 el sobreimpulso del AAC llevaba el
    pico de muestra a 0,0 dBFS), con ``level=false`` para que NO
    aplique auto-level.
    Video copiado sin re-codificar.
    Sin eventos y sin lift, copia los streams sin tocar nada.
    Devuelve (out_path, info) con el RMS por segundo medido tras
    mezclar en cada segundo de efecto.
    """
    if not events and not lift:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", clip_path,
               "-map", "0", "-c", "copy",
               "-movflags", "+faststart", out_path]
        subprocess.run(cmd, check=True)
        return out_path, {"events": [], "post_rms": {}}

    filter_parts = []
    # 1. Lift general (o paso directo).
    if lift:
        filter_parts.append(f"[0:a]{_lift_filter()}[lifted]")
    else:
        filter_parts.append("[0:a]anull[lifted]")
    # 2. Ducking sobre la ventana audible de cada efecto que lo lleve.
    gains = []
    for e in events:
        info = EFFECTS[e["effect"]]
        if info["duck_db"] < 0:
            gains.append(_duck_gain_expr(
                e["start"] + info["loud_offset"],
                info["loud_duration"], info["duck_db"]))
    if gains:
        duck_expr = "*".join(f"({g})" for g in gains)
        filter_parts.append(f"[lifted]volume='{duck_expr}':eval=frame[ducked]")
    else:
        filter_parts.append("[lifted]anull[ducked]")
    # 3. SFX con su ganancia RMS, retardados a su segundo.
    sfx_labels = []
    for i, e in enumerate(events):
        gain_db = sfx_gain_db(e["effect"])
        gain_lin = 10 ** (gain_db / 20.0)
        sfx_labels.append(f"[sfx{i}]")
        filter_parts.append(
            f"[{i + 1}:a]volume={gain_lin:.4f}"
            f",adelay={int(round(e['start'] * 1000))}:all=1[sfx{i}]")
    n_inputs = 1 + len(events)
    filter_parts.append(
        f"[ducked]{''.join(sfx_labels)}amix=inputs={n_inputs}:"
        f"duration=longest:normalize=0[mixed]")
    filter_parts.append("[mixed]alimiter=limit=0.63:level=false[outa]")
    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", clip_path]
    for e in events:
        cmd += ["-i", sfx_path(e["effect"])]
    cmd += ["-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[outa]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True)

    post_rms = {}
    try:
        mixed = rms_per_second(out_path)
        for e in events:
            sec = int(e["start"])
            post_rms[sec] = next((db for s, db in mixed if s == sec), None)
    except Exception:
        pass
    return out_path, {"events": events, "post_rms": post_rms}


def mix_clip_with_sfx(clip_path, transcript=None, payoff_s=20.0,
                      fail_s=None, out_path=None, lift=True, **kwargs):
    """Decide + mezcla en un solo paso. Sin eventos y sin lift, copia el clip."""
    if out_path is None:
        base, ext = os.path.splitext(clip_path)
        out_path = f"{base}_sfx{ext}"
    events = decide_sfx(clip_path, transcript=transcript,
                        payoff_s=payoff_s, fail_s=fail_s, **kwargs)
    return mix_sfx(clip_path, events, out_path, lift=lift)


def load_manifest():
    """El MANIFEST.json de licencias del pack."""
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Mezclador SFX (SFX-AUDIT.md)")
    p.add_argument("clip", help="MP4 ya renderizado")
    p.add_argument("--transcript", help="JSON Whisper del clip")
    p.add_argument("--payoff", type=float, default=20.0)
    p.add_argument("--fail", type=float, default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--no-lift", action="store_true",
                   help="sin lift general de volumen")
    args = p.parse_args()
    transcript = None
    if args.transcript:
        with open(args.transcript, encoding="utf-8") as f:
            transcript = json.load(f)
    out, info = mix_clip_with_sfx(
        args.clip, transcript=transcript, payoff_s=args.payoff,
        fail_s=args.fail, out_path=args.out, lift=not args.no_lift)
    print(f"salida: {out}")
    for e in info["events"]:
        sec = int(e["start"])
        print(f"  {e['effect']} en s={e['start']}: {e['reason']} "
              f"(RMS post-mezcla s={sec}: {info['post_rms'].get(sec)} dBFS)")
    if not info["events"]:
        print("  sin eventos: copia sin cambios")
