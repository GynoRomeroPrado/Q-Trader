# ⚡ Trading Bot — Automated Crypto Trading System

Bot de trading 100% automatizado para Binance, con dashboard móvil en tiempo real.

## Stack

| Componente | Tecnología |
|---|---|
| Exchange API | CCXT Pro (WebSocket) |
| Indicadores | pandas-ta + TA-Lib |
| DB Analítica | DuckDB |
| DB Transaccional | SQLite |
| API/Dashboard | FastAPI + WebSocket |
| Gráficos | TradingView Lightweight Charts v5.1 |
| Acceso Remoto | Cloudflare Tunnel |

## Setup Rápido

```powershell
# 1. Crear entorno virtual
python -m venv .venv
.venv\Scripts\Activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar
copy .env.example .env
# Editar .env con tus API keys de Binance

# 4. Ejecutar (modo desarrollo)
python run_bot.py
```

## Flujo de Capital (Perú 🇵🇪)

### Depósito (PEN → Bot)
1. **Binance P2P** → Comprar USDT con PEN (Yape/BCP/Interbank)
2. USDT llega a tu Spot Wallet de Binance
3. El bot detecta el saldo automáticamente via `fetch_balance()`

### Retiro (Bot → PEN)
1. El bot puede enviar USDT via TRC20 (fee ~1 USDT)
2. Vender USDT en **Binance P2P** por PEN
3. Recibir en tu cuenta bancaria local

## Registrar como Servicio Windows

```powershell
# Descargar NSSM: https://nssm.cc/download
nssm install TradingBot "C:\Python312\python.exe" "c:\Users\GYNO\Apptrading\run_bot.py"
nssm set TradingBot AppDirectory "c:\Users\GYNO\Apptrading"
nssm set TradingBot Start SERVICE_AUTO_START
nssm start TradingBot
```

## Dashboard Remoto (Celular)

```bash
# Instalar Cloudflare Tunnel: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/
cloudflared tunnel --url http://localhost:8888
```

Esto genera una URL pública con HTTPS automático. Ábrela desde tu celular.

## Tests

```powershell
pip install pytest
python -m pytest tests/ -v
```

## Estructura

```
Apptrading/
├── run_bot.py              # Entry point (asyncio)
├── config/settings.py      # Settings dataclass
├── core/
│   ├── exchange_client.py  # ccxt.pro async wrapper
│   ├── balance_manager.py  # Balance + withdrawal
│   ├── strategy_base.py    # Abstract Strategy
│   ├── risk_manager.py     # Position sizing, stop-loss
│   └── trade_executor.py   # Main trading loop
├── strategies/
│   └── ema_crossover.py    # EMA(9/21) example
├── services/
│   ├── db.py               # DuckDB + SQLite
│   ├── auth.py             # JWT auth
│   └── api_server.py       # FastAPI + WebSocket
├── dashboard/
│   ├── index.html
│   ├── style.css           # Glassmorphism dark mode
│   └── app.js              # Lightweight Charts + WS
└── tests/
    ├── test_strategy.py
    └── test_risk.py
```
