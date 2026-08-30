#!/bin/bash
set -e

echo "[run_web.sh] Applying database migrations..."
python manage.py migrate --noinput

echo "[run_web.sh] Starting Daphne ASGI server on port ${PORT:-10000}..."
exec daphne -b 0.0.0.0 -p "${PORT:-10000}" config.asgi:application
