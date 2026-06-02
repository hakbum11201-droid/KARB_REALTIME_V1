/**
 * app.js – KARB Realtime V1 Dashboard (v3)
 * 5탭: dashboard / trades / perf / session / keys
 * STOP 버튼, 세션 배너, 세션 리포트 표시
 */
const POLL_MS = 3000;
const TINY_LIVE_ACTION_LABELS = { arm:'ARM LIVE', disarm:'DISARM', 'execute-once':'EXECUTE ONCE' };
const missingElements = new Set();
const $ = id => {
  const el = document.getElementById(id);
  if (!el && !missingElements.has(id)) {
    missingElements.add(id);
    console.error(`[UI] missing element: ${id}`);
  }
  return el;
};
const fmt  = (n, d=0) => Number(n||0).toLocaleString('ko-KR',{maximumFractionDigits:d});
const pnlC = n => n >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
const esc = value => String(value??'--').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const setText = (id, value) => { const el=$(id); if (el) el.textContent=value; };
const setClass = (id, value) => { const el=$(id); if (el) el.className=value; };
const setStyle = (id, name, value) => { const el=$(id); if (el) el.style[name]=value; };
const setDisabled = (id, value) => { const el=$(id); if (el) el.disabled=value; };
const on = (id, event, handler) => { const el=$(id); if (el) el.addEventListener(event, handler); };
let latestState = {};
let latestEngine = {};
let latestControl = {};
let latestPerformance = {};
let latestLimits = {};
let latestTelemetry = {};
let latestStrategy = {};
let latestStrategyPairs = [];
let latestScanner = {};
let latestRuntimeStore = {};
let latestRateLimit = {};
let lastSummaryFetchAt = 0;

const durationText = seconds => {
  const total = Math.max(0, Math.floor(Number(seconds)||0));
  const h = String(Math.floor(total/3600)).padStart(2,'0');
  const m = String(Math.floor(total%3600/60)).padStart(2,'0');
  const s = String(total%60).padStart(2,'0');
  return `${h}:${m}:${s}`;
};
const ageText = seconds => seconds == null ? '--' : `${Math.max(0, Math.floor(seconds))}s ago`;

function showUiError(error) {
  const message = error instanceof Error ? error.message : String(error);
  console.error('[UI render error]', error);
  setText('conn-label', `UI 렌더 오류: ${message}`);
  const grid = $('quotes-grid-upbit-binance');
  if (grid && !grid.querySelector('.quote-card')) {
    const placeholder = document.createElement('div');
    placeholder.className = 'loading-placeholder';
    placeholder.textContent = `UI 렌더 오류: ${message}`;
    grid.replaceChildren(placeholder);
  }
}

