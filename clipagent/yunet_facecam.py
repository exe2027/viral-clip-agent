#!/usr/bin/env python3
"""Fallback YuNet para detectar la caja de la webcam del streamer.

Port adaptado de ``find_cam`` de gtavi-claudecode
(https://github.com/Bodila51/gtavi-claudecode, licencia MIT,
commit ``dd95d39``, ``scripts/facecam.py``).

Método: YuNet detecta caras en ~12 fotogramas muestreados, se queda
con la cara que se mantiene en la misma posición (las caras del juego
se mueven con la cámara) y crece un rectángulo alrededor de ella con
un mapa de bordes mediano: el borde de la webcam es una línea recta
y estática, así que sobrevive a la mediana mientras los bordes del
gameplay se difuminan.

Por qué existe este fallback (2026-11-19, clip 23 TheGrefg): el
detector por celdas estables no encontró la cámara (cuadro casi
cuadrado, arriba-derecha, bordes redondeados e interior en
movimiento: solo 7% de celdas estables) y Haar sobre miniaturas
160x90 tampoco ve la cara. YuNet, en cambio, la encontró en 12/12
frames con confianza 1.0 y caja [1539, 117, 357, 369] (IoU 0,884
contra la medida a mano). Ver
``runs/clip23-facecam-test/INFORME.md``.

Reglas de este módulo (no se negocian):

* El modelo YuNet está VENDOREADO en
  ``clipagent/models/face_detection_yunet_2023mar.onnx`` (OpenCV
  Zoo, Apache-2.0). NUNCA se descarga en runtime: si el archivo no
  está, el fallback devuelve ``None`` y el pipeline sigue con el
  comportamiento anterior.
* Solo se usa el modo ``"cam"`` (rectángulo con bordes reales). El
  modo ``"face"`` de Bodila (recorte 16:9 alrededor de la cara) NO
  es la caja de la webcam y nunca se usa como tal.
* Toda caja pasa por :func:`validate_cam_box` antes de usarse.
* El módulo nunca levanta: sin cv2/numpy, sin modelo, sin vídeo o
  sin cara estable, devuelve ``None``.
"""

import os
from pathlib import Path

try:
    import cv2
except Exception:  # noqa: BLE001 - sin cv2 no hay fallback, no error
    cv2 = None

try:
    import numpy as np
except Exception:  # noqa: BLE001 - sin numpy no hay fallback
    np = None

MODEL_PATH = Path(
    os.environ.get(
        "VIRAL_CLIP_YUNET_MODEL",
        Path(__file__).parent / "models"
        / "face_detection_yunet_2023mar.onnx",
    )
)
MODEL_MIN_BYTES = 100_000
N_FRAMES = 12
MAX_W = 1280  # ancho de análisis
SNAP_FRAMES = 20
SNAP_RANGE = 80  # px (a ancho de análisis) buscados alrededor del borde
SNAP_SHRINK = 2  # px hacia dentro para no dejar restos de juego/fondo


def model_available():
    """True si el modelo vendoreado existe y tiene tamaño plausible."""
    try:
        return (MODEL_PATH.is_file()
                and MODEL_PATH.stat().st_size >= MODEL_MIN_BYTES)
    except OSError:
        return False


def _detector():
    """FaceDetectorYN de OpenCV, o None si no hay cv2/modelo."""
    if cv2 is None or not model_available():
        return None
    try:
        return cv2.FaceDetectorYN.create(
            str(MODEL_PATH), "", (320, 320), 0.7, 0.3, 5000)
    except Exception:  # noqa: BLE001 - modelo corrupto: sin fallback
        return None


def _duration(video):
    cap = cv2.VideoCapture(str(video))
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.release()
    return n / fps if n > 0 else 0


