/**
 * app.js – KARB Realtime V1 Dashboard (v3)
 * 5탭: dashboard / trades / perf / session / keys
 * STOP 버튼, 세션 배너, 세션 리포트 표시
 */
const POLL_MS = 3000;
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
const setText = (id, value) => { const el=$(id); if (el) el.textContent=value; };
const setClass = (id, value) => { const el=$(id); if (el) el.className=value; };
const setStyle = (id, name, value) => { const el=$(id); if (el) el.style[name]=value; };
const setDisabled = (id, value) => { const el=$(id); if (el) el.disabled=value; };
const on = (id, event, handler) => { const el=$(id); if (el) el.addEventListener(event, handler); };
let latestState = {};
let latestEngine = {};
let latestControl = {};
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
  const grid = $('quotes-grid');
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
    if (tab === 'perf')    fetchPerf();
    if (tab === 'trades')  fetchTrades();
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
    renderTopbar(d.state||{}, d.engine||{});
    renderBanner(d.state||{}, d.control||{});
    renderTelemetry(d.state||{}, d.engine||{}, d.control||{});
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
  setDisabled('btn-start-live', isRunning);
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
function renderQuotes(quotes) {
  const grid = $('quotes-grid');
  if (!grid) return;
  const syms = Object.keys(quotes);
  if (!syms.length) { grid.innerHTML='<div class="loading-placeholder">데이터 없음</div>'; return; }

  grid.innerHTML = syms.map(sym => {
    const q=quotes[sym]||{}, up=q.upbit||{}, bn=q.binance||{}, c=q.calc||{};
    const kimp=Number(c.kimchi_premium_pct||0), surplus=Number(c.best_net_surplus_bp||0);
    const net=Number(c.net_expected_profit_krw||0);
    const qty=Number(c.max_fillable_qty||0), dir=c.best_direction||'--';
    const reason=c.reason_no_trade||'', isGo=reason==='OK';
    const ub=fmt(up.bid||0), ua=fmt(up.ask||0);
    const bb=Number(bn.bid||0).toFixed(4), ba=Number(bn.ask||0).toFixed(4);
    const reasonClass = reason==='OK' ? 'reason-ok'
      : reason==='LOW_SURPLUS' ? 'reason-low-surplus'
      : reason==='WIDE_SPREAD'||reason==='LOW_DEPTH' ? 'reason-warning'
      : reason==='FX_UNTRUSTED' ? 'reason-danger' : 'reason-default';
    return `<div class="quote-card ${isGo?'go':'nogo'} refreshed">
      <div class="qc-header">
        <span class="qc-symbol">${sym} <span class="live-dot"></span><span class="live-label">LIVE</span></span>
        <span class="qc-kimp ${kimp>=0?'pos':'neg'}">${kimp>=0?'+':''}${kimp.toFixed(2)}%</span>
      </div>
      <div class="qc-prices">
        <div><div class="qc-price-exchange">Upbit</div><div class="qc-price-val">${ub} ₩</div><div class="qc-price-bid-ask">bid ${ub} / ask ${ua}</div></div>
        <div><div class="qc-price-exchange">Binance</div><div class="qc-price-val">${bb} $</div><div class="qc-price-bid-ask">bid ${bb} / ask ${ba}</div></div>
      </div>
      <div class="qc-metrics">
        <span class="qc-metric">Dir ${dir}</span>
        <span class="qc-metric">${surplus.toFixed(1)} bp</span>
        <span class="qc-metric">Net ${fmt(net)} ₩</span>
        <span class="qc-metric">Qty ${qty.toFixed(4)}</span>
      </div>
      <div class="qc-verdict">
        <span class="verdict-badge ${isGo?'go-badge':'nogo-badge'}">${isGo?'✓ GO':'✗ NO-GO'}</span>
        <span class="qc-reason ${reasonClass}">${reason||'--'}</span>
      </div>
    </div>`;
  }).join('');
}

// ── Trades ───────────────────────────────────────────────────────────────
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
      <td>${(t.trade_id||'').slice(0,8)}</td><td>${t.symbol||'--'}</td>
      <td class="${dc}">${t.best_direction||'--'}</td><td>${et}</td><td>${xt}</td>
      <td>${t.holding_sec!=null?Number(t.holding_sec).toFixed(0)+'s':'--'}</td>
      <td style="color:${pnlC(pnl)}">${pnl>=0?'+':''}${fmt(pnl)} ₩</td>
      <td class="${wc}">${t.exit_reason||'--'}${t.clean_win?' ★':''}</td>
      <td>${t.win?'✓':'✗'}</td>
    </tr>`;
  }).join('');
}

// ── Performance ─────────────────────────────────────────────────────────
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
      <div class="sr-card"><div class="sr-label">Entries</div><div class="sr-val">${r.paper_entry_count}</div></div>
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
      <div class="sr-card"><div class="sr-label">Quality</div><div class="sr-val">${r.trading_quality||'--'}</div></div>
      <div class="sr-card"><div class="sr-label">Slippage</div><div class="sr-val">${r.configured_slippage_bp}bp</div></div>
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
    ['UPBIT_ACCESS_KEY','UPBIT_SECRET_KEY','BINANCE_API_KEY','BINANCE_API_SECRET'].forEach(k => {
      const b = $(`badge-${k}`);
      if (!b) return;
      const v = s[k]||'Missing';
      b.textContent = v;
      b.className = `key-status-badge ${v==='Set'?'set':'missing'}`;
    });
  } catch {}
}

on('btn-save-keys', 'click', async () => {
  const body = {
    upbit_access_key:  $('inp-upbit-access').value,
    upbit_secret_key:  $('inp-upbit-secret').value,
    binance_api_key:   $('inp-binance-api').value,
    binance_api_secret:$('inp-binance-secret').value,
  };
  const r = $('save-result');
  r.textContent='저장 중...'; r.className='save-result';
  try {
    const res = await fetch('/api/keys/save', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d = await res.json();
    r.textContent = d.message || (d.ok?'완료':'실패');
    r.className = `save-result ${d.ok?'ok':'err'}`;
    ['inp-upbit-access','inp-upbit-secret','inp-binance-api','inp-binance-secret'].forEach(id=>$(id).value='');
    fetchKeyStatus();
  } catch { r.textContent='연결 실패'; r.className='save-result err'; }
});

// ── Init ─────────────────────────────────────────────────────────────────
fetchData();
fetchPerf();
fetchLastSession();
setInterval(fetchData,   POLL_MS);
setInterval(fetchPerf,   POLL_MS*2);
setInterval(fetchTrades, POLL_MS*2);
setInterval(() => renderTelemetry(latestState, latestEngine, latestControl), 1000);