// ── Tab Navigation ──────────────────────────────────────────────────────
document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${tab}`)?.classList.add('active');
    setText('page-title', btn.textContent.trim());
    if (tab === 'keys')    fetchKeyStatus();
    if (tab === 'session') fetchLastSession();
    if (tab === 'inventory') fetchInventory();
    if (tab === 'perf')    fetchPerf();
    if (tab === 'trades')  fetchTrades();
    if (tab === 'decisions') fetchDecisions();
  });
});

// ── Connection ──────────────────────────────────────────────────────────
function setConn(ok) {
  setClass('conn-dot', ok ? 'conn-dot live' : 'conn-dot err');
  setText('conn-label', ok ? '연결됨' : '연결 실패');
}

// ── Engine Controls ───────────────────────────────────────────────────────
async function startEngine(mode) {
  if (!confirm(`${mode.toUpperCase()} 모드로 엔진을 시작하시겠습니까?`)) return;
  try {
    const res = await fetch('/api/engine/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode })
    });
    const d = await res.json();
    alert(d.message);
    fetchData();
  } catch { alert('엔진 시작 요청 실패'); }
}

on('btn-start-paper', 'click', () => startEngine('paper'));
on('btn-start-tiny', 'click', () => startEngine('tiny_live'));
on('btn-start-live', 'click', () => startEngine('live'));

on('btn-stop-engine', 'click', async () => {
  if (!confirm('엔진을 정지하시겠습니까? 세션 리포트가 자동 생성됩니다.')) return;
  try {
    showStopping();
    const res = await fetch('/api/engine/stop', { method: 'POST' });
    const d = await res.json();
    alert(d.message || 'Stop requested');
    fetchData();
  } catch { alert('연결 실패'); }
});
on('btn-stop', 'click', async () => {
  // Graceful stop banner button
  try {
    showStopping();
    const res = await fetch('/api/engine/stop', { method: 'POST' });
    const d = await res.json();
    alert(d.message || 'Stop requested');
  } catch { alert('연결 실패'); }
});

// ── Main Fetch ──────────────────────────────────────────────────────────
async function fetchData() {
  try {
    const res = await fetch('/api/data');
    if (!res.ok) throw 0;
    const d = await res.json();
    latestState = d.state||{};
    latestEngine = d.engine||{};
    latestControl = d.control||{};
    latestPerformance = d.performance||{};
    latestLimits = d.limits||{};
    latestStrategy = d.strategy||{};
    latestStrategyPairs = d.strategy_pairs||[];
    renderTopbar(d.state||{}, d.engine||{});
    renderBanner(d.state||{}, d.control||{});
    renderTelemetry(d.state||{}, d.engine||{}, d.control||{});
    renderPaperProfitSummary(d.performance||{}, d.limits||{});
    renderPairPerformance(d.performance||{});
    renderStrategyPairs(latestStrategyPairs, latestStrategy);
    renderQuotes(d.quotes||{});
    setText('last-update', new Date().toLocaleTimeString('ko-KR'));
    if (missingElements.size) {
      showUiError(new Error(`missing element ${Array.from(missingElements).join(', ')}`));
    } else {
      setConn(true);
    }
  } catch (error) {
    console.error('[fetchData] failed', error);
    setConn(false);
    showUiError(error);
  }
}

function tinyLiveReadiness(perf, limits) {
  const closed = Number(perf.closed_trade_count||0);
  const net = Number(perf.net_pnl_krw||0);
  const winRate = Number(perf.win_rate||0);
  const avg = Number(perf.avg_pnl_krw||0);
  const drawdown = Number(perf.max_drawdown_krw||0);
  const lossLimit = Number(limits.daily_loss_limit_krw||0);
  const judgement = perf.judgement || currentPaperJudgement(perf);
  const reasons = [];
  if (!closed) return { ready: false, reasons: ['NO_CLOSED_TRADES'], judgement };
  if (closed < 10) reasons.push('NOT_ENOUGH_TRADES');
  if (net <= 0) reasons.push('NEGATIVE_PNL');
  if (winRate < 65) reasons.push('LOW_WIN_RATE');
  if (avg <= 0) reasons.push('NEGATIVE_AVG_PNL');
  if (lossLimit && drawdown >= lossLimit) reasons.push('HIGH_DRAWDOWN');
  if (!['PAPER_EDGE_PASS','PAPER_EDGE_WEAK'].includes(judgement) && !reasons.includes(judgement)) reasons.push(judgement);
  return { ready: reasons.length === 0, reasons, judgement };
}

function currentPaperJudgement(perf) {
  const closed = Number(perf.closed_trade_count||0);
  if (closed < 10) return 'NOT_ENOUGH_TRADES';
  if (Number(perf.net_pnl_krw||0) <= 0) return 'PAPER_EDGE_FAIL';
  return 'PAPER_EDGE_WEAK';
}

function renderPaperProfitSummary(perf, limits) {
  const paper_net_pnl = Number(perf.net_pnl_krw||0);
  const closed = Number(perf.closed_trade_count||0);
  const open = Number(perf.open_trade_count||0);
  const avg = Number(perf.avg_pnl_krw||0);
  const drawdown = Number(perf.max_drawdown_krw||0);
  const readiness = tinyLiveReadiness(perf, limits);
  setText('paper-net-pnl', `${paper_net_pnl>=0?'+':''}${fmt(paper_net_pnl)} KRW`);
  setClass('paper-net-pnl', `paper-pnl ${closed ? (paper_net_pnl>=0?'positive':'negative') : 'no-trades'}`);
  setText('paper-trade-count', fmt(perf.paper_trade_count));
  setText('paper-trades', `${closed} closed / ${open} open`);
  setText('paper-win-rate', `${Number(perf.win_rate||0).toFixed(1)}%`);
  setText('paper-avg-pnl', `${avg>=0?'+':''}${fmt(avg)} KRW`);
  setText('paper-max-dd', `${drawdown ? '-' : ''}${fmt(drawdown)} KRW`);
  setText('paper-judgement', readiness.judgement);
  setClass('paper-judgement', `judgement ${judgementClass(readiness.judgement)}`);
  setText('paper-summary-note', closed ? `${closed} closed paper trades` : 'no trades yet');
  const ready = $('paper-tiny-ready');
  if (ready) {
    ready.className = `tiny-ready ${readiness.ready?'yes':'no'}`;
    ready.innerHTML = `<span>TINY LIVE READY</span><strong>${readiness.ready?'YES':'NO'}</strong>`;
  }
  setText('paper-tiny-reasons', readiness.ready ? 'DISPLAY CHECK PASSED' : readiness.reasons.join(' / '));
}

function renderPairPerformance(perf) {
  const grid = $('pair-performance-grid');
  if (!grid) return;
  grid.setAttribute('aria-label', 'Pair Performance');
  const summary = perf.pair_summary||{};
  grid.innerHTML = ['UPBIT_BINANCE','UPBIT_BITHUMB'].map(pairId => {
    const row = summary[pairId]||{}, count=Number(row.closed_trade_count||0);
    const pnl=Number(row.net_pnl_krw||0), avg=Number(row.avg_pnl_krw||0), dd=Number(row.max_drawdown_krw||0);
    const status=!count?'NO DATA':count<10?'TESTING':pnl>=0?'POSITIVE':'NEGATIVE';
    const statusClass=status==='POSITIVE'?'positive':status==='NEGATIVE'?'negative':status==='TESTING'?'testing':'no-data';
    return `<div class="pair-performance-card ${statusClass}">
      <div class="pair-performance-title"><span class="pair-badge ${pairMeta(pairId).badge}">${esc(pairMeta(pairId).label)}</span><strong>${status}</strong></div>
      <div class="pair-performance-stats">
        <div><span>Closed Trades</span><strong>${fmt(count)}</strong></div>
        <div><span>Win Rate</span><strong>${Number(row.win_rate||0).toFixed(1)}%</strong></div>
        <div><span>Net PnL</span><strong style="color:${pnlC(pnl)}">${pnl>=0?'+':''}${fmt(pnl)} KRW</strong></div>
        <div><span>Avg PnL</span><strong>${avg>=0?'+':''}${fmt(avg)} KRW</strong></div>
        <div><span>Max DD</span><strong>${fmt(dd)} KRW</strong></div>
        <div><span>Best / Worst</span><strong>${fmt(row.best_trade_krw)} / ${fmt(row.worst_trade_krw)} KRW</strong></div>
      </div>
    </div>`;
  }).join('');
}

function judgementClass(judgement) {
  return judgement==='PAPER_EDGE_PASS' ? 'pass'
    : judgement==='PAPER_EDGE_WEAK' ? 'weak'
    : judgement==='PAPER_EDGE_FAIL' ? 'fail' : 'not-enough';
}

function showStopping() {
  setText('runtime-status', 'Stopping... generating session report');
  setClass('runtime-status', 'runtime-status stopping');
}

// heartbeat and last update freshness indicators
function renderTelemetry(state, engine, ctrl) {
  const status = ctrl.stop_requested && engine.running ? 'STOPPING' : (engine.status || ctrl.status || 'STOPPED');
  const updatedAge = state.updated_at ? Math.max(0, Date.now()/1000 - state.updated_at) : null;
  const quoteAge = state.last_quote_age_sec == null
    ? updatedAge
    : Math.max(0, Number(state.last_quote_age_sec) + (updatedAge||0));
  let quoteStatus = 'QUOTE WAITING';
  let quoteClass = 'waiting';
  if (quoteAge != null && quoteAge <= 5) { quoteStatus='QUOTE OK'; quoteClass='ok'; }
  else if (quoteAge != null && quoteAge <= 15) { quoteStatus='QUOTE STALE'; quoteClass='stale'; }
  else if (quoteAge != null) { quoteStatus='ENGINE WARNING'; quoteClass='warning'; }

  setText('runtime-status', status === 'RUNNING' ? '🟢 RUNNING' : status === 'STOPPING' ? 'Stopping... generating session report' : status);
  setClass('runtime-status', `runtime-status ${status.toLowerCase()}`);
  setClass('heartbeat', `heartbeat ${status === 'RUNNING' ? 'running' : ''}`);
  setText('quote-status', quoteStatus);
  setClass('quote-status', `quote-status ${quoteClass}`);
  setText('metric-runtime', durationText(state.runtime_sec));
  setText('metric-last-update', ageText(updatedAge));
  setText('metric-loop-count', fmt(state.loop_count));
  setText('metric-quote-count', fmt(state.quote_count));
  setText('metric-p95-loop', state.p95_loop_latency_ms!=null ? `${fmt(state.p95_loop_latency_ms,1)} ms` : '--');
  setText('metric-p95-quote', state.p95_quote_latency_ms!=null ? `${fmt(state.p95_quote_latency_ms,1)} ms` : '--');
  renderLongRunTelemetry({...state, ...latestTelemetry});
  if (status === 'STOPPED' && Date.now() - lastSummaryFetchAt >= POLL_MS) {
    lastSummaryFetchAt = Date.now();
    fetchLastSession();
  }
}

// ── Topbar ──────────────────────────────────────────────────────────────
function renderTopbar(s, e) {
  const isRunning = e.running;
  const status = e.status || 'STOPPED';
  setText('stat-engine-status', status);
  if (status === 'RUNNING') setStyle('stat-engine-status', 'color', 'var(--accent-green)');
  else if (status === 'STOP_PENDING') setStyle('stat-engine-status', 'color', 'var(--accent-orange)');
  else setStyle('stat-engine-status', 'color', 'var(--text-muted)');

  setDisabled('btn-start-paper', isRunning);
  setDisabled('btn-start-tiny', isRunning);
  setDisabled('btn-start-live', true);
  setDisabled('btn-stop-engine', !isRunning);

  setText('stat-session', e.run_id ? e.run_id.slice(0,15) : '--');
  setText('stat-fx', s.krw_usdt ? fmt(s.krw_usdt,1) : '--');
  setText('stat-trades', `${s.open_trades??'--'} / ${s.closed_trades??'--'}`);
  const pnl = Number(s.net_pnl_krw||0);
  const pe = $('stat-pnl');
  if (pe) {
    pe.textContent = `${pnl>=0?'+':''}${fmt(pnl)} ₩`;
    pe.style.color = pnlC(pnl);
  }
  setText('stat-winrate', s.win_rate!=null ? `${Number(s.win_rate).toFixed(1)}%` : '--');
}

// ── Session Banner ──────────────────────────────────────────────────────
function renderBanner(state, ctrl) {
  const b = $('session-banner');
  if (!b) return;
  if (!ctrl.run_id) { b.style.display='none'; return; }
  b.style.display = 'flex';
  const status = ctrl.status || 'UNKNOWN';
  setText('sb-status', status);
  setClass('sb-status', `sb-status ${status==='RUNNING'?'running':'stopped'}`);
  setText('sb-runid', ctrl.run_id);
  const rt = state.runtime_sec || 0;
  const m = Math.floor(rt/60), s = Math.floor(rt%60);
  setText('sb-runtime', `${m}m ${s}s`);
  setText('sb-reason', state.latest_reason || '--');
}

// ── Quote Grid ──────────────────────────────────────────────────────────
function renderLegacyQuotes(quotes) {
  const grid = $('quotes-grid-upbit-binance');
  if (!grid) return;
  const syms = Object.keys(quotes);
  if (!syms.length) { grid.innerHTML='<div class="loading-placeholder">데이터 없음</div>'; return; }

  grid.innerHTML = syms.map(sym => {
    const q=quotes[sym]||{}, up=q.upbit||{}, bn=q.binance||{}, c=q.calc||{};
    const sourceState=(latestTelemetry.symbol_quote_status||[]).find(item=>item.symbol===sym)||{};
    const kimp=Number(c.kimchi_premium_pct||0), surplus=Number(c.best_net_surplus_bp||0);
    const net=Number(c.net_expected_profit_krw||0);
    const threshold_gap = Number(latestLimits.min_net_surplus_bp||0) - surplus;
    const qty=Number(c.max_fillable_qty||0), dir=c.best_direction||'--';
    const quoteSource=(sourceState.quote_source||q.source||up.source||bn.source||'rest').toUpperCase();
    const quoteAge=Number(sourceState.quote_age_ms!=null?sourceState.quote_age_ms/1000:q.quote_age_sec||0);
    const stale=Boolean(sourceState.stale);
    const reason=c.reason_no_trade||'', isGo=reason==='OK';
    const ub=fmt(up.bid||0), ua=fmt(up.ask||0);
    const bb=Number(bn.bid||0).toFixed(4), ba=Number(bn.ask||0).toFixed(4);
    const reasonClass = reason==='OK' ? 'reason-ok'
      : reason==='LOW_SURPLUS' ? 'reason-low-surplus'
      : reason==='WIDE_SPREAD'||reason==='LOW_DEPTH' ? 'reason-warning'
      : reason==='FX_UNTRUSTED' ? 'reason-danger' : 'reason-default';
    return `<div class="quote-card ${isGo?'go':'nogo'} ${stale?'stale':''} refreshed">
      <div class="qc-header">
        <span class="qc-symbol">${sym} <span class="live-dot"></span><span class="live-label">LIVE</span></span>
        <span class="qc-kimp ${kimp>=0?'pos':'neg'}">${kimp>=0?'+':''}${kimp.toFixed(2)}%</span>
      </div>
      <div class="qc-prices">
        <div><div class="qc-price-exchange">Upbit</div><div class="qc-price-val">${ub} ₩</div><div class="qc-price-bid-ask">bid ${ub} / ask ${ua}</div></div>
        <div><div class="qc-price-exchange">Binance</div><div class="qc-price-val">${bb} $</div><div class="qc-price-bid-ask">bid ${bb} / ask ${ba}</div></div>
      </div>
      <div class="qc-metrics">
        <span class="qc-metric">${quoteSource} ${quoteAge.toFixed(2)}s</span>
        <span class="qc-metric">${stale?'STALE':'FRESH'}</span>
        <span class="qc-metric">Upbit ${(sourceState.upbit_source||up.source||'rest').toUpperCase()}</span>
        <span class="qc-metric">Binance ${(sourceState.binance_source||bn.source||'rest').toUpperCase()}</span>
        <span class="qc-metric">Dir ${dir}</span>
        <span class="qc-metric">${surplus.toFixed(1)} bp</span>
        <span class="qc-metric">Net ${fmt(net)} ₩</span>
        <span class="qc-metric">Qty ${qty.toFixed(4)}</span>
        <span class="qc-metric">Dynamic Slippage ${fmt(c.dynamic_slippage_bp,1)} bp</span>
        <span class="qc-metric">Depth ${fmt(c.depth_available_krw)} KRW</span>
        <span class="qc-metric">Liquidity ${esc(c.liquidity_class||'--')}</span>
        <span class="qc-metric">Latency ${fmt(c.latency_used_ms)} ms</span>
        <span class="qc-metric">Paper Latency Sim ${c.paper_latency_sim_enabled?'ON':'OFF'}</span>
        <span class="qc-metric">Paper Edge ${esc(c.paper_edge_quality||'--')}</span>
      </div>
      <div class="qc-verdict">
        <span class="verdict-badge ${isGo?'go-badge':'nogo-badge'}">${isGo?'✓ GO':'✗ NO-GO'}</span>
        <span class="qc-reason ${reasonClass}">${reason||'--'}</span>
      </div>
      <div class="qc-threshold ${isGo?'met':'short'}">${isGo?'Threshold met':threshold_gap>0?`기준까지 ${threshold_gap.toFixed(1)} bp 부족`:`기준 surplus 충족 / ${reason||'NO-GO'} 확인`}</div>
    </div>`;
  }).join('');
}

// ── Trades ───────────────────────────────────────────────────────────────
function pairMeta(pairId) {
  if (pairId === 'UPBIT_BITHUMB') return { label:'UPBIT ↔ BITHUMB', type:'DOMESTIC KRW', fx:'FX OFF', badge:'pair-upbit-bithumb' };
  if (pairId === 'BITHUMB_BINANCE') return { label:'BITHUMB ↔ BINANCE', type:'DISABLED', fx:'FX ON', badge:'pair-disabled' };
  if (pairId === 'BINANCE_MAKER_DOMESTIC_TAKER') return { label:'BINANCE MAKER ↔ DOMESTIC TAKER', type:'DISABLED', fx:'FX ON', badge:'pair-disabled' };
  return { label:'UPBIT ↔ BINANCE', type:'CROSS-BORDER', fx:'FX ON', badge:'pair-upbit-binance' };
}

function directionText(pairId, direction) {
  if (pairId === 'UPBIT_BITHUMB') {
    return direction === 'UPBIT_BITHUMB_B'
      ? 'Bithumb SELL / Upbit BUY · 빗썸이 더 비쌈'
      : 'Upbit SELL / Bithumb BUY · 업비트가 더 비쌈';
  }
  return direction === 'B' ? 'Upbit BUY / Binance SELL' : 'Upbit SELL / Binance BUY';
}

function requiredAssetsText(row) {
  const assets=row.selected_required_assets||{};
  const labels={
    upbit_coin_qty:'Upbit coin', binance_usdt:'Binance USDT',
    upbit_krw:'Upbit KRW', binance_coin_qty:'Binance coin',
    bithumb_krw:'Bithumb KRW', bithumb_coin_qty:'Bithumb coin',
  };
  const required=Object.entries(labels)
    .filter(([key])=>Number(assets[key]||0)>0)
    .map(([key,label])=>`${label} ${fmt(assets[key],key.endsWith('qty')?8:2)}`);
  return `Need: ${required.join(' / ')||'--'} · Notional ${fmt(row.selected_notional_krw)} KRW · ${esc(row.notional_basis||'--')}`;
}

function renderOpportunityCard(row) {
  const pairId=row.pair_id||'UPBIT_BINANCE', domestic=pairId==='UPBIT_BITHUMB', meta=pairMeta(pairId);
  const sym=row.symbol||'--', surplus=Number(row.best_net_surplus_bp||0), net=Number(row.net_expected_profit_krw||0);
  const thresholdGap=Number(latestLimits.min_net_surplus_bp||0)-surplus, reason=row.reason_no_trade||'', isGo=reason==='OK';
  const sourceState=(latestTelemetry.symbol_quote_status||[]).find(item=>item.symbol===sym)||{};
  const quoteSource=(row.quote_source||sourceState.quote_source||'rest').toUpperCase();
  const quoteAge=Number(sourceState.quote_age_ms!=null?sourceState.quote_age_ms/1000:0), stale=Boolean(sourceState.stale);
  const rightBid=domestic?row.bithumb_bid:row.binance_bid, rightAsk=domestic?row.bithumb_ask:row.binance_ask;
  const rightVenue=domestic?'Bithumb':'Binance', rightUnit=domestic?'KRW':'USDT';
  const gapLabel=domestic?'Domestic Gap':'Kimp', gapValue=domestic?`${surplus>=0?'+':''}${surplus.toFixed(1)} bp`:`${Number(row.kimchi_premium_pct||0).toFixed(2)}%`;
  const reasonClass=reason==='OK'?'reason-ok':reason==='LOW_SURPLUS'?'reason-low-surplus':reason==='WIDE_SPREAD'||reason==='LOW_DEPTH'?'reason-warning':reason==='FX_UNTRUSTED'?'reason-danger':'reason-default';
  return `<div class="quote-card ${domestic?'domestic-card':'cross-border-card'} ${isGo?'go':'nogo'} ${stale?'stale':''} refreshed">
    <div class="qc-pair-row"><span class="pair-badge ${meta.badge}">${meta.label}</span><span class="strategy-type-badge">${meta.type}</span><span class="fx-badge ${domestic?'fx-off':'fx-on'}">${meta.fx}</span></div>
    <div class="qc-header"><span class="qc-symbol">${esc(sym)} <span class="live-dot"></span><span class="live-label">LIVE</span></span><span class="qc-kimp ${surplus>=0?'pos':'neg'}">${gapLabel} ${gapValue}</span></div>
    <div class="qc-prices">
      <div><div class="qc-price-exchange">Upbit · KRW</div><div class="qc-price-val">${fmt(row.upbit_bid)} KRW</div><div class="qc-price-bid-ask">bid ${fmt(row.upbit_bid)} / ask ${fmt(row.upbit_ask)}</div></div>
      <div><div class="qc-price-exchange">${rightVenue} · ${rightUnit}</div><div class="qc-price-val">${domestic?fmt(rightBid):Number(rightBid||0).toFixed(4)} ${rightUnit}</div><div class="qc-price-bid-ask">bid ${domestic?fmt(rightBid):Number(rightBid||0).toFixed(4)} / ask ${domestic?fmt(rightAsk):Number(rightAsk||0).toFixed(4)}</div></div>
    </div>
    <div class="qc-direction"><strong>${esc(row.best_direction||'--')}</strong><span>${esc(directionText(pairId,row.best_direction))}</span></div>
    <div class="qc-direction"><span>${requiredAssetsText(row)}</span></div>
    <div class="qc-metrics">
      <span class="qc-metric">${quoteSource} ${quoteAge.toFixed(2)}s</span><span class="qc-metric">${stale?'STALE':'FRESH'}</span>
      <span class="qc-metric">${domestic?'FX 없음':`FX ${fmt(row.krw_usdt,1)} KRW/USDT`}</span><span class="qc-metric">Net Surplus ${surplus.toFixed(1)} bp</span>
      <span class="qc-metric">Net ${fmt(net)} KRW</span><span class="qc-metric">Order KRW ${fmt(row.order_krw_used)}</span>
      <span class="qc-metric">Effective Qty ${Number((row.effective_qty??row.selected_qty??row.max_fillable_qty)||0).toFixed(4)}</span>
      <span class="qc-metric">Raw Depth Qty ${Number((row.max_fillable_qty_raw??row.max_fillable_qty)||0).toFixed(4)}</span>
      <span class="qc-metric">Selected Notional ${fmt(row.selected_notional_krw)} KRW</span>
      <span class="qc-metric">Dynamic Slippage ${fmt(row.dynamic_slippage_bp,1)} bp</span><span class="qc-metric">Liquidity ${esc(row.liquidity_class||'--')}</span>
    </div>
    <div class="qc-verdict"><span class="verdict-badge ${isGo?'go-badge':'nogo-badge'}">${isGo?'GO':'NO-GO'}</span><span class="qc-reason ${reasonClass}">${esc(reason||'--')}</span></div>
    <div class="qc-threshold ${isGo?'met':'short'}">${isGo?'Threshold met':thresholdGap>0?`기준까지 ${thresholdGap.toFixed(1)} bp 부족`:`Surplus threshold met / ${esc(reason||'NO-GO')} 확인`}</div>
  </div>`;
}

function renderQuotes() {
  const opportunities=latestStrategy.all_opportunities||[];
  [['quotes-grid-upbit-binance','UPBIT_BINANCE'],['quotes-grid-upbit-bithumb','UPBIT_BITHUMB']].forEach(([id,pairId]) => {
    const grid=$(id);
    if (!grid) return;
    const rows=opportunities.filter(row=>row.pair_id===pairId);
    grid.innerHTML=rows.length ? rows.map(renderOpportunityCard).join('') : '<div class="loading-placeholder">No opportunities yet</div>';
  });
}

async function fetchTrades() {
  try {
    const r = await fetch('/api/trades/recent');
    if (!r.ok) return;
    const d = await r.json();
    renderTradeTable(d.trades||[]);
  } catch {}
}
function renderTradeTable(trades) {
  const tb = $('trades-body');
  if (!trades.length) { tb.innerHTML='<tr><td colspan="9" class="empty-row">없음</td></tr>'; return; }
  tb.innerHTML = trades.slice().reverse().map(t => {
    const pnl=Number(t.realized_pnl_krw||0);
    const wc=t.exit_reason==='TP'?'win-cell':t.exit_reason==='SL'?'loss-cell':'timeout-cell';
    const dc=t.best_direction==='A'?'dir-a':'dir-b';
    const et=t.entry_time?new Date(t.entry_time*1000).toLocaleTimeString('ko-KR'):'--';
    const xt=t.exit_time?new Date(t.exit_time*1000).toLocaleTimeString('ko-KR'):'--';
    return `<tr>
      <td>${(t.trade_id||'').slice(0,8)}</td><td><span class="pair-badge ${pairMeta(t.pair_id||'UPBIT_BINANCE').badge}">${esc(t.pair_id||'UPBIT_BINANCE')}</span> ${t.symbol||'--'}</td>
      <td class="${dc}">${t.best_direction||'--'}</td><td>${et}</td><td>${xt}</td>
      <td>${t.holding_sec!=null?Number(t.holding_sec).toFixed(0)+'s':'--'}</td>
      <td style="color:${pnlC(pnl)}">${pnl>=0?'+':''}${fmt(pnl)} ₩</td>
      <td class="${wc}">${t.exit_reason||'--'}${t.clean_win?' ★':''}</td>
      <td>${t.win?'✓':'✗'}</td>
    </tr>`;
  }).join('');
}

// ── Performance ─────────────────────────────────────────────────────────
async function fetchDecisions() {
  try {
    const r = await fetch('/api/decisions/recent');
    if (!r.ok) return;
    const d = await r.json();
    renderDecisions(d.decisions||[]);
  } catch (error) {
    console.error('[Decision Log] failed', error);
  }
}

function renderStrategyPairs(pairs=[], strategy={}) {
  const grid=$('strategy-grid');
  if (!grid) return;
  const byPair={};
  (strategy.all_opportunities||[]).forEach(row => {
    const current=byPair[row.pair_id];
    if (!current || Number(row.best_net_surplus_bp||-9999)>Number(current.best_net_surplus_bp||-9999)) byPair[row.pair_id]=row;
  });
  const bestPair=strategy.best_pair||'--', bestSymbol=strategy.best_symbol||'--';
  const bestMeta=pairMeta(bestPair), bestDirection=strategy.best_direction||'--';
  const bestReason=strategy.reason_no_trade||'--', bestGo=bestReason==='OK';
  const best=$('strategy-best');
  if (best) best.innerHTML=`<strong>Best Opportunity</strong>
    <span>Pair: ${esc(bestMeta.label)}</span><span>Symbol: ${esc(bestSymbol)}</span>
    <span>Direction: ${esc(directionText(bestPair,bestDirection))}</span>
    <span>Net Surplus: ${Number(strategy.best_net_surplus_bp||0)>=0?'+':''}${Number(strategy.best_net_surplus_bp||0).toFixed(1)} bp</span>
    <span>Expected: ${Number(strategy.expected_profit_krw||0)>=0?'+':''}${fmt(strategy.expected_profit_krw)} KRW</span>
    <span class="${bestGo?'best-go':'best-nogo'}">Status: ${bestGo?'GO':'NO-GO'} / ${esc(bestReason)}</span>`;
  grid.innerHTML=(pairs||[]).map(pair => {
    const row=byPair[pair.pair_id]||{}, enabled=Boolean(pair.enabled);
    const paperOnly=Boolean(pair.paper_enabled)&&!pair.tiny_live_enabled;
    const reason=row.reason_no_trade||(enabled?'QUOTE WAITING':'DISABLED');
    const go=enabled&&reason==='OK', highlighted=pair.pair_id===bestPair;
    return `<div class="strategy-card ${enabled?'enabled':'disabled'} ${highlighted?'best':''}">
      <div class="strategy-card-title">${esc(pair.pair_id)} ${highlighted?'<span>BEST</span>':''}</div>
      <div class="strategy-card-flags">${enabled?'enabled':'disabled'} / ${paperOnly?'paper-only':'configured'}</div>
      <div class="strategy-card-line">${esc(row.symbol||'--')} / ${esc(row.best_direction||'--')}</div>
      <div class="strategy-card-line">${Number(row.best_net_surplus_bp||0).toFixed(1)} bp / ${fmt(row.net_expected_profit_krw)} KRW</div>
      <div class="strategy-card-verdict ${go?'go':'nogo'}">${go?'GO':'NO-GO'} ${esc(reason)}</div>
    </div>`;
  }).join('');
}

function renderDecisions(decisions) {
  const tb = $('decisions-body');
  if (!tb) return;
  if (!decisions.length) {
    tb.innerHTML='<tr><td colspan="15" class="empty-row">No decisions yet</td></tr>';
    return;
  }
  tb.innerHTML = decisions.map(d => {
    const reason=d.reason_no_trade||'--';
    const reasonClass = reason==='OK' ? 'decision-ok'
      : reason==='STALE_QUOTE' ? 'decision-stale'
      : reason==='LOW_DEPTH'||reason==='WIDE_SPREAD' ? 'decision-warning'
      : 'decision-nogo';
    return `<tr class="${reasonClass}">
      <td>${d.time?new Date(d.time*1000).toLocaleTimeString('ko-KR'):'--'}</td>
      <td><span class="pair-badge ${pairMeta(d.pair_id||'UPBIT_BINANCE').badge}">${esc(d.pair_id||'UPBIT_BINANCE')}</span></td><td>${esc(d.symbol)}</td><td>${esc(d.direction_label||d.direction)}</td>
      <td>${fmt(d.best_net_surplus_bp,1)} bp</td><td>${fmt(d.threshold_gap_bp,1)} bp</td>
      <td>${fmt(d.expected_net_profit_krw)} KRW</td><td>${esc((d.quote_source||'--').toUpperCase())}</td>
      <td>${fmt(d.quote_age_ms,0)} ms</td><td>${fmt(d.dynamic_slippage_bp,1)} bp</td>
      <td>${esc(d.liquidity_class||'--')}</td><td>${fmt(d.latency_used_ms,0)} ms</td>
      <td>${esc(d.fill_quality||'--')}</td><td>${esc(d.go_no_go||'NO-GO')}</td><td>${esc(reason)}</td>
    </tr>`;
  }).join('');
}

async function fetchPerf() {
  try {
    const r = await fetch('/api/perf');
    if (!r.ok) return;
    const d = await r.json();
    renderPerf(d.performance||{});
    renderTradeTable(d.recent_trades||[]);
  } catch {}
}
function renderPerf(p) {
  if (!p) return;
  $('p-total').textContent    = p.closed_trade_count ?? '--';
  const net=p.net_pnl_krw||0;
  $('p-net').textContent      = `${net>=0?'+':''}${fmt(net)} ₩`;
  $('p-net').style.color      = pnlC(net);
  $('p-winrate').textContent  = p.win_rate!=null?`${p.win_rate.toFixed(1)}%`:'--';
  $('p-cleanwin').textContent = p.clean_win_ratio!=null?`${p.clean_win_ratio.toFixed(1)}%`:'--';
  const avg=p.avg_pnl_krw||0;
  $('p-avgpnl').textContent   = `${avg>=0?'+':''}${fmt(avg)} ₩`;
  $('p-drawdown').textContent = p.max_drawdown_krw!=null?`${fmt(p.max_drawdown_krw)} ₩`:'--';
  const td=p.today_pnl_krw||0;
  $('p-today').textContent    = `${td>=0?'+':''}${fmt(td)} ₩`;
  $('p-today').style.color    = pnlC(td);
  $('p-bestsym').textContent  = p.best_symbol||'--';
  // bars
  const tot=p.closed_trade_count||1;
  const wp=Math.round((p.win_count||0)/tot*100);
  const lp=Math.round((p.loss_count||0)/tot*100);
  const tp=Math.round((p.timeout_count||0)/tot*100);
  $('bar-win').style.width=`${wp}%`;     $('bar-win-pct').textContent=`${wp}%`;
  $('bar-loss').style.width=`${lp}%`;    $('bar-loss-pct').textContent=`${lp}%`;
  $('bar-timeout').style.width=`${tp}%`; $('bar-timeout-pct').textContent=`${tp}%`;
}

// ── Session Report ──────────────────────────────────────────────────────
async function fetchLastSession() {
  try {
    const r = await fetch('/api/session/last');
    if (!r.ok) return;
    const d = await r.json();
    renderSessionReport(d);
    renderDashboardSummary(d);
  } catch {}
}
function renderDashboardSummary(r) {
  const el = $('dashboard-summary');
  if (!el || !r || !r.run_id) return;
  const reasons = Object.entries(r.reason_counts||{}).sort((a,b)=>b[1]-a[1]);
  el.innerHTML = `
    <div class="section-label">Last Session Summary</div>
    <div class="summary-grid">
      <div><span>Judgement</span><strong>${r.judgement||'--'}</strong></div>
      <div><span>Win rate</span><strong>${Number(r.win_rate||0).toFixed(1)}%</strong></div>
      <div><span>Net PnL</span><strong>${fmt(r.net_pnl_krw)} ₩</strong></div>
      <div><span>Trades</span><strong>${fmt(r.closed_trade_count)}</strong></div>
      <div><span>Duration</span><strong>${durationText(r.duration_sec)}</strong></div>
    </div>
    <div class="summary-reasons">${reasons.map(([reason,count])=>`<span>${reason}: ${count}</span>`).join('')||'<span>No reasons</span>'}</div>`;
}
function renderSessionReport(r) {
  const el = $('session-report');
  if (!r || !r.run_id) { el.innerHTML='<div class="empty-row">세션 리포트 없음</div>'; return; }

  const jc = r.judgement==='PAPER_EDGE_PASS'?'judgement-pass'
           : r.judgement==='PAPER_EDGE_WEAK'?'judgement-weak'
           : r.judgement==='PAPER_EDGE_FAIL'?'judgement-fail':'judgement-info';
  const jmsg = {
    'PAPER_EDGE_PASS':'✅ 이 전략은 paper 기준 승산 있음. tiny_live 검토 가능.',
    'PAPER_EDGE_WEAK':'⚠️ 일부 조건만 충족. 파라미터 조정 후 재검증 권장.',
    'PAPER_EDGE_FAIL':'❌ 이 전략은 paper 기준 승산 없음.',
    'NOT_ENOUGH_TRADES':'ℹ️ 거래 수 부족. 더 긴 시간 검증 필요.',
    'RUNTIME_ERROR':'🔴 런타임 에러로 분석 불가. 로그 확인 필요.',
  }[r.judgement] || r.judgement;

  const dur = Number(r.duration_sec||0);
  const durStr = dur>=3600?`${(dur/3600).toFixed(1)}h`:`${(dur/60).toFixed(0)}m`;
  const net = Number(r.net_pnl_krw||0);

  // reason distribution
  const reasons = Object.entries(r.reason_counts||{}).sort((a,b)=>b[1]-a[1]);
  const reasonHTML = reasons.map(([k,v])=>`<div class="reason-row"><span class="reason-name">${k}</span><span class="reason-cnt">${v}</span></div>`).join('');

  el.innerHTML = `
    <div class="sr-judgement ${jc}">
      <div class="sr-j-title">${r.judgement}</div>
      <div class="sr-j-msg">${jmsg}</div>
    </div>
    <div class="sr-grid">
      <div class="sr-card"><div class="sr-label">Run ID</div><div class="sr-val">${r.run_id}</div></div>
      <div class="sr-card"><div class="sr-label">Duration</div><div class="sr-val">${durStr}</div></div>
      <div class="sr-card"><div class="sr-label">Loops</div><div class="sr-val">${fmt(r.total_loops)}</div></div>
      <div class="sr-card"><div class="sr-label">Quotes</div><div class="sr-val">${fmt(r.quote_count)}</div></div>
      <div class="sr-card"><div class="sr-label">Candidates</div><div class="sr-val">${fmt(r.candidate_count)}</div></div>
      <div class="sr-card"><div class="sr-label">Decisions</div><div class="sr-val">${fmt(r.total_decision_count)}</div></div>
      <div class="sr-card"><div class="sr-label">OK Signals</div><div class="sr-val">${fmt(r.ok_signal_count)}</div></div>
      <div class="sr-card"><div class="sr-label">Max Surplus</div><div class="sr-val">${fmt(r.max_best_net_surplus_bp,1)}bp</div></div>
      <div class="sr-card"><div class="sr-label">Avg Surplus</div><div class="sr-val">${fmt(r.avg_best_net_surplus_bp,1)}bp</div></div>
      <div class="sr-card"><div class="sr-label">Top Signal Sym</div><div class="sr-val">${r.top_symbol_by_signal||'--'}</div></div>
      <div class="sr-card"><div class="sr-label">Top Surplus Sym</div><div class="sr-val">${r.top_symbol_by_surplus||'--'}</div></div>
      <div class="sr-card"><div class="sr-label">Entries</div><div class="sr-val">${r.paper_entry_count}</div></div>
      <div class="sr-card"><div class="sr-label">Closed Trades</div><div class="sr-val">${fmt(r.closed_trade_count)}</div></div>
      <div class="sr-card green"><div class="sr-label">Net PnL</div><div class="sr-val" style="color:${pnlC(net)}">${net>=0?'+':''}${fmt(net)} ₩</div></div>
      <div class="sr-card blue"><div class="sr-label">Win Rate</div><div class="sr-val">${Number(r.win_rate||0).toFixed(1)}%</div></div>
      <div class="sr-card"><div class="sr-label">Clean Win</div><div class="sr-val">${Number(r.clean_win_ratio||0).toFixed(1)}%</div></div>
      <div class="sr-card"><div class="sr-label">Avg PnL</div><div class="sr-val">${fmt(r.avg_pnl_krw)} ₩</div></div>
      <div class="sr-card red"><div class="sr-label">Max DD</div><div class="sr-val">${fmt(r.max_drawdown_krw)} ₩</div></div>
      <div class="sr-card"><div class="sr-label">Best Sym</div><div class="sr-val">${r.best_symbol||'--'}</div></div>
    </div>
    <div class="section-label" style="margin-top:1.5rem">인프라 & 스트레스</div>
    <div class="sr-grid">
      <div class="sr-card"><div class="sr-label">P95 Loop</div><div class="sr-val">${fmt(r.p95_loop_latency_ms)}ms</div></div>
      <div class="sr-card"><div class="sr-label">P95 Quote</div><div class="sr-val">${fmt(r.p95_quote_latency_ms)}ms</div></div>
      <div class="sr-card"><div class="sr-label">Network</div><div class="sr-val">${r.network_health||'--'}</div></div>
      <div class="sr-card"><div class="sr-label">WS Ratio</div><div class="sr-val">${fmt(r.ws_ratio,1)}%</div></div>
      <div class="sr-card"><div class="sr-label">REST Fallback</div><div class="sr-val">${fmt(r.rest_fallback_count)}</div></div>
      <div class="sr-card"><div class="sr-label">Stale Quotes</div><div class="sr-val">${fmt(r.stale_quote_count)}</div></div>
      <div class="sr-card"><div class="sr-label">Quality</div><div class="sr-val">${r.trading_quality||'--'}</div></div>
      <div class="sr-card"><div class="sr-label">Slippage</div><div class="sr-val">${r.configured_slippage_bp}bp</div></div>
      <div class="sr-card"><div class="sr-label">Avg Dynamic Slippage</div><div class="sr-val">${fmt(r.avg_dynamic_slippage_bp,1)}bp</div></div>
      <div class="sr-card"><div class="sr-label">Max Dynamic Slippage</div><div class="sr-val">${fmt(r.max_dynamic_slippage_bp,1)}bp</div></div>
      <div class="sr-card"><div class="sr-label">Low Depth</div><div class="sr-val">${fmt(r.low_depth_count)}</div></div>
      <div class="sr-card"><div class="sr-label">Simulated Fill Latency</div><div class="sr-val">${fmt(r.avg_latency_used_ms)}ms</div></div>
      <div class="sr-card"><div class="sr-label">Paper Edge PASS / FAIL</div><div class="sr-val">${fmt(r.paper_edge_pass_count)} / ${fmt(r.paper_edge_fail_count)}</div></div>
      <div class="sr-card"><div class="sr-label">+5bp Stress</div><div class="sr-val">${fmt(r.slippage_stress_plus_5bp_estimated_pnl)} ₩</div></div>
      <div class="sr-card"><div class="sr-label">+10bp Stress</div><div class="sr-val">${fmt(r.slippage_stress_plus_10bp_estimated_pnl)} ₩</div></div>
      <div class="sr-card"><div class="sr-label">Log Size</div><div class="sr-val">${Number(r.log_total_size_mb||0).toFixed(2)} MB</div></div>
    </div>
    <div class="section-label" style="margin-top:1.5rem">Reason 분포</div>
    <div class="reason-grid">${reasonHTML||'<div class="empty-row">없음</div>'}</div>
  `;
}

// ── Keys ─────────────────────────────────────────────────────────────────
async function fetchKeyStatus() {
  try {
    const r = await fetch('/api/keys/status');
    if (!r.ok) return;
    const s = await r.json();
    ['UPBIT_ACCESS_KEY','UPBIT_SECRET_KEY','BINANCE_API_KEY','BINANCE_API_SECRET','BITHUMB_ACCESS_KEY','BITHUMB_SECRET_KEY'].forEach(k => {
      const b = $(`badge-${k}`);
      if (!b) return;
      const v = s[k]||'Missing';
      b.textContent = v;
      b.className = `key-status-badge ${v==='Set'?'set':'missing'}`;
    });
  } catch {}
}

async function fetchInventory() {
  try {
    const pair = selectedPair();
    const [inventoryRes, readinessRes, statusRes, planRes] = await Promise.all([
      fetch(`/api/inventory?pair=${encodeURIComponent(pair)}`),
      fetch(`/api/live/readiness?pair=${encodeURIComponent(pair)}`),
      fetch('/api/tiny-live/status'),
      fetch('/api/execution/last-plan'),
    ]);
    if (!inventoryRes.ok || !readinessRes.ok || !statusRes.ok || !planRes.ok) return;
    renderInventory(await inventoryRes.json(), await readinessRes.json(), await statusRes.json(), await planRes.json());
  } catch (error) {
    console.error('[fetchInventory] failed', error);
  }
}

function renderInventory(inventory, readiness, tinyStatus={}, lastPreflight={}) {
  const balances = inventory.balances||{};
  const upbit = balances.upbit||{}, binance = balances.binance||{}, bithumb = balances.bithumb||{};
  const symbols = inventory.symbols||[];
  const balancesEl = $('inventory-balances');
  if (balancesEl) balancesEl.innerHTML = `
    <div><span>Upbit KRW</span><strong>${fmt(upbit.KRW)} KRW</strong></div>
    <div><span>Binance USDT</span><strong>${fmt(binance.USDT,2)} USDT</strong></div>
    <div><span>Bithumb KRW</span><strong>${fmt(bithumb.KRW)} KRW</strong></div>`;
  const readyEl = $('inventory-readiness');
  if (readyEl) {
    readyEl.className = `inventory-readiness ${readiness.ready?'yes':'no'}`;
    readyEl.innerHTML = `<span>Tiny Live Ready</span><strong>${readiness.ready?'YES':'NO'}</strong>`;
  }
  setText('inventory-blockers', readiness.blockers?.length
    ? `Blockers: ${readiness.blockers.join(' / ')}`
    : 'No blockers. Manual review is still required.');
  renderTinyLivePanel(readiness, tinyStatus);
  renderLiveGuard(readiness.live_guard||{}, readiness);
  renderExecutionPlan(lastPreflight.plan || tinyStatus.last_preflight?.plan || {});
  const grid = $('inventory-grid');
  if (!grid) return;
  grid.innerHTML = symbols.map(row => `
    <div class="inventory-card ${row.status==='OK'?'ok':'shortage'}">
      <div class="inventory-symbol">${esc(row.symbol)}</div>
      <div class="inventory-qty">Upbit ${fmt(row.upbit_coin_qty,6)} / ${row.pair_id==='UPBIT_BITHUMB'?'Bithumb':'Binance'} ${fmt(row.bithumb_coin_qty??row.binance_coin_qty,6)}</div>
      <div class="inventory-directions">
        <span class="${row.direction_a_possible?'ok':'no'}">Direction A ${row.direction_a_possible?'YES':'NO'}</span>
        <span class="${row.direction_b_possible?'ok':'no'}">Direction B ${row.direction_b_possible?'YES':'NO'}</span>
      </div>
      <div class="inventory-missing">A missing: ${(row.missing_for_a||[]).map(esc).join(', ')||'none'}</div>
      <div class="inventory-missing">B missing: ${(row.missing_for_b||[]).map(esc).join(', ')||'none'}</div>
      <div class="inventory-manual">Manual Rebalance: ${esc(row.recommended_manual_action)}</div>
    </div>`).join('') || '<div class="empty-row">Inventory unavailable. Review blockers.</div>';
}

async function fetchTelemetry() {
  try {
    const r = await fetch('/api/telemetry');
    if (!r.ok) return;
    const d = await r.json();
    latestTelemetry = d.telemetry||{};
    renderLongRunTelemetry(latestTelemetry);
  } catch (error) {
    console.error('[telemetry] failed', error);
  }
}

function renderLongRunTelemetry(t={}) {
  const summary = t.quote_source_summary||{};
  const wsOk = Boolean(t.ws_connected) && Number(t.ws_symbols_ok||0)>0;
  const fallback = Number(t.rest_fallback_count||0);
  const stale = Number(t.quote_stale_count||0);
  const noGo = Object.entries(t.no_go_reason_counts||{}).sort((a,b)=>b[1]-a[1]).slice(0,3);
  setText('metric-decision-count', fmt(t.total_decision_count||t.decision_count));
  setText('metric-candidate-count', fmt(t.candidate_count));
  setText('metric-ok-signal-count', fmt(t.ok_signal_count));
  setText('metric-max-surplus', `${fmt(t.max_best_net_surplus_bp,1)} bp`);
  setText('metric-best-symbol', t.top_symbol_by_surplus||'--');
  setText('metric-last-decision', t.last_decision_at ? ageText(Date.now()/1000-t.last_decision_at) : '--');
  setText('ws-status', wsOk ? `WS OK ${fmt(summary.ws)}` : 'WS WAITING');
  setClass('ws-status', `source-badge ${wsOk?'ok':'waiting'}`);
  setText('rest-status', `REST FALLBACK ${fmt(fallback)} / throttle ${fmt(t.rate_limit_throttle_count)}`);
  setClass('rest-status', `source-badge ${fallback?'fallback':'ok'}`);
  setText('stale-status', `STALE ${fmt(stale)}`);
  setClass('stale-status', `source-badge ${stale?'stale':'ok'}`);
  setText('no-go-top3', `NO-GO top 3: ${noGo.map(([reason,count])=>`${reason} ${count}`).join(' / ')||'--'}`);
  renderWebSocketHealth(t);
  renderMemoryTelemetry(t);
  renderBithumbCacheStatus(t);
}

function renderMemoryTelemetry(t={}) {
  let memoryEl=document.getElementById('memory-telemetry-status');
  if (!memoryEl) {
    const anchor=document.getElementById('no-go-top3');
    if (anchor?.parentElement) {
      memoryEl=document.createElement('div');
      memoryEl.id='memory-telemetry-status';
      memoryEl.className='active-symbol-list';
      anchor.parentElement.append(memoryEl);
    }
  }
  if (memoryEl) {
    const memory=t.memory_metric_available ? `${fmt(t.process_memory_mb,2)} MB` : 'unavailable';
    memoryEl.textContent=`Quote History Keys ${fmt(t.quote_history_key_count)} | Quote History Rows ${fmt(t.quote_history_row_count)} | Process Memory ${memory} | Memory Metric Available ${String(Boolean(t.memory_metric_available))} | Per-leg Latency Model ${t.paper_fill_latency_model||'--'}`;
  }
}

function renderWebSocketHealth(t={}) {
  let wsHealthEl=document.getElementById('ws-health-status');
  if (!wsHealthEl) {
    const anchor=document.getElementById('no-go-top3');
    if (anchor?.parentElement) {
      wsHealthEl=document.createElement('div');
      wsHealthEl.id='ws-health-status';
      wsHealthEl.className='active-symbol-list';
      anchor.parentElement.append(wsHealthEl);
    }
  }
  if (wsHealthEl) {
    const lastError=t.ws_last_error||'none';
    wsHealthEl.textContent=`WebSocket: Last Msg Age ${fmt(t.last_msg_age_ms)} ms | Upbit ${fmt(t.upbit_last_msg_age_ms)} ms | Binance ${fmt(t.binance_last_msg_age_ms)} ms | Reconnect ${fmt(t.ws_reconnect_count)} | Last Error ${lastError} | Out-of-order Drops ${fmt(t.out_of_order_drop_count)}`;
  }
}

function renderBithumbCacheStatus(t={}) {
  const cache=t.bithumb_quote_cache_status||{};
  let cacheEl=document.getElementById('bithumb-cache-status');
  if (!cacheEl) {
    const anchor=document.getElementById('scanner-active-symbols');
    if (anchor?.parentElement) {
      cacheEl=document.createElement('div');
      cacheEl.id='bithumb-cache-status';
      cacheEl.className='active-symbol-list';
      anchor.parentElement.append(cacheEl);
    }
  }
  if (cacheEl) {
    const lastSuccess=cache.last_success_age_ms==null ? '--' : `${fmt(cache.last_success_age_ms)} ms`;
    cacheEl.textContent=`Bithumb Cache: ${cache.enabled?'ON':'OFF'} | Running ${cache.running?'YES':'NO'} | Stale / Quote ${fmt(cache.stale_count)} / ${fmt(cache.quote_count)} | TS Fallback ${fmt(cache.quote_ts_fallback_count)} | TS Normalized ${fmt(cache.quote_ts_normalized_count)} | Last Success Age ${lastSuccess} | Fail Count ${fmt(cache.fail_count)} | Skipped Bithumb Symbols ${fmt(t.skipped_bithumb_symbol_count)} | Quote History Keys ${fmt(t.quote_history_key_count)}`;
  }
}

async function fetchRuntimeServices() {
  try {
    const [scannerRes, storeRes, rateLimitRes] = await Promise.all([
      fetch('/api/market/scanner'),
      fetch('/api/runtime-store/status'),
      fetch('/api/rate-limit/status'),
    ]);
    if (!scannerRes.ok || !storeRes.ok || !rateLimitRes.ok) return;
    latestScanner = await scannerRes.json();
    latestRuntimeStore = await storeRes.json();
    latestRateLimit = await rateLimitRes.json();
    renderRuntimeServices(latestScanner, latestRuntimeStore, latestRateLimit);
  } catch (error) {
    console.error('[runtime services] failed', error);
  }
}

function renderRuntimeServices(scanner={}, store={}, rateLimit={}) {
  const symbols = scanner.active_symbols||[];
  setText('scanner-active-count', fmt(symbols.length));
  setText('scanner-source', scanner.source||'--');
  setText('scanner-active-symbols', `Active Symbols: ${symbols.join(', ')||'--'}`);
  setText('runtime-store-enabled', store.enabled ? 'ENABLED' : 'DISABLED');
  setText('runtime-store-writes', fmt(store.snapshot_write_count));
  setText('runtime-store-fails', fmt(store.snapshot_fail_count));
  setText('runtime-store-age', store.snapshot_age_sec==null ? '--' : ageText(store.snapshot_age_sec));
  let storeWarningEl = document.getElementById('runtime-store-warning');
  if (!storeWarningEl) {
    const anchor = document.getElementById('scanner-active-symbols');
    if (anchor?.parentElement) {
      storeWarningEl = document.createElement('div');
      storeWarningEl.id = 'runtime-store-warning';
      storeWarningEl.className = 'active-symbol-list';
      anchor.parentElement.append(storeWarningEl);
    }
  }
  if (storeWarningEl) {
    storeWarningEl.textContent = store.enabled ? '' : 'RuntimeStore OFF: RUNTIME_STORE_DISABLED_WARNING. Long paper/live monitoring recommends runtime_store_enabled=true.';
  }
  let scannerStatusEl = document.getElementById('scanner-cache-status');
  if (!scannerStatusEl) {
    const anchor = document.getElementById('scanner-active-symbols');
    if (anchor?.parentElement) {
      scannerStatusEl = document.createElement('div');
      scannerStatusEl.id = 'scanner-cache-status';
      scannerStatusEl.className = 'active-symbol-list';
      anchor.parentElement.append(scannerStatusEl);
    }
  }
  if (scannerStatusEl) {
    const blockers=(scanner.blockers||[]).join(', ')||'none';
    const cacheAge=scanner.scanner_cache_age_sec==null ? '--' : ageText(scanner.scanner_cache_age_sec);
    scannerStatusEl.textContent=`Scanner Cache: ${scanner.scanner_cache_used?'USED':'NOT USED'} | Cache Age ${cacheAge} | Startup ${scanner.scanner_startup_mode||'--'} | Last Refresh ${scanner.scanner_last_refresh_status||'--'} | Fail Count ${fmt(scanner.scanner_fail_count)} | Blockers ${blockers}`;
  }
  let rateLimitEl = document.getElementById('rate-limit-status');
  if (!rateLimitEl) {
    const anchor = document.getElementById('scanner-active-symbols');
    if (anchor?.parentElement) {
      rateLimitEl = document.createElement('div');
      rateLimitEl.id = 'rate-limit-status';
      rateLimitEl.className = 'active-symbol-list';
      anchor.parentElement.append(rateLimitEl);
    }
  }
  if (rateLimitEl) {
    const status=rateLimit.rate_limit||{}, exchanges=status.exchanges||{};
    const backoff=Object.entries(exchanges).filter(([,item])=>item.backoff_active).map(([name])=>name.toUpperCase());
    rateLimitEl.textContent = `Rate Limit: ${status.enabled?'ENABLED':'DISABLED'} | throttle ${fmt(status.total_throttle_count)} | 429 ${fmt(status.total_api_429_count)} | backoff ${backoff.join(', ')||'none'} | REST fallback ${fmt(rateLimit.rest_fallback_count)} | skipped ${fmt(rateLimit.rest_fallback_skip_count)}`;
  }
}

async function fetchIcebergStatus() {
  try {
    const r = await fetch('/api/iceberg/status');
    if (!r.ok) return;
    renderIcebergStatus((await r.json()).iceberg||{});
  } catch (error) {
    console.error('[Iceberg status] failed', error);
  }
}

function renderIcebergStatus(iceberg={}) {
  const grid = $('iceberg-grid');
  if (!grid) return;
  const fields = [
    ['Enabled', String(Boolean(iceberg.enabled))],
    ['Execution Enabled', String(Boolean(iceberg.execution_enabled))],
    ['Min Order', `${fmt(iceberg.min_order_krw)} KRW`],
    ['Slice Count', fmt(iceberg.slice_count)],
    ['Slice Interval', `${fmt(iceberg.slice_interval_ms)} ms`],
    ['Max Total Slippage', `${fmt(iceberg.max_total_slippage_bp,1)} bp`],
  ];
  grid.innerHTML=fields.map(([label,value])=>`<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
  setText('iceberg-warning', iceberg.warnings?.length
    ? `Large order warning: ${iceberg.warnings.join(' / ')}`
    : 'Large order warning: none');
}

