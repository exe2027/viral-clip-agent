#!/usr/bin/env bash
# deploy-all.sh — OBSOLETO (instalación por persona).
# La decisión del usuario 17 nov 2026 es UNA sola página avc.pages.dev
# con login (ver DEPLOY-avc.md). Este script solo reenvía a deploy.sh.
echo "OBSOLETO: ya no hay dos paneles. Usa ./scripts/deploy.sh" >&2
exec "$(cd "$(dirname "$0")" && pwd)/deploy.sh" "$@"
