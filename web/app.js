/**
 * app.js – KARB Realtime V1 Dashboard (v3)
 * 5탭: dashboard / trades / perf / session / keys
 * STOP 버튼, 세션 배너, 세션 리포트 표시
 */
const POLL_MS = 3000;
const $ = id => document.getElementById(id);
const fmt  = (n, d=0) => Number(n||0).toLocaleString('ko-KR',{maximumFractionDigits:d});
const pnlC = n => n >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

// ── Tab Navigation ──────────────────────────────────────────────────────
document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${tab}`).classList.add('active');
    $('page-title').textContent = btn.textContent.trim();
    if (tab === 'keys')    fetchKeyStatus();
    if (tab === 'session') fetchLastSession();
    if (tab === 'perf')    fetchPerf();
    if (tab === 'trades')  fetchTrades();
  });
});

// ── Connection ──────────────────────────────────────────────────────────
function setConn(ok) {
  $('conn-dot').className = ok ? 'conn-dot live' : 'conn-dot err';
  $('conn-label').textContent = ok ? '연결됨' : '연결 실패';
}

// ── STOP Button ─────────────────────────────────────────────────────────
$('btn-stop').addEventListener('click', async () => {
  if (!confirm('엔진을 정지하시겠습니까? 세션 리포트가 자동 생성됩니다.')) return;
  try {
    const res = await fetch('/api/stop', {method:'POST'});
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
    setConn(true);
    renderTopbar(d.state||{});
    renderBanner(d.state||{}, d.control||{});
    renderQuotes(d.quotes||{});
    $('last-update').textContent = new Date().toLocaleTimeString('ko-KR');
  } catch { setConn(false); }
}

// ── Topbar ──────────────────────────────────────────────────────────────
function renderTopbar(s) {
  $('stat-session').textContent = s.run_id ? s.run_id.slice(0,15) : '--';
  $('stat-fx').textContent      = s.krw_usdt ? fmt(s.krw_usdt,1) : '--';
  $('stat-trades').textContent  = `${s.open_trades??'--'} / ${s.closed_trades??'--'}`;
  const pnl = Number(s.net_pnl_krw||0);
  const pe = $('stat-pnl');
  pe.textContent = `${pnl>=0?'+':''}${fmt(pnl)} ₩`;
  pe.style.color = pnlC(pnl);
  $('stat-winrate').textContent = s.win_rate!=null ? `${Number(s.win_rate).toFixed(1)}%` : '--';
}

// ── Session Banner ──────────────────────────────────────────────────────
function renderBanner(state, ctrl) {
  const b = $('session-banner');
  if (!ctrl.run_id) { b.style.display='none'; return; }
  b.style.display = 'flex';
  const status = ctrl.status || 'UNKNOWN';
  $('sb-status').textContent = status;
  $('sb-status').className   = `sb-status ${status==='RUNNING'?'running':'stopped'}`;
  $('sb-runid').textContent  = ctrl.run_id;
  const rt = state.runtime_sec || 0;
  const m = Math.floor(rt/60), s = Math.floor(rt%60);
  $('sb-runtime').textContent = `${m}m ${s}s`;
  $('sb-reason').textContent  = state.latest_reason || '--';
}

// ── Quote Grid ──────────────────────────────────────────────────────────
function renderQuotes(quotes) {
  const grid = $('quotes-grid');
  const syms = Object.keys(quotes);
  if (!syms.length) { grid.innerHTML='<div class="loading-placeholder">데이터 없음</div>'; return; }

  grid.innerHTML = syms.map(sym => {
    const q=quotes[sym]||{}, up=q.upbit||{}, bn=q.binance||{}, c=q.calc||{};
    const kimp=Number(c.kimchi_premium_pct||0), surplus=Number(c.best_net_surplus_bp||0);
    const net=Number(c.net_expected_profit_krw||0), gross=Number(c.gross_gap_krw||0);
    const qty=Number(c.max_fillable_qty||0), dir=c.best_direction||'--';
    const reason=c.reason_no_trade||'', isGo=reason==='OK';
    const ub=fmt(up.bid||0), ua=fmt(up.ask||0);
    const bb=Number(bn.bid||0).toFixed(4), ba=Number(bn.ask||0).toFixed(4);
    return `<div class="quote-card ${isGo?'go':'nogo'}">
      <div class="qc-header">
        <span class="qc-symbol">${sym}</span>
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
        ${reason&&reason!=='OK'?`<span class="qc-reason">${reason}</span>`:''}
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
  } catch {}
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

$('btn-save-keys').addEventListener('click', async () => {
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