function renderTinyLivePanel(readiness, tinyStatus={}) {
  const status = tinyStatus.status||'DISARMED';
  const partialRisk = tinyStatus.partial_risk || status==='PARTIAL_RISK';
  setText('tiny-live-status', status);
  setClass('tiny-live-status', `tiny-live-status ${partialRisk?'danger':tinyStatus.armed?'armed':'disarmed'}`);
  setText('tiny-live-armed', String(Boolean(tinyStatus.armed)));
  setText('tiny-live-partial-risk', String(Boolean(partialRisk)));
  setText('tiny-live-trade-count', fmt(tinyStatus.trade_count));
  setText('tiny-live-daily-loss', `${fmt(tinyStatus.daily_loss_krw)} KRW`);
  const domestic=readiness.pair_id==='UPBIT_BITHUMB';
  setText('selected-pair-note', domestic?'Domestic KRW pair / FX not required / Bithumb live gates required':'Cross-border Spot / FX required');
  setText('tiny-live-limits', `Order ${fmt(domestic?readiness.limits?.upbit_bithumb_order_krw:readiness.limits?.tiny_live_order_krw)} KRW / Max ${fmt(domestic?readiness.limits?.upbit_bithumb_max_order_krw:readiness.limits?.tiny_live_max_order_krw)} KRW`);
  const last = tinyStatus.last_order?.status || tinyStatus.last_preflight?.blockers?.join(' / ') || 'No preflight result.';
  setText('tiny-live-result', `Last result: ${last}`);
  setText('tiny-live-next-action', readiness.next_action||'Review blockers before arming.');
  setText('tiny-live-blockers', readiness.blockers?.length ? `Blockers: ${readiness.blockers.join(' / ')}` : 'Blockers: none');
  setText('tiny-live-warnings', readiness.warnings?.length ? `Warnings: ${readiness.warnings.join(' / ')}` : 'Warnings: none');
  setText('tiny-live-warning', partialRisk
    ? 'PARTIAL_RISK: new entries blocked. Check both venue fills and balances. DISARM after manual cleanup. Automatic repeated orders are disabled.'
    : '');
  setClass('tiny-live-warning', `tiny-live-warning ${partialRisk?'visible':''}`);
  setDisabled('btn-tiny-execute', !tinyStatus.armed || !readiness.ready || partialRisk);
  renderOrderTracker(tinyStatus.order_tracker||{}, tinyStatus.emergency||{});
}

