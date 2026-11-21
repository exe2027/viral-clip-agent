#!/usr/bin/env python3
"""workers.py — el cerebro partido en workers por paso (Opción A).

Un solo sistema, un solo punto de encendido: ``run_supervisor`` (CLI
``clipagent workers start``) procesa TODOS los pasos en un solo bucle:
reclama el siguiente job debido (por prioridad), ejecuta su paso,
guarda el resultado en ``step_jobs`` y encola el paso siguiente.
Nada de un comando distinto por paso para el usuario: el botón
Iniciar del panel arranca este supervisor y la cola avanza sola de
paso en paso sin intervención por clip.

Pasos (cada worker hace UN solo paso, sin reescribir la lógica: cada
handler delega en el módulo que ya lo hacía):

    vod         validar la URL del VOD + lista blanca (jobqueue)
    picos       chat -> picos + score viral (ingest + detect)
    corte       vídeo -> clip vertical 9:16 (render, ffmpeg)
    subtitulos  clip -> SRT + quemado (transcribe, faster-whisper)
    empaquetar  clip -> paquete SEO + prioridad por plataforma
                (seo_score + priority; la fórmula NO se reimplementa)
    publicar    clip + plataforma -> API/borrador (publish + platforms)

Cada paso es idempotente y reanudable: el estado vive en ``step_jobs``
(no en memoria), el reclamo es atómico (dos workers no toman el mismo
job), y si el proceso muere a mitad, ``requeue_stale`` devuelve el job
a ``pending`` sin gastar intento. Los pasos pesados tienen además un
comando CLI propio y estable —``cortar.py``, ``subtitular.py``,
``detectar_picos.py``, ``empaquetar.py``— con entrada/salida clara,
para que en Fase 2 un orquestador Go pueda llamarlos sin tocar
vídeo/IA.

La cadena se detiene sola en ``empaquetar``: ``publicar`` NUNCA se
crea automáticamente. Solo la aprobación humana crea un job de
publicar por clip y plataforma (``enqueue_publish``). Un clip no se
publica sin que alguien lo apruebe, y nunca dos veces en la misma
plataforma (clave anti-duplicado de publicar = clip + plataforma).

Si un paso se atasca: queda en estado visible con motivo y hora
(``esperando_humano`` para lo que solo un humano resuelve —captcha,
credencial, aprobación—; ``failed`` con motivo tras agotar los
reintentos acotados), el resto de la cola SIGUE, y nunca se reintenta
a ciegas hasta el infinito (máx. 3 intentos, backoff con tope 1 h).

Configuración: los handlers leen la configuración en CADA job
(``load_worker_config`` lee ``config.yaml``: cuentas, subtítulos,
ajustes). Lo que el usuario toca en el panel alimenta a los workers
sin pasos técnicos intermedios y sin reiniciar el supervisor: el
siguiente job ya usa la config nueva. Sin secretos en el código: las
credenciales viven en ``tokens/`` / variables de entorno, nunca aquí.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

from .jobs import PIPELINE, PLATFORMS, STEPS

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(HERE, "queue.db")

#: Dónde puede correr cada paso. "worker" = cabe en un Cloudflare
#: Worker (cómputo puro o HTTP, sin ffmpeg ni modelos): cola, estados,
#: SEO, prioridad, picos por chat y publicación por API oficial.
#: "external" = exige cómputo fuera de Workers: ffmpeg (corte),
#: faster-whisper (subtítulos) y navegador Playwright (TikTok).
#:
#: DESTINO PRINCIPAL (decisión del usuario 17 nov 2026, "Todo
#: automático"): GitHub Actions — Cloudflare lleva panel/API/cola y
#: GitHub Actions lleva el cerebro (detección, corte, subtítulos,
#: publicación) con `workers start --once` por corrida programada.
#: Sin GH Actions el 100% automático no es posible (ver
#: docs/jobs-workers.md §7); solo Cloudflare + manual queda como
#: respaldo degradado, no como plan principal.
STEP_COMPUTE = {
    "vod": "worker",        # validar URL + lista blanca (sin red pesada)
    "picos": "worker",      # puntuar el chat: aritmética pura, portable a JS
    "corte": "external",    # ffmpeg: NO corre en Workers (10 ms CPU, 128 MB)
    "subtitulos": "external",  # faster-whisper: NO corre en Workers
    "empaquetar": "worker",    # SEO + prioridad: cómputo puro
    "publicar": "worker",      # APIs oficiales YT/Meta/TikTok: solo HTTP
}


def step_compute_requirements():
    """Qué pasos caben en Cloudflare Workers y cuáles exigen cómputo
    externo. Lo usa el panel para mostrar el plan sin GH Actions."""
    return dict(STEP_COMPUTE)
#: Reintento de un clip retenido por la política (límites, horario).
RETAINED_RETRY_S = 24 * 3600
#: Duración de un clip cortado a partir de un pico.
#: REGLAS 2026-11-18 (usuario): 60 s es el TOPE DURO, no la duración;
#: el objetivo es 40 s (20 s pre-roll + 20 s tras el pico) y el mínimo
#: es 40 s (nunca menos de 40; el clip de 38 s de Rubius p2010 queda
#: fuera). El rango 25-35 sigue prohibido (regla de oro).
CLIP_PRE_S = 20             # empieza 20 s antes del pico
CLIP_DURATION_S = 40        # objetivo: 40 s por clip
CLIP_MIN_DURATION_S = 40    # mínimo: ningún clip por debajo de 40 s
CLIP_MAX_DURATION_S = 60    # tope duro: ningún clip por encima de 60 s


class StepError(Exception):
    """Fallo de un paso. ``kind`` decide el destino del job:

    * ``transient``: red/descarga, reintento con backoff (máx. 3).
    * ``timeout``: como transient, con el mismo tope.
    * ``permanent``: no se arregla reintentando -> ``failed``.
    * ``human``: solo un humano lo resuelve (captcha, credencial,
      aprobación) -> ``esperando_humano``, sin reintento.
    """

    def __init__(self, message, kind="transient"):
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Configuración (la que toca el usuario en el panel)
# ---------------------------------------------------------------------------

def load_worker_config(config_path=None):
    """Config del worker: ``config.yaml`` tal cual (cuentas, subtítulos,
    ajustes, límites). Se lee en cada job, así un cambio en el panel
    aplica al siguiente job sin reiniciar nada. Nunca levanta: sin
    config, devuelve {} y cada paso usa sus valores por defecto."""
    try:
        from .config import load_config
        return load_config(config_path) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Funciones públicas por paso (las usan los handlers Y los CLI
# cortar.py / subtitular.py / detectar_picos.py / empaquetar.py:
# una sola implementación, dos entradas).
# ---------------------------------------------------------------------------

def detect_peaks(chat_path, top_n=5, lang="both"):
    """Chat -> picos con score viral. Reutiliza ingest + detect +
    el filtro previo de auto_clips; no reimplementa nada."""
    if not os.path.isfile(chat_path):
        raise StepError(f"chat no existe: {chat_path}", kind="permanent")
    from .detect import score_clip
    from .ingest import (median_over_all, score_windows,
                         select_top_peaks)
    import auto_clips as _auto  # filtro previo (raíz del repo)

    scores, detail = score_windows(chat_path, lang=lang)
    median = median_over_all(scores)
    raw_peaks = select_top_peaks(scores, top_n=top_n)
    rank_map = {ts: i + 1 for i, (ts, _s) in
                enumerate(sorted(raw_peaks, key=lambda p: -p[1]))}
    selected, discarded = _auto.filter_candidates(
        raw_peaks, detail, rank_map, median)
    # Categoría GTA VI (regla del usuario 2026-11-18): el pico solo
    # vale si el capítulo del VOD donde cae es GTA VI. La categoría
    # se lee del chat.json (capítulos de Twitch) y se guarda por
    # pico; un pico en Just Chatting u otro juego se descarta aquí
    # mismo, no en publicación. Sin capítulos, la categoría queda
    # sin verificar (category_ok=None) y decide el gate de política.
    from .category import chapter_category_from_chat, is_gta6
    kept = []
    for peak_ts, score, multiple in selected:
        cat = chapter_category_from_chat(chat_path, peak_ts)
        _streamer, game, _src = _auto.chat_stream_info(chat_path, peak_ts)
        if cat is not None and not is_gta6(cat):
            discarded.append({"peak": peak_ts, "score": score,
                              "multiple": multiple,
                              "rank": rank_map.get(peak_ts),
                              "motivos": [f"categoría {cat!r}: no es GTA VI"]})
            continue
        kept.append((peak_ts, score, multiple, cat, game, _streamer))
    selected = [(p[0], p[1], p[2]) for p in kept]
    _peak_cat = {(p[0]): (p[3], p[4], p[5]) for p in kept}
    peaks = []
    for peak_ts, score, multiple in selected:
        viral = score_clip(detail[peak_ts], multiple,
                           rank=rank_map.get(peak_ts))
        d = detail[peak_ts]
        cat, game, streamer = _peak_cat.get(peak_ts, (None, None, None))
        # Puerta de render PRE-CORTE (regla del usuario 2026-11-18):
        # el pico solo se corta si ya pasa las puertas con el paquete
        # provisional. Si no pasa, a descartados SIN render. Si la
        # puerta no se puede evaluar (error), el pico pasa con el
        # error registrado: la puerta nunca tumba la detección; la
        # puerta de publicación lo vuelve a medir todo.
        gate = None
        try:
            gate = gate_peak_for_render(
                chat_path, peak_ts, score, multiple, d, median,
                list(scores.values()), lang=lang, game=game,
                streamer=streamer, category=cat,
                n=rank_map.get(peak_ts) or 1)
        except Exception as e:  # noqa: BLE001 - ver comentario arriba
            gate = {"pasa": True,
                    "motivo": f"puerta de render no evaluada ({e}); "
                              "se corta igual",
                    "viral_estimate": float(viral.get("total") or 0.0),
                    "seo": {}, "seo_medido": False, "estados": {},
                    "plataformas_publicables": [],
                    "pre_corte_title": None, "gate_error": str(e)}
        if not gate["pasa"]:
            discarded.append({"peak": peak_ts, "score": score,
                              "multiple": multiple,
                              "rank": rank_map.get(peak_ts),
                              "viral_estimate": gate["viral_estimate"],
                              "seo_pre_corte": gate["seo"],
                              "motivos": [f"puerta de render: "
                                          f"{gate['motivo']}"]})
            continue
        peaks.append({
            "start": int(peak_ts),
            "score": float(score),
            "multiple": float(multiple),
            "viral_total": viral.get("total"),
            "filter_passed": bool(viral.get("filter_passed")),
            # Categoría GTA VI del capítulo donde cae el pico.
            "category": cat,
            "game": game,
            "category_ok": (is_gta6(cat) if cat is not None else None),
            # Puerta de render pre-corte: título provisional, SEO
            # pre-corte y decisión, para que el corte y el panel la
            # re-verifiquen sin recalcular.
            "render_gate": {"pasa": gate["pasa"],
                            "motivo": gate["motivo"],
                            "viral_estimate": gate["viral_estimate"],
                            "seo_medido": gate["seo_medido"],
                            "plataformas_publicables":
                                gate["plataformas_publicables"]},
            "seo_scores": gate["seo"],
            "pre_corte_title": gate["pre_corte_title"],
            "render_gate_error": gate.get("gate_error"),
            # Hype por ráfaga (regla 2026-11-18, v2): velocidad del
            # chat en la ventana y su pico contra el baseline
            # rodante, guardado por pico (valor, ventana, baseline,
            # ratio) para ordenar los picos y alimentar viralidad.
            "burst_score": d.get("burst_score"),
            "burst_z": d.get("burst_z"),
            "burst_ratio": d.get("burst_ratio"),
            "msgs_per_min": d.get("msgs_per_min"),
            "msgs_per_60s": d.get("msgs_per_60s"),
            "msgs_per_sec": d.get("msgs_per_sec"),
            "hype_score": d.get("hype_score"),
            "hype_window_s": d.get("hype_window_s"),
            "hype_baseline": d.get("hype_baseline"),
            "hype_ratio": d.get("hype_ratio"),
            "hype_velocity_z": d.get("hype_velocity_z"),
            "hype_components": d.get("hype_components"),
        })
    return {
        "peaks": peaks,
        "discarded": discarded,
        "median": median,
        "n_windows": len(scores),
        "chat_path": chat_path,
    }


# Puerta de render PRE-CORTE (regla del usuario 2026-11-18): el clip
# SOLO se corta/renderiza si ya pasa las puertas. La viralidad y el
# SEO se miden ANTES del corte con el paquete PROVISIONAL (ficha del
# momento sin clip + títulos desde el chat, sin layout): el SEO sale
# con el mapper real (score_package_seo) y la viralidad es la
# estimación pre-corte (score_clip sin human ni audio).
#
# CALIBRACIÓN MEDIDA 2026-11-18 (no inventada; ver tests):
# - p3220 (Rubius, clip bueno, listo para encolar): viralidad real
#   53,8 (con human 5-7 + audio) pero estimación pre-corte 46,3;
#   SEO pre-corte 89/91/92/92 (máx. 92) vs 92/94/94/94 real.
# - p2010 (Rubius, descartado, viralidad 44,5): estimación = real
#   44,5 (sin human); SEO 92/94/94/94.
# La estimación pre-corte usa defaults conservadores 5,0 en los 5
# componentes humanos y subestima ~7 puntos la viralidad real de un
# clip bueno. Por eso el umbral PRE-CORTE es 45, no 50: con 50, p3220
# (46,3) se descartaría sin render. El 50 REAL sigue vigente en la
# puerta de publicación (policy.evaluate / publishGate del panel):
# nada se publica sin viralidad >= 50 y SEO >= 90 de verdad.
# El SEO pre-corte subestima ~3 puntos por lo visible (coherencia 80
# sin layout, en vez de 100); el umbral SEO pre-corte SÍ es 90 y la
# puerta pide el MÁXIMO >= 90 (al menos una plataforma publicable).
# Si el usuario confirma 50 pre-corte, cambiar PRE_CORTE_VIRAL_MIN
# a 50 sabiendo que p3220 se habría descartado.
PRE_CORTE_VIRAL_MIN = 45


def pre_corte_package(chat_path, peak_ts, score, multiple, detail,
                      median_score, all_scores, lang="es", game=None,
                      streamer=None, vod_id=None, game_source="",
                      clip_start=None, duration=None, category=None,
                      n=1, reel_patterns=None, visible=None):
    """Paquete PROVISIONAL sin vídeo: ficha del momento (sin clip,
    con las citas del chat) + títulos + SEO por plataforma.

    Usa los mismos constructores que el flujo post-corte
    (build_moment_card con clip_path=None, package_clip,
    score_package_seo) con el juego normalizado y los ejemplos del
    chat: sin el juego normalizado los títulos no nombran GTA 6 y
    el SEO sale sistemáticamente bajo. Solo sirve para la puerta de
    render; el paquete DEFINITIVO se construye tras el corte con la
    ficha, el layout y lo visible.
    ``visible`` (2026-11-19, calibración): lo que se ve de verdad
    ("gameplay" | "cams+gameplay"); sin él la coherencia queda en
    80 ("lo visible sin confirmar") y el SEO pre-corte pierde
    2-3 puntos en las 4 plataformas aunque el juego esté
    verificado. Pre-corte se pasa None (aún no hay layout) y el
    paso 7 del cutter lo vuelve a medir con el layout real.
    Devuelve (package, seo_scores) con seo_scores {plataforma:
    {"score": n, ...}}.
    """
    from .ingest import sample_messages
    from .metadata import package_clip, score_package_seo
    from .moment_card import build_moment_card
    d = detail or {}
    start = (max(0.0, float(clip_start)) if clip_start is not None
             else max(0.0, float(peak_ts) - CLIP_PRE_S))
    dur = float(duration) if duration else float(CLIP_DURATION_S)
    risas = d.get("risas", 0)
    hype = d.get("hype", 0)
    card = build_moment_card(
        clip_path=None, chat_path=chat_path,
        streamer=streamer or "", game=game,
        game_source=game_source or
        "categoría del capítulo (pre-corte)",
        language=lang if lang in ("es", "en") else "es",
        peak_ts_vod=peak_ts, clip_start_vod=start,
        clip_duration_s=dur, score=score, risas=risas, hype=hype,
        median_score=median_score, vod_id=vod_id, category=category)
    examples = sample_messages(chat_path, int(peak_ts), window_size=60)
    pkg = package_clip(
        n, peak_ts, score, risas, hype, median_score=median_score,
        all_scores=all_scores, lang=lang, examples=examples,
        moment_card=card, game=game, streamer=streamer, vod_id=vod_id,
        register=False, visible=visible, layout=None,
        reel_patterns=reel_patterns)
    seo = score_package_seo(pkg, game_verified=bool(game),
                            visible=visible,
                            lang=lang if lang in ("es", "en") else "es")
    return pkg, seo


def gate_peak_for_render(chat_path, peak_ts, score, multiple, detail,
                         median_score, all_scores, lang="es", game=None,
                         streamer=None, vod_id=None, game_source="",
                         clip_start=None, duration=None, category=None,
                         n=1, reel_patterns=None, cfg=None):
    """Puerta de render de UN pico, antes de descargar o cortar nada.

    1. Viralidad ESTIMADA pre-corte (score_clip sin human ni audio).
    2. Paquete provisional + SEO pre-corte (solo si el juego es
       conocido y hay título: sin juego no se puede medir el SEO y
       la puerta decide solo por viralidad, anotándolo).
    3. Puerta 50/90 de policy.render_gate con viral_min pre-corte
       (PRE_CORTE_VIRAL_MIN, calibrado, ver comentario arriba) y
       seo_gate 90.
    Devuelve {"pasa", "motivo", "viral_estimate", "seo",
    "seo_medido", "estados", "plataformas_publicables",
    "pre_corte_title"}.
    """
    from .detect import score_clip
    from .policy import render_gate
    viral = score_clip(detail, multiple)
    viral_estimate = float(viral.get("total") or 0.0)
    seo, seo_medido, title = {}, False, None
    error = None
    try:
        pkg, seo_full = pre_corte_package(
            chat_path, peak_ts, score, multiple, detail, median_score,
            all_scores, lang=lang, game=game, streamer=streamer,
            vod_id=vod_id, game_source=game_source,
            clip_start=clip_start, duration=duration,
            category=category, n=n, reel_patterns=reel_patterns)
        title = pkg.get("best_title")
        seo = {p: r.get("score") for p, r in (seo_full or {}).items()}
        # Sin juego conocido o sin título que pase el check, el SEO
        # provisional no es fiable: no se descarta por él.
        seo_medido = bool(game) and title is not None
    except Exception as e:  # noqa: BLE001 - la puerta nunca tumba
        error = str(e)      # la detección; el SEO queda sin medir.
    gate = render_gate(viral_estimate, seo if seo_medido else {},
                       cfg=cfg, viral_min=PRE_CORTE_VIRAL_MIN)
    motivo = gate["motivo"]
    if error:
        motivo += f" (SEO pre-corte no evaluado: {error})"
    if not seo_medido and not error:
        motivo += ("; SEO sin medir pre-corte (juego sin verificar "
                   "o sin título provisional)")
    return {"pasa": bool(gate["pasa"]), "motivo": motivo,
            "viral_estimate": viral_estimate,
            "seo": seo, "seo_medido": seo_medido,
            "estados": gate["estados"],
            "plataformas_publicables":
                gate["plataformas_publicables"],
            "pre_corte_title": title,
            "gate_error": error}


def _do_cut(video, start_sec, duration, output, layout_mode="auto",
            crf=23, cfg=None, hype_s=None):
    """Un corte 9:16 con layout (render.cut_clip_with_layout).
    Función separada para poder sustituirla en tests."""
    from .render import cut_clip_with_layout
    layout_cfg = None
    if cfg:
        layout_cfg = (cfg.get("layout") or {}).copy()
    if hype_s is not None:
        layout_cfg = dict(layout_cfg or {})
        layout_cfg["hype_s"] = list(hype_s)
    return cut_clip_with_layout(
        video, start_sec, duration, output, crf=crf, preset="fast",
        layout_mode=layout_mode, cfg=layout_cfg, hype_s=hype_s)


def cut_clip(video, start_sec, duration, output, layout_mode="auto",
             crf=23, cfg=None, hype_s=None):
    """Corta un clip vertical. Idempotente: si ``output`` ya existe
    y no está vacío, NO vuelve a llamar a ffmpeg: devuelve el
    archivo existente marcado como reutilizado.

    ``hype_s``: segundos de hype en tiempo del clip (gritos del
    streamer / explosión del chat). Con ellos, el punch-in va en
    ventanas de 4 s alrededor de cada momento (no la fija 16-26 s).
    """
    if not os.path.isfile(video):
        raise StepError(f"vídeo no existe: {video}", kind="permanent")
    if os.path.isfile(output) and os.path.getsize(output) > 0:
        return {"output": output, "reused": True,
                "start_sec": float(start_sec),
                "duration": float(duration)}
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    decision = _do_cut(video, start_sec, duration, output,
                       layout_mode=layout_mode, crf=crf, cfg=cfg,
                       hype_s=hype_s)
    if not os.path.isfile(output) or os.path.getsize(output) == 0:
        raise StepError(f"el corte no dejó archivo: {output}",
                        kind="permanent")
    return {"output": output, "reused": False,
            "start_sec": float(start_sec),
            "duration": float(duration),
            "layout": decision}


def _do_transcribe(video, model, lang, cfg, chat_lang):
    from .transcribe import transcribe_audio
    sub_cfg = (cfg or {}).get("subtitles") or {}
    return transcribe_audio(video, model_name=model, language=lang,
                            sub_cfg=sub_cfg, cfg=cfg or {},
                            chat_lang=chat_lang)


def subtitle_clip(video, srt=None, burn=None, style=None, cfg=None,
                  lang=None, model="tiny", chat_lang=None, title=None):
    """Transcribe + SRT (+ quemado opcional). Idempotente: si el SRT
    ya existe, se reutiliza sin re-transcribir. Si el clip no tiene
    voz, termina OK marcado ``no_speech`` (no es un fallo del paso)
    y SIN bloquear: la decisión de publicar la toman las puertas
    con los scores (policy.no_speech_blocked, regla 2026-11-19
    18:41: sin voz + pasa 45/80 -> se publica igual sin
    subtítulos; nunca se inventa texto). El título SÍ se quema
    aunque no haya voz (SRT vacío: solo la franja del título).
    El estilo sale del payload o de config.yaml
    (subtitles.default_style). ``title`` quema además el título en
    UNA línea en la franja 702-800 (plantilla v2, burn_subtitles
    con el style de config.yaml); sin él la franja queda en negro,
    como antes."""
    if not os.path.isfile(video):
        raise StepError(f"vídeo no existe: {video}", kind="permanent")
    cfg = cfg or {}
    srt = srt or (os.path.splitext(video)[0] + ".srt")
    # El estilo sale del payload o de config.yaml
    # (subtitles.default_style: "rubius" desde 2026-11-19). Sin él
    # en el cfg (cfg parcial, p. ej. tests), se carga config.yaml
    # en disco, mismo patrón que load_validator_config. Si el
    # archivo no lo trae, falla fuerte: sin defaults silenciosos
    # (el "rdjavi" silencioso de antes quemaba el VOD como willyrex
    # aunque el default acordado fuera otro; clip 23, 2026-11-19).
    # OJO: DEFAULT_CONFIG ya trae default_style "rubius", así que
    # el ConfigError de abajo solo salta si falta también ahí; el
    # fallo en vivo es para templates.default, que DEFAULT_CONFIG
    # no define.
    style = style or ((cfg.get("subtitles") or {}).get("default_style")
                      or None)
    if not style:
        from .config import load_config
        full = load_config()
        style = ((full.get("subtitles") or {}).get("default_style")
                 or None)
    if not style:
        from .validate import ConfigError
        raise ConfigError(
            "subtitles.default_style: falta en config.yaml (el estilo "
            "por defecto es rubius desde 2026-11-19; sin defaults "
            "silenciosos)")
    result = {"video": video, "srt": srt, "style": style,
              "burn": burn, "no_speech": False, "reused_srt": False,
              "needs_human_transcription": False,
              "publish_blocked": False, "title": title}
    transcript = None
    transcript_path = os.path.splitext(srt)[0] + ".transcript.json"
    if os.path.isfile(srt) and os.path.getsize(srt) > 0:
        result["reused_srt"] = True
        if os.path.isfile(transcript_path):
            with open(transcript_path, encoding="utf-8") as f:
                transcript = json.load(f)
    else:
        try:
            transcript, _info = _do_transcribe(
                video, model, lang, cfg, chat_lang)
        except ImportError as e:
            raise StepError(
                "faster-whisper no instalado en este entorno "
                f"(sin subtítulos): {e}", kind="permanent")
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)
        result["transcript"] = transcript_path
        if transcript.get("no_speech"):
            result["no_speech"] = True
            result["needs_human_transcription"] = False
            result["publish_blocked"] = False
            # Sin voz no hay SRT que quemar, pero el TÍTULO sí se
            # quema (regla 2026-11-19: el clip sin voz con buen
            # score se publica igual y la plantilla exige la franja
            # de título). SRT vacío -> burn_subtitles solo quema
            # el título, sin inventar ni una palabra.
            with open(srt, "w", encoding="utf-8") as f:
                f.write("")
        else:
            from .transcribe import generate_srt
            sub_cfg = (cfg.get("subtitles") or {})
            ok, _n = generate_srt(
                transcript, 0, 10 ** 9, srt,
                corrections=sub_cfg.get("gta6_corrections"))
            if not ok:
                result["no_speech"] = True
                result["needs_human_transcription"] = False
                result["publish_blocked"] = False
                with open(srt, "w", encoding="utf-8") as f:
                    f.write("")
    if burn:
        from .transcribe import burn_subtitles
        burn_subtitles(video, srt, burn, style=style, config=cfg,
                       title=title)
        result["burned"] = burn
    if transcript is not None:
        result["no_speech"] = bool(transcript.get("no_speech"))
        # La decisión de bloquear ya NO vive en la transcripción
        # (regla 2026-11-19): la toman las puertas con los scores.
        # Un transcript viejo en disco puede traer
        # publish_blocked=true: se ignora, nunca se propaga.
        result["needs_human_transcription"] = False
        result["publish_blocked"] = False
        result["words"] = transcript.get("word_count")
    return result


def apply_sfx(video, transcript_path=None, srt_path=None, payoff_s=20.0,
              cfg=None, out_path=None):
    """Mezcla SFX sobre el MP4 ya renderizado (2026-11-19: SFX por
    defecto en el render, vivo y VOD).

    ``cfg["sfx"]``: enabled (default True), max_sfx (default 2),
    lift (default True). La decisión de qué efectos y dónde la
    toma ``decide_sfx`` (payoff + dip de silencio -> vine boom;
    riser antes; fail visual si se conoce). Sin eventos, solo se
    aplica el lift de volumen. Si la mezcla falla por lo que sea,
    devuelve el vídeo original sin tocar (nunca tumba el clip).
    Devuelve (ruta_final, info).
    """
    sfx_cfg = ((cfg or {}).get("sfx") or {})
    if not sfx_cfg.get("enabled", True):
        return video, {"enabled": False, "events": []}
    if out_path is None:
        base, ext = os.path.splitext(video)
        out_path = f"{base}_sfx{ext}"
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return out_path, {"enabled": True, "events": [], "reused": True}
    transcript = None
    if transcript_path and os.path.isfile(transcript_path):
        try:
            with open(transcript_path, encoding="utf-8") as f:
                transcript = json.load(f)
        except Exception:
            transcript = None
    if transcript is None and srt_path:
        tpath = os.path.splitext(srt_path)[0] + ".transcript.json"
        if os.path.isfile(tpath):
            try:
                with open(tpath, encoding="utf-8") as f:
                    transcript = json.load(f)
            except Exception:
                transcript = None
    try:
        from .sfx_mix import mix_clip_with_sfx
        out, info = mix_clip_with_sfx(
            video, transcript=transcript, payoff_s=float(payoff_s),
            out_path=out_path, lift=bool(sfx_cfg.get("lift", True)),
            max_sfx=int(sfx_cfg.get("max_sfx", 2)))
        info = dict(info or {})
        info["enabled"] = True
        return out, info
    except Exception as e:  # noqa: BLE001 - el clip sigue sin SFX
        return video, {"enabled": True, "events": [],
                       "error": f"mezcla SFX falló: {e}"}


def package_clip_step(clip, title, description="", tags=None,
                      hashtags=None, thumbnail_text=None,
                      game_verified=True, visible=None, lang="es",
                      out_path=None):
    """Paquete SEO + prioridad por plataforma. Usa seo_score y
    priority IMPORTADOS (la fórmula viralidad^0,5 x SEO^0,3 x
    chat_norm^0,2 NO se reimplementa ni se toca). Escribe el
    paquete en JSON de forma atómica (tmp + replace)."""
    if not os.path.isfile(clip):
        raise StepError(f"clip no existe: {clip}", kind="permanent")
    if not (title or "").strip():
        raise StepError("falta el título del paquete (empaquetar no "
                        "inventa títulos: vienen del manifiesto o de "
                        "la revisión humana)", kind="permanent")
    from .priority import priority as priority_fn, publish_status
    from .seo_score import score_all_platforms

    pkg = {
        "title": title,
        "description": description or "",
        "tags": tags or [],
        "thumbnail_text": thumbnail_text,
        "hashtags": hashtags or [],
        "game_verified": game_verified,
        "visible": visible,
        "lang": lang,
    }
    seo = score_all_platforms(pkg)
    out_path = out_path or (clip + ".package.json")
    priorities, statuses = {}, {}
    for platform in PLATFORMS:
        score = seo[platform]["score"]
        # La viralidad y el chat llegan en el payload del job si se
        # conocen (del paso picos); sin ellos la prioridad es 0 y el
        # estado lo dice: empaquetar no inventa puntajes.
        viral = pkg.get("_virality")
        chat = pkg.get("_chat_multiple")
        if viral is None or chat is None:
            priorities[platform] = {"priority": 0.0,
                                    "reason": "sin viralidad/chat: "
                                    "la prioridad se calcula cuando "
                                    "el manifiesto trae el score"}
        else:
            priorities[platform] = priority_fn(viral, score, chat)
        status, why = publish_status(viral if viral is not None else 0,
                                     score)
        statuses[platform] = {"status": status, "reason": why}
    package = {"clip": clip, "title": title,
               "description": description or "",
               "tags": tags or [], "hashtags": hashtags or [],
               "thumbnail_text": thumbnail_text,
               "game_verified": game_verified, "visible": visible,
               "lang": lang,
               "seo": {p: seo[p]["score"] for p in PLATFORMS},
               "seo_detail": seo,
               "priority": priorities,
               "publish_status": statuses}
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(package, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)
    return {"package_path": out_path,
            "seo": {p: seo[p]["score"] for p in PLATFORMS},
            "priority": {p: priorities[p].get("priority")
                         for p in PLATFORMS},
            "publish_status": statuses}


# ---------------------------------------------------------------------------
# Handlers por paso (firma común: (store, job, cfg) -> result dict)
# ---------------------------------------------------------------------------

def _require(payload, *names):
    missing = [n for n in names if payload.get(n) in (None, "")]
    if missing:
        raise StepError(
            f"falta(n) campo(s) en el payload: {', '.join(missing)}",
            kind="permanent")


def _h_vod(store, job, cfg):
    payload = job["payload"]
    _require(payload, "url")
    from jobqueue import load_allowlist, parse_url
    platform, vod_id, slug = parse_url(payload["url"])
    streamer = (payload.get("streamer") or slug or "").strip().lower()
    if not payload.get("force"):
        allowlist = load_allowlist()
        if streamer and streamer not in allowlist:
            raise StepError(
                f"streamer fuera de la lista blanca: {streamer!r} "
                "(estar en la lista = permiso ya dado; sin ella el "
                "VOD no entra)", kind="permanent")
    # Este paso valida la entrada. La descarga del VOD/chat la sigue
    # haciendo el flujo legacy (auto_clips/worker) hasta que el
    # watcher escriba chat_path/video/out_dir en el payload del job.
    return {"vod_id": vod_id, "platform": platform,
            "streamer": streamer or None, "url": payload["url"]}


def _h_picos(store, job, cfg):
    payload = job["payload"]
    _require(payload, "chat_path")
    return detect_peaks(payload["chat_path"],
                        top_n=int(payload.get("top_n", 5)),
                        lang=payload.get("lang", "both"))


def _h_corte(store, job, cfg):
    payload = job["payload"]
    _require(payload, "video", "start_sec", "duration", "output")
    # Puerta de render PRE-CORTE (regla del usuario 2026-11-18):
    # si el job trae los scores del paso picos, se re-verifica la
    # puerta ANTES de cortar; si no pasa, NO se renderiza nada (el
    # job falla permanente con el motivo, sin gastar ffmpeg). Sin
    # scores en el payload, el corte es explícito/manual (tests,
    # re-corte a mano) y se hace igual.
    viral = payload.get("viral_total")
    seo = payload.get("seo_scores")
    if viral is not None and seo is not None:
        from .policy import render_gate
        gate = render_gate(viral, seo, cfg=cfg,
                           viral_min=PRE_CORTE_VIRAL_MIN)
        if not gate["pasa"]:
            raise StepError(
                f"puerta de render: {gate['motivo']} (sin render)",
                kind="permanent")
    result = cut_clip(payload["video"], float(payload["start_sec"]),
                    float(payload["duration"]), payload["output"],
                    layout_mode=payload.get("layout_mode", "auto"),
                    crf=int(payload.get("crf", 23)), cfg=cfg,
                    hype_s=payload.get("hype_s"))
    # Plantilla del clip (2026-11-19): la explícita del job manda;
    # si no, la que declara el render o la que produce su variante
    # ("rdjavi" -> willyrex v2). Fluye al paso empaquetar, donde el
    # validador final la usa; sin ella, el default (rubius).
    layout = (result or {}).get("layout") or {}
    from .config import template_for_variant
    result = dict(result)
    result["plantilla"] = (
        payload.get("plantilla")
        or layout.get("plantilla")
        or template_for_variant(layout.get("split_variant")))
    return result


def _h_subtitulos(store, job, cfg):
    payload = job["payload"]
    _require(payload, "video")
    # El título ya existe ANTES del quemado en el camino
    # automático: el VOD trae el pre_corte_title validado por la
    # puerta pre-corte (o el título del job); sin él la franja
    # 702-800 queda en negro.
    return subtitle_clip(
        payload["video"], srt=payload.get("srt"),
        burn=payload.get("burn"), style=payload.get("style"),
        cfg=cfg, lang=payload.get("lang"),
        model=payload.get("model", "tiny"),
        chat_lang=payload.get("chat_lang"),
        title=payload.get("pre_corte_title") or payload.get("title"))


def _h_empaquetar(store, job, cfg):
    payload = job["payload"]
    _require(payload, "clip", "title")
    viral = payload.get("virality")
    chat = payload.get("chat_multiple")
    # package_clip_step lee _virality/_chat_multiple del dict interno.
    result = package_clip_step(
        payload["clip"], payload["title"],
        description=payload.get("description", ""),
        tags=payload.get("tags"), hashtags=payload.get("hashtags"),
        thumbnail_text=payload.get("thumbnail_text"),
        game_verified=payload.get("game_verified", True),
        visible=payload.get("visible"), lang=payload.get("lang", "es"),
        out_path=payload.get("out_path"))
    # La prioridad real necesita viralidad y chat: si venían en el
    # payload, se recalculan aquí (misma función importada, sin
    # reimplementar) y se guardan en el paquete.
    if viral is not None and chat is not None:
        from .priority import priority as priority_fn
        from .seo_score import score_all_platforms
        seo = score_all_platforms({
            "title": payload["title"],
            "description": payload.get("description", ""),
            "tags": payload.get("tags"),
            "thumbnail_text": payload.get("thumbnail_text"),
            "hashtags": payload.get("hashtags"),
            "game_verified": payload.get("game_verified", True),
            "visible": payload.get("visible"),
            "lang": payload.get("lang", "es")})
        result["priority"] = {
            p: priority_fn(viral, seo[p]["score"], chat)["priority"]
            for p in PLATFORMS}
        with open(result["package_path"], encoding="utf-8") as f:
            package = json.load(f)
        package["priority"] = {
            p: priority_fn(viral, seo[p]["score"], chat) for p in PLATFORMS}
        package["virality"] = viral
        package["chat_multiple"] = chat
        tmp = result["package_path"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(package, f, ensure_ascii=False, indent=2)
        os.replace(tmp, result["package_path"])
    return result


def _h_publicar(store, job, cfg):
    payload = job["payload"]
    _require(payload, "clip")
    clip = payload["clip"]
    # La plataforma vive en la columna (un job por clip y
    # plataforma), no en el payload: se lee de ahí.
    platform = job.get("plataforma") or payload.get("plataforma")
    if not platform:
        raise StepError("publicar exige plataforma (un job por clip "
                        "y plataforma)", kind="permanent")
    mode = payload.get("mode", "draft")
    if not os.path.isfile(clip):
        raise StepError(f"clip no existe: {clip}", kind="permanent")
    if mode not in ("draft", "dry-run", "api"):
        raise StepError(f"modo de publicación desconocido: {mode!r} "
                        "(draft | dry-run | api)", kind="permanent")
    from .publish import PublishQueue, publish_one
    pq = PublishQueue()
    existing = pq._find(clip, platform)
    if existing and existing.get("state") == "published":
        return {"action": "already_posted",
                "platform_id": existing.get("platform_id"),
                "reason": "ese clip ya está publicado en esa "
                          "plataforma (anti-duplicado)"}
    if mode == "dry-run":
        return {"action": "dry-run",
                "reason": "simulacro: no se sube nada"}
    item = pq.enqueue(clip, platform, streamer=payload.get("streamer"),
                      vod_id=payload.get("vod_id"))
    if mode == "draft":
        # Borrador para publicar a mano (TikTok sin auditoría,
        # captcha pendiente...): el job queda esperando_humano.
        pq.set_state(item, "draft",
                     "borrador listo para subir a mano")
        return {"action": "draft",
                "reason": "borrador en la cola de publicación: "
                          "lo publica un humano (o el agente de "
                          "navegador cuando el captcha lo pida)"}
    # mode == "api": subida real por API con la política (6.3).
    res = publish_one(pq, item, cfg=cfg, live=True, mode="full")
    action = res.get("action")
    if action == "published":
        return {"action": "published",
                "platform_id": res.get("platform_id"),
                "reason": res.get("reason", "")}
    if action == "draft":
        return {"action": "draft",
                "reason": res.get("reason", "queda en borrador")}
    if action == "retained":
        return {"action": "retained",
                "reason": res.get("reason", "retenido por la política")}
    if action in ("discarded", "quarantined"):
        raise StepError(f"no publicable ({action}): "
                        f"{res.get('reason', '')}", kind="permanent")
    if action in ("limited", "paused", "processing"):
        return {"action": "retained",
                "reason": f"{action}: {res.get('reason', '')}"}
    raise StepError(f"publicación fallida: {res.get('reason', action)}",
                    kind="transient")


HANDLERS = {
    "vod": _h_vod,
    "picos": _h_picos,
    "corte": _h_corte,
    "subtitulos": _h_subtitulos,
    "empaquetar": _h_empaquetar,
    "publicar": _h_publicar,
}


# ---------------------------------------------------------------------------
# Orquestador: un job, y la cadena de pasos
# ---------------------------------------------------------------------------

def enqueue_pipeline(store, clip_id, payload, prioridad=0.0):
    """Encola la cadena completa de un VOD/clip: solo el paso ``vod``.
    Los pasos siguientes los crea ``advance_pipeline`` al terminar
    cada paso (la cola avanza sola). El payload viaja de paso en
    paso (url, chat_path, video, out_dir, título...)."""
    if not (payload or {}).get("url"):
        raise StepError("enqueue_pipeline exige payload.url",
                        kind="permanent")
    return store.enqueue(clip_id, "vod", payload=dict(payload),
                         prioridad=prioridad)


def enqueue_publish(store, clip_id, plataforma, clip_path, mode="draft",
                    prioridad=0.0, **extra):
    """Crea UN job de publicar (clip + plataforma). Lo llama la
    aprobación humana (panel/CLI) y, desde 2026-11-18, también
    advance_pipeline automáticamente para TikTok al terminar
    ``empaquetar`` (orden del usuario: 100% automático). ``mode``:
    draft (borrador a mano), dry-run (simulacro) o api (subida
    real por API)."""
    payload = {"clip": clip_path, "mode": mode}
    payload.update(extra)
    return store.enqueue(clip_id, "publicar", plataforma=plataforma,
                         payload=payload, prioridad=prioridad)


def advance_pipeline(store, job, result, cfg=None):
    """Tras un paso terminado, encola el siguiente (si lo hay).

    * picos -> corte: UN job de corte por pico (fan-out), con salida
      en ``<out_dir>/clips/clip_NN.mp4``. Sin picos no hay nada que
      cortar: la cadena se detiene (no es un fallo).
    * corte -> subtitulos, subtitulos -> empaquetar: un job con el
      payload acumulado (el vídeo de salida pasa a ser la entrada).
    * empaquetar -> publicar (TikTok, mode api, AUTOMÁTICO desde
      2026-11-18 por orden del usuario: 100% automático): solo
      TikTok se auto-encola; YouTube/Instagram/Facebook siguen
      manuales (panel). La puerta de subtítulos es dura: si el
      clip viene con publish_blocked, NO se encola nada; sin voz
      verificable solo se frena si no pasa las puertas (regla
      2026-11-19: sin voz + buen score se publica igual sin
      subtítulos). El resto de puertas (viralidad >= 50,
      SEO >= 90, duración 40-60 s, topes diarios, lista blanca)
      las aplica publish_one al ejecutar el job de publicar.
    Devuelve la lista de jobs creados.
    """
    paso = job["paso"]
    if paso not in PIPELINE:
        return []
    if paso == "empaquetar":
        return _advance_empaquetar_to_publish(store, job, result,
                                              cfg=cfg)
    idx = PIPELINE.index(paso)
    if idx + 1 >= len(PIPELINE):
        return []
    nxt = PIPELINE[idx + 1]
    payload = dict(job["payload"] or {})
    created = []
    if paso == "picos" and nxt == "corte":
        peaks = (result or {}).get("peaks") or []
        video = payload.get("video")
        out_dir = payload.get("out_dir")
        if not peaks or not video or not out_dir:
            store._log(job["id"], "pipeline_stop",
                       "sin picos, sin vídeo o sin out_dir: no se "
                       "encola corte")
            return []
        clips_dir = os.path.join(out_dir, "clips")
        for i, peak in enumerate(peaks, 1):
            start = max(0.0, float(peak["start"]) - CLIP_PRE_S)
            # Regla 2026-11-18: la duración pedida se sujeta al rango
            # [40, 60] s (mínimo 40, tope duro 60); un pedido corto se
            # alarga hasta 40, nunca se deja un clip de 21 s ni de 38 s.
            dur = float(payload.get("duration", CLIP_DURATION_S))
            dur = max(CLIP_MIN_DURATION_S,
                      min(CLIP_MAX_DURATION_S, dur))
            out = os.path.join(clips_dir, f"clip_{i:02d}.mp4")
            p = dict(payload)
            p.update({"video": video, "start_sec": start,
                      "duration": dur,
                      "output": out,
                      "peak_start": peak["start"],
                      "viral_total": peak.get("viral_total"),
                      # Puerta de render: los scores del pico viajan
                      # al job de corte para que _h_corte re-verifique
                      # antes de renderizar.
                      "seo_scores": peak.get("seo_scores"),
                      "pre_corte_title": peak.get("pre_corte_title"),
                      "chat_multiple": peak.get("multiple")})
            created.append(store.enqueue(
                f"{job['clip_id']}:p{int(peak['start'])}", "corte",
                payload=p, prioridad=float(
                    peak.get("viral_total") or peak.get("score") or 0),
                vod_job_id=job.get("vod_job_id")))
        store._log(job["id"], "pipeline_advance",
                   f"picos -> {len(created)} corte(s)")
        return created
    merged = dict(payload)
    merged.update({k: v for k, v in (result or {}).items()
                   if k in ("output", "burned", "package_path", "srt",
                            "video", "clip", "publish_blocked",
                            "plantilla",
                            "needs_human_transcription", "no_speech")})
    if paso == "corte":
        merged["video"] = (result or {}).get("output") or merged.get("video")
    if paso == "subtitulos":
        merged["clip"] = ((result or {}).get("burned")
                          or (result or {}).get("video")
                          or merged.get("video"))
        merged["title"] = merged.get("title") or payload.get("title")
    created.append(store.enqueue(job["clip_id"], nxt, payload=merged,
                                 prioridad=job.get("prioridad", 0),
                                 vod_job_id=job.get("vod_job_id")))
    store._log(job["id"], "pipeline_advance", f"{paso} -> {nxt}")
    return created


def _advance_empaquetar_to_publish(store, job, result, cfg=None):
    """empaquetar -> publicar (TikTok, mode api, automático).

    Puerta dura de subtítulos (regla 2026-11-17 22:10, matizada
    2026-11-19 18:41): publish_blocked explícito -> no se encola
    nada y la cadena se detiene con motivo visible. Sin voz
    verificable (no_speech) -> solo se frena si NO pasa las
    puertas (policy.no_speech_blocked, viralidad/SEO de
    config.yaml): sin voz + buen score se publica igual sin
    subtítulos. Sin clip o sin archivo -> tampoco. En el resto, el
    job de publicar en modo api pasa por publish_one, que aplica
    el resto de puertas (viralidad, SEO, duración, topes, lista
    blanca); si no las pasa, queda retenido/descartado, nunca se
    publica. Sin scores en el payload no se puede probar que pase
    las puertas: se frena (falla cerrado).
    Idempotente: store.enqueue no duplica por clave.

    Validación final del preview (2026-11-18, final_review.py):
    DESPUÉS del render y ANTES de auto-encolar. Puerta DURA de
    cara + plantilla (2026-11-19): rechazado -> no se encola nada.
    Pendiente (sin Gemini) -> el job se encola en modo draft para
    revisión humana, nunca en api. ENCENDIDA por defecto
    (2026-11-18 22:57); desactivada explícitamente
    (final_review.enabled=False) -> comportamiento de antes
    (mode api).
    """
    from .policy import no_speech_blocked
    payload = dict(job["payload"] or {})
    blocked = bool(payload.get("publish_blocked")
                   or (result or {}).get("publish_blocked")
                   or no_speech_blocked(
                       bool(payload.get("no_speech")
                            or (result or {}).get("no_speech")),
                       payload.get("viral_total"),
                       payload.get("seo_scores"), cfg))
    if blocked:
        store._log(job["id"], "pipeline_stop",
                   "empaquetar: subtítulos incompletos "
                   "(publish_blocked o sin voz bajo puertas): "
                   "no se auto-encola publicar")
        return []
    clip = (payload.get("clip") or (result or {}).get("clip")
            or payload.get("video") or (result or {}).get("video"))
    if not clip or not os.path.isfile(clip):
        store._log(job["id"], "pipeline_stop",
                   "empaquetar: sin clip en disco: no se auto-encola "
                   "publicar")
        return []
    # Validación final del preview (2026-11-18, final_review.py):
    # DESPUÉS del render y ANTES de auto-encolar. Puerta DURA de
    # cara + plantilla (2026-11-19, validate_final_clip): si la
    # cara o la plantilla fallan, NO se encola nada y la cadena se
    # detiene con motivo visible para revisión humana (nunca en
    # silencio); el re-render automático en este camino queda
    # pendiente de poder pasar una config por job al paso "corte"
    # (hoy la cfg es global del run_step). Pendiente (sin Gemini)
    # -> el job se encola en modo draft para revisión humana, nunca
    # en api. ENCENDIDA por defecto (2026-11-18 22:57);
    # desactivada explícitamente (final_review.enabled=False) ->
    # comportamiento de antes (mode api). Sin config del gate,
    # ConfigError: falla en voz alta, nunca con defaults.
    from .final_review import (
        transcript_text_for_srt, validate_final_clip)
    transcript = (payload.get("transcript_text") or
                  transcript_text_for_srt(payload.get("srt")))
    final_review = validate_final_clip(
        clip, payload.get("title") or "",
        transcript_text=transcript,
        srt_path=payload.get("srt"), cfg=cfg,
        plantilla=payload.get("plantilla"))
    decision = final_review.get("decision")
    store._log(job["id"], "pipeline_advance",
               f"validación final del preview: {decision} "
               f"({final_review.get('motivo') or final_review.get('resumen') or ''})")
    if decision == "rechazado":
        store._log(job["id"], "pipeline_stop",
                   "validación final: "
                   f"{final_review.get('motivo')}: no se auto-encola "
                   "publicar"
                   + (" (revisión humana)"
                      if final_review.get("needs_human_review")
                      else ""))
        return []
    mode = "draft" if decision == "pendiente_revision" else "api"
    pub = store.enqueue(
        job["clip_id"], "publicar", plataforma="tiktok",
        payload={"clip": clip, "mode": mode,
                 "streamer": payload.get("streamer"),
                 "vod_id": payload.get("vod_id"),
                 "final_review": decision},
        prioridad=job.get("prioridad", 0),
        vod_job_id=job.get("vod_job_id"))
    store._log(job["id"], "pipeline_advance",
               f"empaquetar -> publicar (tiktok, mode {mode}, "
               f"validación final: {decision})")
    return [pub]


def bridge_legacy_queue(store, queue, limit=1):
    """Puente cola legacy (VODs) -> step pipelines (costura GH Actions).

    Toma jobs legacy en ``queued`` (los que deja ``watch --once``) y
    crea una pipeline por VOD con payload {url, lang, out_dir,
    vod_job_id}. El job legacy queda marcado ``processing`` (reclamado
    por el puente; si el supervisor muere, el legacy lo recupera con
    ``requeue_stale_processing``). La idempotencia del paso ``vod``
    hace que correr el puente dos veces no duplique pipelines.

    LÍMITE DECLARADO (no escondido): el pipeline de pasos espera
    ``chat_path`` y ``video`` en el payload, y el watcher legacy hoy
    NO los escribe (el chat/vídeo los descarga el worker legacy
    dentro de auto_clips). Hasta que ese cableado exista, ``picos``
    falla permanente con motivo claro ("falta chat_path") y la cadena
    se detiene de forma visible, nunca en silencio. El puente es la
    costura; el cableado del chat/vídeo es trabajo pendiente (ver
    docs/jobs-workers.md §7.5). No correr el worker legacy y el
    supervisor en paralelo sobre el mismo VOD.
    """
    created = []
    for _ in range(limit):
        job = queue.next_queued()
        if job is None:
            break
        payload = {"url": job["url"], "lang": job.get("lang", "es"),
                   "out_dir": job["out_dir"], "vod_job_id": job["id"]}
        step_job = enqueue_pipeline(store, job["vod_id"], payload)
        queue.set_state(job["id"], "processing")
        queue.log(job["id"], "bridged_to_steps",
                  f"pipeline step job #{step_job['id']} "
                  f"(clip {job['vod_id']})")
        created.append(step_job)
    return created


def _classify_unexpected(exc):
    try:
        from jobqueue import classify_error
        kind = classify_error(str(exc))
        return "transient" if kind == "transient" else "permanent"
    except Exception:
        return "permanent"


def run_step(store, job_id, cfg=None):
    """Ejecuta UN job: reclamar -> handler -> done/failed/
    esperando_humano/scheduled (+ avance de la cadena si terminó).

    Devuelve el job actualizado. La config se lee fresca en cada
    job (lo del panel aplica sin reiniciar)."""
    cfg = load_worker_config() if cfg is None else cfg
    job = store.get(job_id)
    if job is None:
        raise StepError(f"no existe el step job #{job_id}",
                        kind="permanent")
    claimed = store.claim(job_id)
    if claimed is None:
        return job  # otro worker lo tomó, o ya no está pendiente
    job = claimed
    handler = HANDLERS.get(job["paso"])
    if handler is None:
        return store.fail(job_id, f"sin handler para el paso "
                                  f"{job['paso']!r}", "permanent")
    try:
        result = handler(store, job, cfg)
    except StepError as e:
        if e.kind == "human":
            return store.wait_human(job_id, str(e), result=None)
        return store.fail(job_id, str(e), e.kind)
    except Exception as e:  # no dejar el job colgado en running
        return store.fail(job_id, f"error inesperado: {e}",
                          _classify_unexpected(e))
    # Interpretación del resultado en publicar (el resto: done).
    if job["paso"] == "publicar":
        action = (result or {}).get("action")
        if action == "draft":
            return store.wait_human(
                job_id, (result or {}).get("reason", "borrador listo"),
                result=result)
        if action == "retained":
            retry_at = (datetime.now(timezone.utc)
                        + timedelta(seconds=RETAINED_RETRY_S)).isoformat(
                            timespec="seconds")
            return store.schedule(job_id, retry_at, result=result)
        if action == "dry-run":
            return store.complete(job_id, result, target="done")
        return store.complete(job_id, result)
    done = store.complete(job_id, result)
    try:
        advance_pipeline(store, done, result, cfg=cfg)
    except Exception as e:  # el avance no debe tumbar el paso hecho
        store._log(job_id, "pipeline_advance_failed", str(e)[:300])
    return done


# ---------------------------------------------------------------------------
# Supervisor: UN SOLO PUNTO DE ENCENDIDO
# ---------------------------------------------------------------------------

def _log(msg):
    print(f"[workers {datetime.now().strftime('%H:%M:%S')}] {msg}",
          flush=True)


def run_supervisor(store, once=False, max_jobs=None, poll_s=5,
                   include_scheduled=True):
    """El supervisor único: lo que enciende el botón Iniciar.

    Destino principal: GitHub Actions (workflows cerebro-*.yml: una
    corrida programada 09:00 America/Santiago que ejecuta
    ``python -m clipagent workers start --once``). Cloudflare solo
    lleva panel/API/cola; el cerebro Python (ffmpeg, Whisper) corre
    en el runner de GitHub, $0 dentro de los 2.000 min/mes gratis
    del repo privado (1 clip/día cabe de sobra; la lentitud no
    importa por ser programado).

    Bucle: recuperar jobs de un worker muerto -> tomar el siguiente
    job debido (cualquier paso, por prioridad) -> ejecutarlo ->
    repetir. Si aparece ``queue.kill`` (kill-switch), se pausa de
    forma segura y sale con 1 (los jobs quedan donde estaban, nada
    se pierde). ``once`` procesa hasta vaciar lo debido y sale (para
    cron/tests). Sin ``once`` corre hasta Ctrl+C.

    Devuelve 0 si terminó sin error, 1 si se pausó por kill-switch.
    """
    try:
        from jobqueue import kill_switch_active
    except Exception:
        kill_switch_active = lambda: False  # noqa: E731
    done = 0
    while True:
        if kill_switch_active():
            _log("Kill-switch ACTIVO (queue.kill): pausa segura. "
                 "Bórralo para reanudar.")
            return 1
        try:
            stale = store.requeue_stale()
        except Exception as e:
            _log(f"no se pudo recuperar jobs viejos: {e}")
            stale = 0
        if stale:
            _log(f"Recuperados {stale} job(s) de un worker muerto.")
        due = store.next_due(include_scheduled=include_scheduled,
                             limit=1)
        if not due:
            if once:
                _log("Nada debido: cola al día.")
                return 0
            time.sleep(poll_s)
            continue
        job = due[0]
        _log(f"Job #{job['id']} [{job['paso']}] {job['clip_id']} "
             f"({job.get('plataforma') or '-'}) -> en marcha")
        try:
            final = run_step(store, job["id"])
        except StepError as e:
            _log(f"Job #{job['id']}: {e} ({e.kind})")
            final = store.get(job["id"])
        _log(f"Job #{job['id']}: {final['estado']}"
             + (f" — {final.get('error', '')[:120]}"
                if final.get("error") else ""))
        done += 1
        if max_jobs is not None and done >= max_jobs:
            return 0


def run_worker(store, paso, once=False, max_jobs=None, poll_s=5,
               include_scheduled=False):
    """Worker de UN solo paso (uso operativo/depuración; el usuario
    normal usa el supervisor, que corre todos los pasos)."""
    if paso not in STEPS:
        raise StepError(f"paso desconocido: {paso!r}", kind="permanent")
    done = 0
    while True:
        store.requeue_stale()
        due = store.next_due(paso=paso,
                             include_scheduled=include_scheduled,
                             limit=1)
        if not due:
            if once:
                return 0
            time.sleep(poll_s)
            continue
        run_step(store, due[0]["id"])
        done += 1
        if once or (max_jobs is not None and done >= max_jobs):
            return 0


def workers_status(store):
    """Estado para el panel / CLI: conteos por estado, atascados con
    motivo y hora, y próximos jobs debidos por paso."""
    counts = store.counts()
    stuck = store.stuck(limit=20)
    due = {}
    for paso in STEPS:
        due[paso] = len(store.next_due(
            paso=paso, include_scheduled=True, limit=100))
    return {"counts": counts, "stuck": stuck, "due_by_step": due,
            "compute": step_compute_requirements()}
