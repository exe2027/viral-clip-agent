#!/usr/bin/env python3
"""Pruebas de las reglas de publicación 2026-11-19.

Fuente: ~/workspace/user/files/reglas-publicacion-viral-clip-agent.md
(2026-11-19). Reemplazan la decisión 2026-11-18 "sin franja" (las
franjas vuelven) y la madurez vieja "30 días + 10 publicaciones"
(calentamiento nuevo: < 14 días o < 20 publicaciones).
"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clipagent.publish as publisher
from clipagent import accounts
from clipagent.panel_settings import (
    apply_panel_settings,
    sanitize_panel_settings,
)
from clipagent.config import load_config
from clipagent.publish import (
    DAILY_LIMITS,
    MIN_SPACING_HOURS,
    PUBLISH_WINDOW,
    ROLLING_24H_MAX,
    PublishQueue,
    _rest_weekday,
    check_limits,
    classify_publish_error,
    hashtag_limit,
    load_limits,
    next_retry,
    plan_daily,
    requires_editing,
)

CHILE = timezone(timedelta(hours=-3))


def _clip(clip_id, viral=80, platform=None, title=None, desc=None,
          tags=None, **extra):
    clip = {"clip_id": clip_id, "viral_score": viral,
            "titulo": title or f"Momento {clip_id}",
            "descripcion": desc or f"Clip {clip_id} de GTA 6",
            "hashtags": tags if tags is not None else ["#GTA6", "#GTA"],
            "aporte_propio": True}
    if platform:
        clip["plataforma"] = platform
    clip.update(extra)
    return clip


def _normal_accounts(platforms):
    """accounts_info con calentamiento superado en esas plataformas,
    SIN subida al máximo duro (sin reach_trend: solo el plan)."""
    return {p: {"warmup_stage": "normal", "age_days": 60,
                "restricted": False}
            for p in platforms}


def _free_day(platforms, start=date(2026, 11, 20)):
    """Un día en el que ninguna de esas plataformas tiene día libre."""
    day = start
    for _ in range(30):
        if all(day.weekday() != _rest_weekday(p, day.isoformat())
               for p in platforms):
            return day.isoformat()
        day += timedelta(days=1)
    raise AssertionError("sin día libre de todas en 30 días")


class TestLimits20261119(unittest.TestCase):
    def test_topes_por_plataforma(self):
        limits = load_limits()
        self.assertEqual(
            (limits["youtube_shorts"]["plan_per_day"],
             limits["youtube_shorts"]["max_per_day"]), (1, 2))
        self.assertEqual(
            (limits["tiktok"]["plan_per_day"],
             limits["tiktok"]["max_per_day"]), (2, 3))
        self.assertEqual(
            (limits["instagram_reels"]["plan_per_day"],
             limits["instagram_reels"]["max_per_day"]), (1, 2))
        self.assertEqual(
            (limits["facebook_reels"]["plan_per_day"],
             limits["facebook_reels"]["max_per_day"]), (1, 2))
        self.assertEqual(DAILY_LIMITS["youtube_shorts"], (1, 2))
        self.assertEqual(DAILY_LIMITS["tiktok"], (2, 3))
        # Cuota YT y Zernio no cambian (datos de plataforma).
        self.assertEqual(
            limits["youtube_shorts"]["quota_upload_cost_units"], 1)
        self.assertEqual(
            limits["youtube_shorts"]["quota_upload_calls_per_day"], 100)
        self.assertEqual(limits["tiktok"]["zernio_per_day"], 15)

    def test_espaciamiento_rolling_warmup_slots_y_backoff(self):
        limits = load_limits()
        self.assertEqual(limits["min_spacing_hours"], 3)
        self.assertEqual(limits["youtube_spacing_hours"], 4)
        self.assertEqual(limits["min_spacing_minutes"], 5)
        self.assertEqual(limits["cross_platform_min_minutes"], 15)
        self.assertEqual(limits["rolling_24h_max"], 3)
        self.assertEqual(limits["warmup"]["new_if_days"], 14)
        self.assertEqual(limits["warmup"]["new_if_publishes"], 20)
        self.assertEqual(limits["retry_backoff_minutes"], [5, 15, 45])
        self.assertEqual(limits["pause_hours"], 48)
        self.assertEqual(
            (limits["tiktok"]["days_per_week_min"],
             limits["tiktok"]["days_per_week_max"]), (5, 6))
        self.assertEqual(
            (limits["instagram_reels"]["days_per_week_min"],
             limits["instagram_reels"]["days_per_week_max"]), (4, 5))
        self.assertEqual(MIN_SPACING_HOURS, 3)
        self.assertEqual(ROLLING_24H_MAX, 3)
        # La franja VUELVE (revocada la decisión 2026-11-18).
        self.assertEqual(PUBLISH_WINDOW, {
            "morning": ("12:00", "14:00"),
            "afternoon": ("18:00", "20:00"),
            "night": ("21:00", "23:00")})

    def test_fallback_sin_archivo_trae_los_valores_nuevos(self):
        limits = load_limits("/no/existe/limits.yaml")
        self.assertEqual(limits["youtube_shorts"]["plan_per_day"], 1)
        self.assertEqual(limits["tiktok"]["max_per_day"], 3)
        self.assertEqual(limits["min_spacing_hours"], 3)
        self.assertEqual(limits["rolling_24h_max"], 3)


class TestWarmup(unittest.TestCase):
    def _accounts_dir(self, created_at=None, published_count=None):
        tmp = tempfile.TemporaryDirectory()
        lines = []
        if created_at:
            lines.append(f'  created_at: "{created_at}"')
        if published_count is not None:
            lines.append(f"  published_count: {published_count}")
        with open(os.path.join(tmp.name, "accounts.yaml"), "w",
                  encoding="utf-8") as f:
            f.write("youtube_shorts:\n" + "\n".join(lines) + "\n")
        return tmp

    def test_semana1_menos_de_7_dias(self):
        tmp = self._accounts_dir("2026-11-16")
        self.assertEqual(accounts.warmup_stage(
            "youtube_shorts", tmp.name, today=date(2026, 11, 17)),
            "week1")
        self.assertTrue(accounts.is_new_account(
            "youtube_shorts", tmp.name, today=date(2026, 11, 17)))
        tmp.cleanup()

    def test_semana2_de_7_a_13_dias(self):
        tmp = self._accounts_dir("2026-11-09")
        # 8 días: antes (regla < 7 días) ya no era nueva; ahora es
        # semana 2 (nueva hasta 14 días).
        self.assertEqual(accounts.warmup_stage(
            "youtube_shorts", tmp.name, today=date(2026, 11, 17)),
            "week2")
        self.assertTrue(accounts.is_new_account(
            "youtube_shorts", tmp.name, today=date(2026, 11, 17)))
        tmp.cleanup()

    def test_14_dias_con_pocas_publicaciones_sigue_nueva(self):
        tmp = self._accounts_dir("2026-11-02", published_count=5)
        self.assertEqual(accounts.warmup_stage(
            "youtube_shorts", tmp.name, today=date(2026, 11, 16)),
            "week2")
        self.assertTrue(accounts.is_new_account(
            "youtube_shorts", tmp.name, today=date(2026, 11, 16)))
        tmp.cleanup()

    def test_14_dias_y_20_publicaciones_es_normal(self):
        tmp = self._accounts_dir("2026-11-02", published_count=25)
        self.assertEqual(accounts.warmup_stage(
            "youtube_shorts", tmp.name, today=date(2026, 11, 16)),
            "normal")
        self.assertFalse(accounts.is_new_account(
            "youtube_shorts", tmp.name, today=date(2026, 11, 16)))
        tmp.cleanup()

    def test_sin_fecha_ni_conteo_es_antigua(self):
        tmp = self._accounts_dir()
        self.assertEqual(accounts.warmup_stage(
            "youtube_shorts", tmp.name, today=date(2026, 11, 17)),
            "normal")
        self.assertFalse(accounts.is_new_account(
            "youtube_shorts", tmp.name, today=date(2026, 11, 17)))
        tmp.cleanup()

    def test_warmup_max_1_por_dia_y_normal_sin_tope(self):
        self.assertEqual(
            accounts.warmup_max_per_day("week1"), 1)
        self.assertEqual(
            accounts.warmup_max_per_day("week2"), 1)
        self.assertIsNone(accounts.warmup_max_per_day("normal"))


class TestCheckLimits20261119(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _queue(self):
        return PublishQueue(os.path.join(self.tmp.name, "pq.json"))

    def _now(self, day=17, hour=20, minute=0):
        return datetime(2026, 11, day, hour, minute, tzinfo=CHILE)

    def _publish_at(self, q, platform, clip, when):
        with mock.patch.object(publisher, "_now_chile",
                               return_value=when):
            item = q.enqueue(clip, platform)
            q.mark_published(item, "vid")

    def test_rolling_24h_bloquea_aunque_sea_otro_dia_calendario(self):
        q = self._queue()
        # 3 publicados ayer a las 21:00 (dentro de 24 h móviles a
        # las 20:00 de hoy) -> rolling 3/3 bloquea.
        for i in range(3):
            self._publish_at(q, "tiktok", f"/tmp/r{i}.mp4",
                             self._now(day=16, hour=21))
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(day=17, hour=20)):
            ok, msg = check_limits(q, "tiktok")
            self.assertFalse(ok)
            self.assertIn("rolling 24 h", msg)

    def test_rolling_24h_pasa_si_la_ultima_fue_hace_mas_de_24h(self):
        q = self._queue()
        self._publish_at(q, "tiktok", "/tmp/viejo.mp4",
                         self._now(day=15, hour=19))
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(day=17, hour=20)):
            ok, msg = check_limits(q, "tiktok")
            self.assertTrue(ok, msg)

    def test_espaciamiento_3h_y_4h_youtube(self):
        q = self._queue()
        # Última subida hace 2 h: bloquea TikTok (3 h).
        self._publish_at(q, "tiktok", "/tmp/t0.mp4",
                         self._now(hour=18))
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(hour=20)):
            ok, msg = check_limits(q, "tiktok")
            self.assertFalse(ok)
            self.assertIn("espaciamiento", msg)
        # Hace 3,5 h: TikTok pasa (3 h).
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(hour=21, minute=30)):
            ok, msg = check_limits(q, "tiktok")
            self.assertTrue(ok, msg)
        # YouTube (4 h): última subida ayer 23:00, hoy 02:30
        # (3,5 h, 0 publicados hoy: el plan no tapa el freno).
        q2 = self._queue()
        self._publish_at(q2, "youtube_shorts", "/tmp/y0.mp4",
                         self._now(day=17, hour=23))
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(day=18, hour=2,
                                                     minute=30)):
            ok, msg = check_limits(q2, "youtube_shorts")
            self.assertFalse(ok)
            self.assertIn("espaciamiento", msg)
            self.assertIn("4 h", msg)
        # A las 03:00 (4 h justas) YouTube pasa.
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(day=18, hour=3)):
            ok, msg = check_limits(q2, "youtube_shorts")
            self.assertTrue(ok, msg)

    def test_calentamiento_bloquea_al_segundo_del_dia(self):
        q = self._queue()
        self._publish_at(q, "tiktok", "/tmp/w0.mp4", self._now(hour=12))
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(hour=20)):
            ok, msg = check_limits(q, "tiktok", warmup_stage="week1")
            self.assertFalse(ok)
            self.assertIn("calentamiento week1", msg)
            ok, msg = check_limits(q, "tiktok", warmup_stage="normal")
            self.assertTrue(ok, msg)

    def test_dia_libre_bloquea(self):
        q = self._queue()
        ok, msg = check_limits(q, "tiktok", rest_day=True)
        self.assertFalse(ok)
        self.assertIn("día libre", msg)

    def test_plan_cubierto_antes_del_techo(self):
        q = self._queue()
        # Plan YT = 1: con 1 publicado hoy, el plan está cubierto.
        self._publish_at(q, "youtube_shorts", "/tmp/p0.mp4",
                         self._now(hour=12))
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(hour=20)):
            ok, msg = check_limits(q, "youtube_shorts")
            self.assertFalse(ok)
            self.assertIn("plan del día cubierto", msg)


class TestErroresYReintentos(unittest.TestCase):
    def test_backoff_5_15_45_max_3_intentos(self):
        self.assertEqual(
            next_retry("429 Too Many Requests", 0),
            {"action": "retry", "wait_minutes": 5,
             "reason": "temporal: reintento 1/3 en 5 min"})
        self.assertEqual(
            next_retry("503 Service Unavailable", 1)["wait_minutes"], 15)
        self.assertEqual(
            next_retry("red caída", 2)["wait_minutes"], 45)
        self.assertEqual(next_retry("429", 3)["action"], "give_up")

    def test_spam_no_reintenta_el_mismo_dia(self):
        self.assertEqual(
            classify_publish_error("spam_risk_too_many_posts"),
            "spam_limit")
        result = next_retry("spam_risk_too_many_posts", 0)
        self.assertEqual(result["action"], "stop_today")
        self.assertNotIn("wait_minutes", result)
        # Ni siquiera en el primer intento: no hay reintento hoy.
        self.assertEqual(
            next_retry("límite de publicaciones excedido", 0)["action"],
            "stop_today")

    def test_429_temporal_vs_spam_por_limite(self):
        self.assertEqual(classify_publish_error("429"), "transient")
        self.assertEqual(
            classify_publish_error("429 too many posts"), "spam_limit")


class TestAntiSpam(unittest.TestCase):
    def test_hashtag_max_5_en_todas(self):
        self.assertEqual(hashtag_limit("instagram_reels"), 5)
        self.assertEqual(hashtag_limit("tiktok"), 5)
        self.assertEqual(hashtag_limit("youtube_shorts"), 5)

    def test_corte_crudo_requiere_edicion(self):
        self.assertTrue(requires_editing({"clip_id": "c1"}))
        self.assertTrue(requires_editing(
            {"clip_id": "c1", "aporte_propio": False}))
        self.assertFalse(requires_editing(
            {"clip_id": "c1", "aporte_propio": True}))
        self.assertFalse(requires_editing(
            {"clip_id": "c1", "edits": ["subtitulos", "zoom"]}))


class TestPlanDaily(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _queue(self):
        return PublishQueue(os.path.join(self.tmp.name, "pq.json"))

    def _hora(self, pub):
        return pub["hora_local"]

    def test_json_valido_y_estructura(self):
        platforms = ["tiktok", "youtube_shorts", "instagram_reels",
                     "facebook_reels"]
        day = _free_day(platforms)
        clips = [_clip("c1", 90, platform="tiktok"),
                 _clip("c2", 80, platform="youtube_shorts")]
        plan = plan_daily(clips, queue=self._queue(), day=day,
                          accounts_info=_normal_accounts(platforms))
        self.assertEqual(set(plan), {"fecha", "publicaciones",
                                     "en_cola", "alertas"})
        self.assertEqual(plan["fecha"], day)
        self.assertEqual(len(plan["publicaciones"]), 2)
        for pub in plan["publicaciones"]:
            self.assertEqual(set(pub), {"clip_id", "plataforma",
                                        "cuenta", "hora_local",
                                        "titulo", "descripcion",
                                        "hashtags", "motivo"})
            self.assertRegex(pub["hora_local"], r"^\d{2}:\d{2}$")
            # Hora dentro de alguna franja.
            hh, mm = (int(x) for x in pub["hora_local"].split(":"))
            moment = hh * 60 + mm
            self.assertTrue(any(
                s <= moment < e for s, e in
                ((12 * 60, 14 * 60), (18 * 60, 20 * 60),
                 (21 * 60, 23 * 60))))

    def test_topes_por_plataforma_y_resto_a_cola(self):
        platforms = ["tiktok", "youtube_shorts", "instagram_reels",
                     "facebook_reels"]
        day = _free_day(platforms)
        clips = ([_clip(f"tk{i}", 90 - i, platform="tiktok")
                  for i in range(3)]
                 + [_clip(f"yt{i}", 85 - i, platform="youtube_shorts")
                    for i in range(2)]
                 + [_clip(f"ig{i}", 80 - i, platform="instagram_reels")
                    for i in range(2)]
                 + [_clip(f"fb{i}", 75 - i, platform="facebook_reels")
                    for i in range(2)])
        plan = plan_daily(clips, queue=self._queue(), day=day,
                          accounts_info=_normal_accounts(platforms))
        per = {}
        for pub in plan["publicaciones"]:
            per.setdefault(pub["plataforma"], []).append(pub["clip_id"])
        self.assertEqual(len(per["tiktok"]), 2)      # plan 2/3
        self.assertEqual(len(per["youtube_shorts"]), 1)  # plan 1/2
        self.assertEqual(len(per["instagram_reels"]), 1)
        self.assertEqual(len(per["facebook_reels"]), 1)
        self.assertEqual(len(plan["en_cola"]), 4)
        # Orden por viralidad: los mejores primero.
        self.assertEqual(per["tiktok"], ["tk0", "tk1"])

    def test_un_video_al_dia_va_tarde_o_noche(self):
        day = _free_day(["tiktok"])
        plan = plan_daily([_clip("solo", 90, platform="tiktok")],
                          queue=self._queue(), day=day,
                          accounts_info=_normal_accounts(["tiktok"]))
        self.assertEqual(len(plan["publicaciones"]), 1)
        hora = plan["publicaciones"][0]["hora_local"]
        self.assertTrue("18:00" <= hora < "20:00"
                        or "21:00" <= hora < "23:00", hora)

    def test_dos_videos_mediodia_y_noche(self):
        day = _free_day(["tiktok"])
        clips = [_clip("a", 90, platform="tiktok"),
                 _clip("b", 80, platform="tiktok")]
        plan = plan_daily(clips, queue=self._queue(), day=day,
                          accounts_info=_normal_accounts(["tiktok"]))
        horas = sorted(p["hora_local"] for p in plan["publicaciones"])
        self.assertEqual(len(horas), 2)
        self.assertTrue("12:00" <= horas[0] < "14:00", horas)
        self.assertTrue("21:00" <= horas[1] < "23:00", horas)

    def test_hora_exacta_varia_entre_dias(self):
        day1 = _free_day(["tiktok"])
        day2 = (date.fromisoformat(day1) + timedelta(days=1)).isoformat()
        plan1 = plan_daily([_clip("x", 90, platform="tiktok")],
                           queue=self._queue(), day=day1,
                           accounts_info=_normal_accounts(["tiktok"]))
        plan2 = plan_daily([_clip("x", 90, platform="tiktok")],
                           queue=self._queue(), day=day2,
                           accounts_info=_normal_accounts(["tiktok"]))
        # El jitter ±10 min depende del día: las horas no tienen
        # por qué coincidir (y en la práctica no coinciden).
        self.assertNotEqual(plan1["publicaciones"][0]["hora_local"],
                            plan2["publicaciones"][0]["hora_local"])

    def test_calentamiento_max_1_por_dia(self):
        day = _free_day(["tiktok"])
        info = {"tiktok": {"warmup_stage": "week1", "age_days": 3}}
        clips = [_clip("w1", 90, platform="tiktok"),
                 _clip("w2", 80, platform="tiktok")]
        plan = plan_daily(clips, queue=self._queue(), day=day,
                          accounts_info=info)
        self.assertEqual(len(plan["publicaciones"]), 1)
        self.assertEqual(plan["en_cola"], ["w2"])
        self.assertTrue(any("calentamiento week1" in a
                            for a in plan["alertas"]))

    def test_requiere_edicion_a_cola_sin_publicar(self):
        day = _free_day(["tiktok"])
        clips = [_clip("crudo", 95, platform="tiktok",
                       aporte_propio=False),
                 _clip("bueno", 80, platform="tiktok")]
        plan = plan_daily(clips, queue=self._queue(), day=day,
                          accounts_info=_normal_accounts(["tiktok"]))
        self.assertEqual([p["clip_id"] for p in plan["publicaciones"]],
                         ["bueno"])
        self.assertIn("crudo", plan["en_cola"])
        self.assertTrue(any("requiere_edicion" in a
                            for a in plan["alertas"]))

    def test_hashtags_recortados_a_5(self):
        day = _free_day(["instagram_reels"])
        tags = ["#a", "#b", "#c", "#d", "#e", "#f", "#g"]
        plan = plan_daily(
            [_clip("h1", 90, platform="instagram_reels", tags=tags)],
            queue=self._queue(), day=day,
            accounts_info=_normal_accounts(["instagram_reels"]))
        pub = plan["publicaciones"][0]
        self.assertEqual(len(pub["hashtags"]), 5)
        self.assertTrue(any("hashtags recortados" in a
                            for a in plan["alertas"]))

    def test_texto_repetido_mismo_dia_a_cola(self):
        day = _free_day(["tiktok", "instagram_reels"])
        clips = [_clip("d1", 90, platform="tiktok",
                       title="Mismo título", desc="Misma descripción"),
                 _clip("d2", 80, platform="instagram_reels",
                       title="Mismo título", desc="Misma descripción")]
        plan = plan_daily(clips, queue=self._queue(), day=day,
                          accounts_info=_normal_accounts(
                              ["tiktok", "instagram_reels"]))
        self.assertEqual(len(plan["publicaciones"]), 1)
        self.assertIn("d2", plan["en_cola"])

    def test_mismo_clip_en_dos_plataformas_con_15_min(self):
        day = _free_day(["tiktok", "instagram_reels"])
        clips = [_clip("same", 90, platform="tiktok",
                       title="Título TikTok", desc="Desc TikTok"),
                 _clip("same", 90, platform="instagram_reels",
                       title="Título IG", desc="Desc IG")]
        plan = plan_daily(clips, queue=self._queue(), day=day,
                          accounts_info=_normal_accounts(
                              ["tiktok", "instagram_reels"]))
        self.assertEqual(len(plan["publicaciones"]), 2)
        horas = sorted(p["hora_local"] for p in plan["publicaciones"])
        gap = ((int(horas[1][:2]) * 60 + int(horas[1][3:]))
               - (int(horas[0][:2]) * 60 + int(horas[0][3:])))
        self.assertGreaterEqual(gap, 15)

    def test_mismo_clip_misma_cuenta_mismo_dia_a_cola(self):
        day = _free_day(["tiktok"])
        clips = [_clip("rep", 90, platform="tiktok"),
                 _clip("rep", 85, platform="tiktok")]
        plan = plan_daily(clips, queue=self._queue(), day=day,
                          accounts_info=_normal_accounts(["tiktok"]))
        self.assertEqual(len(plan["publicaciones"]), 1)
        self.assertIn("rep", plan["en_cola"])

    def test_dia_libre_rota_y_deja_la_cuenta_en_cero(self):
        # Un día en el que TikTok tiene día libre: capacidad 0.
        day = None
        for offset in range(14):
            cand = (date(2026, 11, 20) + timedelta(days=offset))
            if cand.weekday() == _rest_weekday(
                    "tiktok", cand.isoformat()):
                day = cand.isoformat()
                break
        self.assertIsNotNone(day)
        plan = plan_daily([_clip("libre", 90, platform="tiktok")],
                          queue=self._queue(), day=day,
                          accounts_info=_normal_accounts(["tiktok"]))
        self.assertEqual(plan["publicaciones"], [])
        self.assertIn("libre", plan["en_cola"])
        self.assertTrue(any("día libre" in a for a in plan["alertas"]))

    def test_maximo_duro_solo_con_cuenta_madura_y_alcance(self):
        day = _free_day(["tiktok"])
        info = {"tiktok": {"warmup_stage": "normal", "age_days": 60,
                           "restricted": False, "reach_trend": "up"}}
        clips = [_clip(f"m{i}", 90 - i, platform="tiktok")
                 for i in range(3)]
        plan = plan_daily(clips, queue=self._queue(), day=day,
                          accounts_info=info)
        self.assertEqual(len(plan["publicaciones"]), 3)  # techo 3
        # Sin alcance en subida, solo el plan (2).
        info["tiktok"]["reach_trend"] = "down"
        plan2 = plan_daily(clips, queue=self._queue(), day=day,
                           accounts_info=info)
        self.assertEqual(len(plan2["publicaciones"]), 2)

    def test_reparto_entre_plataformas_sin_saturar_una(self):
        platforms = ["tiktok", "youtube_shorts", "instagram_reels",
                     "facebook_reels"]
        day = _free_day(platforms)
        clips = [_clip(f"r{i}", 90 - i) for i in range(6)]
        plan = plan_daily(clips, queue=self._queue(), day=day,
                          accounts_info=_normal_accounts(platforms))
        per = {p: 0 for p in platforms}
        for pub in plan["publicaciones"]:
            per[pub["plataforma"]] += 1
        self.assertTrue(all(v >= 1 for v in per.values()), per)
        self.assertEqual(sum(per.values()), 5)  # planes 2+1+1+1
        self.assertEqual(len(plan["en_cola"]), 1)


class TestVerifyPlan(unittest.TestCase):
    def test_mismo_clip_cross_platform_a_10_min_es_violacion(self):
        plan = {"fecha": "2026-11-20",
                "publicaciones": [
                    {"clip_id": "c", "plataforma": "tiktok",
                     "cuenta": "@x", "hora_local": "12:00",
                     "titulo": "T1", "descripcion": "D1",
                     "hashtags": ["#a"], "motivo": "m"},
                    {"clip_id": "c", "plataforma": "instagram_reels",
                     "cuenta": "@x", "hora_local": "12:10",
                     "titulo": "T2", "descripcion": "D2",
                     "hashtags": ["#a"], "motivo": "m"}],
                "en_cola": [], "alertas": []}
        violations = publisher._verify_plan(plan)
        self.assertTrue(any("15 min" in v[2] for v in violations),
                        violations)


class TestPanelSettings20261119(unittest.TestCase):
    def test_campos_nuevos_se_sanean(self):
        clean = sanitize_panel_settings({
            "tiktok_plan_per_day": 2, "tiktok_max_per_day": 3,
            "youtube_spacing_hours": 4,
            "cross_platform_min_minutes": 15,
            "rolling_24h_max": 3,
            "warmup_new_if_days": 14, "warmup_new_if_publishes": 20,
            "hashtag_max": 5,
        })
        self.assertEqual(clean["tiktok_plan_per_day"], 2)
        self.assertEqual(clean["youtube_spacing_hours"], 4)
        self.assertEqual(clean["cross_platform_min_minutes"], 15)
        self.assertEqual(clean["rolling_24h_max"], 3)
        self.assertEqual(clean["warmup_new_if_days"], 14)
        self.assertEqual(clean["hashtag_max"], 5)

    def test_apply_mapea_los_nuevos_campos_a_limits(self):
        cfg = apply_panel_settings(load_config(), {
            "youtube_plan_per_day": 1, "youtube_max_per_day": 2,
            "tiktok_plan_per_day": 2, "tiktok_max_per_day": 3,
            "other_plan_per_day": 1, "other_max_per_day": 2,
            "min_spacing_hours": 3, "youtube_spacing_hours": 4,
            "cross_platform_min_minutes": 15, "rolling_24h_max": 3,
            "warmup_new_if_days": 14, "warmup_new_if_publishes": 20,
            "hashtag_max": 5,
        })
        limits = cfg["_panel_limits_override"]
        self.assertEqual(limits["youtube_shorts"], {
            "plan_per_day": 1, "max_per_day": 2})
        self.assertEqual(limits["tiktok"]["plan_per_day"], 2)
        self.assertEqual(limits["tiktok"]["max_per_day"], 3)
        self.assertEqual(limits["instagram_reels"]["plan_per_day"], 1)
        self.assertEqual(limits["facebook_reels"]["max_per_day"], 2)
        self.assertEqual(limits["min_spacing_hours"], 3)
        self.assertEqual(limits["youtube_spacing_hours"], 4)
        self.assertEqual(limits["cross_platform_min_minutes"], 15)
        self.assertEqual(limits["rolling_24h_max"], 3)
        self.assertEqual(limits["warmup"], {
            "new_if_days": 14, "new_if_publishes": 20})
        self.assertEqual(limits["hashtag_max"], {
            "instagram_reels": 5, "default": 5})

    def test_other_sigue_valiendo_para_tiktok_si_no_hay_campo_propio(self):
        cfg = apply_panel_settings(load_config(), {
            "other_plan_per_day": 1, "other_max_per_day": 2})
        limits = cfg["_panel_limits_override"]
        self.assertEqual(limits["tiktok"]["plan_per_day"], 1)
        self.assertEqual(limits["instagram_reels"]["plan_per_day"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