def _sample_frames(video, start, end, n=N_FRAMES):
    """n fotogramas repartidos por [start, end], a <= MAX_W de ancho."""
    dur = _duration(video)
    s = max(0.0, start or 0.0)
    e = min(end if end is not None else dur, dur) if dur else (end or s + 20)
    if e <= s:
        e = s + 1
    cap = cv2.VideoCapture(str(video))
    frames = []
    for i in range(n):
        t = s + (e - s) * (i + 0.5) / n
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        if ok and f is not None:
            h, w = f.shape[:2]
            if w > MAX_W:
                f = cv2.resize(f, (MAX_W, int(h * MAX_W / w)),
                               interpolation=cv2.INTER_AREA)
            frames.append(f)
    cap.release()
    return frames


def _detect_faces(frames):
    det = _detector()
    if det is None:
        return []
    out = []
    for f in frames:
        h, w = f.shape[:2]
        det.setInputSize((w, h))
        _, faces = det.detect(f)
        out.append([] if faces is None else
                   [tuple(map(float, r[:4])) + (float(r[14]),)
                    for r in faces])
    return out


def _stable_face(dets, n):
    """Cara estable: cluster de detecciones en la misma posición.

    Devuelve (caja mediana [x, y, w, h] en px de análisis, hits).
    """
    best = None
    flat = [(i, d) for i, ds in enumerate(dets) for d in ds]
    for _, seed in flat:
        members = {}
        for i, d in flat:
            # Misma cara de webcam: centro a < 1,3 anchos de cara
            # (el streamer se balancea) y tamaño parecido.
            dx = (seed[0] + seed[2] / 2) - (d[0] + d[2] / 2)
            dy = (seed[1] + seed[3] / 2) - (d[1] + d[3] / 2)
            if ((dx * dx + dy * dy) ** 0.5 < 1.3 * max(seed[2], d[2])
                    and 0.6 < d[2] / seed[2] < 1.67):
                if i not in members or d[4] > members[i][4]:
                    members[i] = d
        if best is None or len(members) > len(best):
            best = members
    if not best:
        return None, 0
    arr = np.array(list(best.values()))
    return np.median(arr[:, :4], axis=0), len(best)


def _edge_map(frames):
    """Mediana por fotograma del gradiente: solo bordes estáticos."""
    gs = []
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g, (3, 3), 0)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        gs.append((gx, gy))
    mx = np.median(np.abs([a for a, _ in gs]), axis=0)
    my = np.median(np.abs([b for _, b in gs]), axis=0)
    # Los bordes tienen 2-3 px de grosor: máximo con los vecinos.
    mx = np.maximum(mx, np.maximum(np.roll(mx, 1, 1), np.roll(mx, -1, 1)))
    my = np.maximum(my, np.maximum(np.roll(my, 1, 0), np.roll(my, -1, 0)))
    return mx, my