function renderOrderTracker(tracker={}, emergency={}) {
  const grid = $('order-tracker-grid');
  if (!grid) return;
  if (!tracker.plan_id) {
    grid.innerHTML='<div class="empty-row">No tracked tiny-live order.</div>';
  } else {
    const upbit=tracker.left_leg||tracker.upbit_leg||{}, binance=tracker.right_leg||tracker.binance_leg||{};
    const fields = [
      ['Status', tracker.status], ['Plan ID', tracker.plan_id], ['Selected Pair', tracker.pair_id||'UPBIT_BINANCE'], [`${upbit.venue||'Upbit'} Leg`, upbit.status||'--'],
      [`${binance.venue||'Right'} Leg`, binance.status||'--'], ['Net Filled Qty', fmt(tracker.net_filled_qty,8)],
      ['Exposure Qty', `${fmt(tracker.exposure_qty,8)} ${tracker.exposure_side||''}`],
      ['Partial Risk', String(tracker.status==='PARTIAL_RISK')], ['Emergency Required', String(Boolean(tracker.emergency_required))],
      ['Exposure Side', tracker.exposure_side||'FLAT'], ['Exposure Notional KRW', `${fmt(tracker.exposure_notional_krw)} KRW`],
      ['Failed Leg', tracker.failed_leg||'--'], ['Filled Leg', tracker.filled_leg||'--'],
      ['Strategy', tracker.emergency_strategy||emergency.strategy||'COMPLETE_MISSING_LEG'],
      ['Emergency Status', tracker.emergency_status||emergency.emergency_status||'NOT_REQUIRED'],
      ['Emergency Attempted', String(Boolean(tracker.emergency_attempted))], ['Emergency Enabled', String(Boolean(emergency.enabled))],
      ['Auto Execute OFF', String(!emergency.auto_execute)], ['One Attempt Per Plan', String(Boolean(emergency.one_attempt_per_plan))],
    ];
    grid.innerHTML=fields.map(([label,value])=>`<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
  }
  const partial=tracker.status==='PARTIAL_RISK'||tracker.status==='EMERGENCY_PENDING'||tracker.status==='EMERGENCY_FAILED';
  setText('order-tracker-warning', partial
    ? 'PARTIAL_RISK: new entries blocked. Inspect Upbit fills, Binance fills, and both balances. Resolve Spot exposure manually before clearing.'
    : '');
  setClass('order-tracker-warning', `tiny-live-warning ${partial?'visible':''}`);
  setText('order-tracker-action', `Manual Action: ${tracker.emergency_manual_action||tracker.suggested_manual_action||emergency.suggested_manual_action||'No manual action required.'}`);
}

// Live Guard and Execution Plan render read-only safety state from the API.
function renderLiveGuard(guard, readiness) {
  const keyStatus = guard.key_status||readiness.key_status||{};
  const keysOk = Object.values(keyStatus).length>0 && Object.values(keyStatus).every(value=>value==='Set');
  const fields = [
    ['Mode', guard.mode, guard.mode==='tiny_live'],
    ['Enable live trading', guard.enable_live_trading, guard.enable_live_trading],
    ['Tiny live enabled', guard.tiny_live_enabled, guard.tiny_live_enabled],
    ['Live order enabled', guard.live_order_enabled, guard.live_order_enabled],
    ['Live mode enabled', guard.live_mode_enabled, guard.live_mode_enabled],
    ['Withdrawals disabled', !guard.withdrawals_enabled, !guard.withdrawals_enabled],
    ['Futures hedge disabled', !guard.futures_hedge_enabled, !guard.futures_hedge_enabled],
    ['Manual rebalance only', guard.manual_rebalance_only, guard.manual_rebalance_only],
    ['Paper pass', guard.paper_pass, guard.paper_pass],
    ['Keys', keysOk?'SET':'MISSING', keysOk],
    ['Inventory', guard.inventory_status||'BLOCKED', guard.inventory_status==='OK'],
    ['Quote freshness', `${guard.quote_freshness||'STALE'}${guard.quote_age_ms==null?'':` ${fmt(guard.quote_age_ms,0)}ms`}`, guard.quote_freshness==='OK'],
    ['Min order', guard.min_order_status||'BLOCKED', guard.min_order_status==='OK'],
  ];
  const grid = $('live-guard-grid');
  if (!grid) return;
  grid.innerHTML = fields.map(([label,value,ok]) => `
    <div class="guard-item ${ok?'ok':'blocked'}">
      <span>${esc(label)}</span><strong>${esc(value)}</strong>
    </div>`).join('');
}

function renderExecutionPlan(plan={}) {
  const el = $('execution-plan');
  const guide = $('execution-direction-guide');
  const domestic = plan.pair_id === 'UPBIT_BITHUMB' || selectedPair() === 'UPBIT_BITHUMB';
  if (guide) guide.innerHTML = domestic
    ? '<span>UPBIT_BITHUMB_A = Upbit SELL / Bithumb BUY</span><span>UPBIT_BITHUMB_B = Upbit BUY / Bithumb SELL</span>'
    : '<span>A_KIMCHI = Upbit SELL / Binance BUY</span><span>B_REVERSE_KIMCHI = Upbit BUY / Binance SELL</span>';
  if (!el) return;
  if (!plan.plan_id) {
    el.textContent = 'Execution Plan: no preflight yet.';
    return;
  }
  const leftVenue = plan.left_venue || 'UPBIT';
  const rightVenue = plan.right_venue || 'BINANCE';
  const leftSide = plan.left_side || plan.upbit_side || '--';
  const rightSide = plan.right_side || plan.binance_side || '--';
  const leftExpected = plan.left_expected_price ?? plan.upbit_expected_price;
  const rightExpected = plan.right_expected_price ?? plan.binance_expected_price;
  const fields = [
    ['Plan ID', plan.plan_id], ['Pair', plan.pair_id], ['Symbol', plan.symbol], ['Direction', plan.direction],
    ['Direction Label', plan.direction_label], [`${leftVenue} Side`, leftSide],
    [`${rightVenue} Side`, rightSide], ['Order KRW', `${fmt(plan.order_krw_used??plan.order_krw)} KRW`],
    ['Effective Qty', fmt(plan.effective_qty??plan.selected_qty??plan.qty,8)],
    ['Raw Depth Qty', fmt(plan.max_fillable_qty_raw,8)], ['Selected Notional', `${fmt(plan.selected_notional_krw)} KRW`],
    ['Order USDT', `${fmt(plan.order_usdt,4)} USDT`], ['Qty', fmt(plan.qty,8)],
    ['Normalized Qty', fmt(plan.normalized_qty,8)], [`${leftVenue} Expected`, fmt(leftExpected,2)],
    [`${rightVenue} Expected`, fmt(rightExpected,8)], ['Quote Source', plan.quote_source||'--'],
    ['Quote Age', `${fmt(plan.quote_age_ms,0)} ms`], ['Net Surplus', `${fmt(plan.best_net_surplus_bp,1)} bp`],
    ['Expected Profit', `${fmt(plan.expected_net_profit_krw)} KRW`], ['Preflight', plan.preflight_status],
    ['Executable', String(Boolean(plan.executable))], ['Iceberg Required', String(Boolean(plan.iceberg_required))],
    ['Iceberg Enabled', String(Boolean(plan.iceberg_enabled))], ['Iceberg Execution', String(Boolean(plan.iceberg_execution_enabled))],
    ['Iceberg Slices', fmt(plan.iceberg_slice_count)], ['Iceberg Warning', plan.iceberg_warning||'none'],
  ];
  el.innerHTML = `
    <div class="execution-plan-grid">${fields.map(([label,value])=>`<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('')}</div>
    <div class="execution-plan-list">Blockers: ${(plan.blockers||[]).map(esc).join(' / ')||'none'}</div>
    <div class="execution-plan-list">Warnings: ${(plan.warnings||[]).map(esc).join(' / ')||'none'}</div>`;
}

async function tinyLiveAction(action) {
  if (action === 'execute-once' && !confirm('Execute one guarded tiny-live Spot order pair?')) return;
  try {
    const label = TINY_LIVE_ACTION_LABELS[action]||action;
    const res = await fetch(`/api/tiny-live/${action}`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({pair_id:selectedPair()}) });
    const data = await res.json();
    setText('tiny-live-result', data.ok
      ? `${label}: ${data.status}`
      : `${label} blocked: ${(data.blockers||[]).join(' / ')||data.error||'UNKNOWN_ERROR'}`);
    fetchInventory();
  } catch (error) {
    console.error('[tinyLiveAction] failed', error);
  }
}

function selectedPair() { return $('live-pair-select')?.value || 'UPBIT_BINANCE'; }

on('btn-tiny-arm', 'click', () => tinyLiveAction('arm'));
on('btn-tiny-disarm', 'click', () => tinyLiveAction('disarm'));
on('btn-tiny-execute', 'click', () => tinyLiveAction('execute-once'));
on('live-pair-select', 'change', fetchInventory);
on('btn-manual-clear', 'click', async () => {
  if (!confirm('Manual clear does not place an order. Continue only after checking both exchanges and resolving any Spot exposure.')) return;
  const reason = prompt('Enter the manual clearing reason:');
  if (!reason?.trim()) return;
  try {
    const res = await fetch('/api/emergency/manual-clear', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reason:reason.trim()}),
    });
    const data = await res.json();
    setText('tiny-live-result', data.ok ? 'MANUAL CLEAR recorded: DISARMED' : `MANUAL CLEAR blocked: ${data.error||'UNKNOWN_ERROR'}`);
    fetchInventory();
  } catch (error) {
    console.error('[Emergency manual clear] failed', error);
  }
});

