#!/usr/bin/env bash
# Watchdog for crypto_live_ticker.py
# Checks if the ticker process is alive; if dead, cleans up stale Discord messages.
# Designed for no_agent cronjob mode — stdout is delivered verbatim when non-empty.

set -euo pipefail

PID_FILE="/tmp/crypto_ticker.pid"
MSG_ID_FILE="/tmp/crypto_ticker_msg_id"
WEBHOOK="${DISCORD_WEBHOOK}"
UA="User-Agent: Mozilla/5.0 (compatible; HermesBot/1.0)"

# Check if PID file exists
if [ ! -f "$PID_FILE" ]; then
    # No PID file = no ticker has ever run, nothing to do
    exit 0
fi

PID=$(cat "$PID_FILE")

# Check if process is alive
if kill -0 "$PID" 2>/dev/null; then
    # Process running — all good
    exit 0
fi

# Process is dead! Clean up stale message.
echo "[watchdog] crypto_ticker PID $PID is dead. Cleaning up..."

# Read and delete the stale message
if [ -f "$MSG_ID_FILE" ]; then
    MSG_ID=$(cat "$MSG_ID_FILE")
    if [ -n "$MSG_ID" ]; then
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
            "$WEBHOOK/messages/$MSG_ID" \
            -H "$UA")
        echo "[watchdog] DELETE msg_id=$MSG_ID → HTTP $HTTP_CODE"
    fi
fi

# Clean up stale files
rm -f "$PID_FILE" "$MSG_ID_FILE"
echo "[watchdog] Cleanup complete."
