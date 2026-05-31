async function fetchData() {
    try {
        const response = await fetch('/api/data');
        const data = await response.json();
        
        if (data.state) {
            document.getElementById('mode').textContent = data.state.mode || '--';
            document.getElementById('krw-usdt').textContent = data.state.krw_usdt ? data.state.krw_usdt.toFixed(2) : '--';
            document.getElementById('paper-trades').textContent = data.state.paper_trade_count || 0;
        }
        
        if (data.quotes) {
            const tbody = document.getElementById('quotes-body');
            tbody.innerHTML = '';
            
            for (const [sym, quote] of Object.entries(data.quotes)) {
                const upbitMid = (quote.upbit.bid + quote.upbit.ask) / 2;
                const binanceMid = (quote.binance.bid + quote.binance.ask) / 2;
                const fx = data.state.krw_usdt || 1350;
                
                const kimp = ((upbitMid - binanceMid * fx) / (binanceMid * fx) * 100).toFixed(2);
                
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${sym}</td>
                    <td>${upbitMid.toLocaleString()} KRW</td>
                    <td>${binanceMid.toFixed(2)} USDT</td>
                    <td>${kimp}%</td>
                    <td class="no-go">No-Go</td>
                    <td>Check backend logs</td>
                `;
                tbody.appendChild(tr);
            }
        }
    } catch (err) {
        console.error("Failed to fetch data", err);
    }
}

setInterval(fetchData, 1000);
fetchData();
