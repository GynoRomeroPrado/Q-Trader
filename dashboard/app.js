/**
 * Q-Trader Command Center — Client Application v3
 * Connects to FastAPI backend via REST + WebSocket
 * Real-time audit console, oracle status, and trading controls
 */

// ============================================
// State
// ============================================

let token = localStorage.getItem("bot_token") || "";
let ws = null;
let chart = null;
let equitySeries = null;
let logPollTimer = null;
let dataPollTimer = null;
let lastLogId = 0;
const API = "http://127.0.0.1:8888"; // Hardcode standard port for PyWebView
const MAX_CONSOLE_LINES = 200;
const POLL_MS = 10000;
const LOG_POLL_MS = 3000;

// Domain-scoped polling intervals
const POLL_INTERVALS = { crypto: null, stocks: null, sports: null, logs: null, oracle: null };

// ============================================
// Domain System — Multi-Vertical Architecture
// ============================================

const DOMAINS = {
    CRYPTO: 'crypto',
    STOCKS: 'stocks',
    SPORTS: 'sports',
};

let currentDomain = DOMAINS.CRYPTO;

// Screen IDs per domain (maps to HTML div ids for Stitch MCP)
const SCREENS = {
    [DOMAINS.CRYPTO]: {
        main: 'crypto_main',       // screen-dashboard wraps this
        trades: 'crypto_trades',
        risk: 'crypto_risk',
    },
    [DOMAINS.STOCKS]: {
        main: 'stocks_main',
    },
    [DOMAINS.SPORTS]: {
        main: 'sports_main',
    },
};

// Domain configuration — endpoints, metrics, labels
const DASHBOARD_CONFIG = {
    [DOMAINS.CRYPTO]: {
        label: 'Criptomonedas',
        icon: '₿',
        accentVar: '--accent-cyan',
        endpoints: {
            status:      '/api/status',
            pnl:         '/api/pnl',
            trades:      '/api/trades?limit=30',
            equity:      '/api/equity?limit=200',
            balance:     '/api/paper/balance',
            oracle:      '/api/oracle',
            performance: '/api/performance',
        },
        metrics: ['current-balance', 'unrealized-pnl', 'oracle-status', 'total-trades', 'win-rate', 'max-drawdown'],
    },
    [DOMAINS.STOCKS]: {
        label: 'Acciones',
        icon: '📊',
        accentVar: '--accent-blue',
        endpoints: {
            // TODO: connect to stock broker API
            // status:      '/api/stocks/status',
            // performance: '/api/stocks/performance',
        },
        metrics: [],
        placeholder: true,
    },
    [DOMAINS.SPORTS]: {
        label: 'Apuestas Deportivas',
        icon: '🏆',
        accentVar: '--accent-purple',
        endpoints: {
            // TODO: connect to sports data API
            // performance: '/api/sports/performance',
        },
        metrics: [],
        placeholder: true,
    },
};

// ============================================
// Domain Switching
// ============================================

function switchDomain(domain) {
    if (!DASHBOARD_CONFIG[domain]) return;
    currentDomain = domain;

    // Update tab bar
    document.querySelectorAll('.domain-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.domain === domain);
    });

    // ── Clear domain-specific polling (keep logs + oracle) ──
    ['crypto', 'stocks', 'sports'].forEach(d => {
        if (d !== domain && POLL_INTERVALS[d] !== null) {
            clearInterval(POLL_INTERVALS[d]);
            POLL_INTERVALS[d] = null;
        }
    });

    // Show/hide domain screens
    const cryptoScreens = document.getElementById('screen-dashboard');
    const stocksScreen = document.getElementById('stocks_main');
    const sportsScreen = document.getElementById('sports_main');
    const sidebar = document.querySelector('.sidebar');

    // Hide all domain screens
    [cryptoScreens, stocksScreen, sportsScreen].forEach(el => {
        if (el) el.style.display = 'none';
    });

    // Show sidebar for all domains (Oracle, Config accessible everywhere)
    if (sidebar) sidebar.style.display = '';

    // Show active domain
    if (domain === DOMAINS.CRYPTO) {
        if (cryptoScreens) { cryptoScreens.style.display = ''; cryptoScreens.classList.add('active'); }
    } else if (domain === DOMAINS.STOCKS) {
        if (stocksScreen) { stocksScreen.style.display = 'block'; stocksScreen.classList.add('active'); }
    } else if (domain === DOMAINS.SPORTS) {
        if (sportsScreen) { sportsScreen.style.display = 'block'; sportsScreen.classList.add('active'); }
    }

    // Load data + start polling for active domain
    loadDataForDomain(domain);
    _startDomainPolling(domain);

    // Dev verification
    _verifyDomainSwitch(domain);
}

function _startDomainPolling(domain) {
    if (domain === DOMAINS.CRYPTO && !POLL_INTERVALS.crypto) {
        POLL_INTERVALS.crypto = setInterval(loadAllData, POLL_MS);
    } else if (domain === DOMAINS.STOCKS && !POLL_INTERVALS.stocks) {
        POLL_INTERVALS.stocks = setInterval(() => loadDataForDomain(DOMAINS.STOCKS), POLL_MS);
    } else if (domain === DOMAINS.SPORTS && !POLL_INTERVALS.sports) {
        POLL_INTERVALS.sports = setInterval(() => loadDataForDomain(DOMAINS.SPORTS), POLL_MS);
    }
}

function renderDomain(domain) {
    switchDomain(domain);
}

async function loadDataForDomain(domain) {
    fetchOracleStatus(); // global — always fetch across domains
    switch (domain) {
        case DOMAINS.CRYPTO:
            await loadAllData();
            break;
        case DOMAINS.STOCKS:
            await Promise.all([
                fetchStocksPerformance(),
                fetchStocksTrades(),
                fetchStocksPnl(),
                fetchStocksBotStatus(),
                fetchStocksConfig(),
                fetchStocksRadar(),
            ]);
            break;
        case DOMAINS.SPORTS:
            await fetchSportsPerformance();
            break;
    }
}

// ============================================
// Oracle Status — Global Badge
// ============================================

async function fetchOracleStatus() {
    const data = await apiFetch('/api/oracle');
    if (data) renderOracleBadge(data);
}