def _find_rect(frames, face):
    """Crece desde la cara hasta los bordes estáticos de la webcam.

    Devuelve dict con L/R/T/B (px de análisis) y cuántos lados se
    encontraron, o None si no hay fotogramas.
    """
    h, w = frames[0].shape[:2]
    gx, gy = _edge_map(frames)
    fx, fy, fw, fh = face
    cx, cy = fx + fw / 2, fy + fh / 2
    thr_v = max(20.0, np.percentile(gx, 99.0) * 0.35)
    thr_h = max(20.0, np.percentile(gy, 99.0) * 0.35)

    def run_len(line_mask):
        best = cur = gap = 0
        for v in line_mask:
            if v:
                cur += 1 + gap
                gap = 0
                best = max(best, cur)
            elif cur and gap < 4:
                gap += 1
            else:
                cur = gap = 0
        return best

    def search(axis_edges, lo, hi, other_lo, other_hi, thr, vertical):
        """Mejor borde estático en [lo, hi), juzgado en el tramo dado."""
        cands = []
        for p in range(max(lo, 1), min(hi, (w if vertical else h) - 1)):
            sl = (axis_edges[other_lo:other_hi, p] if vertical
                  else axis_edges[p, other_lo:other_hi])
            m = sl > thr
            if m.mean() < 0.4:
                continue
            frac = run_len(m) / max(1, other_hi - other_lo)
            if frac < 0.4:  # un borde real es una línea larga y recta
                continue
            cands.append((p, frac * float(sl.mean())))
        if not cands:
            return None, 0
        top = max(sc for _, sc in cands)
        # Líneas casi tan fuertes: la exterior (el marco de la
        # webcam está fuera de las líneas interiores del cuarto).
        strong = [(p, sc) for p, sc in cands if sc >= 0.9 * top]
        ref = cx if vertical else cy
        after = lo > ref
        return max(strong, key=lambda t: t[0] if after else -t[0])

    ys0, ys1 = int(max(0, cy - fh * 2.5)), int(min(h, cy + fh * 2.5))
    xs0, xs1 = int(max(0, cx - fw * 4)), int(min(w, cx + fw * 4))

    def sides(ys0, ys1, xs0, xs1):
        return (search(gx, int(cx - fw * 10), int(fx - fw * 0.1),
                       ys0, ys1, thr_v, True),
                search(gx, int(fx + fw * 1.1), int(cx + fw * 10),
                       ys0, ys1, thr_v, True),
                search(gy, int(cy - fh * 4.5), int(fy - fh * 0.1),
                       xs0, xs1, thr_h, False),
                search(gy, int(fy + fh * 1.1), int(cy + fh * 4.5),
                       xs0, xs1, thr_h, False))

    (L, sL), (R, sR), (T, sT), (B, sB) = sides(ys0, ys1, xs0, xs1)
    # Refinado: juzgar cada línea sobre la extensión de la caja.
    for _ in range(2):
        if None in (L, R, T, B):
            break
        (L2, sL), (R2, sR), (T2, sT), (B2, sB) = sides(T, B, L, R)
        L, R, T, B = (L2 if L2 is not None else L), \
            (R2 if R2 is not None else R), \
            (T2 if T2 is not None else T), (B2 if B2 is not None else B)
    # Los bordes del fotograma cuentan (cámara pegada a una esquina).
    notes = []
    if L is None and fx - fw * 10 < 3:
        L = 0
        sL = 1
        notes.append("L=frame")
    if R is None and cx + fw * 10 > w - 3:
        R = w
        sR = 1
        notes.append("R=frame")
    if T is None and cy - fh * 4.5 < 3:
        T = 0
        sT = 1
        notes.append("T=frame")
    if B is None and cy + fh * 4.5 > h - 3:
        B = h
        sB = 1
        notes.append("B=frame")
    found = [x is not None for x in (L, R, T, B)]
    weak = [n for n, s, t in (("L", sL, thr_v), ("R", sR, thr_v),
                              ("T", sT, thr_h), ("B", sB, thr_h))
            if s < 0.9 * t]
    return dict(L=L, R=R, T=T, B=B, found=found, notes=notes,
                weak=len(weak))


