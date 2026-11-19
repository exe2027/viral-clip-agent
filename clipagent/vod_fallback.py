"""Fallback VOD del cerebro (2026-11-19, corrección del usuario).

Cuando NO hay ningún streamer en español en directo en GTA VI, el
cerebro cambia solo a modo VOD y corta de los VOD más recientes
(Rubius primero, después IlloJuan), con los mismos filtros de
siempre. Lo hace el workflow Cerebro — Live Cutter (GitHub Actions,
100% automático): nada manual, nada local, y sigue trabajando aunque
el usuario se desloguee (el cron */15 y el dispatch del panel lo
disparan sin sesión del usuario).

Reglas duras (las del usuario, no se tocan):
  * El corte de VOD es trabajo del CEREBRO, nunca de un agente
    manual (2026-11-19 02:18). Este módulo es el cerebro.
  * Solo se baja el SEGMENTO del clip, nunca el VOD entero, y del
    chat solo la ventana alrededor del momento (reutiliza
    auto_clips.py, que ya lo hace así con TwitchDownloaderCLI).
  * Filtros: viralidad >= 50, SEO >= 90 en las 4 plataformas, clip
    de 40 a 60 s, juego GTA VI, subtítulos completos verificados
    (nada inventado, nada sin voz). Sin rescate por gritos: la
    puerta es 50/90 tal cual.
  * Los clips van a la Cola del panel con el MP4 (origin='vod'),
    SIN publicar: este módulo no tiene ninguna llamada a publicar
    (ni publish_clip del puente ni publish loop). La publicación la
    decide el panel después, con sus topes.
  * Dedup contra el panel: un (VOD, inicio) ya encolado no se
    vuelve a encolar, aunque el estado local se pierda.
  * Coste acotado: máx. 2 VODs y 2 clips por corrida, y cada VOD
    tiene 6 h de enfriamiento tras intentarlo. En estado estable
    (VODs ya intentados) la corrida es solo el barrido + la lectura
    de estado, ~1-2 min.

El estado vive en runs/vod-fallback/state.json y se restaura/guarda
con el artefacto `cerebro-live-vod-state` del workflow (como el
estado diario de cerebro-sebastian, pero separado para no pisarlo).
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
MIN_VIRALITY = 50
MIN_SEO = 90
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


def fallback_order(streamers, only=None):
    """Orden de streamers para el fallback VOD.

    Rubius primero, IlloJuan después, luego el resto de ES enabled
    en el orden de streamers.yaml. `only` (nombres, minúsculas)
    restringe la lista (dispatch manual del workflow).
    """
    only_set = {s.strip().lower() for s in (only or []) if s.strip()}
    es = [e for e in streamers
          if (e.get("language") or "") == "es"
          and e.get("enabled", True)
          and (e.get("estado") or "") == "concedido"
          and (not only_set or e["name"] in only_set)]
    head = [e for n in FALLBACK_PRIORITY for e in es if e["name"] == n]
    tail = [e for e in es if e["name"] not in FALLBACK_PRIORITY]
    return head + tail


def clip_passes_gates(clip, vod_id):
    """¿El clip del manifiesto pasa los filtros duros del fallback?

    Devuelve (ok, motivos). Falla cerrado: sin viral_score, sin SEO
    en alguna plataforma o sin juego verificado, no pasa.
    """
    reasons = []
    dur = clip.get("duration")
    if not isinstance(dur, (int, float)) or not (MIN_DUR_S <= dur <= MAX_DUR_S):
        reasons.append(f"duración {dur}s fuera de {MIN_DUR_S}-{MAX_DUR_S}s")
    viral = clip.get("viral_score") or {}
    total = viral.get("total")
    if not isinstance(total, (int, float)) or total < MIN_VIRALITY:
        reasons.append(f"viralidad {total} < {MIN_VIRALITY}")
    seo = clip.get("seo_scores") or {}
    missing = [p for p in PLATFORMS if p not in seo]
    if missing:
        reasons.append(f"sin SEO en: {', '.join(missing)}")
    else:
        worst = min((seo[p] or {}).get("score", 0) for p in PLATFORMS)
        if worst < MIN_SEO:
            detail = ", ".join(
                f"{p} {(seo[p] or {}).get('score')}" for p in PLATFORMS)
            reasons.append(f"SEO mínimo {worst} < {MIN_SEO} ({detail})")
    game = ((clip.get("moment_card") or {}).get("game")
            or (clip.get("viral") or {}).get("game"))
    from .category import is_gta6
    if not game or not is_gta6(game):
        reasons.append(f"juego no verificado como GTA VI: {game!r}")
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

def latest_vod_for(streamer_entry):
    """El VOD archivado más reciente de un streamer (Twitch/Kick).

    Devuelve el dict del watcher (vod_id, url, title, category,
    live, ...) o None si no hay VODs. No baja nada.
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
    else:
        _log(f"{name}: plataforma {platform!r} sin VODs en el fallback.")
        return None
    vods = [v for v in (vods or []) if not v.get("live")]
    if not vods:
        return None
    return vods[0]


