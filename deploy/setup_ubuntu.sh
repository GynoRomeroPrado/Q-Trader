#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Q-Trader — Ubuntu Server 24.04 Deployment Script
#
# Usage:
#   sudo bash deploy/setup_ubuntu.sh
#
# What this script does:
#   1. Creates system user 'tradingbot' (no login shell)
#   2. Copies project to /opt/tradingbot
#   3. Creates Python venv and installs dependencies
#   4. Installs systemd service with Watchdog
#   5. Sets up log rotation
#   6. Installs external health check (cron)
#   7. Configures firewall (ufw)
#   8. Sets file permissions (600 on .env)
#
# Prerequisites:
#   - Ubuntu Server 24.04 LTS (minimal install)
#   - Python 3.11+ installed
#   - Internet access for pip install
#   - .env file configured with API keys
# ──────────────────────────────────────────────────────────────

set -euo pipefail

# === Configuration ===
BOT_USER="tradingbot"
BOT_DIR="/opt/tradingbot"
VENV_DIR="${BOT_DIR}/venv"
DATA_DIR="${BOT_DIR}/data"
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$DEPLOY_DIR")"

echo "═══════════════════════════════════════════════════════"
echo "⚡ Q-Trader — Ubuntu Server Deployment"
echo "═══════════════════════════════════════════════════════"

# === Step 1: System User ===
echo ""
echo "📦 Step 1: Creating system user '${BOT_USER}'..."
if id "$BOT_USER" &>/dev/null; then
    echo "   → User already exists, skipping"
else
    useradd --system --home-dir "$BOT_DIR" --shell /usr/sbin/nologin "$BOT_USER"
    echo "   → Created system user '${BOT_USER}'"
fi

# === Step 2: Copy Project ===
echo ""
echo "📂 Step 2: Deploying to ${BOT_DIR}..."
mkdir -p "$BOT_DIR"
mkdir -p "$DATA_DIR"

# Copy project files (exclude .git, __pycache__, data dir, venv)
rsync -a --exclude='.git' \
         --exclude='__pycache__' \
         --exclude='*.pyc' \
         --exclude='data/' \
         --exclude='venv/' \
         --exclude='.env' \
         "$PROJECT_DIR/" "$BOT_DIR/"

# Copy .env if it doesn't exist at destination
if [ ! -f "${BOT_DIR}/.env" ]; then
    if [ -f "${PROJECT_DIR}/.env" ]; then
        cp "${PROJECT_DIR}/.env" "${BOT_DIR}/.env"
        echo "   → .env copied from project"
    elif [ -f "${PROJECT_DIR}/.env.example" ]; then
        cp "${PROJECT_DIR}/.env.example" "${BOT_DIR}/.env"
        echo "   ⚠️  .env.example copied — EDIT IT with real credentials!"
    else
        echo "   ⚠️  No .env found! Create ${BOT_DIR}/.env manually"
    fi
fi

# === Step 3: Python Virtual Environment ===
echo ""
echo "🐍 Step 3: Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "   → Created venv at ${VENV_DIR}"
fi

"${VENV_DIR}/bin/pip" install --upgrade pip wheel setuptools -q
"${VENV_DIR}/bin/pip" install -r "${BOT_DIR}/requirements.txt" -q
echo "   → Dependencies installed"

# === Step 4: File Permissions ===
echo ""
echo "🔒 Step 4: Setting permissions..."
chown -R "${BOT_USER}:${BOT_USER}" "$BOT_DIR"
chmod 600 "${BOT_DIR}/.env"
chmod -R 750 "$DATA_DIR"
echo "   → .env: 600 | data/: 750 | owner: ${BOT_USER}"

# === Step 5: systemd Service ===
echo ""
echo "⚙️  Step 5: Installing systemd service..."
cp "${BOT_DIR}/deploy/tradingbot.service" /etc/systemd/system/tradingbot.service
systemctl daemon-reload
systemctl enable tradingbot
echo "   → Service installed and enabled"

# === Step 6: Log Rotation ===
echo ""
echo "📋 Step 6: Configuring log rotation..."
cat > /etc/logrotate.d/tradingbot << 'LOGROTATE'
/opt/tradingbot/data/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 640 tradingbot tradingbot
    postrotate
        systemctl reload tradingbot 2>/dev/null || true
    endscript
}
LOGROTATE
echo "   → /etc/logrotate.d/tradingbot created (14 days retention)"

# === Step 7: Health Check Cron ===
echo ""
echo "🩺 Step 7: Installing health check cron..."
cp "${BOT_DIR}/deploy/healthcheck.sh" /opt/tradingbot/healthcheck.sh
chmod +x /opt/tradingbot/healthcheck.sh
cat > /etc/cron.d/tradingbot-health << 'CRON'
# Q-Trader health check — runs every 5 minutes
*/5 * * * * root /opt/tradingbot/healthcheck.sh
CRON
echo "   → Health check cron installed (every 5 min)"

# === Step 8: Firewall ===
echo ""
echo "🛡️  Step 8: Configuring firewall..."
if command -v ufw &>/dev/null; then
    ufw --force enable 2>/dev/null || true
    ufw allow 22/tcp comment 'SSH' 2>/dev/null || true

    # Dashboard port (only if remote access needed)
    # ufw allow 8888/tcp comment 'Q-Trader Dashboard'

    echo "   → ufw enabled (SSH allowed, dashboard blocked by default)"
    echo "   → To allow remote dashboard: ufw allow 8888/tcp"
else
    echo "   → ufw not found, skipping firewall config"
fi

# === Step 9: Journal size limit ===
echo ""
echo "💾 Step 9: Configuring journald..."
mkdir -p /etc/systemd/journald.conf.d/
cat > /etc/systemd/journald.conf.d/tradingbot.conf << 'JOURNAL'
[Journal]
SystemMaxUse=200M
SystemMaxFileSize=50M
JOURNAL
systemctl restart systemd-journald 2>/dev/null || true
echo "   → Journal max size: 200M"

# === Done! ===
echo ""
echo "═══════════════════════════════════════════════════════"
echo "✅ Deployment complete!"
echo ""
echo "   Next steps:"
echo "   1. Edit /opt/tradingbot/.env with your API keys"
echo "   2. Start the bot:  sudo systemctl start tradingbot"
echo "   3. Check status:   sudo systemctl status tradingbot"
echo "   4. View logs:      sudo journalctl -u tradingbot -f"
echo "   5. Stop the bot:   sudo systemctl stop tradingbot"
echo ""
echo "   Remote access (optional):"
echo "   - Cloudflare Tunnel: see deploy/cloudflared.md"
echo "   - Direct: sudo ufw allow 8888/tcp"
echo "═══════════════════════════════════════════════════════"