def _edge_profile(M, S, p_lo, p_hi, span, vertical):
    """Puntúa posiciones candidatas de borde en [p_lo, p_hi).

    M = imagen mediana en color, S = mapa de desviación temporal.
    Evidencia: (1) línea recta: el salto de color se mantiene en
    casi toda la línea; (2) cambio temporal: cámara y juego se
    comportan distinto a cada lado de la línea.
    """
    a, b = span
    if not vertical:
        M = M.transpose(1, 0, 2)
        S = S.T
    Ms = M[a:b]
    Ss = S[a:b]
    n = Ms.shape[1]
    ps = np.arange(max(p_lo, 4), min(p_hi, n - 3))
    if len(ps) == 0:
        return ps, np.zeros(0)
    cs = np.concatenate([np.zeros((Ms.shape[0], 1, 3), np.float32),
                         np.cumsum(Ms, axis=1)], axis=1)
    right = (cs[:, ps + 3] - cs[:, ps]) / 3
    left = (cs[:, ps] - cs[:, ps - 3]) / 3
    step = np.abs(right - left).sum(axis=2)
    cons = (step > 24).mean(axis=0)
    mag = np.clip(np.median(step, axis=0) / 45.0, 0, 1)
    cs2 = np.concatenate([np.zeros((Ss.shape[0], 1), np.float32),
                          np.cumsum(Ss, axis=1)], axis=1)
    k0 = np.clip(ps - 10, 0, n)
    k1 = np.clip(ps - 2, 0, n)
    k2 = np.clip(ps + 2, 0, n)
    k3 = np.clip(ps + 10, 0, n)
    sl = (cs2[:, k1] - cs2[:, k0]) / np.maximum(1, k1 - k0)
    sr = (cs2[:, k3] - cs2[:, k2]) / np.maximum(1, k3 - k2)
    rel = np.abs(sl - sr) / (sl + sr + 2.0)
    tcons = (rel > 0.35).mean(axis=0)
    tmag = np.clip(np.median(rel, axis=0) * 2, 0, 1)
    return ps, cons * mag + 0.6 * tcons * tmag


def _snap_edges(frames, box, search=SNAP_RANGE, shrink=SNAP_SHRINK,
                face=None):
    """Ajusta [L, T, R, B] al borde real de la webcam.

    Devuelve (L, T, R, B, bordes_crudo). Es solo un refinado: si
    falla, el llamador conserva la caja sin ajustar.
    """
    h, w = frames[0].shape[:2]
    L, T, R, B = [int(round(v)) for v in box]
    idx = np.linspace(0, len(frames) - 1,
                      min(len(frames), SNAP_FRAMES)).astype(int)
    fs = [frames[i] for i in idx]
    M = np.median(np.array(fs), axis=0).astype(np.float32)
    S = np.array([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                  for f in fs]).astype(np.float32).std(axis=0)
    M = cv2.GaussianBlur(M, (3, 3), 0)
    S = cv2.GaussianBlur(S, (3, 3), 0)
    H, W = B - T, R - L
    yspan = (int(T + 0.15 * H), int(B - 0.15 * H))
    xspan = (int(L + 0.15 * W), int(R - 0.15 * W))
    out = {}
    for name, p0, vertical, span in (
            ("L", L, True, yspan), ("R", R, True, yspan),
            ("T", T, False, xspan), ("B", B, False, xspan)):
        lim = w if vertical else h
        if p0 <= 3 or p0 >= lim - 3:
            out[name] = 0 if p0 <= 3 else lim
            continue
        ps, sc = _edge_profile(M, S, p0 - search, p0 + search + 1,
                               span, vertical)
        if len(ps) == 0 or sc.max() < 0.25:
            out[name] = p0
            continue
        sc = sc - 0.05 * np.abs(ps - p0) / search
        strong = sc >= 0.9 * sc.max()
        if face is not None:
            fx, fy, fw_, fh_ = face
            lim_in = {"L": fx - 0.3 * fw_, "R": fx + 1.3 * fw_,
                      "T": fy - 0.3 * fh_, "B": fy + 1.3 * fh_}[name]
            ok = (ps < lim_in) if name in ("L", "T") else (ps > lim_in)
            if (strong & ok).any():
                strong &= ok
        idx = np.flatnonzero(strong)
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
        run = runs[-1] if name in ("L", "T") else runs[0]
        out[name] = int(round((ps[run[0]] + ps[run[-1]]) / 2))
    nL = out["L"] if out["L"] <= 3 else out["L"] + shrink
    nT = out["T"] if out["T"] <= 3 else out["T"] + shrink
    nR = out["R"] if out["R"] >= w - 3 else out["R"] - shrink
    nB = out["B"] if out["B"] >= h - 3 else out["B"] - shrink
    return nL, nT, nR, nB, out


