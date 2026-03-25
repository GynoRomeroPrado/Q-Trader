/**
 * Trading Bot Dashboard — Client Application
 * Connects to FastAPI backend via REST + WebSocket
 * Uses TradingView Lightweight Charts v5.1 for equity curve
 */

// ============================================
// State
// ============================================

let token = localStorage.getItem("bot_token") || "";
let ws = null;
let chart = null;
let equitySeries = null;
const API = window.location.origin;

// ============================================
// Auth
// ============================================

async function handleLogin() {
    const key = document.getElementById("api-key-input").value.trim();
    const errorEl = document.getElementById("login-error");

    if (!key) {
        errorEl.textContent = "Ingresa tu API key";
        return;
    }

    try {
        const res = await fetch(`${API}/api/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_key: key }),
        });

        if (!res.ok) {
            errorEl.textContent = "API key inválida";
            return;
        }

        const data = await res.json();
        token = data.token;
        localStorage.setItem("bot_token", token);
        showDashboard();
    } catch (e) {
        errorEl.textContent = "Error de conexión";
    }
}

function showDashboard() {
    document.getElementById("login-screen").classList.add("hidden");
    document.getElementById("dashboard").classList.remove("hidden");
    initChart();
    loadAllData();
    connectWebSocket();
}

// ============================================
// API Calls
// ============================================

async function apiFetch(endpoint) {
    const res = await fetch(`${API}${endpoint}`, {
        headers: { "X-API-Key": token },
    });
    if (res.status === 401) {
        localStorage.removeItem("bot_token");
        location.reload();
        return null;
    }
    return res.json();
}

async function loadAllData() {
    try {
        const [status, pnl, trades, equity] = await Promise.all([
            apiFetch("/api/status"),
            apiFetch("/api/pnl"),
            apiFetch("/api/trades?limit=30"),
            apiFetch("/api/equity?limit=200"),
        ]);

        if (status) updateStatus(status);
        if (pnl) updatePnL(pnl);
        if (trades) updateTradesTable(trades);
        if (equity) updateEquityChart(equity);

        document.getElementById("last-update").textContent =
            new Date().toLocaleTimeString("es-PE");
    } catch (e) {
        console.error("Error loading data:", e);
    }
}

// ============================================
// UI Updates
// ============================================

function updateStatus(status) {
    const badge = document.getElementById("bot-status-badge");
    const isRunning = status.state === "running";
    badge.textContent = isRunning ? "Running" : "Offline";
    badge.className = `badge ${isRunning ? "badge-running" : "badge-offline"}`;
}

function updatePnL(pnl) {
    setMetric("total-pnl", pnl.total_pnl, true);
    setMetric("today-pnl", pnl.today_pnl, true);
    document.getElementById("total-trades").textContent = pnl.total_trades;
    document.getElementById("today-trades-count").textContent =
        `${pnl.today_trades} trades`;
}

function setMetric(id, value, isPnL = false) {
    const el = document.getElementById(id);
    const numVal = parseFloat(value) || 0;
    el.textContent = `$${numVal.toFixed(2)}`;
    if (isPnL) {
        el.className = `metric-value ${
            numVal > 0 ? "pnl-positive" : numVal < 0 ? "pnl-negative" : "pnl-neutral"
        }`;
    }
}

function updateTradesTable(trades) {
    const tbody = document.getElementById("trades-body");
    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Sin trades aún</td></tr>';
        return;
    }

    tbody.innerHTML = trades
        .map(
            (t) => `
        <tr>
            <td>${formatTime(t.timestamp)}</td>
            <td>${t.symbol}</td>
            <td class="${t.side === "buy" ? "side-buy" : "side-sell"}">
                ${t.side.toUpperCase()}
            </td>
            <td>$${parseFloat(t.price).toFixed(2)}</td>
            <td>${parseFloat(t.amount).toFixed(6)}</td>
            <td class="${t.pnl >= 0 ? "pnl-positive" : "pnl-negative"}">
                $${parseFloat(t.pnl).toFixed(2)}
            </td>
        </tr>
    `
        )
        .join("");
}

function formatTime(ts) {
    if (!ts) return "—";
    const d = new Date(ts);
    return d.toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" });
}

// ============================================
// Lightweight Charts — Equity Curve
// ============================================

function initChart() {
    const container = document.getElementById("equity-chart");
    container.innerHTML = "";

    chart = LightweightCharts.createChart(container, {
        width: container.clientWidth - 32,
        height: container.clientHeight - 32,
        layout: {
            background: { type: "solid", color: "transparent" },
            textColor: "#94a3b8",
            fontFamily: "Inter, sans-serif",
            fontSize: 12,
        },
        grid: {
            vertLines: { color: "rgba(255,255,255,0.03)" },
            horzLines: { color: "rgba(255,255,255,0.03)" },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: "rgba(255,255,255,0.08)",
        },
        timeScale: {
            borderColor: "rgba(255,255,255,0.08)",
            timeVisible: true,
        },
    });

    equitySeries = chart.addSeries(
        LightweightCharts.AreaSeries,
        {
            topColor: "rgba(59, 130, 246, 0.4)",
            bottomColor: "rgba(59, 130, 246, 0.0)",
            lineColor: "#3b82f6",
            lineWidth: 2,
        }
    );

    // Responsive resize
    const resizeObserver = new ResizeObserver(() => {
        chart.applyOptions({
            width: container.clientWidth - 32,
            height: container.clientHeight - 32,
        });
    });
    resizeObserver.observe(container);
}

function updateEquityChart(data) {
    if (!equitySeries || !data || data.length === 0) return;

    const chartData = data.map((d) => ({
        time: Math.floor(new Date(d.timestamp).getTime() / 1000),
        value: d.total,
    }));

    equitySeries.setData(chartData);
    chart.timeScale().fitContent();
}

// ============================================
// WebSocket — Real-time Updates
// ============================================

function connectWebSocket() {
    const wsUrl = `${API.replace("http", "ws")}/ws/live`;
    ws = new WebSocket(wsUrl);

    const statusEl = document.getElementById("ws-status");

    ws.onopen = () => {
        statusEl.textContent = "⬤ Conectado";
        statusEl.className = "ws-indicator connected";
        // Keepalive ping
        setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, 30000);
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleWsMessage(msg);
        } catch (e) {
            console.error("WS parse error:", e);
        }
    };

    ws.onclose = () => {
        statusEl.textContent = "⬤ Desconectado";
        statusEl.className = "ws-indicator";
        // Auto-reconnect in 5 seconds
        setTimeout(connectWebSocket, 5000);
    };

    ws.onerror = () => {
        statusEl.textContent = "⬤ Error";
        statusEl.className = "ws-indicator";
    };
}

function handleWsMessage(msg) {
    switch (msg.type) {
        case "trade":
            addTradeRow(msg.data);
            loadAllData(); // Refresh metrics
            break;
        case "balance":
            document.getElementById("current-balance").textContent =
                `$${parseFloat(msg.data.total).toFixed(2)}`;
            break;
        case "status":
            updateStatus(msg.data);
            break;
        case "pong":
            break;
        default:
            console.log("Unknown WS message:", msg);
    }

    document.getElementById("last-update").textContent =
        new Date().toLocaleTimeString("es-PE");
}

function addTradeRow(trade) {
    const tbody = document.getElementById("trades-body");
    // Remove "no trades" placeholder
    const empty = tbody.querySelector(".empty-state");
    if (empty) empty.parentElement.remove();

    const row = document.createElement("tr");
    row.className = "trade-flash";
    row.innerHTML = `
        <td>${formatTime(trade.timestamp)}</td>
        <td>${trade.symbol}</td>
        <td class="${trade.side === "buy" ? "side-buy" : "side-sell"}">
            ${trade.side.toUpperCase()}
        </td>
        <td>$${parseFloat(trade.price).toFixed(2)}</td>
        <td>${parseFloat(trade.amount).toFixed(6)}</td>
        <td class="${trade.pnl >= 0 ? "pnl-positive" : "pnl-negative"}">
            $${parseFloat(trade.pnl).toFixed(2)}
        </td>
    `;
    tbody.insertBefore(row, tbody.firstChild);

    // Keep max 30 rows
    while (tbody.children.length > 30) {
        tbody.removeChild(tbody.lastChild);
    }
}

// ============================================
// Init
// ============================================

document.addEventListener("DOMContentLoaded", () => {
    // Check for existing token
    if (token) {
        showDashboard();
    }

    // Enter key to login
    document.getElementById("api-key-input")
        .addEventListener("keydown", (e) => {
            if (e.key === "Enter") handleLogin();
        });
});
