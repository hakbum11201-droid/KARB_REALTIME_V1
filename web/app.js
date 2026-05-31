/**
 * app.js – KARB_REALTIME_V1 Dashboard
 * 3초마다 /api/data 폴링 → 상태/호가/트레이드 렌더링
 */

const POLL_INTERVAL_MS = 3000;

// ---------- Tab Navigation ----------
document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${tab}`).classList.add('active');
    document.getElementById('page-title').textContent = btn.textContent.trim();
  });
});

// ---------- State ----------
let paperTrades = [];
let kimpHistory = []; // { sym, kimp, net, ts }

// ---------- Fetch & Render ----------
async function fetchAndRender() {
  try {
    const res = await fetch('/api/data');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    setConnected(true);
    renderTopbar(data.state || {});
    renderQuotes(data.quotes || {});
    renderTrades(data.state || {}, data.quotes || {});
    renderPerf(data.state || {}, data.quotes || {});

    document.getElementById('last-update').textContent =
      new Date().toLocaleTimeString('ko-KR');
  } catch (e) {
    setConnected(false);
  }
}

// ---------- Connection status ----------
function setConnected(ok) {
  const dot = document.querySelector('.conn-dot');
  const label = document.getElementById('conn-label');
  if (ok) {
    dot.className = 'conn-dot live';
    label.textContent = '연결됨';
  } else {
    dot.className = 'conn-dot err';
    label.textContent = '연결 실패';
  }
}

// ---------- Topbar ----------
function renderTopbar(state) {
  document.getElementById('stat-mode').textContent = (state.mode || '--').toUpperCase();
  if (state.krw_usdt) {
    document.getElementById('stat-fx').textContent = Number(state.krw_usdt).toLocaleString('ko-KR', { maximumFractionDigits: 1 });
  }
  if (state.paper_trade_count !== undefined) {
    document.getElementById('stat-paper-trades').textContent = state.paper_trade_count;
  }
  if (state.latest_paper_pnl !== undefined) {
    const pnl = Number(state.latest_paper_pnl);
    const el = document.getElementById('stat-pnl');
    el.textContent = `${pnl >= 0 ? '+' : ''}${pnl.toLocaleString('ko-KR', { maximumFractionDigits: 0 })} ₩`;
    el.style.color = pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
  }
}

// ---------- Quote Cards ----------
function renderQuotes(quotes) {
  const grid = document.getElementById('quotes-grid');
  const symbols = Object.keys(quotes);
  if (!symbols.length) {
    grid.innerHTML = '<div class="loading-placeholder">데이터 없음 – 엔진 실행 확인</div>';
    return;
  }

  grid.innerHTML = symbols.map(sym => {
    const q = quotes[sym];
    const upbit = q.upbit || {};
    const binance = q.binance || {};
    const calc = q.calc || {};

    const kimp = Number(calc.kimchi_premium_pct || 0);
    const kimpClass = kimp >= 0 ? 'pos' : 'neg';
    const netSurplus = Number(calc.best_net_surplus_bp || 0);
    const netProfit = Number(calc.net_expected_profit_krw || 0);
    const grossGap = Number(calc.gross_gap_krw || 0);
    const reason = calc.reason_no_trade || '';
    const isGo = reason === '' && netSurplus > 0;
    const dir = calc.best_direction || '--';

    // kimpHistory 누적 (최근 60건)
    kimpHistory = [...kimpHistory.slice(-60), { sym, kimp, net: netProfit, ts: Date.now() }];

    const upbitBid = Number(upbit.bid || 0).toLocaleString('ko-KR', { maximumFractionDigits: 0 });
    const upbitAsk = Number(upbit.ask || 0).toLocaleString('ko-KR', { maximumFractionDigits: 0 });
    const binBid = Number(binance.bid || 0).toFixed(4);
    const binAsk = Number(binance.ask || 0).toFixed(4);

    return `
      <div class="quote-card ${isGo ? 'go' : 'nogo'}" id="card-${sym}">
        <div class="qc-header">
          <span class="qc-symbol">${sym}</span>
          <span class="qc-kimp ${kimpClass}">${kimp >= 0 ? '+' : ''}${kimp.toFixed(2)}%</span>
        </div>
        <div class="qc-prices">
          <div class="qc-price-block">
            <div class="qc-price-exchange">Upbit</div>
            <div class="qc-price-val">${upbitBid} ₩</div>
            <div class="qc-price-bid-ask">bid ${upbitBid} / ask ${upbitAsk}</div>
          </div>
          <div class="qc-price-block">
            <div class="qc-price-exchange">Binance</div>
            <div class="qc-price-val">${binBid} $</div>
            <div class="qc-price-bid-ask">bid ${binBid} / ask ${binAsk}</div>
          </div>
        </div>
        <div class="qc-metrics">
          <span class="qc-metric">Dir ${dir}</span>
          <span class="qc-metric">Surplus ${netSurplus.toFixed(1)} bp</span>
          <span class="qc-metric">Gross ${grossGap.toLocaleString('ko-KR', { maximumFractionDigits: 0 })} ₩</span>
          <span class="qc-metric">Net ${netProfit.toLocaleString('ko-KR', { maximumFractionDigits: 0 })} ₩</span>
        </div>
        <div class="qc-verdict">
          <span class="verdict-badge ${isGo ? 'go-badge' : 'nogo-badge'}">${isGo ? '✓ GO' : '✗ NO-GO'}</span>
          ${reason ? `<span class="qc-reason">${reason}</span>` : ''}
        </div>
      </div>`;
  }).join('');
}

// ---------- Trade Log ----------
function renderTrades(state, quotes) {
  // quotes 내 paper trade 정보를 최근 20건으로 렌더
  // web_server에서 trades 별도 API 없으면 quotes 내 calc 기반 표시
  const tbody = document.getElementById('trades-body');
  const count = state.paper_trade_count || 0;
  if (!count) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-row">Paper Trade 없음</td></tr>';
    return;
  }

  // state에 latest 정보만 있으므로, 전반적 요약 행 표시
  const pnl = Number(state.latest_paper_pnl || 0);
  const ts = state.latest_update ? new Date(state.latest_update * 1000).toLocaleTimeString('ko-KR') : '--';
  const krwUsdt = Number(state.krw_usdt || 0).toLocaleString('ko-KR', { maximumFractionDigits: 1 });

  tbody.innerHTML = `
    <tr>
      <td>${ts}</td>
      <td>--</td>
      <td>--</td>
      <td>--</td>
      <td>--</td>
      <td class="${pnl >= 0 ? 'profit-pos' : 'profit-neg'}">${pnl >= 0 ? '+' : ''}${pnl.toLocaleString('ko-KR', {maximumFractionDigits: 0})} ₩</td>
      <td>--</td>
      <td>${krwUsdt}</td>
    </tr>
    <tr><td colspan="8" class="empty-row" style="padding:12px!important;font-size:11px">
      최근 ${count}건 누적 / 상세 내역: logs/paper_trades.jsonl
    </td></tr>`;
}

// ---------- Performance ----------
function renderPerf(state, quotes) {
  const count = state.paper_trade_count || 0;
  const pnl = Number(state.latest_paper_pnl || 0);

  document.getElementById('perf-count').textContent = count;
  document.getElementById('perf-total-net').textContent =
    count > 0 ? `${(pnl).toLocaleString('ko-KR', { maximumFractionDigits: 0 })} ₩` : '--';
  document.getElementById('perf-avg-net').textContent =
    count > 0 ? `${(pnl / count).toLocaleString('ko-KR', { maximumFractionDigits: 0 })} ₩` : '--';
  document.getElementById('perf-latest-pnl').textContent =
    `${pnl >= 0 ? '+' : ''}${pnl.toLocaleString('ko-KR', { maximumFractionDigits: 0 })} ₩`;

  // Histogram: kimpHistory 기반
  const hist = document.getElementById('histogram');
  if (!kimpHistory.length) {
    hist.innerHTML = '<div class="empty-row">데이터 없음</div>';
    return;
  }
  const maxNet = Math.max(...kimpHistory.map(h => Math.abs(h.net)), 1);
  hist.innerHTML = kimpHistory.map(h => {
    const pct = Math.max((Math.abs(h.net) / maxNet) * 90, 6);
    const cls = h.net >= 0 ? 'pos' : 'neg';
    return `<div class="hist-bar ${cls}" style="height:${pct}px" title="${h.sym}: ${h.net.toFixed(0)}₩"></div>`;
  }).join('');
}

// ---------- Init ----------
fetchAndRender();
setInterval(fetchAndRender, POLL_INTERVAL_MS);
