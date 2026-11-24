"""Fallback VOD del cerebro (2026-11-19, corrección del usuario;
2026-11-19: también EN, fallo 3 del test de día completo).

Cuando NO hay ningún streamer en directo en GTA VI (ni ES ni EN),
el cerebro cambia solo a modo VOD y corta de los VOD más recientes
(ES primero: Rubius, IlloJuan, resto ES; después EN en el orden de
streamers.yaml), con los mismos filtros de siempre. Lo hace el
workflow Cerebro — Live Cutter (GitHub Actions, 100% automático):
nada manual, nada local, y sigue trabajando aunque el usuario se
desloguee (el cron */15 y el dispatch del panel lo disparan sin
sesión del usuario).

Los VODs EN se procesan con --lang en: títulos, descripción, tags,
transcripción de subtítulos, revisión y SEO en inglés (auto_clips
ya es bilingüe: el manifiesto trae title_en/desc_en y el SEO se
puntúa con lang=en). Se encolan con language="en" (columna
clips.language del panel, migración 0019: sin ella el panel asume
"es" y el clip EN caería en las cuentas ES al publicar).

Reglas duras (las del usuario, no se tocan salvo orden suya):
  * El corte de VOD es trabajo del CEREBRO, nunca de un agente
    manual (2026-11-19 02:18). Este módulo es el cerebro.
  * Solo se baja el SEGMENTO del clip, nunca el VOD entero, y del
    chat solo la ventana alrededor del momento (reutiliza
    auto_clips.py, que ya lo hace así con TwitchDownloaderCLI).
  * Filtros (2026-11-19, orden del usuario: listones bajados de
    50/90 a 45/80): viralidad >= 45, SEO >= 80 en las 4
    plataformas, clip de 40 a 60 s, juego GTA VI, subtítulos
    completos verificados (nada inventado, nada sin voz). Sin
    rescate por gritos: la puerta es 45/80 tal cual.
  * Pre-chequeo de VOD (2026-11-19): un VOD que claramente no es
    GTA VI (título sin mención de GTA VI y categoría Helix
    conocida que no es GTA VI) se salta sin gastar el cupo de 2
    VODs ni refrescar el enfriamiento. Ante la duda se procesa
    (fail open: la puerta precisa sigue siendo el juego del
    manifiesto por clip).
  * Los clips van a la Cola del panel con el MP4 (origin='vod'),
    SIN publicar: este módulo no tiene ninguna llamada a publicar
    (ni publish_clip del puente ni publish loop). La publicación la
    decide el panel después, con sus topes.
  * YouTube (2026-11-21, flag ``--youtube``, apagado por
    defecto): ``latest_vod_for`` lista los VODs del canal con
    ``clipagent.youtube.youtube_check`` (yt-dlp --flat-playlist,
    sin API key, $0). Sin el flag, un streamer de YouTube se
    salta como antes. OJO: ``auto_clips.py`` sigue siendo
    Twitch/Kick (chat + TwitchDownloaderCLI), así que un VOD de
    YouTube listado llega a ``run_auto_clips`` y falla sin
    clips; el corte automático desde YouTube (segmento +
    heatmap) es trabajo posterior, no este flag.
  * Dedup contra el panel: un (VOD, inicio) ya encolado no se
    vuelve a encolar, aunque el estado local se pierda.
  * Coste acotado: máx. 2 VODs y 2 clips por corrida, y cada VOD
    tiene 6 h de enfriamiento tras intentarlo. En estado estable
    (VODs ya intentados) la corrida es solo el barrido + la lectura
    de estado, ~1-2 min.

El estado vive en runs/vod-fallback/state.json y se restaura/guarda
con el artefacto `cerebro-live-vod-state` del workflow (como el
estado diario de cerebro, pero separado para no pisarlo).
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

# Prioridad de VODs ordenada por el usuario (2026-11-19): Rubius
# primero, después IlloJuan; el resto de ES en el orden de
# streamers.yaml.
FALLBACK_PRIORITY = ["rubius", "illojuan"]
# Listones duros del fallback (2026-11-19, orden del usuario:
# bajados de 50/90 a 45/80).
MIN_VIRALITY = 45
MIN_SEO = 80
MIN_DUR_S = 40
MAX_DUR_S = 60
PLATFORMS = ["youtube_shorts", "tiktok", "instagram_reels",
             "facebook_reels"]
MAX_VODS_PER_RUN = 2
MAX_CLIPS_PER_RUN = 2
TOP_N_PER_VOD = 2
VOD_COOLDOWN_H = 6
AUTO_CLIPS_TIMEOUT_S = 1200  # 20 min por VOD
DEFAULT_STATE_PATH = os.path.join("runs", "vod-fallback", "state.json")
DEFAULT_OUT_DIR = os.path.join("runs", "vod-fallback")


def _log(msg):
    print(f"[vod-fallback {datetime.now().strftime('%H:%M:%S')}] {msg}",
          flush=True)


def _report_phase(phase, detail="", bridge=None, dry_run=False):
    """Fase al panel (2026-11-19): el panel muestra qué hace el
    cerebro AHORA (Modo VOD al entrar, Sin directos al terminar).
    Nunca levanta ni tumba la corrida; en dry-run no se reporta."""
    if dry_run:
        return
    try:
        from .panel_bridge import report_brain_phase
        report_brain_phase(phase, detail, bridge=bridge)
    except Exception:  # noqa: BLE001 - la fase es informativa
        pass


def _report_event(kind, message, bridge=None, dry_run=False):
    """Evento de clip al panel (2026-11-19): cortado, encolado o
    descartado, para que el usuario vea dónde se pierden los
    clips. Nunca levanta ni tumba la corrida; en dry-run no se
    reporta."""
    if dry_run:
        return
    try:
        from .panel_bridge import report_brain_event
        report_brain_event(kind, message, bridge=bridge)
    except Exception:  # noqa: BLE001 - el evento es informativo
        pass


def _clip_title(clip, lang="es"):
    """Título del manifiesto para los mensajes de evento (best_title
    del paquete, o title_es/title_en según el idioma)."""
    viral = clip.get("viral") or {}
    title_key = "title_en" if lang == "en" else "title_es"
    return viral.get("best_title") or clip.get(title_key) or ""


def _seo_summary(seo_scores):
    """'YT 92 · TT 89 · IG 91 · FB 90' a partir de
    {plataforma: {score}} (manifiesto de auto_clips)."""
    short = {"youtube_shorts": "YT", "tiktok": "TT",
             "instagram_reels": "IG", "facebook_reels": "FB"}
    return " · ".join(
        f"{short.get(p, p)} {((seo_scores or {}).get(p) or {}).get('score')}"
        for p in PLATFORMS)


# ---------------------------------------------------------------------------
# Decisión pura (testeable sin red ni panel)
# ---------------------------------------------------------------------------

def es_live_gta6(live_entries, streamers=None):
    """¿Hay algún ES en directo en GTA VI?

    live_entries: la lista de scan_live (solo los que YA están en
    vivo en GTA VI). streamers: entradas de load_streamers (si no
    se pasan, se cargan). Un nombre sin entrada conocida no cuenta
    como ES (no se inventa el idioma).
    """
    from .streamers import load_streamers
    entries = streamers if streamers is not None else load_streamers()
    lang = {e["name"]: (e.get("language") or "") for e in entries}
    return [x for x in (live_entries or [])
            if lang.get(x.get("name"), "") == "es"]


def live_gta6(live_entries, streamers=None):
    """¿Hay algún streamer conocido en directo en GTA VI, en
    cualquier idioma (ES o EN)?

    Es la puerta del fallback (2026-11-19, fallo 3): si hay
    alguien en vivo en GTA VI, el cortador en vivo se encarga y no
    hay modo VOD, sea ES o EN. Un nombre sin entrada conocida no
    cuenta (no se inventa el idioma).
    """
    from .streamers import load_streamers
    entries = streamers if streamers is not None else load_streamers()
    known = {e["name"] for e in entries}
    return [x for x in (live_entries or [])
            if x.get("name") in known]


def fallback_order(streamers, only=None):
    """Orden de streamers para el fallback VOD.

    ES primero (Rubius, IlloJuan, resto ES en el orden de
    streamers.yaml) y después EN en el orden de streamers.yaml
    (2026-11-19, fallo 3: sin ES en vivo tampoco hay EN en vivo,
    así que los VODs EN también se procesan). Solo enabled +
    estado concedido. `only` (nombres, minúsculas) restringe la
    lista (dispatch manual del workflow).
    """
    only_set = {s.strip().lower() for s in (only or []) if s.strip()}

    def _eligible(lang):
        return [e for e in streamers
                if (e.get("language") or "") == lang
                and e.get("enabled", True)
                and (e.get("estado") or "") == "concedido"
                and (not only_set or e["name"] in only_set)]

    es = _eligible("es")
    head = [e for n in FALLBACK_PRIORITY for e in es if e["name"] == n]
    tail = [e for e in es if e["name"] not in FALLBACK_PRIORITY]
    return head + tail + _eligible("en")


def _seo_score_value(entry):
    """Score numérico de una entrada SEO del manifiesto ({score}).
    Sin número -> 0 (falla cerrado, nunca rompe el min())."""
    value = (entry or {}).get("score")
    return value if isinstance(value, (int, float)) else 0


def _gate_components(clip, vod_id):
    """Componentes crudos de los filtros duros, para
    clip_passes_gates. ``vod_id`` solo por simetría con
    clip_passes_gates."""
    dur = clip.get("duration")
    dur_ok = (isinstance(dur, (int, float))
              and MIN_DUR_S <= dur <= MAX_DUR_S)
    viral = clip.get("viral_score") or {}
    total = viral.get("total")
    viral_ok = isinstance(total, (int, float)) and total >= MIN_VIRALITY
    seo = clip.get("seo_scores") or {}
    missing = [p for p in PLATFORMS if p not in seo]
    if missing:
        seo_min = None
    else:
        seo_min = min(_seo_score_value(seo.get(p)) for p in PLATFORMS)
    seo_ok = seo_min is not None and seo_min >= MIN_SEO
    game = ((clip.get("moment_card") or {}).get("game")
            or (clip.get("viral") or {}).get("game"))
    from .category import is_gta6
    game_ok = bool(game) and bool(is_gta6(game))
    return {"dur": dur, "dur_ok": dur_ok, "total": total,
            "viral_ok": viral_ok, "seo": seo, "missing_seo": missing,
            "seo_min": seo_min, "seo_ok": seo_ok,
            "game": game, "game_ok": game_ok}


def clip_passes_gates(clip, vod_id):
    """¿El clip del manifiesto pasa los filtros duros del fallback?

    Devuelve (ok, motivos). Falla cerrado: sin viral_score, sin SEO
    en alguna plataforma o sin juego verificado, no pasa.
    """
    c = _gate_components(clip, vod_id)
    reasons = []
    if not c["dur_ok"]:
        reasons.append(
            f"duración {c['dur']}s fuera de {MIN_DUR_S}-{MAX_DUR_S}s")
    if not c["viral_ok"]:
        reasons.append(f"viralidad {c['total']} < {MIN_VIRALITY}")
    if c["missing_seo"]:
        reasons.append(f"sin SEO en: {', '.join(c['missing_seo'])}")
    elif not c["seo_ok"]:
        detail = ", ".join(
            f"{p} {(c['seo'].get(p) or {}).get('score')}"
            for p in PLATFORMS)
        reasons.append(
            f"SEO mínimo {c['seo_min']} < {MIN_SEO} ({detail})")
    if not c["game_ok"]:
        reasons.append(
            f"juego no verificado como GTA VI: {c['game']!r}")
    return (not reasons), reasons


def already_enqueued(panel_clips, vod_id, start_sec):
    """¿Ese (VOD, inicio) ya está en la Cola del panel?

    panel_clips: lo que devuelve PanelBridge.list_clips() (cada clip
    trae source_id y start_sec). La comparación es por entero de
    inicio: el panel guarda start_sec entero.
    """
    try:
        want_start = int(start_sec)
    except (TypeError, ValueError):
        return False
    for c in (panel_clips or []):
        try:
            same_vod = str(c.get("source_id")) == str(vod_id)
            same_start = int(c.get("start_sec")) == want_start
        except (TypeError, ValueError):
            continue
        if same_vod and same_start:
            return True
    return False


def panel_score(viral_total, seo_scores, chat_multiple):
    """Score de la Cola: prioridad de YouTube (misma fórmula que el
    cortador en vivo: finalize_package)."""
    from .priority import priority as priority_fn
    seo_yt = (seo_scores or {}).get("youtube_shorts") or {}
    p = priority_fn(viral_total, seo_yt.get("score", 0), chat_multiple)
    return int(round(p["priority"]))


# ---------------------------------------------------------------------------
# Estado local (eficiencia: enfriamiento por VOD)
# ---------------------------------------------------------------------------

def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict) and isinstance(st.get("vods"), dict):
            return st
    except (OSError, ValueError):
        pass
    return {"vods": {}}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def vod_on_cooldown(state, vod_id, now_s=None, cooldown_h=VOD_COOLDOWN_H):
    """¿Ese VOD se intentó hace menos de `cooldown_h` horas?"""
    now_s = now_s if now_s is not None else time.time()
    tried = (state.get("vods") or {}).get(str(vod_id)) or {}
    at = tried.get("attempted_at")
    if not at:
        return False
    try:
        return (now_s - float(at)) < cooldown_h * 3600
    except (TypeError, ValueError):
        return False


def mark_vod_attempted(state, vod_id, enqueued_starts=None):
    vods = state.setdefault("vods", {})
    entry = vods.setdefault(str(vod_id), {})
    entry["attempted_at"] = time.time()
    if enqueued_starts:
        seen = set(entry.get("enqueued_starts") or [])
        seen.update(int(s) for s in enqueued_starts)
        entry["enqueued_starts"] = sorted(seen)


# ---------------------------------------------------------------------------
# VODs y auto_clips (reutiliza el pipeline aprobado del worker)
# ---------------------------------------------------------------------------

def latest_vod_for(streamer_entry, youtube_enabled=False):
    """El VOD archivado más reciente de un streamer (Twitch/Kick/
    YouTube).

    Devuelve el dict del watcher (vod_id, url, title, category,
    live, ...) o None si no hay VODs. No baja nada. YouTube solo
    con ``youtube_enabled=True`` (flag ``--youtube``; apagado por
    defecto hasta que se active): sin él, un streamer de YouTube
    se salta como antes, con motivo visible.
    """
    import watcher as _watcher
    name = streamer_entry["name"]
    platform = streamer_entry.get("platform") or "twitch"
    if platform == "kick":
        _live, vods = _watcher.kick_check(name)
    elif platform == "twitch":
        import os as _os
        cfg = {"twitch_client_id": _os.environ.get("TWITCH_CLIENT_ID", ""),
               "twitch_client_secret":
                   _os.environ.get("TWITCH_CLIENT_SECRET", "")}
        twitch, _mode = _watcher.twitch_client(cfg)
        _live, vods = twitch.check(name)
    elif platform == "youtube":
        if not youtube_enabled:
            _log(f"{name}: YouTube desactivado por flag (usa "
                 "--youtube para activar); sin VODs en el fallback.")
            return None
        from .youtube import youtube_check
        channel_url = (streamer_entry.get("channel_url") or "").strip()
        if not channel_url:
            _log(f"{name}: sin channel_url de YouTube; sin VODs en "
                 "el fallback.")
            return None
        _live, vods = youtube_check(channel_url)
    else:
        _log(f"{name}: plataforma {platform!r} sin VODs en el fallback.")
        return None
    vods = [v for v in (vods or []) if not v.get("live")]
    if not vods:
        return None
    return vods[0]


_GTA6_TITLE_TOKENS = ("gtavi", "gta6", "grandtheftautovi")


def _title_mentions_gta6(title):
    """¿El título menciona GTA VI? Búsqueda por subcadena sobre
    el título compactado (minúsculas, sin espacios ni signos):
    "GTA 6", "GTA VI", "GTA6", "Grand Theft Auto VI". Un título
    como "GTA Vice City" también casa (fail open: se procesa)."""
    compact = "".join(ch for ch in (title or "").lower()
                      if ch.isalnum())
    return any(tok in compact for tok in _GTA6_TITLE_TOKENS)


def vod_clearly_not_gta6(vod):
    """¿El VOD claramente NO es GTA VI (pre-chequeo, 2026-11-19)?

    Devuelve el motivo o None. Se salta solo si el título NO
    menciona GTA VI / GTA 6 / Grand Theft Auto VI Y la categoría
    Helix es conocida y no es GTA VI (p. ej. GTA IV, Slots). Ante
    la duda (título con mención, sin categoría o categoría GTA
    VI) se procesa: la puerta precisa sigue siendo el juego del
    manifiesto en clip_passes_gates. Un título vacío no menciona
    nada: con categoría conocida no GTA VI, se salta igual.
    """
    vod = vod or {}
    if _title_mentions_gta6(vod.get("title") or ""):
        return None
    category = (vod.get("category") or "").strip()
    if not category:
        return None
    from .category import is_gta6
    if is_gta6(category):
        return None
    return (f"título sin mención de GTA VI y categoría Helix "
            f"{category!r} (no es GTA VI)")


def run_auto_clips(vod_url, out_dir, top_n=TOP_N_PER_VOD,
                   timeout_s=AUTO_CLIPS_TIMEOUT_S, lang="es"):
    """auto_clips.py sobre el VOD (mismo comando que el worker).

    Baja solo el segmento de cada pico y el chat para detectar picos
    (el pipeline aprobado); nunca el VOD entero. Devuelve el
    manifiesto (dict) o None si auto_clips falló / no dejó
    manifiesto. `lang` es el idioma del streamer ("es" o "en"):
    títulos, revisión y SEO van en ese idioma.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    auto_clips = os.path.join(here, "auto_clips.py")
    os.makedirs(out_dir, exist_ok=True)
    lang = lang if lang in ("es", "en") else "es"
    cmd = [sys.executable, "-u", auto_clips, vod_url,
           "--lang", lang, "--out", out_dir,
           "--top-n", str(top_n), "--llm-select"]
    _log(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=here, timeout=timeout_s,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        _log(f"auto_clips superó el timeout de {timeout_s}s; se salta "
             "este VOD.")
        return None
    tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-500:]
    if proc.returncode != 0:
        _log(f"auto_clips salió con código {proc.returncode}; se salta "
             f"este VOD. Cola:\n{tail}")
        return None
    manifest_path = os.path.join(out_dir, "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        _log(f"Sin manifiesto válido en {manifest_path} ({e}); se "
             "salta este VOD.")
        return None


