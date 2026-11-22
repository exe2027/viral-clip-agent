#!/usr/bin/env python3
"""Puente publisher-bridge — mecanismo ALTERNATIVO dirigido por el
Worker, no la vía principal de cómputo.

Decisión 17 nov 2026 ("Todo automático"): la vía principal es
Cloudflare + GitHub Actions con la cadena del cerebro
(.github/workflows/cerebro-*.yml, cron 09:00; ver ../COMPUTE.md).
Este puente es alternativo: el Worker pide jobs y el puente ejecuta
la subida, por si hace falta disparar subidas fuera del cron del
cerebro.

Si se activa, corre en GitHub Actions (runner efímero, nada prendido)
o en cualquier máquina con Python: toma jobs del Worker
(GET /api/jobs/next, header X-Bridge-Token), ejecuta la subida con
el código ya probado de viral-clip-agent (clipagent/platforms.py,
DirectPublisher, YouTubePublisher) y devuelve el resultado
(POST /api/jobs/:id/complete). `posted` solo con evidence_url.

Uso (dentro del checkout de exe2027/viral-clip-agent, con bridge.py
en la raíz):
    WORKER_URL=https://clips-worker-sebastian.<subdominio>.workers.dev \\
    BRIDGE_TOKEN=<secreto> python3 bridge.py --once
    WORKER_URL=... BRIDGE_TOKEN=... python3 bridge.py --loop --interval 300

Variables: WORKER_URL, BRIDGE_TOKEN, BRIDGE_TENANT (sebastian|kaez).
Sin credenciales en el código:
los tokens de plataforma los resuelve el Worker; el puente solo
descarga el MP4 (GET /api/jobs/:id/video) y usa los adaptadores de
clipagent con los tokens que el runner tenga en entorno
(YOUTUBE_REFRESH_TOKEN, TIKTOK_ACCESS_TOKEN, META_PAGE_TOKEN,
IG_USER_ID, FACEBOOK_PAGE_ID) — ver clipagent/credentials.py.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

WORKER_URL = os.environ.get("WORKER_URL", "").rstrip("/")
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")
# Tenant del puente (sebastian | kaez): el Worker solo entrega jobs
# de ese tenant (instalación única avc, header X-Bridge-Tenant).
BRIDGE_TENANT = os.environ.get("BRIDGE_TENANT", "")


def api(method, path, body=None):
    req = urllib.request.Request(
        WORKER_URL + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Bridge-Token": BRIDGE_TOKEN,
                 "X-Bridge-Tenant": BRIDGE_TENANT,
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode() or "{}")


def download_video(job_id, dest):
    req = urllib.request.Request(
        f"{WORKER_URL}/api/jobs/{job_id}/video",
        headers={"X-Bridge-Token": BRIDGE_TOKEN,
                 "X-Bridge-Tenant": BRIDGE_TENANT},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    return dest


def publish_with_clipagent(job, clip_path):
    """Sube con clipagent. Devuelve (state, fields).
    state: posted | failed | draft | processing."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from clipagent.publish import DirectPublisher, YouTubePublisher  # noqa: E402
    platform = job["platform"]
    if platform == "youtube_shorts":
        res = YouTubePublisher().publish(clip_path, privacy="public", live=True)
        if res.get("ok"):
            return "posted", {"platform_id": res["video_id"],
                              "evidence_url": res["url"]}
        return "failed", {"error": res.get("reason", "sin motivo"),
                          "retryable": False}
    pub = DirectPublisher(platform)
    res = pub.publish(clip_path, live=True)
    if res.get("ok"):
        pid = res.get("post_id") or res.get("media_id") or res.get("publish_id")
        url = {"tiktok": f"https://www.tiktok.com/@elclipdegta6/video/{pid}",
               "instagram_reels": f"https://www.instagram.com/reel/{pid}/",
               "facebook_reels": f"https://www.facebook.com/reel/{pid}/"}.get(platform, "")
        return "posted", {"platform_id": pid, "evidence_url": url}
    if res.get("mode") == "draft":
        return "draft", {"error": res.get("reason", "draft")}
    if res.get("mode") == "processing":
        return "processing", {"resume_data": json.dumps(res)}
    return "failed", {"error": res.get("reason", "sin motivo"),
                      "retryable": bool(res.get("retryable"))}


def run_once():
    nxt = api("GET", "/api/jobs/next?kind=publish")
    job = (nxt or {}).get("job")
    if not job:
        print("sin jobs pendientes")
        return False
    print(f"job #{job['id']} clip={job['clip_id']} plataforma={job['platform']}")
    clip_path = f"/tmp/bridge-clip-{job['id']}.mp4"
    try:
        download_video(job["id"], clip_path)
        state, fields = publish_with_clipagent(job, clip_path)
    except Exception as e:  # noqa: BLE001 - el fallo se reporta al Worker
        state, fields = "failed", {"error": f"puente: {e}", "retryable": True}
    finally:
        if os.path.exists(clip_path):
            os.remove(clip_path)
    api("POST", f"/api/jobs/{job['id']}/complete",
        {"state": state, **fields})
    print(f"job #{job['id']} → {state} {fields}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    args = ap.parse_args()
    if not WORKER_URL or not BRIDGE_TOKEN or BRIDGE_TENANT not in ("sebastian", "kaez"):
        print("faltan WORKER_URL, BRIDGE_TOKEN y BRIDGE_TENANT=sebastian|kaez", file=sys.stderr)
        return 2
    if args.loop:
        while True:
            try:
                run_once()
            except Exception as e:  # noqa: BLE001 - el loop no muere
                print(f"error del loop: {e}", file=sys.stderr)
            time.sleep(args.interval)
        return 0
    run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
