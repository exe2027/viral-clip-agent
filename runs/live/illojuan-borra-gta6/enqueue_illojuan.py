#!/usr/bin/env python3
"""Encola el clip IlloJuan borra GTA 6 en la Cola del panel web AVC.

Variante del enqueue_clip.py de Rubius para este clip concreto:
- duracion 19 s (el script de Rubius aborta fuera de 40-60 s; el panel
  hoy tiene min_duration_s temporal en 15 s por decision del usuario
  de forzar la publicacion hoy rompiendo el tope de 1/dia).
- influencer IlloJuan, no Rubius.
- SIN score de viralidad: no hay medicion de chat para este clip
  (repost de Instagram, no VOD de Twitch), asi que no se inventa
  ningun numero; el panel no exige score si no se pasa.
- SEO medido con clipagent/seo_score.py: YT 97, TT 94, IG 95, FB 95.

Lee manifest.json de este run, crea el clip con POST /api/clips y
sube el MP4 con subtitulos quemados con PUT /api/clips/:id/video.
NO publica.

Auth: PANEL_USERNAME + PANEL_PASSWORD en el entorno (secretos de
GitHub Actions, nunca en el repo).
"""
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)
from clipagent.panel_bridge import PanelBridge, PanelError

PLATFORMS = ["youtube_shorts", "tiktok", "instagram_reels",
             "facebook_reels"]
MARKER = "illojuan-borra-gta6"


def main():
    run_dir = os.path.dirname(os.path.abspath(__file__))
    manifest = json.load(open(os.path.join(run_dir, "manifest.json"),
                              encoding="utf-8"))
    c = manifest["clips"][0]
    seo = c.get("seo_scores", {})
    for p in PLATFORMS:
        if seo.get(p, 0) < 90:
            sys.exit(f"ABORTO: SEO {p} {seo.get(p)} < 90 (no pasa la puerta).")
    video_path = os.path.join(run_dir, c["file"])
    if not os.path.exists(video_path):
        sys.exit(f"ABORTO: no existe el MP4: {video_path}")
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if size_mb > 128:
        sys.exit(f"ABORTO: MP4 {size_mb:.1f} MB > 128 MB (limite Worker).")
    full_desc = c["desc_es"]
    tags = c.get("hashtags_es", "")
    if tags and tags not in full_desc:
        full_desc = full_desc.rstrip() + "\n\n" + tags
    payload = {
        "title": c["title_es"],
        "start_sec": int(c["start"]),
        "end_sec": int(c["start"]) + int(c["duration"]),
        "game": "GTA VI",
        "language": "es",
        "origin": "live",
        "platforms": PLATFORMS,
        "influencer_id": manifest.get("streamer", "illojuan"),
        "subtitles_complete": 1,
        "subtitle_style": "simple",
        "seo_title": c["title_es"],
        "seo_description": full_desc,
        "seo_tags": tags,
        "seo_scores": {p: int(seo[p]) for p in PLATFORMS},
        "thumbnail_text": c.get("thumbnail_text", ""),
        "reason": (
            f"IlloJuan borra GTA 6 sin querer en directo. Fuente: "
            f"{manifest.get('source', '')}. SEO YT {seo['youtube_shorts']} / "
            f"TT {seo['tiktok']} / IG {seo['instagram_reels']} / "
            f"FB {seo['facebook_reels']}, {c['duration']} s, subtitulos "
            f"verificados. MP4 {os.path.basename(video_path)}. "
            f"Marker: {MARKER}."
        ),
    }
    bridge = PanelBridge.from_env()
    if bridge is None:
        sys.exit("ABORTO: sin credenciales del panel "
                 "(PANEL_USERNAME/PANEL_PASSWORD).")
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