function renderOracleBadge(data) {
    const indicator = document.getElementById('oracle-indicator');
    const label = document.getElementById('oracle-label');
    const modelLabel = document.getElementById('oracle-model-label');
    if (!indicator || !label) return;

    // Remove previous depth classes
    indicator.classList.remove('oracle-normal', 'oracle-elevated', 'oracle-critical');

    if (!data.enabled) {
        indicator.style.color = '#8b949e';
        label.textContent = 'Oracle OFF';
        label.style.color = '#8b949e';
        if (modelLabel) modelLabel.textContent = '';
    } else if (data.market_panic) {
        indicator.classList.add('oracle-critical');
        label.textContent = 'PANIC';
        label.style.color = '#f85149';
        if (modelLabel) modelLabel.textContent = '';
    } else {
        // Color by analysis_depth
        const depth = data.analysis_depth || 'normal';
        const depthClass = `oracle-${depth}`;
        indicator.classList.add(depthClass);

        const depthLabels = { normal: 'Oracle ON', elevated: 'ELEVATED', critical: 'DEEP' };
        label.textContent = depthLabels[depth] || 'Oracle ON';
        label.style.color = depth === 'elevated' ? '#d29922' : depth === 'critical' ? '#f85149' : '#3fb8af';
    }

    // Model sub-label
    if (modelLabel && data.enabled) {
        const model = (data.model_used_last || 'flash').charAt(0).toUpperCase() + (data.model_used_last || 'flash').slice(1);
        modelLabel.textContent = model;
        modelLabel.style.color = data.model_used_last === 'pro' ? '#d29922' : '#8b949e';
    }
}

// ============================================
// Future Endpoint Hooks (Stocks & Sports)
// ============================================

/** Fetch stocks performance from backend and render the screen */
async function fetchStocksPerformance() {
    const [perf, status] = await Promise.all([
        apiFetch('/api/stocks/performance'),
        apiFetch('/api/stocks/status'),
    ]);

    if (status) renderStocksStatus(status);
    if (perf) renderStocksTable(perf);

    if (!perf && !status) {
        const el = document.getElementById('stocks-error');
        if (el) { el.textContent = 'No se pudo cargar información de acciones'; el.style.display = 'block'; }
    }
}

function renderStocksStatus(s) {
    const el = (id) => document.getElementById(id);
    const setVal = (id, val) => { const e = el(id); if (e) e.textContent = val; };

    setVal('stocks-portfolio-val', `$${s.total_pnl.toFixed(2)}`);
    setVal('stocks-total-trades', s.total_trades);
    setVal('stocks-win-rate', `${(s.win_rate * 100).toFixed(1)}%`);
    setVal('stocks-max-dd', `${(s.max_drawdown * 100).toFixed(1)}%`);
    setVal('stocks-state', s.state?.toUpperCase() || '—');

    // Color win rate
    const wrEl = el('stocks-win-rate');
    if (wrEl) wrEl.className = `metric-value ${s.win_rate >= 0.5 ? 'pnl-positive' : 'pnl-negative'}`;

    // Color PnL
    const pnlEl = el('stocks-portfolio-val');
    if (pnlEl) pnlEl.className = `metric-value ${s.total_pnl > 0 ? 'pnl-positive' : s.total_pnl < 0 ? 'pnl-negative' : 'pnl-neutral'}`;

    // TAREA 4 — Alpaca balance bar
    const fmt = (v) => v != null ? '$' + Number(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}) : '—';
    const ab = s.alpaca_balance;
    const setSkl = (id, val) => {
        const e = el(id);
        if (!e) return;
        e.textContent = val;
        e.classList.remove('skeleton');
    };
    if (ab) {
        setSkl('alpaca-cash-val', fmt(ab.cash));
        setSkl('alpaca-equity-val', fmt(ab.equity));
        setSkl('alpaca-bp-val', fmt(ab.buying_power));
        const modeBadge = el('alpaca-mode-badge');
        if (modeBadge) { modeBadge.textContent = (ab.mode || 'paper').toUpperCase(); }
    }

    // TAREA 3 — sync domain toggle button
    if (s.domain_status) _updateDomainToggleBtn(s.domain_status === 'running');
}

function renderStocksTable(symbols) {
    const tbody = document.getElementById('stocks-tbody');
    if (!tbody) return;

    if (!symbols || symbols.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">Sin datos de acciones</td></tr>';
        return;
    }

    tbody.innerHTML = symbols.map(s => {
        const pnlClass = s.total_pnl > 0 ? 'pnl-positive' : s.total_pnl < 0 ? 'pnl-negative' : 'pnl-neutral';
        const dayClass = (s.day_change_pct || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';
        const daySign = (s.day_change_pct || 0) >= 0 ? '+' : '';
        return `<tr>
            <td style="font-weight:600; color:var(--text-primary)">${s.symbol}</td>
            <td>${s.last_price != null ? '$' + s.last_price.toFixed(2) : '—'}</td>
            <td class="${dayClass}">${s.day_change_pct != null ? daySign + s.day_change_pct.toFixed(2) + '%' : '—'}</td>
            <td>${s.total_trades}</td>
            <td class="${pnlClass}">${s.total_pnl >= 0 ? '+' : ''}$${s.total_pnl.toFixed(2)}</td>
            <td>${(s.win_rate * 100).toFixed(1)}%</td>
            <td>${(s.max_drawdown * 100).toFixed(1)}%</td>
        </tr>`;
    }).join('');
}

/** TODO: Connect to sports data/odds API */
async function fetchSportsPerformance() {
    // Placeholder — returns dummy data until betting model integration
    return {
        bankroll: null,
        roi: null,
        bets_placed: 0,
        hit_rate: null,
    };
}

// ============================================
// Stocks — Trades + PnL (Observability)
// ============================================

async function fetchStocksTrades(limit = 50) {
    const data = await apiFetch(`/api/stocks/trades?limit=${limit}`);
    if (data) renderStocksTradesTable(data);
}

async function fetchStocksPnl() {
    const data = await apiFetch('/api/stocks/pnl');
    if (data) renderStocksPnlSummary(data);
}

function renderStocksTradesTable(trades) {
    const tbody = document.getElementById('stocks-trades-tbody');
    if (!tbody) return;

    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">Sin trades de acciones aún</td></tr>';
        return;
    }

    tbody.innerHTML = trades.map(t => {
        const pnl = t.pnl || 0;
        const pnlClass = pnl > 0 ? 'pnl-positive' : pnl < 0 ? 'pnl-negative' : 'pnl-neutral';
        const sideClass = t.side === 'buy' ? 'pnl-positive' : 'pnl-negative';
        const ts = t.timestamp ? t.timestamp.replace('T', ' ').slice(0, 19) : '—';
        return `<tr>
            <td style="font-size:12px; opacity:0.8">${ts}</td>
            <td style="font-weight:600; color:var(--text-primary)">${t.symbol}</td>
            <td class="${sideClass}" style="font-weight:600; text-transform:uppercase">${t.side}</td>
            <td>$${Number(t.price).toFixed(2)}</td>
            <td>${Number(t.qty).toFixed(2)}</td>
            <td class="${pnlClass}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
            <td style="font-size:11px; opacity:0.7; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">${t.reason || '—'}</td>
        </tr>`;
    }).join('');
}

