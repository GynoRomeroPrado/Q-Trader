# Deploy QTrader en Ubuntu (IdeaPad 1-14ADA05)

## Prerequisitos Ubuntu

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv rsync
sudo systemctl enable ssh && sudo systemctl start ssh
```

## Pasos (en orden)

### 1. Obtener IP del IdeaPad

En Ubuntu:
```bash
ip addr show | grep "inet " | grep -v 127.0.0.1
```

### 2. Copiar .env al IdeaPad (solo la primera vez)

```bash
scp .env gyno@<IP_IDEAPAD>:/home/gyno/qtrader/.env
```

### 3. Sincronizar proyecto desde Windows

Desde Git Bash o WSL en el directorio del proyecto:
```bash
IDEAPAD_IP=192.168.1.XX bash deploy/sync_from_windows.sh
```

### 4. Instalar servicio en Ubuntu

```bash
ssh gyno@<IP_IDEAPAD>
cd /home/gyno/qtrader
bash deploy/install_service.sh
```

### 5. Verificar

```bash
# Logs en tiempo real
sudo journalctl -u qtrader-bot -f

# Dashboard
curl http://<IP_IDEAPAD>:8888/api/status
```

Abre el dashboard en: `http://<IP_IDEAPAD>:8888`

## Para actualizaciones futuras

```bash
# Desde Windows:
IDEAPAD_IP=192.168.1.XX bash deploy/sync_from_windows.sh

# Desde Ubuntu:
sudo systemctl restart qtrader-bot
```

## Comandos útiles

| Comando | Descripción |
|---------|-------------|
| `sudo systemctl status qtrader-bot` | Ver estado |
| `sudo journalctl -u qtrader-bot -f` | Logs en vivo |
| `sudo systemctl restart qtrader-bot` | Reiniciar |
| `sudo systemctl stop qtrader-bot` | Detener |
| `sudo systemctl disable qtrader-bot` | Desactivar autostart |

## Troubleshooting

```bash
# Si el servicio falla al arrancar:
sudo journalctl -u qtrader-bot --no-pager -n 50

# Verificar que el .env existe:
cat /home/gyno/qtrader/.env | head -5

# Verificar Python:
/home/gyno/qtrader/venv/bin/python --version

# Re-instalar dependencias:
/home/gyno/qtrader/venv/bin/pip install -r requirements.txt
```

## Configurar Alertas Telegram

1. **Crear bot**: Abre [@BotFather](https://t.me/BotFather) en Telegram → `/newbot` → copia el token
2. **Obtener chat_id**: Envía `/start` a [@userinfobot](https://t.me/userinfobot) → copia el ID
3. **Configurar en `.env`**:
   ```env
   TELEGRAM_ENABLED=true
   TELEGRAM_BOT_TOKEN=123456789:ABCdef...
   TELEGRAM_CHAT_ID=987654321
   ```
4. **Reiniciar el bot**:
   ```bash
   sudo systemctl restart qtrader-bot
   ```

**Eventos que generan alerta:**
- 🚨 Circuit Breaker activado (Oracle en pánico)
- 📉 Kill-switch de Drawdown activado
- 🧊 Racha de pérdidas alcanzada (cooldown iniciado)
- ⚡ Bot iniciado / detenido

## Configurar Alertas Telegram

1. **Crear bot**: Abre [@BotFather](https://t.me/BotFather) en Telegram -> `/newbot` -> copia el token
2. **Obtener chat_id**: Envia `/start` a [@userinfobot](https://t.me/userinfobot) -> copia el ID
3. **Configurar en `.env`**:
```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
TELEGRAM_CHAT_ID=987654321
```
4. **Reiniciar**: `sudo systemctl restart qtrader-bot`

**Alertas activas:** Circuit Breaker, Drawdown Kill-switch, Loss-streak cooldown, Bot start/stop