def validate_cam_box(box, face, tol=0.02):
    """Valida una caja de webcam candidata (fracciones 0..1).

    ``box`` y ``face``: [x, y, w, h] en fracciones del fotograma.
    La caja debe estar en una esquina SUPERIOR (abajo están el
    minimapa y el HUD), ocupar < 45% del fotograma, tener aspecto
    entre 0,5 y 2,0, y contener la cara entera. Devuelve bool.
    """
    try:
        x, y, w, h = [float(v) for v in box[:4]]
        fx, fy, fw, fh = [float(v) for v in face[:4]]
    except (TypeError, ValueError, IndexError):
        return False
    if not (0.08 <= w <= 0.6 and 0.08 <= h <= 0.7):
        return False
    if w * h >= 0.45:
        return False
    if not (0.5 <= w / h <= 2.0):
        return False
    if y > 0.35:  # esquina superior: borde superior en el tercio de arriba
        return False
    cx = x + w / 2
    if not (cx < 0.4 or cx > 0.6):  # izquierda o derecha, no centrada
        return False
    if (fx < x - tol or fy < y - tol or fx + fw > x + w + tol
            or fy + fh > y + h + tol):
        return False
    return True


def find_cam(video, start=None, end=None):
    """Detecta la webcam en [start, end] del vídeo.

    Devuelve {"mode": "cam"|"face"|"none", "box": [x, y, w, h] en
    fracciones, "face": [...], "confidence": 0..1, "reason": ...}.
    Solo "cam" es una caja de webcam usable; "face" es un recorte
    alrededor de la cara (sin borde de cámara) y "none" es que no
    hay cara estable. Nunca levanta.
    """
    if cv2 is None or np is None or not model_available():
        return {"mode": "none", "confidence": 0.0,
                "reason": "YuNet no disponible (falta cv2/numpy/modelo)"}
    try:
        frames = _sample_frames(video, start, end)
    except Exception as exc:  # noqa: BLE001 - vídeo ilegible
        return {"mode": "none", "confidence": 0.0,
                "reason": f"no se pudo leer el vídeo: {exc}"}
    if len(frames) < 4:
        return {"mode": "none", "confidence": 0.0,
                "reason": f"solo se leyeron {len(frames)} fotogramas"}
    h, w = frames[0].shape[:2]
    dets = _detect_faces(frames)
    face, hits = _stable_face(dets, len(frames))
    need = max(3, int(len(frames) * 0.5))
    total = sum(len(d) for d in dets)
    if face is None or hits < need:
        return {"mode": "none", "confidence": 0.0, "faces_seen": total,
                "reason": ("sin cara estable entre fotogramas "
                           f"(mejor cluster {hits}/{len(frames)}, "
                           f"mínimo {need})")}
    fx, fy, fw, fh = face
    fracface = [round(fx / w, 4), round(fy / h, 4),
                round(fw / w, 4), round(fh / h, 4)]
    if fw / w > 0.5:
        return {"mode": "none", "confidence": 0.0, "face": fracface,
                "reason": "la cara estable es enorme (plano general, "
                          "no una webcam superpuesta)"}
    stab = hits / len(frames)
    r = _find_rect(frames, face)
    L, R, T, B = r["L"], r["R"], r["T"], r["B"]
    n_found = sum(r["found"])
    # Ajustar lados pegados al borde del fotograma.
    m = 0.01 * w
    if L is not None and L < m:
        L = 0
    if R is not None and R > w - m:
        R = w
    if T is not None and T < 0.01 * h:
        T = 0
    if B is not None and B > h - 0.01 * h:
        B = h
    ok = L is not None and R is not None and T is not None and B is not None
    if ok:
        bw, bh = R - L, B - T
        contains = L <= fx and R >= fx + fw and T <= fy and B >= fy + fh
        sane = (0.08 < bw / w < 0.6 and 0.08 < bh / h < 0.7 and contains
                and bw > fw * 1.2 and bh > fh * 1.2)
        if sane:
            try:
                sf = _sample_frames(video, start, end, SNAP_FRAMES)
                nL, nT, nR, nB, raw = _snap_edges(
                    sf if len(sf) >= 8 else frames, (L, T, R, B),
                    face=(fx, fy, fw, fh))
                if (nR - nL > fw * 1.2 and nB - nT > fh * 1.2
                        and nL <= fx and nR >= fx + fw
                        and nT <= fy and nB >= fy + fh):
                    L, T, R, B = nL, nT, nR, nB
                    bw, bh = R - L, B - T
                    r["notes"] = list(r["notes"]) + [f"snapped {raw}"]
            except Exception as exc:  # noqa: BLE001 - el snap es refinado
                r["notes"] = list(r["notes"]) + [f"snap failed: {exc}"]
            box = [round(L / w, 4), round(T / h, 4),
                   round(bw / w, 4), round(bh / h, 4)]
            conf = round(min(1.0, 0.55 + 0.25 * stab
                             + 0.2 * (1 if n_found == 4 else 0.5)), 2)
            return {"mode": "cam", "box": box, "face": fracface,
                    "confidence": conf,
                    "reason": (f"cara estable en {hits}/{len(frames)} "
                               f"fotogramas, bordes en {n_found}/4 lados "
                               f"{r['notes']}")}
    # Sin rectángulo limpio: recorte alrededor de la cara. NO es una
    # caja de webcam: el llamador no debe usarlo como tal.
    cw = min(w, fw * 3.2)
    ch = cw * 9 / 16
    if ch < fh * 1.8:
        ch = fh * 1.8
        cw = ch * 16 / 9
    ccx, ccy = fx + fw / 2, fy + fh * 0.55
    x0 = min(max(0, ccx - cw / 2), w - cw)
    y0 = min(max(0, ccy - ch / 2), h - ch)
    box = [round(x0 / w, 4), round(y0 / h, 4),
           round(cw / w, 4), round(ch / h, 4)]
    conf = round(min(0.8, 0.35 + 0.4 * stab), 2)
    return {"mode": "face", "box": box, "face": fracface,
            "confidence": conf,
            "reason": (f"cara estable en {hits}/{len(frames)} fotogramas "
                       f"pero sin rectángulo limpio ({n_found}/4 lados); "
                       "recorte centrado en la cara")}