function renderStocksPnlSummary(s) {
    const setVal = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
    const setClass = (id, cls) => { const e = document.getElementById(id); if (e) e.className = `metric-value ${cls}`; };

    const pnl = s.total_pnl || 0;
    const wr = s.win_rate || 0;
    const todayPnl = s.today_pnl || 0;

    setVal('stocks-bot-pnl', `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`);
    setClass('stocks-bot-pnl', pnl > 0 ? 'pnl-positive' : pnl < 0 ? 'pnl-negative' : 'pnl-neutral');

    setVal('stocks-bot-trades', s.total_trades || 0);

    setVal('stocks-bot-wr', `${(wr * 100).toFixed(1)}%`);
    setClass('stocks-bot-wr', wr >= 0.5 ? 'pnl-positive' : 'pnl-negative');

    setVal('stocks-bot-today', `${todayPnl >= 0 ? '+' : ''}$${todayPnl.toFixed(2)}`);
    setClass('stocks-bot-today', todayPnl > 0 ? 'pnl-positive' : todayPnl < 0 ? 'pnl-negative' : 'pnl-neutral');

    setVal('stocks-bot-today-count', `${s.today_trades || 0} trades hoy`);
}

// ============================================
// Stocks — Bot Controls (Status + Commands)
// ============================================

async function fetchStocksBotStatus() {
    const data = await apiFetch('/api/stocks/bot/status');
    if (data) renderStocksBotStatus(data);
}

async function sendStocksBotCommand(action) {
    const data = await apiFetch(`/api/stocks/bot/${action}`, 'POST');
    if (data) {
        await fetchStocksBotStatus();
    }
}

// TAREA 3 — Start/Stop Stocks domain from dashboard
async function toggleStocksDomain() {
    const btn = document.getElementById('stocks-domain-toggle-btn');
    if (!btn) return;
    const isRunning = btn.dataset.running === 'true';
    const action = isRunning ? 'stop' : 'start';
    btn.disabled = true;
    btn.textContent = '...';
    try {
        await apiFetch(`/api/domain/${action}`, 'POST', { domain: 'stocks' });
        showTradeToast(isRunning ? '⏹ Stocks Bot detenido' : '✅ Stocks Bot activado');
        // Wait 2s then refresh status
        await new Promise(r => setTimeout(r, 2000));
        const domStatus = await apiFetch('/api/domain/status');
        if (domStatus) _updateDomainToggleBtn(domStatus.stocks === 'running');
        await fetchStocksBotStatus();
    } finally {
        btn.disabled = false;
    }
}

function _updateDomainToggleBtn(isRunning) {
    const btn = document.getElementById('stocks-domain-toggle-btn');
    if (!btn) return;
    btn.dataset.running = isRunning ? 'true' : 'false';
    btn.textContent = isRunning ? '⏹ Detener Stocks Bot' : '▶ Activar Stocks Bot';
    btn.style.background = isRunning ? '#e74c3c' : '#2ecc71';
}


function renderStocksBotStatus(s) {
    const badge = document.getElementById('stocks-bot-state-badge');
    const lastCycle = document.getElementById('stocks-bot-last-cycle');
    const lastError = document.getElementById('stocks-bot-last-error');

    if (badge) {
        const state = s.state || 'offline';
        badge.textContent = state.toUpperCase();
        badge.className = 'badge ' + ({
            running: 'badge-running',
            paused: 'badge-paused',
            stopped: 'badge-stopped',
            offline: 'badge-paper',
        }[state] || 'badge-paper');
    }

    if (lastCycle && s.last_cycle_ts) {
        const ts = s.last_cycle_ts.replace('T', ' ').slice(0, 19);
        lastCycle.textContent = `Último ciclo: ${ts}`;
    } else if (lastCycle) {
        lastCycle.textContent = '';
    }

    if (lastError) {
        lastError.textContent = s.last_error ? `⚠ ${s.last_error}` : '';
        lastError.title = s.last_error || '';
    }

    // Toggle button visibility
    const pauseBtn = document.getElementById('stocks_pause_btn');
    const resumeBtn = document.getElementById('stocks_resume_btn');
    const panicBtn = document.getElementById('stocks_panic_btn');
    const isRunning = s.state === 'running';
    const isPaused = s.state === 'paused';

    if (pauseBtn) pauseBtn.style.display = isRunning ? '' : 'none';
    if (resumeBtn) resumeBtn.style.display = isPaused ? '' : 'none';
    if (panicBtn) panicBtn.style.display = (isRunning || isPaused) ? '' : 'none';
}

// Wire up bot control buttons on load
document.addEventListener('DOMContentLoaded', () => {
    const pauseBtn = document.getElementById('stocks_pause_btn');
    const resumeBtn = document.getElementById('stocks_resume_btn');
    const panicBtn = document.getElementById('stocks_panic_btn');

    if (pauseBtn) pauseBtn.addEventListener('click', () => sendStocksBotCommand('pause'));
    if (resumeBtn) resumeBtn.addEventListener('click', () => sendStocksBotCommand('resume'));
    if (panicBtn) panicBtn.addEventListener('click', () => {
        if (confirm('¿Seguro que deseas ejecutar PANIC STOP? Se cerrarán todas las posiciones.')) {
            sendStocksBotCommand('panic');
        }
    });
});

// ============================================
// Stocks — Configuration (Strategy + Risk)
// ============================================

async function fetchStocksConfig() {
    const data = await apiFetch('/api/stocks/config');
    if (data) populateStocksConfigForm(data);
}

async function saveStocksConfig(cfg) {
    return apiFetch('/api/stocks/config', 'PUT', cfg);
}

function populateStocksConfigForm(cfg) {
    const set = (id, val) => { const e = document.getElementById(id); if (e && val != null) e.value = val; };
    set('stocks_watchlist_input', cfg.watchlist);
    set('stocks_ma_fast_input', cfg.ma_fast_window);
    set('stocks_ma_slow_input', cfg.ma_slow_window);
    set('stocks_margin_input', cfg.signal_margin);
    set('stocks_default_qty_input', cfg.default_qty);
    set('stocks_max_qty_input', cfg.max_position_qty);
    set('stocks_max_daily_input', cfg.max_daily_trades);
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('stocks_config_form');
    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const msg = document.getElementById('stocks_config_status_msg');

        const cfg = {
            watchlist: document.getElementById('stocks_watchlist_input')?.value || '',
            ma_fast_window: parseInt(document.getElementById('stocks_ma_fast_input')?.value) || 5,
            ma_slow_window: parseInt(document.getElementById('stocks_ma_slow_input')?.value) || 20,
            signal_margin: parseFloat(document.getElementById('stocks_margin_input')?.value) || 0.002,
            default_qty: parseFloat(document.getElementById('stocks_default_qty_input')?.value) || 1.0,
            max_position_qty: parseFloat(document.getElementById('stocks_max_qty_input')?.value) || 10.0,
            max_daily_trades: parseInt(document.getElementById('stocks_max_daily_input')?.value) || 50,
        };

        if (msg) { msg.textContent = 'Guardando...'; msg.style.color = 'var(--text-secondary)'; }

        const result = await saveStocksConfig(cfg);
        if (result && result.ok) {
            if (msg) { msg.textContent = '✅ Configuración guardada'; msg.style.color = '#2ecc71'; }
            if (result.config) populateStocksConfigForm(result.config);
            setTimeout(() => { if (msg) msg.textContent = ''; }, 3000);
        } else {
            const detail = result?.detail;
            const errText = Array.isArray(detail) ? detail.join(', ') : (detail || 'Error al guardar');
            if (msg) { msg.textContent = `❌ ${errText}`; msg.style.color = '#e74c3c'; }
        }
    });
});

