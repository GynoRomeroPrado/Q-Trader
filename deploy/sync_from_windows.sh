#!/bin/bash
# Sincronizar proyecto desde Windows al IdeaPad via rsync/SSH
# Ejecutar desde Windows en Git Bash o WSL, desde el directorio del proyecto.
#
# Prerequisitos:
#   - OpenSSH instalado en Windows
#   - SSH activo en Ubuntu: sudo systemctl enable ssh
#   - rsync instalado en Ubuntu: sudo apt install rsync

set -e

UBUNTU_USER="gyno"
UBUNTU_IP="${IDEAPAD_IP:-<IP_LOCAL_DEL_IDEAPAD>}"   # Set env IDEAPAD_IP or edit here
REMOTE_DIR="/home/gyno/qtrader"

if [ "$UBUNTU_IP" = "<IP_LOCAL_DEL_IDEAPAD>" ]; then
    echo "ERROR: Set IDEAPAD_IP environment variable or edit this script."
    echo "  Usage: IDEAPAD_IP=192.168.1.XX bash deploy/sync_from_windows.sh"
    exit 1
fi

echo "Sincronizando proyecto -> $UBUNTU_USER@$UBUNTU_IP:$REMOTE_DIR"

rsync -avz --progress \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='venv/' \
    --exclude='data/*.db' \
    --exclude='data/*.duckdb' \
    --exclude='data/*.log' \
    --exclude='.env' \
    --exclude='node_modules/' \
    ./ "$UBUNTU_USER@$UBUNTU_IP:$REMOTE_DIR/"

echo ""
echo "[OK] Sync completado"
echo ""
echo "Siguiente paso en Ubuntu:"
echo "  ssh $UBUNTU_USER@$UBUNTU_IP"
echo "  cd $REMOTE_DIR && bash deploy/install_service.sh"
