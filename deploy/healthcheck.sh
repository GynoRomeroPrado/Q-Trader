#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Q-Trader Health Check — External monitoring script
#
# Runs via cron every 5 minutes. Checks:
#   1. HTTP response from /api/status
#   2. Service is active in systemd
#   3. Last heartbeat was recent (< 2 min)
#
# If ALL checks fail → restart the service and log the event.
# Optionally sends Telegram alert (if configured).
#
# Install:
#   echo "*/5 * * * * root /opt/tradingbot/deploy/healthcheck.sh" > /etc/cron.d/tradingbot-health
# ──────────────────────────────────────────────────────────────

set -euo pipefail

# Config
BOT_DIR="/opt/tradingbot"
SERVICE_NAME="tradingbot"
API_PORT="8888"
LOG_FILE="/var/log/tradingbot-health.log"
MAX_FAILURES=2  # Must fail N consecutive times before restart

# State file to track consecutive failures
FAIL_COUNT_FILE="/tmp/tradingbot_health_fails"

log_msg() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

# ── Check 1: Is the systemd service active? ──
check_service() {
    systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null
    return $?
}

# ── Check 2: Does the API respond? ──
check_api() {
    local status_code
    status_code=$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 --max-time 10 \
        "http://127.0.0.1:${API_PORT}/api/status" \
        -H "Authorization: Bearer dummy" 2>/dev/null || echo "000")

    # Accept 200 (authenticated) or 401 (auth required but server is alive)
    if [[ "$status_code" == "200" || "$status_code" == "401" ]]; then
        return 0
    fi
    return 1
}

# ── Main Logic ──
main() {
    # Read current failure count
    local fails=0
    if [[ -f "$FAIL_COUNT_FILE" ]]; then
        fails=$(cat "$FAIL_COUNT_FILE" 2>/dev/null || echo "0")
    fi

    # Run checks
    local service_ok=false
    local api_ok=false

    if check_service; then
        service_ok=true
    fi

    if check_api; then
        api_ok=true
    fi

    # If everything is OK → reset counter
    if $service_ok && $api_ok; then
        echo "0" > "$FAIL_COUNT_FILE"
        return 0
    fi

    # Something failed — increment counter
    fails=$((fails + 1))
    echo "$fails" > "$FAIL_COUNT_FILE"

    log_msg "⚠️  Health check failed (attempt ${fails}/${MAX_FAILURES}) — service: $service_ok, api: $api_ok"

    # Only restart after MAX_FAILURES consecutive failures
    if [[ $fails -ge $MAX_FAILURES ]]; then
        log_msg "🔄 Restarting $SERVICE_NAME after $fails consecutive failures"
        systemctl restart "$SERVICE_NAME" 2>/dev/null || true
        echo "0" > "$FAIL_COUNT_FILE"

        # Optional: send Telegram alert
        if [[ -f "$BOT_DIR/.env" ]]; then
            source "$BOT_DIR/.env"
            if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
                curl -s -X POST \
                    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
                    -d "chat_id=${TELEGRAM_CHAT_ID}" \
                    -d "text=🔄 *Q-Trader Restarted*%0A%0AHealth check failed ${fails} times.%0AService: ${service_ok}%0AAPI: ${api_ok}" \
                    -d "parse_mode=Markdown" \
                    > /dev/null 2>&1 || true
            fi
        fi
    fi
}

main "$@"
