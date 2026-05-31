/**
 * app.js – KARB Realtime V1 Dashboard (v2)
 * 4탭: dashboard / trades / perf / keys
 * 3초 폴링: /api/data, /api/perf, /api/keys/status
 */

const POLL_MS = 3000;

// ── Tab Navigation ──────────────────────────────────────────────────────
document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${tab}`).classList.add('active');
    document.getElementById('page-title').textContent = btn.textContent.trim();
    if (tab === 'keys') fetchKeyStatus();
    if (tab === 'perf') fetchPerf();
    if (tab === 'trades') fetchTrades();
  });
});

// ── Helpers ─────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const fmt  = (n, d=0) => Number(n || 0).toLocaleString('ko-KR', {maximumFractionDigits: d});
const fmtS = (n, suffix='') => `${fmt(n)}${suffix}`;
const pnlColor = n => n >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

function setConnected(ok) {
  const dot   = $('conn-dot');
  const label = $('conn-label');
  dot.className   = ok ? 'conn-dot live' : 'conn-dot err';
  label.textContent = ok ? '연결됨' : '연결 실패';
}

// ── Topbar ──────────────────────────────────────────────────────────────
function renderTopbar(state) {
  $('stat-mode').textContent   = (state.mode || '--').toUpperCase();
  $('stat-fx').textContent     = state.krw_usdt ? fmt(state.krw_usdt, 1) : '--';
  $('stat-trades').textContent = `${state.open_trades ?? '--'} / ${state.closed_trades ?? '--'}`;

  const pnl = Number(state.net_pnl_krw || 0);
  const pnlEl = $('stat-pnl');
  pnlEl.textContent = `${pnl >= 0 ? '+' : ''}${fmt(pnl)} ₩`;
  pnlEl.style.color = pnlColor(pnl);

  $('stat-winrate').textContent = state.win_rate != null ? `${Number(state.win_rate).toFixed(1)}%` : '--';
}

// ── Quote Grid ───────────────────────────────────────────────────────────
function renderQuotes(quotes) {
  const grid = $('quotes-grid');
  const syms = Object.keys(quotes);
  if (!syms.length) {
    grid.innerHTML = '<div class="loading-placeholder">데이터 없음 – 엔진 실행 확인</div>';
    return;
  }

  grid.innerHTML = syms.map(sym => {
    const q     = quotes[sym] || {};
    const upbit = q.upbit   || {};
    const bin   = q.binance || {};
    const calc  = q.calc    || {};

    const kimp    = Number(calc.kimchi_premium_pct || 0);
    const surplus = Number(calc.best_net_surplus_bp || 0);
    const net     = Number(calc.net_expected_profit_krw || 0);
    const gross   = Number(calc.gross_gap_krw || 0);
    const qty     = Number(calc.max_fillable_qty || 0);
    const dir     = calc.best_direction || '--';
    const reason  = calc.reason_no_trade || '';
    const isGo    = reason === 'OK';

    const kimpCls = kimp >= 0 ? 'pos' : 'neg';
    const u_bid = fmt(upbit.bid || 0);
    const u_ask = fmt(upbit.ask || 0);
    const b_bid = Number(bin.bid || 0).toFixed(4);
    const b_ask = Number(bin.ask || 0).toFixed(4);

    return `<div class="quote-card ${isGo ? 'go' : 'nogo'}">
      <div class="qc-header">
        <span class="qc-symbol">${sym}</span>
        <span class="qc-kimp ${kimpCls}">${kimp >= 0 ? '+' : ''}${kimp.toFixed(2)}%</span>
      </div>
      <div class="qc-prices">
        <div>
          <div class="qc-price-exchange">Upbit</div>
          <div class="qc-price-val">${u_bid} ₩</div>
          <div class="qc-price-bid-ask">bid ${u_bid} / ask ${u_ask}</div>
        </div>
        <div>
          <div class="qc-price-exchange">Binance</div>
          <div class="qc-price-val">${b_bid} $</div>
          <div class="qc-price-bid-ask">bid ${b_bid} / ask ${b_ask}</div>
        </div>
      </div>
      <div class="qc-metrics">
        <span class="qc-metric">Dir ${dir}</span>
        <span class="qc-metric">${surplus.toFixed(1)} bp</span>
        <span class="qc-metric">Gross ${fmt(gross)} ₩</span>
        <span class="qc-metric">Net ${fmt(net)} ₩</span>
        <span class="qc-metric">Qty ${qty.toFixed(4)}</span>
      </div>
      <div class="qc-verdict">
        <span class="verdict-badge ${isGo ? 'go-badge' : 'nogo-badge'}">${isGo ? '✓ GO' : '✗ NO-GO'}</span>
        ${reason && reason !== 'OK' ? `<span class="qc-reason">${reason}</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ── Trade Table ─────────────────────────────────────────────────────────
function renderTradeTable(trades) {
  const tbody = $('trades-body');
  if (!trades || !trades.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-row">Paper Trade 없음</td></tr>';
    return;
  }
  tbody.innerHTML = trades.slice().reverse().map(t => {
    const pnl = Number(t.realized_pnl_krw || 0);
    const winCls = t.exit_reason === 'TP' ? 'win-cell'
                 : t.exit_reason === 'SL' ? 'loss-cell' : 'timeout-cell';
    const dirCls = t.best_direction === 'A' ? 'dir-a' : 'dir-b';
    const ets = t.entry_time ? new Date(t.entry_time * 1000).toLocaleTimeString('ko-KR') : '--';
    const xts = t.exit_time  ? new Date(t.exit_time  * 1000).toLocaleTimeString('ko-KR') : '--';
    return `<tr>
      <td>${(t.trade_id || '').slice(0,8)}</td>
      <td>${t.symbol || '--'}</td>
      <td class="${dirCls}">${t.best_direction || '--'}</td>
      <td>${ets}</td>
      <td>${xts}</td>
      <td>${t.holding_sec != null ? Number(t.holding_sec).toFixed(0) + 's' : '--'}</td>
      <td style="color:${pnlColor(pnl)}">${pnl >= 0 ? '+' : ''}${fmt(pnl)} ₩</td>
      <td class="${winCls}">${t.exit_reason || '--'}${t.clean_win ? ' ★' : ''}</td>
      <td>${t.win ? '✓' : '✗'}</td>
    </tr>`;
  }).join('');
}

// ── Performance ─────────────────────────────────────────────────────────
function renderPerf(perf) {
  if (!perf) return;
  $('p-total').textContent    = perf.closed_trade_count ?? '--';
  const net = perf.net_pnl_krw || 0;
  $('p-net').textContent      = `${net >= 0 ? '+' : ''}${fmt(net)} ₩`;
  $('p-net').style.color      = pnlColor(net);
  $('p-winrate').textContent  = perf.win_rate != null    ? `${perf.win_rate.toFixed(1)}%` : '--';
  $('p-cleanwin').textContent = perf.clean_win_ratio != null ? `${perf.clean_win_ratio.toFixed(1)}%` : '--';
  const avg = perf.avg_pnl_krw || 0;
  $('p-avgpnl').textContent   = `${avg >= 0 ? '+' : ''}${fmt(avg)} ₩`;
  $('p-drawdown').textContent = perf.max_drawdown_krw != null ? `${fmt(perf.max_drawdown_krw)} ₩` : '--';
  const today = perf.today_pnl_krw || 0;
  $('p-today').textContent    = `${today >= 0 ? '+' : ''}${fmt(today)} ₩`;
  $('p-today').style.color    = pnlColor(today);
  $('p-bestsym').textContent  = perf.best_symbol || '--';

  // 분포 바
  const total = perf.closed_trade_count || 1;
  const winPct     = Math.round((perf.win_count     || 0) / total * 100);
  const lossPct    = Math.round((perf.loss_count    || 0) / total * 100);
  const timeoutPct = Math.round((perf.timeout_count || 0) / total * 100);
  $('bar-win').style.width         = `${winPct}%`;
  $('bar-win-pct').textContent     = `${winPct}%`;
  $('bar-loss').style.width        = `${lossPct}%`;
  $('bar-loss-pct').textContent    = `${lossPct}%`;
  $('bar-timeout').style.width     = `${timeoutPct}%`;
  $('bar-timeout-pct').textContent = `${timeoutPct}%`;
}

// ── Key Status ───────────────────────────────────────────────────────────
function renderKeyStatus(status) {
  ['UPBIT_ACCESS_KEY','UPBIT_SECRET_KEY','BINANCE_API_KEY','BINANCE_API_SECRET'].forEach(k => {
    const badge = $(`badge-${k}`);
    if (!badge) return;
    const val = status[k] || 'Missing';
    badge.textContent  = val;
    badge.className    = `key-status-badge ${val === 'Set' ? 'set' : 'missing'}`;
  });
}

// ── API Calls ────────────────────────────────────────────────────────────
async function fetchData() {
  try {
    const res = await fetch('/api/data');
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();
    setConnected(true);
    renderTopbar(d.state || {});
    renderQuotes(d.quotes || {});
    $('last-update').textContent = new Date().toLocaleTimeString('ko-KR');
  } catch {
    setConnected(false);
  }
}

async function fetchTrades() {
  try {
    const res = await fetch('/api/trades/recent');
    if (!res.ok) return;
    const d = await res.json();
    renderTradeTable(d.trades || []);
  } catch {}
}

async function fetchPerf() {
  try {
    const res = await fetch('/api/perf');
    if (!res.ok) return;
    const d = await res.json();
    renderPerf(d.performance || {});
    renderTradeTable(d.recent_trades || []);
  } catch {}
}

async function fetchKeyStatus() {
  try {
    const res = await fetch('/api/keys/status');
    if (!res.ok) return;
    renderKeyStatus(await res.json());
  } catch {
    ['UPBIT_ACCESS_KEY','UPBIT_SECRET_KEY','BINANCE_API_KEY','BINANCE_API_SECRET'].forEach(k => {
      const b = $(`badge-${k}`);
      if (b) { b.textContent = 'localhost 전용'; b.className = 'key-status-badge loading'; }
    });
  }
}

// ── Key Save ─────────────────────────────────────────────────────────────
$('btn-save-keys').addEventListener('click', async () => {
  const body = {
    upbit_access_key:  $('inp-upbit-access').value,
    upbit_secret_key:  $('inp-upbit-secret').value,
    binance_api_key:   $('inp-binance-api').value,
    binance_api_secret:$('inp-binance-secret').value,
  };
  const result = $('save-result');
  result.textContent = '저장 중...';
  result.className   = 'save-result';
  try {
    const res = await fetch('/api/keys/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await res.json();
    result.textContent = d.message || (d.ok ? '저장 완료' : '저장 실패');
    result.className   = `save-result ${d.ok ? 'ok' : 'err'}`;
    // 입력 필드 초기화 (보안)
    ['inp-upbit-access','inp-upbit-secret','inp-binance-api','inp-binance-secret'].forEach(id => {
      $(id).value = '';
    });
    // 상태 갱신
    fetchKeyStatus();
  } catch (e) {
    result.textContent = '연결 실패 (localhost에서 실행하세요)';
    result.className   = 'save-result err';
  }
});

// ── Init & Poll ──────────────────────────────────────────────────────────
fetchData();
fetchPerf();
setInterval(fetchData, POLL_MS);
setInterval(fetchPerf,  POLL_MS * 2);
setInterval(fetchTrades, POLL_MS * 2);
