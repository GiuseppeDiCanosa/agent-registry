#!/usr/bin/env bash
# GENERATED FROM SPEC — DO NOT EDIT DIRECTLY
# Source: openspec/specs/container-deployment/spec.md
#
# Servizio `db`: inizializza (una tantum) il git-sync della home del registry e poi
# esegue un loop di sync a intervallo configurabile. Un errore di rete/push non deve
# mai terminare il container: viene loggato e si riprova al giro successivo.
set -uo pipefail

HOME_DIR="${AGENT_REGISTRY_HOME:-/data}"
INTERVAL="${SYNC_INTERVAL:-60}"
REMOTE="${AGENT_REGISTRY_GIT_REMOTE:-}"

if [ ! -d "$HOME_DIR/.git" ] && [ -n "$REMOTE" ]; then
  echo "[sync] inizializzo git-sync su $HOME_DIR -> $REMOTE"
  git clone "$REMOTE" "$HOME_DIR" 2>/dev/null \
    || python3 /app/scripts/sync_manager.py init --git-remote "$REMOTE" \
    || echo "[sync] init fallito, riprovo al prossimo giro"
fi

echo "[sync] avvio loop di sync ogni ${INTERVAL}s (home=$HOME_DIR)"
while true; do
  python3 /app/scripts/sync_manager.py sync 2>&1 || echo "[sync] errore di sync, continuo"
  sleep "$INTERVAL"
done
