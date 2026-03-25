# Cloudflare Tunnel — Persistent Dashboard Access

## Install cloudflared

```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee \
    /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
    https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | \
    sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt-get update && sudo apt-get install -y cloudflared
```

## Authenticate (one-time)

```bash
cloudflared tunnel login
```

Abre el link que aparece en la terminal, selecciona tu dominio (o crea uno gratis en `.trycloudflare.com`).

## Create Named Tunnel

```bash
cloudflared tunnel create tradingbot
cloudflared tunnel route dns tradingbot bot.tudominio.com
```

## Config File

```bash
sudo mkdir -p /etc/cloudflared
sudo tee /etc/cloudflared/config.yml << 'EOF'
tunnel: tradingbot
credentials-file: /root/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: bot.tudominio.com
    service: http://localhost:8888
  - service: http_status:404
EOF
```

## Install as systemd Service

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

## Verify

```bash
sudo systemctl status cloudflared
curl -s https://bot.tudominio.com/api/status -H "X-API-Key: TU_API_KEY"
```

## Quick Mode (sin dominio propio)

Si no tienes dominio, usa el modo rápido (URL temporal):

```bash
cloudflared tunnel --url http://localhost:8888
```

Esto genera una URL `https://xxx-xxx.trycloudflare.com` — pero se pierde al reiniciar.
Para persistir, usa el método de Named Tunnel + systemd arriba.
