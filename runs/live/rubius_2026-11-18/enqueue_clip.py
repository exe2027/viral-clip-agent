#!/usr/bin/env python3
"""Encola un clip ya producido en la Cola del panel web AVC.

Lee el manifest del run (titulo, SEO, duracion, viralidad, subtitulos)
y crea el clip con POST /api/clips + sube el MP4 con subtitulos
quemados con PUT /api/clips/:id/video. NO publica (no llama a
POST /api/clips/:id/publish).

Auth: PANEL_SESSION_TOKEN (token de sesion del panel, 30 dias) o
PANEL_USERNAME + PANEL_PASSWORD en el entorno. Sin credenciales,
sale con error claro y no toca nada.

Uso:
  PANEL_SESSION_TOKEN=... python3 enqueue_clip.py <dir_run>
"""
import json
import os
import sys

# Raiz del repo = 4 niveles sobre este script (runs/live/<sesion>/).
# Antes era una ruta fija de la VM local (/home/hatch/...), que no
# existe en el runner de GitHub Actions (REAL TEST, intento 1).
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)
from clipagent.panel_bridge import PanelBridge, PanelError

PLATFORMS = ["youtube_shorts", "tiktok", "instagram_reels",
             "facebook_reels"]


def main():
    run_dir = sys.argv[1]
    manifest = json.load(open(os.path.join(run_dir, "manifest.json"),
                              encoding="utf-8"))
    c = manifest["clips"][0]
    viral = c.get("viral_score", {})
    seo = c.get("seo_scores", {})
    subs = (c.get("subtitles") or {})
    total = viral.get("total")
    if total is None or total < 50:
        sys.exit(f"ABORTO: viralidad {total} < 50 (no pasa la puerta).")
    for p in PLATFORMS:
        s = (seo.get(p) or {}).get("score", 0)
        if s < 90:
            sys.exit(f"ABORTO: SEO {p} {s} < 90 (no pasa la puerta).")
    dur = c["duration"]
    if not (40 <= dur <= 60):
        sys.exit(f"ABORTO: duracion {dur} s fuera de 40-60.")
    # MP4 con subtitulos quemados (si existe), si no el original.
    # argv[2] opcional: ruta explicita del MP4 final (p. ej. el
    # re-render RDJavi en rdjavi/).
    clips_dir = os.path.join(run_dir, "clips")
    if len(sys.argv) > 2:
        video_path = sys.argv[2]
        if not os.path.exists(video_path):
            sys.exit(f"ABORTO: no existe el MP4 indicado: {video_path}")
    else:
        base = os.path.basename(c["file"])
        stem = base[:-4] if base.endswith(".mp4") else base
        subs_path = os.path.join(clips_dir, stem + "_subs.mp4")
        video_path = subs_path if os.path.exists(subs_path) else os.path.join(
            clips_dir, base)
        if "_subs.mp4" not in video_path:
            sys.exit("ABORTO: no hay MP4 con subtitulos quemados (_subs.mp4).")
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if size_mb > 128:
        sys.exit(f"ABORTO: MP4 {size_mb:.1f} MB > 128 MB (limite Worker).")
    # Estilo de subtitulo: por defecto "tiktok" (simple RDJavi). La v3
    # karaoke se encola con SUBTITLE_STYLE=karaoke (el panel solo
    # acepta tiktok/highlight/karaoke; "ref" no es valido).
    subtitle_style = os.environ.get("SUBTITLE_STYLE", "tiktok")
    if subtitle_style not in ("tiktok", "highlight", "karaoke"):
        sys.exit(f"ABORTO: SUBTITLE_STYLE invalido: {subtitle_style}")
    # Descripcion completa generada con IA (nada vacio): la
    # description_first_2_lines del manifiesto (169 car., hook + CTA)
    # + los hashtags. El desc_es corto ("El chat se murió...") es
    # solo el titular del momento, no la descripcion publicable.
    # Por plataforma las captions ya estan en
    # viral.platforms[<p>].caption; el panel publica un solo
    # titulo+caption en las 4 (ver POST /publish), asi que se usa
    # la descripcion completa comun con hashtags.
    viral_meta = c.get("viral") or {}
    full_desc = (viral_meta.get("description_first_2_lines")
                 or c.get("desc_es", ""))
    tags = c.get("hashtags_es", "")
    if tags and tags not in full_desc:
        full_desc = full_desc.rstrip() + "\n\n" + tags
    payload = {
        "title": c.get("title_es") or viral_meta.get("best_title"),
        "start_sec": int(c["start"]),
        "end_sec": int(c["start"]) + int(c["duration"]),
        "game": "GTA VI",
        "platforms": PLATFORMS,
        "score": int(round(total)),
        "source_id": int(manifest["vod_id"]),
        "influencer_id": "rubius",
        "subtitles_complete": 1,
        "subtitle_style": subtitle_style,
        "seo_title": viral_meta.get("best_title") or c.get("title_es"),
        "seo_description": full_desc,
        "seo_tags": tags,
        "thumbnail_text": viral_meta.get("thumbnail_text", ""),
        "reason": (f"Viralidad {total} (real). Pico {c['peak']} s, "
                   f"{c['risas']} risas + {c['hype']} hype, "
                   f"SEO YT {seo['youtube_shorts']['score']} / "
                   f"TT {seo['tiktok']['score']} / "
                   f"IG {seo['instagram_reels']['score']} / "
                   f"FB {seo['facebook_reels']['score']}, "
                   f"{dur} s, subtitulos verificados. "
                   f"MP4 {os.path.basename(video_path)}."),
    }
    bridge = PanelBridge.from_env()
    if bridge is None:
        sys.exit("ABORTO: sin credenciales del panel "
                 "(PANEL_SESSION_TOKEN o PANEL_USERNAME/PANEL_PASSWORD).")
    print("health:", bridge.health())
    clip = bridge.create_clip(payload)
    print("CLIP CREADO EN LA COLA: id =", clip.get("id"),
          "| titulo =", clip.get("title"))
    up = bridge.upload_video(clip["id"], video_path)
    print("MP4 SUBIDO:", video_path, f"({size_mb:.1f} MB) ->", up)
    bridge.tracking_step(clip["id"], "paquete",
                         f"MP4 con subtitulos subido ({size_mb:.1f} MB).")
    got = bridge.list_clips()
    mine = [x for x in got if x.get("id") == clip["id"]]
    print("VERIFICADO EN LA COLA:", json.dumps(mine, ensure_ascii=False)[:400])
    print("OK: encolado, sin publicar.")


if __name__ == "__main__":
    try:
        main()
    except PanelError as e:
        sys.exit(f"ERROR PANEL: {e}")