on('btn-save-keys', 'click', async () => {
  const body = {
    upbit_access_key:  $('inp-upbit-access').value,
    upbit_secret_key:  $('inp-upbit-secret').value,
    binance_api_key:   $('inp-binance-api').value,
    binance_api_secret:$('inp-binance-secret').value,
    bithumb_access_key: $('inp-bithumb-access').value,
    bithumb_secret_key: $('inp-bithumb-secret').value,
  };
  const r = $('save-result');
  r.textContent='저장 중...'; r.className='save-result';
  try {
    const res = await fetch('/api/keys/save', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d = await res.json();
    r.textContent = d.message || (d.ok?'완료':'실패');
    r.className = `save-result ${d.ok?'ok':'err'}`;
    ['inp-upbit-access','inp-upbit-secret','inp-binance-api','inp-binance-secret','inp-bithumb-access','inp-bithumb-secret'].forEach(id=>$(id).value='');
    fetchKeyStatus();
  } catch { r.textContent='연결 실패'; r.className='save-result err'; }
});

// ── Init ─────────────────────────────────────────────────────────────────
fetchData();
fetchTelemetry();
fetchRuntimeServices();
fetchIcebergStatus();
fetchDecisions();
fetchPerf();
fetchLastSession();
fetchInventory();
setInterval(fetchData,   POLL_MS);
setInterval(fetchPerf,   POLL_MS*2);
setInterval(fetchTrades, POLL_MS*2);
setInterval(fetchTelemetry, POLL_MS);
setInterval(fetchRuntimeServices, POLL_MS);
setInterval(fetchIcebergStatus, POLL_MS*2);
setInterval(fetchDecisions, POLL_MS*2);
setInterval(() => renderTelemetry(latestState, latestEngine, latestControl), 1000);
