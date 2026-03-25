#!/usr/bin/env bash
# ============================================================
# setup_ubuntu.sh — Ubuntu Server 24.04 LTS Provisioning Script
# Trading Bot deployment on a dedicated Mini PC (headless)
#
# Usage:
#   chmod +x deploy/setup_ubuntu.sh
#   sudo ./deploy/setup_ubuntu.sh
# ============================================================

set -euo pipefail

# --- Config ---
APP_USER="tradingbot"
APP_DIR="/opt/tradingbot"
PYTHON_VERSION="3.14"
REPO_SOURCE="$(cd "$(dirname "$0")/.." && pwd)"

echo "═══════════════════════════════════════════════════"
echo "⚡ Trading Bot — Ubuntu Server 24.04 Setup"
echo "═══════════════════════════════════════════════════"

# --- 1. System packages ---
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    software-properties-common \
    build-essential \
    libffi-dev \
    libssl-dev \
    libsqlite3-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libncursesw5-dev \
    liblzma-dev \
    tk-dev \
    uuid-dev \
    curl \
    wget \
    git

# --- 2. Python 3.14 via deadsnakes PPA ---
echo "[2/7] Installing Python ${PYTHON_VERSION}..."
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -qq
apt-get install -y -qq \
    "python${PYTHON_VERSION}" \
    "python${PYTHON_VERSION}-venv" \
    "python${PYTHON_VERSION}-dev"

# Verify
python${PYTHON_VERSION} --version || { echo "❌ Python ${PYTHON_VERSION} install failed"; exit 1; }

# --- 3. Create app user (no login shell) ---
echo "[3/7] Creating system user '${APP_USER}'..."
if ! id "${APP_USER}" &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home-dir "${APP_DIR}" "${APP_USER}"
fi

# --- 4. Deploy application ---
echo "[4/7] Deploying application to ${APP_DIR}..."
mkdir -p "${APP_DIR}"
# Copy project files (exclude .env, data/, .venv/, .git/)
rsync -a --exclude='.env' --exclude='data/' --exclude='.venv/' \
    --exclude='.git/' --exclude='__pycache__/' \
    "${REPO_SOURCE}/" "${APP_DIR}/"

# Create data directory
mkdir -p "${APP_DIR}/data"

# --- 5. Python venv + dependencies ---
echo "[5/7] Creating virtual environment and installing packages..."
python${PYTHON_VERSION} -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip -q
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

echo "    ✅ Installed packages:"
"${APP_DIR}/.venv/bin/pip" list --format=columns | grep -iE "ccxt|fastapi|uvicorn|duckdb|ta "

# --- 6. Set permissions ---
echo "[6/7] Setting file permissions..."
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
chmod 700 "${APP_DIR}/data"
# .env must be created manually with restricted permissions
if [ ! -f "${APP_DIR}/.env" ]; then
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"
    echo "    ⚠️  Created .env from template — EDIT IT with your API keys!"
fi

# --- 7. Install systemd service ---
echo "[7/7] Installing systemd service..."
cp "${APP_DIR}/deploy/tradingbot.service" /etc/systemd/system/tradingbot.service
systemctl daemon-reload
systemctl enable tradingbot.service

echo ""
echo "═══════════════════════════════════════════════════"
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit your API keys:    sudo -u ${APP_USER} nano ${APP_DIR}/.env"
echo "  2. Start the bot:         sudo systemctl start tradingbot"
echo "  3. Check status:          sudo systemctl status tradingbot"
echo "  4. View logs:             sudo journalctl -u tradingbot -f"
echo "  5. Setup tunnel:          See deploy/cloudflared.md"
echo "═══════════════════════════════════════════════════"