# ---------------------------------------------------------------------------
# Quemado de subtítulos + encolado en el panel (SIN publicar)
# ---------------------------------------------------------------------------

def _parse_fmt_ts(text):
    """'07h12m34s' / '12m34s' / '45s' -> segundos, o None.

    Es el formato del nombre de los segmentos del VOD
    (segments/seg_{n:02d}_t*.mp4): el inicio absoluto del segmento
    en el VOD. Sirve para reconstruir dónde empieza el clip dentro
    de su segmento al re-hacerlo.
    """
    import re
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?",
                     str(text or "").strip())
    if not m or not any(m.groups()):
        return None
    h, mnt, s = (int(v) if v else 0 for v in m.groups())
    return h * 3600 + mnt * 60 + s


def _rerender_vod_clip(final_review, final_video, video, sub, out_dir,
                       clip, cfg, lang, title, plantilla=None):
    """UN re-render VOD tras un fallo de la puerta dura (2026-11-19).

    Re-corta el mismo pico desde su segmento fuente
    (segments/seg_{n:02d}_t*.mp4) con la cara forzada (facecam_box
    de la cadena face-crop -> bordes -> YuNet en el segmento) +
    re-quema subtítulos y título (reutiliza el SRT ya transcrito)
    + SFX por defecto, y vuelve a validar el vídeo re-hecho.
    Devuelve (final_review, final_video) del re-render si salió, o
    la revisión original (con needs_human_review=True) si no se
    pudo re-hacer o sigue fallando: el llamador NO encola y deja
    el motivo visible para revisión humana. Nunca levanta salvo
    ConfigError (falta la config del gate).
    """
    import glob
    from .final_review import (
        find_facecam_box_in_source, transcript_text_for_srt,
        validate_final_clip)
    from .validate import ConfigError
    from .workers import apply_sfx, subtitle_clip

    def _human_review(motivo_extra):
        review = dict(final_review)
        review["needs_human_review"] = True
        review["motivo"] = ((final_review.get("motivo") or "")
                            + " — " + motivo_extra
                            + ": revisión humana")
        return review, final_video

    hard = final_review.get("hard_failures") or []
    idx = clip.get("n")
    seg_glob = ""
    if idx is not None:
        try:
            seg_glob = os.path.join(
                out_dir, "segments", f"seg_{int(idx):02d}_t*.mp4")
        except (TypeError, ValueError):
            seg_glob = ""
    segs = sorted(glob.glob(seg_glob)) if seg_glob else []
    if not segs:
        return _human_review("sin segmento fuente para re-hacerlo")
    seg_path = segs[0]
    seg_start = _parse_fmt_ts(
        os.path.basename(seg_path).split("_t", 1)[-1]
        .rsplit(".", 1)[0])
    if seg_start is None:
        return _human_review(
            "inicio del segmento no reconstruible del nombre")
    cut_start = float(clip.get("start", 0) or 0) - seg_start
    cut_dur = float(clip.get("duration") or 0)
    if cut_start < 0 or cut_dur <= 0:
        return _human_review(
            "inicio/duración del pico no reconstruible")
    face_box = None
    if "cara" in hard:
        try:
            from .thumbnails import get_duration
            seg_dur = get_duration(seg_path) or 0
        except Exception:  # noqa: BLE001 - sin duración no hay caja
            seg_dur = 0
        found = find_facecam_box_in_source(seg_path, seg_dur, cfg)
        if not found:
            return _human_review("sin cara detectable en la fuente")
        face_box = found["box"]
    try:
        from .config import get_layout_config
        from .render import cut_clip_with_layout
        layout_cfg = get_layout_config(cfg)
        if face_box is not None:
            layout_cfg["facecam_box"] = face_box
        if clip.get("game_voice"):
            layout_cfg["game_voice"] = clip["game_voice"]
        out2 = os.path.splitext(video)[0] + "_r1.mp4"
        cut_clip_with_layout(
            seg_path, cut_start, cut_dur, out2,
            layout_mode="auto", cfg=layout_cfg)
    except Exception:  # noqa: BLE001 - sin re-render, no tumba
        return _human_review("el re-corte falló")
    burned2 = os.path.splitext(out2)[0] + "_subs.mp4"
    try:
        sub2 = subtitle_clip(
            out2, srt=sub.get("srt"), burn=burned2, cfg=cfg or {},
            lang=lang, title=title)
    except Exception:  # noqa: BLE001 - sin re-render, no tumba
        return _human_review("el re-quemado falló")
    final2 = burned2 if os.path.isfile(burned2) else out2
    try:
        final2, _sfx2 = apply_sfx(
            final2, transcript_path=sub2.get("transcript"),
            srt_path=sub2.get("srt"), payoff_s=20.0, cfg=cfg or {})
    except Exception:  # noqa: BLE001 - el SFX nunca tumba
        pass
    _log(f"  pico {clip.get('peak')}: re-hecho tras la puerta dura "
         f"({final_review.get('rerender_reason') or ''})")
    try:
        review2 = validate_final_clip(
            final2, title,
            transcript_text=transcript_text_for_srt(
                sub2.get("srt") or ""),
            srt_path=sub2.get("srt") or "",
            cfg=cfg or {}, plantilla=plantilla)
    except ConfigError:
        raise
    except Exception:  # noqa: BLE001 - sin veredicto, no tumba
        return _human_review("sin veredicto del re-render")
    review2 = dict(review2)
    review2["rerendered_from"] = final_video
    review2["rerender_facecam_box"] = face_box
    if review2.get("decision") == "rechazado":
        review2["needs_human_review"] = True
    return review2, final2


