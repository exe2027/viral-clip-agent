# SPEC — Arreglo de los 5 tests que tumbaron CircleCI #60 (2026-11-26)

## Objetivo

Dejar en verde el job `tests` de CI (GitHub Actions + CircleCI) en `main`,
sin tocar el comportamiento del sistema en vivo (Worker, panel, D1).

## Contexto (hechos verificados)

- CircleCI #60 (commit `eb0ed45`, solo 3 specs .md) y GHA `45909361399`
  fallaron con los mismos 5 tests: 2195 pasan, 5 fallan.
- Los 5 ya están diagnosticados; hay 3 arreglos en total (uno por
  causa): 2 commiteados (`6604c53`, `e46d829`, cubren 2 de los 5
  tests) y 1 sin commitear (fan-out, cubre los otros 3). Falta:
  revisión, commit del arreglo de fan-out, push y verificación
  del CI en verde.

## Los 5 fallos y su causa

1. `test_compilation.py::TestPublishLongform::test_sube_con_watch_url_y_categoria_gaming`
   — `ModuleNotFoundError: No module named 'googleapiclient'`.
   Causa: `google-api-python-client` vive solo en el extra `youtube`
   de `pyproject.toml`; CI instalaba `.[dev,vision]`.
2-4. `test_fanout.py::TestFanOutLoop` (3 tests) — los ítems del fan-out
   se archivan antes de publicarse (`published 1, archived 3`).
   Causa: el test congela la hora en `NOW = 2026-11-17 20:00 -03` con
   `mock.patch("clipagent.publish._now_chile")`, pero `order_queue`
   calcula la edad del clip con `datetime.now()` real. El 26-nov a
   las 20:00 -03 se cumplieron 9 días: penalización por edad −20,
   prioridad 82,8 → 62,8 (< 70) y la regla 6B.2 archiva solo.
   A las 18:57 del mismo día (8 días, prioridad 72,8) aún pasaba.
5. `test_hook_formulas.py::TestAuditIntegration::test_la_formula_aparece_por_clip_y_agregada`
   — `formula_top` real `{"The Steal": 2}`, esperado
   `{"The Steal": 2, "unclassified": 3}`.
   Causa: la auditoría v2 (`4ff1779`) pasó `formula_top/bottom` de
   top5/bottom5 al tercio superior/inferior; con 7 clips el tercio
   es 2 y los 3 `unclassified` del top 5 quedaron fuera.

## Cambios

1. **CI (ya commiteado, `6604c53`):** GHA `ci.yml` y CircleCI
   `config.yml` (job `tests`) instalan `.[dev,vision,youtube]`.
   No se toca el job `render` de CircleCI (ya tenía `whisper`).
2. **Auditoría (ya commiteado, `e46d829`):** `formula_top/bottom`
   vuelven a contar sobre top5/bottom5, incluido `unclassified`,
   y `render_text` restaura la sección `FÓRMULAS` y la fórmula por
   fila. Las medianas y `top_formulas` siguen en tercio (swipe v2).
   Decisión: se restaura el contrato previo que el test de `4b88497`
   exige, en vez de reescribir el test. Resolución de la pregunta
   abierta 2 (revisión Goldfish 20:40): el docstring de
   `build_report` en `audit_published.py` documenta el reparto —
   medianas y `top_formulas` en tercio, `formula_top/bottom` en
   top5 "por compatibilidad" — y `test_audit_v2.py:321` fija
   `top_formulas` en tercio, así que no hay contradicción entre
   los dos tests. No hay otros consumidores de `formula_top/bottom`
   fuera de `audit_published.py` y `test_hook_formulas.py`.
   Queda pendiente corregir el documento de la spec v2 si dice
   tercio para los conteos; en el repo no hay tal documento, solo
   el mensaje del commit `4ff1779`.
3. **Fan-out (sin commitear, en `test_fanout.py`):** clase
   `_FrozenDatetime(datetime.datetime)` con `now()` devolviendo
   `NOW`, y parche `mock.patch("clipagent.priority.datetime",
   _FrozenDatetime)` en `_patches()`. `fromisoformat` se hereda y
   devuelve datetimes reales. No se toca código de producción.

