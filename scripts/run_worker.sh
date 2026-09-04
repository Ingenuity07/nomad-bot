#!/bin/bash
set -e

# ==============================================================================
# Dual-Process Supervisor: Celery Worker + HTTP Health Server
# ==============================================================================

echo "[run_worker.sh] Starting Celery worker dual-process supervisor..."

# 1. Start lightweight HTTP health server
python scripts/worker_health_server.py &
HEALTH_PID=$!
echo "[run_worker.sh] HTTP health server started with PID: $HEALTH_PID"

# 2. Start Celery worker
celery -A config worker --loglevel=INFO --concurrency=1 &
CELERY_PID=$!
echo "[run_worker.sh] Celery worker started with PID: $CELERY_PID"

BEAT_PID=""
if [ "${RUN_CELERY_BEAT:-False}" = "True" ]; then
    celery -A config beat --loglevel=INFO &
    BEAT_PID=$!
    echo "[run_worker.sh] Celery beat started with PID: $BEAT_PID"
fi

# Graceful termination handler
cleanup() {
    echo "[run_worker.sh] Termination signal received. Stopping child processes..."
    if kill -0 "$CELERY_PID" 2>/dev/null; then
        echo "[run_worker.sh] Sending SIGTERM to Celery worker (PID: $CELERY_PID)..."
        kill -TERM "$CELERY_PID" 2>/dev/null || true
    fi
    if kill -0 "$HEALTH_PID" 2>/dev/null; then
        echo "[run_worker.sh] Sending SIGTERM to HTTP health server (PID: $HEALTH_PID)..."
        kill -TERM "$HEALTH_PID" 2>/dev/null || true
    fi
    if [ -n "$BEAT_PID" ] && kill -0 "$BEAT_PID" 2>/dev/null; then
        echo "[run_worker.sh] Sending SIGTERM to Celery beat (PID: $BEAT_PID)..."
        kill -TERM "$BEAT_PID" 2>/dev/null || true
        wait "$BEAT_PID" 2>/dev/null || true
    fi
    wait "$CELERY_PID" 2>/dev/null || true
    wait "$HEALTH_PID" 2>/dev/null || true
    echo "[run_worker.sh] Child processes stopped. Exiting cleanly."
    exit 0
}

# Trap termination signals
trap cleanup SIGTERM SIGINT SIGQUIT

# Process liveness monitoring loop
while true; do
    if ! kill -0 "$HEALTH_PID" 2>/dev/null; then
        echo "[run_worker.sh] ERROR: HTTP health server (PID: $HEALTH_PID) exited unexpectedly!"
        if kill -0 "$CELERY_PID" 2>/dev/null; then
            echo "[run_worker.sh] Terminating surviving Celery worker (PID: $CELERY_PID)..."
            kill -TERM "$CELERY_PID" 2>/dev/null || true
            wait "$CELERY_PID" 2>/dev/null || true
        fi
        exit 1
    fi

    if ! kill -0 "$CELERY_PID" 2>/dev/null; then
        echo "[run_worker.sh] ERROR: Celery worker (PID: $CELERY_PID) exited unexpectedly!"
        if kill -0 "$HEALTH_PID" 2>/dev/null; then
            echo "[run_worker.sh] Terminating surviving HTTP health server (PID: $HEALTH_PID)..."
            kill -TERM "$HEALTH_PID" 2>/dev/null || true
            wait "$HEALTH_PID" 2>/dev/null || true
        fi
        exit 1
    fi

    if [ -n "$BEAT_PID" ] && ! kill -0 "$BEAT_PID" 2>/dev/null; then
        echo "[run_worker.sh] ERROR: Celery beat (PID: $BEAT_PID) exited unexpectedly!"
        kill -TERM "$CELERY_PID" 2>/dev/null || true
        kill -TERM "$HEALTH_PID" 2>/dev/null || true
        exit 1
    fi

    sleep 2
done