def burn_and_enqueue(bridge, streamer, vod_id, out_dir, clip, cfg=None,
                     lang="es"):
    """Quema subtítulos + título y encola el clip en el panel.

    Devuelve (panel_id o None, motivo). Sin voz verificable el
    clip ya NO queda fuera siempre (regla 2026-11-19 18:41): si
    pasa las puertas (viralidad >=45, SEO >=80) se encola
    PUBLICABLE sin subtítulos (subtitles_complete=0, honesto);
    sin voz + bajo las puertas sigue fuera. Nunca se inventa
    texto de subtítulos. `lang` ("es" o "en") es el idioma del
    streamer: subtítulos, título y `language` del payload van en
    ese idioma.
    """
    from .workers import subtitle_clip
    lang = lang if lang in ("es", "en") else "es"
    if not cfg:
        # 2026-11-19 (clip 23, TheGrefg): el fallback nunca cargaba
        # config.yaml y subtitle_clip caia en su default silencioso
        # "karaoke". La plantilla acordada es la de config.yaml
        # (subtitles.default_style; decisión 2026-11-19 20:48 TODOS
        # a rubius). Se carga la config real para que el quemado y
        # el validador final usen los mismos ajustes que el camino
        # en vivo. Sin config real no hay gate válido: falla en voz
        # alta (ConfigError) en vez de seguir con {} y defaults
        # inventados.
        try:
            from .config import load_config
            cfg = load_config()
        except Exception as exc:
            from .validate import ConfigError
            raise ConfigError(
                "vod_fallback: sin config.yaml válido para el "
                f"validador final: {exc}") from exc
    viral = clip.get("viral") or {}
    title_key = "title_en" if lang == "en" else "title_es"
    # El título es el del paquete del manifiesto (best_title,
    # Gemini cuando el VOD lo trae): vídeo, seo_title y panel
    # usan el MISMO. El título crudo de la ficha solo es el
    # fallback si el paquete no trae best_title.
    title = viral.get("best_title") or clip.get(title_key) or ""
    # Texto de miniatura del paquete del manifiesto (2026-11-19):
    # viaja al panel (columna thumb_text); sin él queda vacía.
    thumbnail_text = (viral.get("thumbnail_text") or "").strip()
    video = os.path.join(out_dir, clip.get("file", ""))
    if not os.path.isfile(video):
        return None, f"falta el MP4 {video}"
    burned = os.path.splitext(video)[0] + "_subs.mp4"
    try:
        sub = subtitle_clip(video, burn=burned, cfg=cfg or {},
                            lang=lang, title=title)
    except Exception as e:  # noqa: BLE001 - un clip no tumba la corrida
        return None, f"subtítulos fallaron ({e})"
    # Puerta sin voz (regla 2026-11-19 18:41): los scores del clip
    # ya se conocen (el manifiesto los trae) y la corrida ya pasó
    # clip_passes_gates antes de llegar aquí, así que un sin voz
    # que llega hasta aquí pasa las puertas y se encola publicable
    # sin subtítulos. La comprobación explícita deja la función
    # correcta también si se la llama directa: sin voz + bajo
    # puertas sigue fuera, como antes. No se confía en
    # sub["publish_blocked"]: subtitle_clip ya no lo marca.
    viral_score = clip.get("viral_score") or {}
    total = viral_score.get("total") or 0
    seo = clip.get("seo_scores") or {}
    from .policy import no_speech_blocked
    no_speech = bool(sub.get("no_speech"))
    if no_speech_blocked(no_speech, total, seo, cfg):
        return None, ("sin voz verificable y bajo las puertas "
                      "(viralidad/SEO): no se encola")
    final_video = burned if os.path.isfile(burned) else video
    # SFX por defecto (2026-11-19): sobre el MP4 final y antes de
    # la validación final (el validador ve el archivo que se
    # publica). Si la mezcla falla, el clip sigue sin SFX.
    try:
        from .workers import apply_sfx
        final_video, _sfx_info = apply_sfx(
            final_video, transcript_path=sub.get("transcript"),
            srt_path=sub.get("srt"), payoff_s=20.0, cfg=cfg or {})
    except Exception:  # noqa: BLE001 - el clip sigue sin SFX
        pass
    # Validación final DEFINITIVA (2026-11-19, final_review.py):
    # puerta dura de cara + plantilla sobre el MP4 final + juez
    # Gemini. La plantilla es la del clip (campo plantilla del
    # manifiesto) o la que produce el render (variante "rdjavi" ->
    # willyrex v2); sin ella, el validador usa el default de
    # config.yaml (rubius). Si la puerta dura falla, UN re-render
    # con la cara forzada (segmento fuente + facecam_box) y se
    # vuelve a validar; si sigue fallando, NO se encola y el motivo
    # queda visible para revisión humana (nunca bloqueado en
    # silencio). Pendiente (sin Gemini) o desactivada -> se encola
    # sin marcar: el freno sigue para revisión humana.
    from .config import template_for_variant
    from .render import resolve_split_variant
    plantilla = (clip.get("plantilla")
                 or template_for_variant(resolve_split_variant(
                     (cfg or {}).get("layout") or {})))
    try:
        from .final_review import (
            transcript_text_for_srt, validate_final_clip)
        from .validate import ConfigError
        final_review = validate_final_clip(
            final_video, title,
            transcript_text=transcript_text_for_srt(
                sub.get("srt") or ""),
            srt_path=sub.get("srt") or "",
            cfg=cfg or {}, plantilla=plantilla)
    except ConfigError:
        raise
    except Exception:  # noqa: BLE001 - sin veredicto no se aprueba
        final_review = {"decision": "pendiente_revision"}
    if final_review.get("needs_rerender"):
        final_review, final_video = _rerender_vod_clip(
            final_review, final_video, video, sub, out_dir, clip,
            cfg, lang, title, plantilla=plantilla)
    if final_review.get("decision") == "rechazado":
        human = bool(final_review.get("needs_human_review"))
        return None, (f"validación final: "
                      f"{final_review.get('motivo') or ''}: "
                      f"{'revisión humana' if human else 'no se encola'}")
    chat_multiple = (viral_score.get("chat_multiple")
                     or (viral or {}).get("viral_multiple") or 1.0)
    start = int(clip.get("start", 0))
    end = int(start + (clip.get("duration") or 0))
    seo_txt = ", ".join(
        f"{p} {(seo.get(p) or {}).get('score')}" for p in PLATFORMS)
    try:
        vod_id_int = int(vod_id)
    except (TypeError, ValueError):
        vod_id_int = 0
    payload = {
        "title": title,
        "start_sec": start,
        "end_sec": end,
        "score": panel_score(total, seo, chat_multiple),
        "platforms": list(PLATFORMS),
        "influencer_id": streamer,
        "source_id": vod_id_int,
        "seo_title": title,
        "seo_description": (viral.get("description")
                            or viral.get("description_first_2_lines")
                            or ""),
        "seo_tags": viral.get("tags") or [],
        # Honesto: 0 cuando no hay voz (el clip se publica igual
        # sin subtítulos por la regla 2026-11-19); 1 con voz.
        "subtitles_complete": 0 if no_speech else 1,
        "needs_human_transcription": 0,
        "origin": "vod",
        "game": "GTA VI",
        # Idioma del clip (columna clips.language del panel,
        # migración 0019): sin él el panel asume "es" y un clip EN
        # caería en las cuentas ES al publicar.
        "language": lang,
        "reason": (f"VOD GTA VI ({streamer}, VOD {vod_id}, {lang}) — "
                   f"viralidad {total} — SEO {seo_txt} — "
                   + ("sin subtítulos (sin voz: se publica igual, "
                      "pasa puertas)" if no_speech else
                      "subtítulos completos")
                   + (" — re-hecho tras puerta dura"
                      if final_review.get("rerendered_from") else "")),
    }
    # Solo si hay texto: el panel rechaza thumbnail_text null
    # con 400.
    if thumbnail_text:
        payload["thumbnail_text"] = thumbnail_text
    try:
        created = bridge.create_clip(payload)
        panel_id = created.get("id")
        if not panel_id:
            return None, f"POST /api/clips sin id: {str(created)[:200]}"
        bridge.upload_video(panel_id, final_video)
        if final_review.get("decision") == "aprobado":
            try:
                bridge.preview_seen(panel_id)
            except Exception:  # noqa: BLE001 - no tumba el encolado
                pass
    except Exception as e:  # noqa: BLE001 - el panel no tumba la corrida
        return None, f"panel: no se pudo encolar ({e})"
    # SEO + veredicto al panel por PATCH (2026-11-24, auto-seo): el
    # POST no lleva seo_scores (la puerta SEO del POST tiraría los
    # de máximo < 80); el PATCH no tiene puerta. Solo plataformas
    # con número medido: nunca 0 por una sin medir (0 es un bloque
    # medido, no "sin medir"). El veredicto se construye DESPUÉS
    # del posible re-render (final_review ya es el definitivo). Si
    # el PATCH falla, el clip ya está en la Cola y se sigue.
    try:
        from .quality_gate import build_quality_gate
        _scores = {}
        for _p in PLATFORMS:
            _entry = (seo or {}).get(_p)
            if isinstance(_entry, (int, float)):
                _value = _entry
            elif isinstance(_entry, dict):
                _value = _entry.get("score")
            else:
                _value = None
            if isinstance(_value, (int, float)):
                _scores[_p] = _value
        _fields = {"viral": {
            "quality_gate": build_quality_gate(
                final_review, thumbnail_text=thumbnail_text,
                no_speech=no_speech),
            "seo_source": "corte",
        }}
        if _scores:
            _fields["seo_scores"] = _scores
        bridge.update_clip(panel_id, _fields)
    except Exception:  # noqa: BLE001 - el clip ya está en la Cola
        pass
    return panel_id, ""


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def run(max_vods=MAX_VODS_PER_RUN, max_clips=MAX_CLIPS_PER_RUN,
        top_n=TOP_N_PER_VOD, only=None, state_path=DEFAULT_STATE_PATH,
        out_root=DEFAULT_OUT_DIR, dry_run=False, bridge=None,
        scan_fn=None, latest_vod_fn=None, auto_clips_fn=None,
        enqueue_fn=None, ignore_cooldown=False, youtube_enabled=False):
    """Una corrida del fallback. Devuelve (n_enqueued, resumen).

    Las funciones *_fn existen para los tests (sin red ni panel).
    ``youtube_enabled`` (CLI ``--youtube``) activa la fuente
    YouTube en ``latest_vod_for``; apagado por defecto.
    """
    from .live_scan import scan_live
    from .streamers import load_streamers
    # 1. ¿Hay alguien en directo en GTA VI (ES o EN)? Si sí, no hay
    # fallback: el cortador en vivo se encarga.
    try:
        scan = (scan_fn or scan_live)(only=only or None)
    except Exception as e:  # noqa: BLE001 - sin lectura fiable no se
        # entra en modo VOD a ciegas; el próximo ciclo reintenta.
        _log(f"Sin lectura fiable de directos ({e}): no se entra en "
             "modo VOD a ciegas.")
        return 0, "sin lectura de directos"
    live_any = live_gta6(scan.get("live") or [])
    if live_any:
        names = ", ".join(x["name"] for x in live_any)
        _log(f"Hay directos en GTA VI ({names}): sin fallback VOD.")
        return 0, f"directos en GTA VI: {names}"
    _log("Sin directos en GTA VI: modo VOD.")
    streamers = load_streamers()
    order = fallback_order(streamers, only=only)
    if not order:
        _log("Sin streamers para el fallback.")
        return 0, "sin streamers"
    state = load_state(state_path)
    if bridge is None and not dry_run:
        from .panel_bridge import PanelBridge
        bridge = PanelBridge.from_env()
        if bridge is None:
            _log("Sin PANEL_USERNAME/PANEL_PASSWORD: no se puede "
                 "encolar en el panel; sin fallback.")
            return 0, "sin credenciales del panel"
    panel_clips = []
    if bridge is not None:
        try:
            panel_clips = bridge.list_clips() or []
        except Exception as e:  # noqa: BLE001 - sin lista no se
            _log(f"No se pudo listar la Cola del panel ({e}); se "
                 "sigue sin dedup contra el panel.")
            panel_clips = []
    # Fase en vivo: entrando en modo VOD (ES primero, luego EN).
    _report_phase("modo_vod", ", ".join(e["name"] for e in order),
                  bridge=bridge, dry_run=dry_run)
    enqueued = 0
    vods_tried = 0
    for entry in order:
        if enqueued >= max_clips or vods_tried >= max_vods:
            break
        name = entry["name"]
        try:
            if latest_vod_fn is not None:
                vod = latest_vod_fn(entry)
            else:
                vod = latest_vod_for(entry,
                                     youtube_enabled=youtube_enabled)
        except Exception as e:  # noqa: BLE001 - un streamer no tumba
            _log(f"{name}: no se pudo listar sus VODs ({e}); se sigue.")
            continue
        if not vod:
            _log(f"{name}: sin VOD archivado reciente; se sigue.")
            continue
        vod_id = str(vod.get("vod_id") or "")
        if not vod_id:
            continue
        # La categoría Helix del VOD es la del ÚLTIMO tramo (Rubius
        # 2932415021 termina en Just Chatting tras horas de GTA VI),
        # así que NO veta el VOD: la puerta precisa es por pico
        # (capítulos del VOD en auto_clips) + el juego del manifiesto
        # en clip_passes_gates. Un VOD entero de Just Chatting no
        # deja ningún clip que pase esa puerta.
        if vod_on_cooldown(state, vod_id) and not ignore_cooldown:
            _log(f"{name}: VOD {vod_id} en enfriamiento (intentado "
                 "hace <6 h); se sigue.")
            continue
        # Pre-chequeo de VOD (2026-11-19): si el VOD claramente NO
        # es GTA VI, se salta SIN gastar el cupo de 2 VODs ni
        # refrescar el enfriamiento (auto_clips quemaría ~20 min en
        # un VOD de GTA IV o Slots que no deja ningún clip que pase
        # la puerta de juego por pico). Ante la duda se procesa
        # (fail open: la puerta precisa sigue siendo el juego del
        # manifiesto en clip_passes_gates). La categoría Helix es
        # la del ÚLTIMO tramo, así que por sí sola no veta: hace
        # falta que el título tampoco mencione GTA VI.
        skip_reason = vod_clearly_not_gta6(vod)
        if skip_reason:
            _log(f"{name}: VOD {vod_id} se salta ({skip_reason}); "
                 "sin gastar cupo ni enfriamiento.")
            continue
        vods_tried += 1
        # Fase en vivo: se refresca al empezar cada VOD (un VOD
        # tarda ~8 min; sin esto la fase envejece y el panel la
        # marca "sin señal reciente" a mitad de corrida).
        _report_phase("modo_vod", name, bridge=bridge, dry_run=dry_run)
        lang = (entry.get("language") or "es").lower()
        lang = lang if lang in ("es", "en") else "es"
        _log(f"{name}: VOD {vod_id} ({vod.get('title', '')[:60]!r}, "
             f"categoría Helix {vod.get('category')!r}, idioma {lang})")
        if dry_run:
            _log(f"{name}: dry-run, no se corta ni se encola.")
            mark_vod_attempted(state, vod_id)
            continue
        out_dir = os.path.join(out_root, str(vod_id))
        manifest = (auto_clips_fn or run_auto_clips)(
            vod.get("url") or "", out_dir, top_n=top_n, lang=lang)
        if not manifest:
            mark_vod_attempted(state, vod_id)
            continue
        _vl = manifest.get("vod_layout") or {}
        if _vl.get("facecam_box"):
            _log(f"  layout VOD: facecam {_vl.get('facecam_box')} "
                 f"(method={_vl.get('method')}, {_vl.get('hits')}/"
                 f"{_vl.get('samples')} muestras).")
        else:
            _log("  layout VOD: sin facecam estable "
                 f"(mejor grupo {_vl.get('hits', 0)}/"
                 f"{_vl.get('samples', 0)} muestras); cada clip decide "
                 "su layout solo.")
        enqueued_here = []
        for clip in (manifest.get("clips") or [])[:top_n]:
            if enqueued >= max_clips:
                break
            start = clip.get("start", 0)
            ok, reasons = clip_passes_gates(clip, vod_id)
            if not ok:
                _log(f"  pico {clip.get('peak')}: descartado "
                     f"({'; '.join(reasons)})")
                _report_event(
                    "clip_descartado",
                    f"{name}: «{_clip_title(clip, lang)}» "
                    f"descartado — {'; '.join(reasons)} "
                    f"(VOD {vod_id})",
                    bridge=bridge, dry_run=dry_run)
                continue
            if already_enqueued(panel_clips, vod_id, start):
                _log(f"  pico {clip.get('peak')}: ya en la Cola del "
                     "panel; se salta.")
                continue
            panel_id, why = (enqueue_fn or burn_and_enqueue)(
                bridge, name, vod_id, out_dir, clip, lang=lang)
            if panel_id:
                _log(f"  pico {clip.get('peak')}: en Cola del panel "
                     f"como clip #{panel_id} (VOD, sin publicar).")
                _report_event(
                    "clip_encolado",
                    f"{name}: «{_clip_title(clip, lang)}» encolado "
                    f"(VOD {vod_id}; viralidad "
                    f"{(clip.get('viral_score') or {}).get('total')}; "
                    f"SEO {_seo_summary(clip.get('seo_scores'))})",
                    bridge=bridge, dry_run=dry_run)
                enqueued += 1
                enqueued_here.append(int(start))
                panel_clips.append({"source_id": vod_id,
                                    "start_sec": int(start)})
            else:
                _log(f"  pico {clip.get('peak')}: no encolado ({why})")
                _report_event(
                    "clip_descartado",
                    f"{name}: «{_clip_title(clip, lang)}» descartado — "
                    f"{why} (VOD {vod_id})",
                    bridge=bridge, dry_run=dry_run)
        mark_vod_attempted(state, vod_id, enqueued_here)
    save_state(state_path, state)
    summary = (f"modo VOD: {vods_tried} VOD(s) procesado(s), "
               f"{enqueued} clip(s) en la Cola del panel (sin publicar)")
    _log(summary)
    # Fase en vivo: corrida VOD terminada, de vuelta a espera.
    _report_phase("sin_directos", summary, bridge=bridge,
                  dry_run=dry_run)
    return enqueued, summary


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Fallback VOD: sin directos en GTA VI (ni ES ni "
                    "EN), corta de los VOD más recientes (ES primero: "
                    "Rubius, IlloJuan; después EN) y encola en el "
                    "panel sin publicar.")
    p.add_argument("--max-vods", type=int, default=MAX_VODS_PER_RUN)
    p.add_argument("--max-clips", type=int, default=MAX_CLIPS_PER_RUN)
    p.add_argument("--top-n", type=int, default=TOP_N_PER_VOD)
    p.add_argument("--streamers", default="",
                   help="Canales a considerar, separados por comas "
                        "(vacío = todos: ES primero, luego EN)")
    p.add_argument("--state", default=DEFAULT_STATE_PATH)
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    p.add_argument("--dry-run", action="store_true",
                   help="Solo decide (barrido + VODs + filtros), sin "
                        "cortar ni encolar")
    p.add_argument("--ignore-cooldown", action="store_true",
                   help="Reprocesa VODs intentados hace <6 h (para "
                        "reintentos tras un arreglo; el cron normal "
                        "nunca lo usa)")
    p.add_argument("--youtube", action="store_true",
                   help="Activa la fuente YouTube (yt-dlp "
                        "--flat-playlist, sin API key, $0): sin este "
                        "flag los streamers de YouTube se saltan "
                        "como antes")
    args = p.parse_args(argv)
    only = [s for s in args.streamers.split(",") if s.strip()] \
        or None
    _n, _summary = run(
        max_vods=args.max_vods, max_clips=args.max_clips,
        top_n=args.top_n, only=only, state_path=args.state,
        out_root=args.out_dir, dry_run=args.dry_run,
        ignore_cooldown=args.ignore_cooldown,
        youtube_enabled=args.youtube)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
