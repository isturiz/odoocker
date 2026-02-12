#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8069}"

if ! command -v ss >/dev/null 2>&1; then
  echo "Error: 'ss' no está instalado. En Debian: sudo apt-get update && sudo apt-get install -y iproute2" >&2
  exit 1
fi

PIDS=$(ss -lptn "sport = :${PORT}" 2>/dev/null | awk 'NR>1 {print $6}' | sed -E 's/.*pid=([0-9]+).*/\1/' | sort -u)

if [[ -z "${PIDS}" ]]; then
  echo "No hay procesos escuchando en el puerto ${PORT}."
  exit 0
fi

echo "Procesos escuchando en el puerto ${PORT}: ${PIDS}"

for PID in ${PIDS}; do
  if kill -0 "${PID}" 2>/dev/null; then
    echo "Deteniendo PID ${PID}..."
    kill "${PID}"
  fi
 done

sleep 1

REMAINING=$(ss -lptn "sport = :${PORT}" 2>/dev/null | awk 'NR>1 {print $6}' | sed -E 's/.*pid=([0-9]+).*/\1/' | sort -u)
if [[ -n "${REMAINING}" ]]; then
  echo "Aún quedan procesos en el puerto ${PORT}: ${REMAINING}" >&2
  exit 2
fi

echo "Puerto ${PORT} liberado."