## Criterio de éxito

- Los 5 tests pasan en local con `.venv/bin/python -m pytest`
  (verificado: 17 pasan con el árbol actual: `test_fanout.py`
  completo — 15 — + `test_compilation.py::TestPublishLongform::
  test_sube_con_watch_url_y_categoria_gaming` + `test_hook_formulas
  .py::TestAuditIntegration::test_la_formula_aparece_por_clip_y_agregada`).
- Suite completa en local antes del push + `ruff check .` limpio.
- Tras el push, GHA `CI` y CircleCI `tests` en verde en el HEAD.

## Resoluciones de la revisión Goldfish (20:40)

- **Import `googleapiclient` (hallazgo a.1):** verificado en
  `clipagent/compilation.py:573` — el `else` (servicio falso
  pasado por el test) también hace
  `from googleapiclient.http import MediaFileUpload`, así que el
  import se ejecuta incluso con servicio falso. Instalar el extra
  en CI es el arreglo correcto; aplazar el import queda descartado.
- **Docstring `_FrozenDatetime` (hallazgo a.4):** corregido —
  verificado que `fromisoformat` devuelve instancias de la
  subclase, no datetimes planos; sin efecto práctico (solo
  aritmética/comparación en `_clip_age_days`).
- **Pregunta abierta 1 (un solo reloj):** se difiere — fuera del
  alcance de este arreglo. Invariante documentada: todo test que
  congele `_now_chile` debe congelar también
  `clipagent.priority.datetime`. Archivos hermanos que mockean
  `_now_chile`: `test_counter_loop`, `test_fanout`,
  `test_platform_errors`, `test_policy`,
  `test_publication_rules_2026_11_19`, `test_score_wiring`,
  `test_seo_retry`, `test_smoke` — solo `test_fanout` combina
  ese mock con el cálculo de edad de `priority`; la suite completa
  lo confirmará.
- **Equivalencia GHA ↔ CircleCI (hallazgos a.5/d.6):** GHA corre
  `pytest -q` (todo); CircleCI corre `circleci testsuite run
  "ci tests"` sobre `test_*.py` salvo `test_subtitle_policy.py`
  y `test_subtitles_always.py`. Los 5 tests fallidos están en
  archivos que corren ambas suites. Se acepta verificar CircleCI
  tras el push (su log no es accesible sin token).
- **Coste del extra `youtube` (hallazgo c.3):** no cuantificado;
  son wheels pequeños frente a una suite de ~7 min. Solo lo
  ejerce `publish_longform`; ningún otro job lo usa sin el extra
  (el job `render` de CircleCI no se toca).

## Lo que NO debe cambiar

- Nada del Worker (`panel-backend/`), del panel, de D1 ni de crons.
- Nada de `clipagent/publish.py` ni `clipagent/priority.py` (el
  arreglo del reloj es solo en el test).
- Nada de la semántica v2 de la auditoría salvo `formula_top/bottom`.

## Supuestos

1. Que el log de GHA del mismo commit describe también el fallo de
   CircleCI #60 (el log de CircleCI no es accesible sin token; la
   API devuelve "Not Found"). Los 5 tests son los mismos en ambas
   suites porque ambas corren el mismo `pytest` sobre los mismos
   archivos.
2. Que `formula_top/bottom` sobre top5 es el contrato correcto y el
   tercio era un cambio no intencionado de la v2 (no una decisión
   de la spec v2).
3. Que congelar `clipagent.priority.datetime` solo afecta a los
   tests de este archivo (el parche vive en `_patches()` de
   `test_fanout.py`).

## Preguntas abiertas

1. ¿Conviene además pasar `now` explícito a `order_queue` desde
   `scored_items` para que producción y test usen un solo reloj,
   en vez de congelar `datetime` solo en el test?
2. ¿La spec de auditoría v2 debe corregirse para decir top5 en
   `formula_top/bottom`, o el código debe volver al tercio y
   actualizarse el test?
