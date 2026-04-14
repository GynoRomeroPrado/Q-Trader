#!/bin/bash
set -e

PROJECT_DIR="/home/gyno/qtrader"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE_FILE="deploy/qtrader-bot.service"

echo "=== QTrader — Instalacion de servicios systemd ==="

# 1. Crear virtualenv si no existe
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "[OK] Virtualenv creado en $VENV_DIR"
fi

# 2. Instalar dependencias
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" -q
echo "[OK] Dependencias instaladas"

# 3. Crear directorio de datos si no existe
mkdir -p "$PROJECT_DIR/data"
echo "[OK] Directorio data/ verificado"

# 4. Instalar servicio systemd
sudo cp "$PROJECT_DIR/$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable qtrader-bot.service
sudo systemctl start qtrader-bot.service
echo "[OK] Servicio qtrader-bot instalado y arrancado"

# 5. Verificar estado
sleep 2
echo ""
echo "=== Estado del servicio ==="
sudo systemctl status qtrader-bot.service --no-pager || true

echo ""
echo "=== Comandos utiles ==="
echo "  Ver logs en vivo:  sudo journalctl -u qtrader-bot -f"
echo "  Reiniciar bot:     sudo systemctl restart qtrader-bot"
echo "  Detener bot:       sudo systemctl stop qtrader-bot"
echo "  Ver estado:        sudo systemctl status qtrader-bot"
echo "  Dashboard:         http://$(hostname -I | awk '{print $1}'):8888"