def yunet_facecam_box(video, start=None, end=None):
    """Caja de webcam YuNet en px del vídeo original, o None.

    Solo acepta el modo ``"cam"`` validado por
    :func:`validate_cam_box`. El modo ``"face"`` y ``"none"`` dan
    ``None``. Nunca levanta.
    """
    if cv2 is None or np is None or not model_available():
        return None
    if not os.path.isfile(str(video)):
        return None
    try:
        res = find_cam(video, start, end)
    except Exception:  # noqa: BLE001 - el fallback nunca tumba el clip
        return None
    if res.get("mode") != "cam" or not res.get("box"):
        return None
    if not validate_cam_box(res["box"], res.get("face") or []):
        return None
    try:
        cap = cv2.VideoCapture(str(video))
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
    except Exception:  # noqa: BLE001 - sin tamaño no hay caja en px
        return None
    if src_w <= 0 or src_h <= 0:
        return None
    x, y, w, h = res["box"]
    box = [int(round(x * src_w)), int(round(y * src_h)),
           int(round(w * src_w)), int(round(h * src_h))]
    fx, fy, fw, fh = res["face"]
    face = [int(round(fx * src_w)), int(round(fy * src_h)),
            int(round(fw * src_w)), int(round(fh * src_h))]
    return {"box": box, "face": face,
            "confidence": float(res.get("confidence") or 0.0),
            "reason": res.get("reason", "")}