// ============================================
// Alpha Radar X — AI Forecast
// ============================================

async function fetchAiForecast(domain, symbols, timeframe) {
    return apiFetch('/api/ai/forecast', 'POST', { domain, symbols, timeframe });
}

async function fetchCryptoRadar() {
    const symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT'];
    const data = await fetchAiForecast('crypto', symbols, '1h');
    if (data) renderRadar('crypto_radar', data, '--accent-cyan');
}

async function fetchStocksRadar() {
    const symbols = ['AAPL', 'MSFT', 'TSLA', 'NVDA', 'AMZN'];
    const data = await fetchAiForecast('stocks', symbols, '1d');
    if (data) renderRadar('stocks_radar', data, '--accent-blue');
}

function renderRadar(containerId, forecasts, accentVar) {
    const section = document.getElementById(containerId);
    if (!section) return;

    const top = forecasts.slice(0, 2);
    const accent = getComputedStyle(document.documentElement).getPropertyValue(accentVar).trim();

    const trendLabel = (v) => v > 0.3 ? '🟢 Alcista' : v < -0.3 ? '🔴 Bajista' : '🟡 Neutral';
    const trendClass = (v) => v > 0.3 ? 'pnl-positive' : v < -0.3 ? 'pnl-negative' : 'pnl-neutral';
    const pct = (v) => (v * 100).toFixed(0) + '%';
    const barWidth = (v) => Math.abs(v) * 100;

    let html = `<div class="radar-highlights">`;
    top.forEach(f => {
        html += `
        <div class="radar-highlight-card">
            <div class="radar-symbol">${f.symbol}</div>
            <div class="radar-trend ${trendClass(f.trend_score)}">${trendLabel(f.trend_score)}</div>
            <div class="radar-bars">
                <div class="radar-bar-row"><span class="radar-bar-label">Trend</span><div class="radar-bar-track"><div class="radar-bar-fill" style="width:${barWidth(f.trend_score)}%;background:${f.trend_score > 0 ? 'var(--pnl-positive)' : 'var(--pnl-negative)'}"></div></div><span class="radar-bar-val">${(f.trend_score > 0 ? '+' : '') + f.trend_score.toFixed(2)}</span></div>
                <div class="radar-bar-row"><span class="radar-bar-label">Vol</span><div class="radar-bar-track"><div class="radar-bar-fill" style="width:${f.volatility_score * 100}%;background:var(--accent-amber)"></div></div><span class="radar-bar-val">${pct(f.volatility_score)}</span></div>
                <div class="radar-bar-row"><span class="radar-bar-label">Conf</span><div class="radar-bar-track"><div class="radar-bar-fill" style="width:${f.confidence * 100}%;background:${accent}"></div></div><span class="radar-bar-val">${pct(f.confidence)}</span></div>
            </div>
            <div class="radar-horizon">${f.timeframe} · ${f.forecast_horizon} velas</div>
        </div>`;
    });
    html += `</div>`;

    html += `
    <div class="table-wrapper" style="margin-top:12px;">
        <table>
            <thead><tr><th>Symbol</th><th>Trend</th><th>Volatility</th><th>Confidence</th><th>TF</th><th>Horizonte</th></tr></thead>
            <tbody>${forecasts.map(f => `
                <tr>
                    <td style="font-weight:600;color:var(--text-primary)">${f.symbol}</td>
                    <td class="${trendClass(f.trend_score)}">${(f.trend_score > 0 ? '+' : '') + f.trend_score.toFixed(3)}</td>
                    <td>${pct(f.volatility_score)}</td>
                    <td>${pct(f.confidence)}</td>
                    <td>${f.timeframe}</td>
                    <td>${f.forecast_horizon} velas</td>
                </tr>`).join('')}
            </tbody>
        </table>
    </div>`;

    section.innerHTML = `<div class="section-header"><h2>📡 Alpha Radar X</h2><span class="badge badge-paper" style="font-size:9px">AI DUMMY</span></div>` + html;
}

// ============================================
// Dev Verification (console.assert in dev mode)
// ============================================

function _verifyDomainSwitch(domain) {
    if (typeof console.assert !== 'function') return;

    console.assert(
        Object.values(DOMAINS).includes(domain),
        `[Q-Trader] Invalid domain: ${domain}`
    );
    console.assert(
        currentDomain === domain,
        `[Q-Trader] currentDomain not updated: expected ${domain}, got ${currentDomain}`
    );

    // Verify correct screen is visible
    const screenMap = {
        crypto: 'screen-dashboard',
        stocks: 'stocks_main',
        sports: 'sports_main',
    };
    for (const [d, id] of Object.entries(screenMap)) {
        const el = document.getElementById(id);
        if (!el) continue;
        const isVisible = el.style.display !== 'none';
        if (d === domain) {
            console.assert(isVisible, `[Q-Trader] ${id} should be visible for domain ${domain}`);
        } else {
            console.assert(!isVisible, `[Q-Trader] ${id} should be hidden for domain ${domain}`);
        }
    }
    console.log(`[Q-Trader] ✓ Domain switched to: ${domain}`);
}

// ============================================
// Init & Screen Routing
// ============================================

