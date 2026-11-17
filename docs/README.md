# docs/ — documentación del proyecto Clips GTA 6

Lo que antes vivía solo en Muse (prompts, diseños y decisiones) ahora vive
versionado en el repo. Estos archivos son la fuente de verdad documental
del proyecto; el código y sus tests son la fuente de verdad ejecutable.

## Precedencia

**El MÁSTER PROMPT FINAL manda en cualquier contradicción.** Si un
documento de `docs/` o una decisión antigua contradice
`MASTER-PROMPT-FINAL.md`, se aplica el MÁSTER PROMPT y se corrige el
documento. En particular:

- Ya NO hay aprobación humana obligatoria por clip: la publicación es
  automática, con red de seguridad (Fase 6 del MÁSTER PROMPT).
- El sistema de puntaje (`viral_score`, `seo_score`, `priority`) es
  INTOCABLE en fórmulas, pesos y umbrales: solo se conecta y se prueba.

## Qué vive en cada archivo

| Archivo | Contenido | Origen |
|---|---|---|
| `MASTER-PROMPT-FINAL.md` | **Manda sobre todo lo demás.** Objetivo ("pegar links y presionar Iniciar"), reglas inamovibles R1–R9 y fases 1–9 del agente: bugs, APIs/credenciales, lista única de streamers, refactor, automatización total con red de seguridad, sistema de puntaje como cerebro (Fase 6 bis), calidad de clips, métricas y directos. | Copia fiel de `~/workspace/user/files/MASTER-PROMPT-FINAL-viral-clip-agent.md` (17 nov 2026) |
| `decisiones-2026-11-16-17.md` | Decisiones tomadas por Sebastián el 16–17 nov 2026: canal ES primero, fórmula de prioridad intocable, trazabilidad + prioridad LIVE (+15), borrado por plataforma, reglas de publicación (1/día TikTok, $0), estado de cuentas y panel, plan de independencia. | Resumen redactado a partir de los tres documentos siguientes y de las decisiones en chat |
| `trazabilidad-live-prioridad.md` | Diseño aprobado de trazabilidad paso a paso (11 pasos) + prioridad LIVE: Cola en 2 segmentos (LIVE arriba, VOD abajo), impulso +15 (opción B, elegida 17 nov 12:59), score live provisional con re-puntuación al cerrar el VOD, y §9 no borrar el clip hasta que todas sus plataformas terminen. | Copia fiel de `~/workspace/gta6-clips/trazabilidad-live-prioridad.md` |
| `independencia-cloudflare-plan.md` | Plan maestro para sacar el proyecto fuera de Muse: auditoría del panel (v17) y del repo (HEAD 7afaf79), arquitectura $0 por persona (una instalación por usuario), qué se pierde al salir de Muse y cómo se arregla, TikTok por navegador, qué falta y qué necesita del usuario. | Copia fiel de `~/workspace/gta6-clips/independencia-cloudflare-plan.md` |
| `agentes-en-codigo-go-vs-python.md` | Análisis Go vs Python (y TypeScript/Rust) pieza por pieza, con ranking: Opción A todo Python (22/25, recomendada, 2–4 días), C híbrido Go+Python (20/25, Fase 2), D TS+Python (20/25), B todo Go (17/25), F Rust (16/25), E todo TS (14/25). El aislamiento por tenant no cambia el ranking. | Copia fiel de `~/workspace/gta6-clips/agentes-en-codigo-go-vs-python.md` |

## Qué NO vive aquí

- **Ningún secreto.** Nada de tokens, contraseñas, claves de API ni
  códigos de verificación. Los secretos van en `.env` o en `tokens/`
  (gitignored), nunca en `docs/` ni en ningún archivo versionado
  (MÁSTER PROMPT FINAL, R3).
- **Scripts operativos sueltos.** Los scripts de `vodcheck-2026-11-17/`
  no se versionan: son trabajo puntual con URLs firmadas de Twitch
  (contienen tokens de reproducción) y fragmentos de vídeo descargados.
  Si alguno se convierte en herramienta reutilizable, se limpia de
  secretos y se integra en el paquete `clipagent/` con sus tests, no en
  `docs/`.

## Cómo mantener estos docs

- Cada decisión nueva del usuario que cambie el diseño se anota en un
  archivo de decisiones fechado o se actualiza
  `decisiones-2026-11-16-17.md`, con fecha y quién la tomó.
- Un cambio en el MÁSTER PROMPT genera una nueva versión versionada
  (no se reescribe la historia: se registra qué cambió y por qué).
- Los docs no sustituyen a los tests: si un doc dice algo que el código
  no hace, el código manda hasta que se implemente (y se anota como
  pendiente, no se borra el doc).