def run_auto_clips(vod_url, out_dir, top_n=TOP_N_PER_VOD,
                   timeout_s=AUTO_CLIPS_TIMEOUT_S):
    """auto_clips.py sobre el VOD (mismo comando que el worker).

    Baja solo el segmento de cada pico y el chat para detectar picos
    (el pipeline aprobado); nunca el VOD entero. Devuelve el
    manifiesto (dict) o None si auto_clips falló / no dejó
    manifiesto.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    auto_clips = os.path.join(here, "auto_clips.py")
    os.makedirs(out_dir, exist_ok=True)
    cmd = [sys.executable, "-u", auto_clips, vod_url,
           "--lang", "es", "--out", out_dir,
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

def burn_and_enqueue(bridge, streamer, vod_id, out_dir, clip, cfg=None):
    """Quema subtítulos + título y encola el clip en el panel.

    Devuelve (panel_id o None, motivo). Si no hay voz verificable
    el clip queda fuera (publish_blocked): nunca se encola sin
    subtítulos completos y nunca se inventa texto.
    """
    from .workers import subtitle_clip
    viral = clip.get("viral") or {}
    title = viral.get("best_title") or clip.get("title_es") or ""
    video = os.path.join(out_dir, clip.get("file", ""))
    if not os.path.isfile(video):
        return None, f"falta el MP4 {video}"
    burned = os.path.splitext(video)[0] + "_subs.mp4"
    try:
        sub = subtitle_clip(video, burn=burned, cfg=cfg or {},
                            lang="es", title=title)
    except Exception as e:  # noqa: BLE001 - un clip no tumba la corrida
        return None, f"subtítulos fallaron ({e})"
    if sub.get("publish_blocked") or sub.get("no_speech"):
        return None, ("sin voz verificable: subtítulos incompletos, "
                      "no se encola (publish_blocked)")
    final_video = burned if os.path.isfile(burned) else video
    viral_score = clip.get("viral_score") or {}
    total = viral_score.get("total") or 0
    seo = clip.get("seo_scores") or {}
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
        "subtitles_complete": 1,
        "needs_human_transcription": 0,
        "origin": "vod",
        "game": "GTA VI",
        "reason": (f"VOD GTA VI ({streamer}, VOD {vod_id}) — viralidad "
                   f"{total} — SEO {seo_txt} — subtítulos completos"),
    }
    try:
        created = bridge.create_clip(payload)
        panel_id = created.get("id")
        if not panel_id:
            return None, f"POST /api/clips sin id: {str(created)[:200]}"
        bridge.upload_video(panel_id, final_video)
    except Exception as e:  # noqa: BLE001 - el panel no tumba la corrida
        return None, f"panel: no se pudo encolar ({e})"
    return panel_id, ""


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def run(max_vods=MAX_VODS_PER_RUN, max_clips=MAX_CLIPS_PER_RUN,
        top_n=TOP_N_PER_VOD, only=None, state_path=DEFAULT_STATE_PATH,
        out_root=DEFAULT_OUT_DIR, dry_run=False, bridge=None,
        scan_fn=None, latest_vod_fn=None, auto_clips_fn=None,
        enqueue_fn=None):
    """Una corrida del fallback. Devuelve (n_enqueued, resumen).

    Las funciones *_fn existen para los tests (sin red ni panel).
    """
    from .live_scan import scan_live
    from .streamers import load_streamers
    # 1. ¿Hay ES en directo en GTA VI? Si sí, no hay fallback.
    try:
        scan = (scan_fn or scan_live)(only=only or None)
    except Exception as e:  # noqa: BLE001 - sin lectura fiable no se
        # entra en modo VOD a ciegas; el próximo ciclo reintenta.
        _log(f"Sin lectura fiable de directos ({e}): no se entra en "
             "modo VOD a ciegas.")
        return 0, "sin lectura de directos"
    live_es = es_live_gta6(scan.get("live") or [])
    if live_es:
        names = ", ".join(x["name"] for x in live_es)
        _log(f"Hay ES en directo en GTA VI ({names}): sin fallback VOD.")
        return 0, f"directos ES en GTA VI: {names}"
    _log("Sin directos ES en GTA VI: modo VOD.")
    streamers = load_streamers()
    order = fallback_order(streamers, only=only)
    if not order:
        _log("Sin streamers ES para el fallback.")
        return 0, "sin streamers ES"
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
    enqueued = 0
    vods_tried = 0
    for entry in order:
        if enqueued >= max_clips or vods_tried >= max_vods:
            break
        name = entry["name"]
        try:
            vod = (latest_vod_fn or latest_vod_for)(entry)
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
        if vod_on_cooldown(state, vod_id):
            _log(f"{name}: VOD {vod_id} en enfriamiento (intentado "
                 "hace <6 h); se sigue.")
            continue
        vods_tried += 1
        _log(f"{name}: VOD {vod_id} ({vod.get('title', '')[:60]!r}, "
             f"categoría Helix {vod.get('category')!r})")
        if dry_run:
            _log(f"{name}: dry-run, no se corta ni se encola.")
            mark_vod_attempted(state, vod_id)
            continue
        out_dir = os.path.join(out_root, str(vod_id))
        manifest = (auto_clips_fn or run_auto_clips)(
            vod.get("url") or "", out_dir, top_n=top_n)
        if not manifest:
            mark_vod_attempted(state, vod_id)
            continue
        enqueued_here = []
        for clip in (manifest.get("clips") or [])[:top_n]:
            if enqueued >= max_clips:
                break
            start = clip.get("start", 0)
            ok, reasons = clip_passes_gates(clip, vod_id)
            if not ok:
                _log(f"  pico {clip.get('peak')}: descartado "
                     f"({'; '.join(reasons)})")
                continue
            if already_enqueued(panel_clips, vod_id, start):
                _log(f"  pico {clip.get('peak')}: ya en la Cola del "
                     "panel; se salta.")
                continue
            panel_id, why = (enqueue_fn or burn_and_enqueue)(
                bridge, name, vod_id, out_dir, clip)
            if panel_id:
                _log(f"  pico {clip.get('peak')}: en Cola del panel "
                     f"como clip #{panel_id} (VOD, sin publicar).")
                enqueued += 1
                enqueued_here.append(int(start))
                panel_clips.append({"source_id": vod_id,
                                    "start_sec": int(start)})
            else:
                _log(f"  pico {clip.get('peak')}: no encolado ({why})")
        mark_vod_attempted(state, vod_id, enqueued_here)
    save_state(state_path, state)
    summary = (f"modo VOD: {vods_tried} VOD(s) procesado(s), "
               f"{enqueued} clip(s) en la Cola del panel (sin publicar)")
    _log(summary)
    return enqueued, summary


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Fallback VOD: sin ES en vivo en GTA VI, corta de "
                    "los VOD más recientes (Rubius, IlloJuan) y encola "
                    "en el panel sin publicar.")
    p.add_argument("--max-vods", type=int, default=MAX_VODS_PER_RUN)
    p.add_argument("--max-clips", type=int, default=MAX_CLIPS_PER_RUN)
    p.add_argument("--top-n", type=int, default=TOP_N_PER_VOD)
    p.add_argument("--streamers", default="",
                   help="Canales a considerar, separados por comas "
                        "(vacío = todos los ES)")
    p.add_argument("--state", default=DEFAULT_STATE_PATH)
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    p.add_argument("--dry-run", action="store_true",
                   help="Solo decide (barrido + VODs + filtros), sin "
                        "cortar ni encolar")
    args = p.parse_args(argv)
    only = [s for s in args.streamers.split(",") if s.strip()] \
        or None
    _n, _summary = run(
        max_vods=args.max_vods, max_clips=args.max_clips,
        top_n=args.top_n, only=only, state_path=args.state,
        out_root=args.out_dir, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