async function checkAppStatus() {
    try {
        const res = await fetch(`${API}/api/config/status`);
        if (!res.ok) throw new Error("API error");
        const data = await res.json();
        
        document.getElementById("lbl-username").textContent = data.username || "Trader";

        if (!data.configured) {
            // Show setup wizard
            document.getElementById("setup-screen").classList.remove("hidden");
            document.getElementById("desktop-app").classList.add("hidden");
            document.getElementById("api-keys-section").classList.remove("hidden");
            document.getElementById("btn-save-cfg").classList.remove("hidden");
            document.getElementById("login-only-section").classList.add("hidden");
            document.getElementById("btn-login-cfg").classList.add("hidden");
        } else {
            // Ask for Dashboard Login
            document.getElementById("setup-screen").classList.remove("hidden");
            document.getElementById("api-keys-section").classList.add("hidden");
            document.getElementById("btn-save-cfg").classList.add("hidden");
            document.getElementById("login-only-section").classList.remove("hidden");
            document.getElementById("btn-login-cfg").classList.remove("hidden");
            
            // Auto login if token exists
            if (token) {
                // Verify token using a quick API call
                const test = await fetch(`${API}/api/status`, { headers: {"X-API-Key": token} });
                if (test.ok) {
                    showDashboard();
                } else {
                    token = "";
                    localStorage.removeItem("bot_token");
                }
            }
        }
    } catch(e) {
        // App is likely waking up
        setTimeout(checkAppStatus, 2000);
    }
}

