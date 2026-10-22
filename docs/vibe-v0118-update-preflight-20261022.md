# Preparación de actualización Vibe — 2026-10-22

## Estado y límites
Último despliegue informado: `9160b7c7e6466e00225ba056a23feda869736f24` (PR #85, healthy), **no comprobado ahora en el host**. Consulta GitHub de fork `main`: `dd8e62e82af3252e7c56fd72501cfe699baee739` (PR #84). Resolver discrepancia entre main y producción antes de cambiar nada. Upstream publicado: `v0.1.18`, con fixes adicionales post-release como `6208424`. La comparación histórica `9160b7c7...v0.1.18` figura diverged (412 ahead, 857 behind); no equiparar conteo con cambios exclusivos útiles.

## Plan ejecutable
1. Preflight read-only en host: SHA, OCI revision, imagen, mounts, redes, tags, branches, free disk/Docker cache y rollback. Leer runbook existente y validar que no haya tareas concurrentes incompatibles.
2. Diff temático fork/upstream v0.1.18 + fixes posteriores. Matriz **custom -> implementación upstream -> contrato/test -> conservar/quitar**, con: PPI/AC read-only, `snapshot_id`/`read_identity`, signed cash/P&L, `technical_indicators(end_date)`, ISIN .BA, matrices/grounding de identidad, no_cache, repair contract, replay lease, PDF protegido y redacción, conectores, budget/uso de tokens.
3. Evaluar mejoras y medibilidad: tool schemas lazy-load, consumo tokens, run preview, audit de informes, tareas programadas, PDF por email, Pixel Office y paper trading solo en modo investigación. No activar trading/paper que pueda accionar una cuenta real.
4. Preparar integración en worktree/branch aislado. Corregir problemas autónomamente. Probar regresiones focales y E2E `tg_ale`, `mail_ale`, MCP AC, TWR canónico sin recálculos, cartera y riesgo ACCIONES/CEDEARs, refs/snapshots, cifras negativas, matrices, PDFs, envíos y permisos. Confirmar `PPI_TRADING_DISABLED=True`.
5. Backup probado, rollback ejecutable, guardias y pruebas de health/OCI/0 restart. Deploy solo con PASS completo y bajo procedimiento probado; ante gate fallido, NO-GO sin alterar producción. Revisar builds, cache y limpieza segura solo luego de confirmar retención necesaria.
6. Documentar evidencia, matriz de test, mejoras aprovechadas, divergencias y residuos/ramas eliminados; PR y cierre. Evitar tokens, credenciales y montos privados.

## Prioridades
P0 seguridad PPI, identidad canónica, mounts/secretos, rollback.
P1 eficiencia tokens, latencia, correcciones presupuesto de agente y confiabilidad informes.
P2 paper trading BYMA y Pixel Office como evaluación posterior; no bloquear actualización base.

## Cierre
Entrega PASS/NO-GO con SHA desplegado vs anterior, historial/tag/commit reconciliados, lista de customizaciones eliminadas y conservadas, tokens/tiempos antes-después (si medibles), CI/E2E, rollback y comprobación de limpieza. No frenar por errores resolubles; detenerse ante decisión estructural o pérdida de garantías.
