#!/bin/bash
set -e

# Clean up stale locks if container was previously forced to stop
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

echo "Starting Xvfb virtual frame buffer on DISPLAY=:99..."
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for Xvfb socket to appear
for i in {1..10}; do
    if [ -e /tmp/.X11-unix/X99 ]; then
        echo "Xvfb successfully started on DISPLAY=:99."
        break
    fi
    sleep 0.5
done

# Cleanup Xvfb on termination
cleanup() {
    echo "Stopping server and terminating Xvfb (PID $XVFB_PID)..."
    kill -TERM "$XVFB_PID" 2>/dev/null || true
    wait "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT SIGINT SIGTERM

echo "Starting Captcha Bypass & SERP Tracker server..."
exec python server.py "$@"