async function saveConfiguration() {
    const errorEl = document.getElementById("setup-error");
    const payload = {
        username: document.getElementById("cfg-username").value.trim(),
        dashboard_api_key: document.getElementById("cfg-dashkey").value.trim(),
        binance_api_key: document.getElementById("cfg-binkey").value.trim(),
        binance_secret: document.getElementById("cfg-binsec").value.trim(),
        gemini_api_key: document.getElementById("cfg-gemkey").value.trim(),
    };

    try {
        const res = await fetch(`${API}/api/config`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        if (res.ok && data.status === "success") {
            errorEl.style.color = "var(--pnl-positive)";
            errorEl.textContent = data.message;
            setTimeout(() => location.reload(), 3000);
        } else {
            errorEl.style.color = "var(--pnl-negative)";
            errorEl.textContent = data.error || data.detail || "Error de validación.";
        }
    } catch(e) {
        errorEl.textContent = "Error al comunicarse con el Engine HFT.";
    }
}

async function handleLogin() {
    const key = document.getElementById("login-apipw").value.trim();
    const errorEl = document.getElementById("setup-error");

    if (!key) { errorEl.textContent = "Ingresa tu Access Key"; return; }

    try {
        const res = await fetch(`${API}/api/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_key: key }),
        });
        if (!res.ok) { errorEl.textContent = "API key inválida"; return; }
        const data = await res.json();
        token = data.token;
        localStorage.setItem("bot_token", token);
        showDashboard();
    } catch (e) {
        errorEl.textContent = "Error de conexión";
    }
}

async function startDemo() {
    try {
        const res = await fetch(`${API}/api/demo`, { method: "POST" });
        if (res.ok) {
            const data = await res.json();
            token = data.token;
            localStorage.setItem("bot_token", token);
            showDashboard();
        }
    } catch(e) {
        document.getElementById("setup-error").textContent = "Error de conexión local";
    }
}

function showDashboard() {
    document.getElementById("setup-screen").classList.add("hidden");
    document.getElementById("desktop-app").classList.remove("hidden");
    
    // Reset view to main dashboard
    switchScreen('screen-dashboard');
    
    initChart();
    loadAllData();
    refreshLogs();
    fetchOracleStatus();
    connectWebSocket();
    // Domain-scoped data polling + global logs/oracle polling
    _startDomainPolling(currentDomain);
    if (!POLL_INTERVALS.logs) POLL_INTERVALS.logs = setInterval(refreshLogs, LOG_POLL_MS);
    if (!POLL_INTERVALS.oracle) POLL_INTERVALS.oracle = setInterval(fetchOracleStatus, POLL_MS);
}

function switchScreen(screenId) {
    // Hide all screens
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    // Deactivate all nav buttons
    document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
    
    // Show active screen
    document.getElementById(screenId).classList.add('active');
    
    // Find button that activated it and mark it
    const selector = `.nav-btn[onclick="switchScreen('${screenId}')"]`;
    const btn = document.querySelector(selector);
    if(btn) btn.classList.add('active');
}

// ============================================
// API Calls
// ============================================

async function apiFetch(endpoint, method = "GET", body = null) {
    const opts = {
        method,
        headers: { "X-API-Key": token },
    };
    if (body) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
    }
    try {
        const res = await fetch(`${API}${endpoint}`, opts);
        if (res.status === 401) {
            localStorage.removeItem("bot_token");
            location.reload();
            return null;
        }
        return await res.json();
    } catch (e) {
        console.error(`API error (${endpoint}):`, e);
        return null;
    }
}

async function loadAllData() {
    const [status, pnl, trades, equity, paperBal, oracle, perf] = await Promise.all([
        apiFetch("/api/status"),
        apiFetch("/api/pnl"),
        apiFetch("/api/trades?limit=30"),
        apiFetch("/api/equity?limit=200"),
        apiFetch("/api/paper/balance"),
        apiFetch("/api/oracle"),
        apiFetch("/api/performance"),
    ]);

    if (status) updateStatus(status);
    if (pnl) updatePnL(pnl);
    if (trades) updateTradesTable(trades);
    if (equity) updateEquityChart(equity);
    if (paperBal) updatePaperBalance(paperBal);
    if (oracle) updateOracle(oracle);
    if (perf) updatePerformance(perf);

    // Alpha Radar (non-blocking)
    fetchCryptoRadar();

    document.getElementById("last-update").textContent =
        new Date().toLocaleTimeString("es-PE");
}

// ============================================
// UI Updates — Metrics
// ============================================

function updateStatus(status) {
    const badge = document.getElementById("bot-status-badge");
    const isRunning = status.state === "running";
    badge.textContent = isRunning ? "Running" : status.state || "Offline";
    badge.className = `badge ${isRunning ? "badge-running" : "badge-offline"}`;

    // Paper mode badge
    const paperBadge = document.getElementById("paper-badge");
    if (status.paper_mode) {
        paperBadge.classList.remove("hidden");
    } else {
        paperBadge.classList.add("hidden");
    }

    // Engine state in control panel
    const engineEl = document.getElementById("engine-state");
    engineEl.textContent = status.state || "—";

    // Market hours badge
    const marketBadge = document.getElementById("market-hours-badge");
    if (marketBadge && status.market) {
        const m = status.market;
        if (m.is_open) {
            marketBadge.innerHTML = '<span style="color:#3fb8af;">&#11044;</span> Mercado abierto';
            marketBadge.title = m.next_event || 'Closes at 16:00 ET';
            marketBadge.className = 'badge badge-running';
        } else {
            marketBadge.innerHTML = '<span style="color:#f85149;">&#11044;</span> Mercado cerrado';
            marketBadge.title = m.next_event || 'Opens next business day';
            marketBadge.className = 'badge badge-stopped';
        }
    }

    // Active domain badge (B3) — includes market type
    const domainBadge = document.getElementById("active-domain-badge");
    if (domainBadge) {
        const domain = (status.domain || 'crypto').toUpperCase();
        const mtype = (status.market_type || 'spot').toUpperCase();
        domainBadge.textContent = `${domain} · ${mtype}`;
    }
}

function updatePnL(pnl) {
    document.getElementById("total-trades").textContent = pnl.total_trades || 0;
    document.getElementById("today-trades-count").textContent =
        `${pnl.today_trades || 0} hoy`;
}

function updatePerformance(perf) {
    const wr = (perf.win_rate * 100).toFixed(1);
    const dd = (perf.max_drawdown * 100).toFixed(1);
    const wrEl = document.getElementById("win-rate");
    const ddEl = document.getElementById("max-drawdown");
    wrEl.textContent = `${wr}%`;
    wrEl.className = `metric-value ${perf.win_rate >= 0.5 ? 'pnl-positive' : 'pnl-negative'}`;
    ddEl.textContent = `${dd}%`;
    ddEl.className = `metric-value ${perf.max_drawdown > 0.1 ? 'pnl-negative' : 'pnl-positive'}`;
}

function updatePaperBalance(balances) {
    const usdt = balances.USDT || balances.USD || {};
    const free = usdt.free || 0;
    const el = document.getElementById("current-balance");
    el.textContent = `$${free.toFixed(2)}`;

    // Calculate unrealized PnL (assume initial 1000)
    const initial = 1000;
    const pnlVal = free - initial;
    const pnlPct = initial > 0 ? ((pnlVal / initial) * 100) : 0;

    const pnlEl = document.getElementById("unrealized-pnl");
    pnlEl.textContent = `${pnlVal >= 0 ? '+' : ''}$${pnlVal.toFixed(2)}`;
    pnlEl.className = `metric-value ${pnlVal > 0 ? 'pnl-positive' : pnlVal < 0 ? 'pnl-negative' : 'pnl-neutral'}`;

    document.getElementById("pnl-percent").textContent =
        `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
}

function updateOracle(oracle) {
    const statusEl = document.getElementById("oracle-status");
    const iconEl = document.getElementById("oracle-icon");
    const detailEl = document.getElementById("oracle-detail");

    if (oracle.market_panic) {
        statusEl.textContent = "PÁNICO";
        statusEl.className = "metric-value oracle-danger";
        iconEl.textContent = "🚨";
        detailEl.textContent = "Circuit Breaker ACTIVO";
    } else if (oracle.network_ok === false && oracle.headlines_cached === 0) {
        statusEl.textContent = "⚠ NO_NET";
        statusEl.className = "metric-value oracle-warn";
        iconEl.textContent = "📡";
        detailEl.textContent = "Sin conexión RSS";
    } else {
        statusEl.textContent = "SEGURO";
        statusEl.className = "metric-value oracle-safe";
        iconEl.textContent = "🧠";
        const score = oracle.last_score !== null && oracle.last_score !== undefined
            ? oracle.last_score.toFixed(2) : "—";
        detailEl.textContent = `Score: ${score}`;
    }

    // Detail card
    document.getElementById("oracle-state-detail").textContent =
        oracle.market_panic ? "⛔ PANIC" : oracle.network_ok === false ? "📡 No Network" : "✅ Safe";
    document.getElementById("oracle-score-detail").textContent =
        oracle.last_score !== null && oracle.last_score !== undefined
            ? oracle.last_score.toFixed(3) : "N/A";
    document.getElementById("oracle-provider-detail").textContent =
        oracle.last_provider || "N/A";
    document.getElementById("oracle-headlines-detail").textContent =
        oracle.headlines_cached || 0;

    const panicRow = document.getElementById("oracle-panic-row");
    const panicReason = document.getElementById("oracle-panic-reason");
    if (oracle.market_panic && oracle.panic_reason) {
        panicRow.style.display = "flex";
        panicReason.textContent = oracle.panic_reason.substring(0, 60);
    } else if (oracle.last_error) {
        panicRow.style.display = "flex";
        panicReason.textContent = oracle.last_error.substring(0, 60);
        panicReason.className = "oracle-val oracle-warn";
    } else {
        panicRow.style.display = "none";
    }
}

// ============================================
// Trades Table
// ============================================

function updateTradesTable(trades) {
    const tbody = document.getElementById("trades-body");
    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Sin trades aún</td></tr>';
        return;
    }
    tbody.innerHTML = trades.map(t => `
        <tr>
            <td>${formatTime(t.timestamp)}</td>
            <td>${t.symbol}</td>
            <td class="${t.side === 'buy' ? 'side-buy' : 'side-sell'}">${t.side.toUpperCase()}</td>
            <td>$${parseFloat(t.price).toFixed(2)}</td>
            <td>${parseFloat(t.amount).toFixed(6)}</td>
            <td class="${t.pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">$${parseFloat(t.pnl).toFixed(2)}</td>
        </tr>
    `).join("");
}

function addTradeRow(trade) {
    const tbody = document.getElementById("trades-body");
    const empty = tbody.querySelector(".empty-state");
    if (empty) empty.parentElement.remove();

    const row = document.createElement("tr");
    row.className = "trade-flash";
    row.innerHTML = `
        <td>${formatTime(trade.timestamp)}</td>
        <td>${trade.symbol}</td>
        <td class="${trade.side === 'buy' ? 'side-buy' : 'side-sell'}">${trade.side.toUpperCase()}</td>
        <td>$${parseFloat(trade.price).toFixed(2)}</td>
        <td>${parseFloat(trade.amount).toFixed(6)}</td>
        <td class="${trade.pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">$${parseFloat(trade.pnl).toFixed(2)}</td>
    `;
    tbody.insertBefore(row, tbody.firstChild);
    while (tbody.children.length > 30) tbody.removeChild(tbody.lastChild);
}

function formatTime(ts) {
    if (!ts) return "—";
    return new Date(ts).toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ============================================
// Lightweight Charts — Equity Curve
// ============================================

function initChart() {
    const container = document.getElementById("equity-chart");
    container.innerHTML = "";

    chart = LightweightCharts.createChart(container, {
        width: container.clientWidth - 24,
        height: container.clientHeight - 24,
        layout: {
            background: { type: "solid", color: "transparent" },
            textColor: "#8b949e",
            fontFamily: "Inter, sans-serif",
            fontSize: 11,
        },
        grid: {
            vertLines: { color: "rgba(255,255,255,0.02)" },
            horzLines: { color: "rgba(255,255,255,0.02)" },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: "rgba(255,255,255,0.06)" },
        timeScale: { borderColor: "rgba(255,255,255,0.06)", timeVisible: true },
    });

    equitySeries = chart.addSeries(LightweightCharts.AreaSeries, {
        topColor: "rgba(56, 139, 253, 0.3)",
        bottomColor: "rgba(56, 139, 253, 0.0)",
        lineColor: "#388bfd",
        lineWidth: 2,
    });

    new ResizeObserver(() => {
        chart.applyOptions({
            width: container.clientWidth - 24,
            height: container.clientHeight - 24,
        });
    }).observe(container);
}

function updateEquityChart(data) {
    if (!equitySeries || !data || data.length === 0) return;
    const chartData = data.map(d => ({
        time: Math.floor(new Date(d.timestamp).getTime() / 1000),
        value: d.total,
    }));
    equitySeries.setData(chartData);
    chart.timeScale().fitContent();
}

// ============================================
// Audit Console
// ============================================

async function refreshLogs() {
    const filter = document.getElementById("log-filter").value;
    const params = new URLSearchParams({ limit: "80" });
    if (filter) params.set("level", filter);

    const logs = await apiFetch(`/api/logs?${params}`);
    if (!logs) return;

    const console = document.getElementById("audit-console");

    // Only append new logs (by tracking max ID)
    const newLogs = logs.filter(l => l.id > lastLogId).reverse();
    if (newLogs.length === 0) return;

    // If this is the first load, clear placeholder
    if (lastLogId === 0) {
        console.innerHTML = "";
    }

    for (const log of newLogs) {
        appendLogLine(console, log);
        if (log.id > lastLogId) lastLogId = log.id;
    }

    // Trim old lines
    while (console.children.length > MAX_CONSOLE_LINES) {
        console.removeChild(console.firstChild);
    }

    // Auto-scroll to bottom
    console.scrollTop = console.scrollHeight;
}

function appendLogLine(container, log) {
    const line = document.createElement("div");
    line.className = `audit-line audit-flash`;

    const ts = formatTime(log.timestamp);
    const level = (log.level || "INFO").toUpperCase();
    const badgeClass = {
        INFO: "badge-info",
        WARNING: "badge-warning",
        ERROR: "badge-error",
        CRITICAL: "badge-critical",
    }[level] || "badge-info";

    const source = (log.source || "").substring(0, 12);
    const action = log.action || "";

    // Parse detail JSON for display
    let detail = "";
    try {
        const d = JSON.parse(log.detail || "{}");
        detail = Object.entries(d).map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`).join(" ");
    } catch { detail = log.detail || ""; }

    line.innerHTML = `
        <span class="audit-ts">${ts}</span>
        <span class="audit-badge ${badgeClass}">${source}</span>
        <span class="audit-msg">${action} ${detail ? '— ' + detail.substring(0, 100) : ''}</span>
    `;
    container.appendChild(line);
}

function clearConsole() {
    document.getElementById("audit-console").innerHTML = "";
    lastLogId = 0;
}

// ============================================
// Trading Controls
// ============================================

async function controlAction(action) {
    const res = await apiFetch(`/api/control/${action}`, "POST");
    if (res) {
        appendSystemLog(`Acción ejecutada: ${action.toUpperCase()}`);
        loadAllData();
    }
}

async function controlPanic() {
    if (!confirm("⚠️ ¿PANIC STOP? Esto pausará el trading y cancelará órdenes activas.")) return;
    const res = await apiFetch("/api/control/panic", "POST");
    if (res) {
        appendSystemLog("🚨 PANIC STOP ejecutado por el usuario");
        document.getElementById("btn-panic").classList.add("panic-active");
        setTimeout(() => {
            document.getElementById("btn-panic").classList.remove("panic-active");
        }, 5000);
        loadAllData();
    }
}

function appendSystemLog(msg) {
    const console = document.getElementById("audit-console");
    const line = document.createElement("div");
    line.className = "audit-line audit-flash";
    line.innerHTML = `
        <span class="audit-ts">${new Date().toLocaleTimeString("es-PE", {hour:"2-digit",minute:"2-digit",second:"2-digit"})}</span>
        <span class="audit-badge badge-warning">SYS</span>
        <span class="audit-msg">${msg}</span>
    `;
    console.appendChild(line);
    console.scrollTop = console.scrollHeight;
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
        setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, 25000);
    };

    ws.onmessage = (event) => {
        try {
            handleWsMessage(JSON.parse(event.data));
        } catch (e) {
            console.error("WS parse error:", e);
        }
    };

    ws.onclose = () => {
        statusEl.textContent = "⬤ Desconectado";
        statusEl.className = "ws-indicator";
        setTimeout(connectWebSocket, 3000);
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
            showTradeToast(msg.data);
            loadAllData();
            break;
        case "balance":
            document.getElementById("current-balance").textContent =
                `$${parseFloat(msg.data.total || msg.data.free || 0).toFixed(2)}`;
            break;
        case "status":
            updateStatus(msg.data);
            break;
        case "control":
            const action = msg.data.action;
            if (action === "paused") {
                appendSystemLog("⏸ Trading PAUSADO remotamente");
            } else if (action === "resumed") {
                appendSystemLog("▶ Trading REANUDADO remotamente");
            } else if (action === "panic") {
                appendSystemLog("🚨 PANIC STOP ejecutado");
            }
            loadAllData();
            break;
        case "pong":
            break;
        default:
            console.log("WS:", msg);
    }
    document.getElementById("last-update").textContent =
        new Date().toLocaleTimeString("es-PE");
}

// ============================================
// Init
// ============================================

document.addEventListener("DOMContentLoaded", () => {
    checkAppStatus();
    
    // Add enter key support for login password
    const loginInput = document.getElementById("login-apipw");
    if (loginInput) {
        loginInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") handleLogin();
        });
    }

    // Sentiment toggle styling
    const sentToggle = document.getElementById('cfg-sentiment-enabled');
    if (sentToggle) {
        sentToggle.addEventListener('change', () => {
            const label = document.getElementById('cfg-sentiment-label');
            const slider = sentToggle.nextElementSibling;
            if (sentToggle.checked) {
                if (label) label.textContent = 'ON';
                if (slider) slider.style.background = '#3fb8af';
            } else {
                if (label) label.textContent = 'OFF';
                if (slider) slider.style.background = '#484f58';
            }
        });
    }

    // Global UI Interaction Tracker (for Stitch Mapping & Auditing)
    document.addEventListener("click", (e) => {
        const btn = e.target.closest("button");
        if (btn) {
            const componentId = btn.id || btn.className.split(" ")[0] || "unnamed_button";
            const actionText = btn.textContent.trim() || btn.title || "click";
            
            // Send asynchronously to backend
            fetch(`${API}/api/audit/ui`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    component_id: componentId,
                    action: "click",
                    details: actionText
                })
            }).catch(() => {});
        }
    });
});

// ============================================
// Screen Navigation
// ============================================

function switchScreen(screenId) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    const target = document.getElementById(screenId);
    if (target) target.classList.add('active');

    // Update sidebar active state
    document.querySelectorAll('.sidebar .nav-btn').forEach(b => b.classList.remove('active'));
}

function showConfigScreen() {
    switchScreen('config-screen');
    loadConfigStatus();
}

// ============================================
// Config Management
// ============================================

async function loadConfigStatus() {
    const data = await apiFetch('/api/config/status');
    if (!data) return;

    // Gemini badge
    const geminiBadge = document.getElementById('cfg-gemini-badge');
    const geminiInput = document.getElementById('cfg-gemini-key');
    if (geminiBadge) {
        if (data.gemini.configured) {
            geminiBadge.textContent = '✅ Configurada';
            geminiBadge.style.color = '#3fb8af';
            if (geminiInput) geminiInput.placeholder = data.gemini.preview + '••••';
        } else {
            geminiBadge.textContent = '⚪ No configurada';
            geminiBadge.style.color = '#8b949e';
            if (geminiInput) geminiInput.placeholder = 'AIza...';
        }
    }

    // Alpaca badge
    const alpacaBadge = document.getElementById('cfg-alpaca-badge');
    const alpacaInput = document.getElementById('cfg-alpaca-key');
    if (alpacaBadge) {
        if (data.alpaca.configured) {
            alpacaBadge.textContent = '✅ Configurada';
            alpacaBadge.style.color = '#3fb8af';
            if (alpacaInput) alpacaInput.placeholder = data.alpaca.preview + '••••';
        } else {
            alpacaBadge.textContent = '⚪ No configurada';
            alpacaBadge.style.color = '#8b949e';
            if (alpacaInput) alpacaInput.placeholder = 'PK...';
        }
    }

    // Sentiment toggle
    const sentToggle = document.getElementById('cfg-sentiment-enabled');
    const sentLabel = document.getElementById('cfg-sentiment-label');
    if (sentToggle) {
        sentToggle.checked = data.sentiment_enabled;
        if (sentLabel) sentLabel.textContent = data.sentiment_enabled ? 'ON' : 'OFF';
        const slider = sentToggle.nextElementSibling;
        if (slider) slider.style.background = data.sentiment_enabled ? '#3fb8af' : '#484f58';
    }

    // Thresholds
    const obiInput = document.getElementById('cfg-obi-threshold');
    const spreadInput = document.getElementById('cfg-spread-mult');
    const cooldownInput = document.getElementById('cfg-cooldown');
    if (obiInput) obiInput.value = data.thresholds.obi;
    if (spreadInput) spreadInput.value = data.thresholds.spread_multiplier;
    if (cooldownInput) cooldownInput.value = data.thresholds.pro_cooldown_seconds;
}

async function saveConfig() {
    const statusEl = document.getElementById('cfg-save-status');
    const payload = {};

    // Only send fields the user actually filled
    const geminiKey = document.getElementById('cfg-gemini-key')?.value?.trim();
    if (geminiKey) payload.gemini_api_key = geminiKey;

    const alpacaKey = document.getElementById('cfg-alpaca-key')?.value?.trim();
    if (alpacaKey) payload.alpaca_api_key = alpacaKey;

    const alpacaSecret = document.getElementById('cfg-alpaca-secret')?.value?.trim();
    if (alpacaSecret) payload.alpaca_api_secret = alpacaSecret;

    const sentToggle = document.getElementById('cfg-sentiment-enabled');
    if (sentToggle) payload.sentiment_enabled = sentToggle.checked;

    const obiVal = document.getElementById('cfg-obi-threshold')?.value;
    if (obiVal) payload.gemini_pro_obi_threshold = parseFloat(obiVal);

    const spreadVal = document.getElementById('cfg-spread-mult')?.value;
    if (spreadVal) payload.gemini_pro_spread_multiplier = parseFloat(spreadVal);

    const cooldownVal = document.getElementById('cfg-cooldown')?.value;
    if (cooldownVal) payload.gemini_pro_cooldown_seconds = parseInt(cooldownVal);

    if (statusEl) {
        statusEl.textContent = '⏳ Guardando...';
        statusEl.style.color = '#8b949e';
    }

    try {
        const res = await fetch(`${API}/api/config/update`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': token,
            },
            body: JSON.stringify(payload),
        });

        const data = await res.json();

        if (res.ok) {
            if (statusEl) {
                statusEl.textContent = '✅ Configuración guardada';
                statusEl.style.color = '#3fb8af';
            }
            // Clear sensitive fields after save
            const gk = document.getElementById('cfg-gemini-key');
            const ak = document.getElementById('cfg-alpaca-key');
            const as = document.getElementById('cfg-alpaca-secret');
            if (gk) gk.value = '';
            if (ak) ak.value = '';
            if (as) as.value = '';

            // Reload status and oracle
            await loadConfigStatus();
            fetchOracleStatus();
        } else {
            const errMsg = data.errors ? data.errors.join(', ') : (data.error || 'Error desconocido');
            if (statusEl) {
                statusEl.textContent = `❌ ${errMsg}`;
                statusEl.style.color = '#f85149';
            }
        }
    } catch (e) {
        if (statusEl) {
            statusEl.textContent = `❌ Error de conexión: ${e.message}`;
            statusEl.style.color = '#f85149';
        }
    }
}


// ============================================
// Toast Notifications (M6 — Trade alerts)
// ============================================

function showTradeToast(trade) {
    const container = document.getElementById('toast-container') || createToastContainer();
    const toast = document.createElement('div');
    const isBuy = trade.side === 'buy';
    const pnlStr = trade.pnl != null ? `${trade.pnl >= 0 ? '+' : ''}$${parseFloat(trade.pnl).toFixed(2)}` : '';
    toast.style.cssText = `
        padding: 12px 18px; border-radius: 10px; margin-bottom: 8px;
        background: ${isBuy ? 'rgba(63,182,139,0.15)' : 'rgba(248,81,73,0.15)'};
        border: 1px solid ${isBuy ? 'rgba(63,182,139,0.3)' : 'rgba(248,81,73,0.3)'};
        color: var(--text-primary); font-size: 13px; font-family: Inter, sans-serif;
        animation: fadeIn 0.3s ease; backdrop-filter: blur(12px);
        display: flex; align-items: center; gap: 10px;
    `;
    toast.innerHTML = `
        <span style="font-size:18px">${isBuy ? '\ud83d\udfe2' : '\ud83d\udd34'}</span>
        <span><strong>${trade.symbol}</strong> ${trade.side?.toUpperCase()}</span>
        <span style="margin-left:auto;font-weight:600;color:${trade.pnl >= 0 ? 'var(--pnl-positive)' : 'var(--pnl-negative)'}">${pnlStr}</span>
    `;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function createToastContainer() {
    const c = document.createElement('div');
    c.id = 'toast-container';
    c.style.cssText = 'position:fixed;top:60px;right:20px;z-index:9999;max-width:320px;';
    document.body.appendChild(c);
    return c;
}
