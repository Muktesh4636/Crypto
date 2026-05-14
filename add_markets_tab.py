#!/usr/bin/env python3
"""Inject Markets tab into the CryptoIntel frontend."""
import re

FRONTEND = '/var/www/crypto.pravoo.in/frontend/index.html'

with open(FRONTEND, 'r') as f:
    html = f.read()

# ── 1. Add nav tab ──────────────────────────────────────────────────────────
old_nav = '<button class="nav-tab" onclick="showPage(\'reddit\')">Reddit</button>'
new_nav = old_nav + '\n    <button class="nav-tab" onclick="showPage(\'markets\')">Markets</button>'
html = html.replace(old_nav, new_nav, 1)

# ── 2. Add stat card for market symbols ─────────────────────────────────────
old_stat = '<div class="stat-card"><div class="stat-label">Macro Events</div>'
new_stat = '''<div class="stat-card"><div class="stat-label">Market Symbols</div><div class="stat-value" id="s-market-symbols" style="color:var(--orange)">—</div><div class="stat-sub">Gold,Stocks,ETFs</div></div>
    <div class="stat-card"><div class="stat-label">Fear &amp; Greed</div><div class="stat-value" id="s-fear-greed" style="color:var(--yellow)">—</div><div class="stat-sub">today's index</div></div>
    ''' + old_stat
html = html.replace(old_stat, new_stat, 1)

# ── 3. Add Markets page before </div> that closes container ─────────────────
reddit_page_end = '''</div>
</div>

<script>'''

markets_page = '''</div>

  <!-- MARKETS -->
  <div id="page-markets" class="page">
    <div class="section-title">Global Markets &amp; Macro Indicators</div>

    <!-- Fear & Greed -->
    <div class="card" style="margin-bottom:16px">
      <div class="card-header" style="display:flex;align-items:center;gap:12px">
        <span style="font-weight:700">Crypto Fear &amp; Greed Index</span>
        <div id="fg-badge" style="padding:4px 14px;border-radius:20px;font-size:13px;font-weight:700;background:var(--bg3)">Loading...</div>
      </div>
      <div class="card-body">
        <canvas id="fgChart" height="80"></canvas>
      </div>
    </div>

    <!-- BTC vs Markets correlation -->
    <div class="card" style="margin-bottom:16px">
      <div class="card-header" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span style="font-weight:700">BTC vs Global Markets (Normalized)</span>
        <div style="display:flex;gap:6px;flex-wrap:wrap" id="corr-toggles"></div>
        <select id="corr-days" onchange="loadCorrelations()" style="margin-left:auto;background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:6px;font-size:12px">
          <option value="365">1 Year</option>
          <option value="730">2 Years</option>
          <option value="1825" selected>5 Years</option>
        </select>
      </div>
      <div class="card-body">
        <canvas id="corrChart2" height="120"></canvas>
      </div>
      <div class="card-body" style="font-size:11px;color:var(--text3);padding-top:0">
        All series normalized to 100 at start date. Shows relative performance vs BTC.
      </div>
    </div>

    <!-- ETF Flows -->
    <div class="card" style="margin-bottom:16px">
      <div class="card-header">
        <span style="font-weight:700">Bitcoin ETF Prices (IBIT · FBTC · GBTC · ARKB · BITB)</span>
        <select id="etf-days" onchange="loadEtfFlows()" style="float:right;background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:6px;font-size:12px">
          <option value="90">3 Months</option>
          <option value="180">6 Months</option>
          <option value="365" selected>1 Year</option>
          <option value="587">All (since Jan 2024)</option>
        </select>
      </div>
      <div class="card-body">
        <canvas id="etfChart" height="100"></canvas>
      </div>
    </div>

    <!-- Live market prices table -->
    <div class="card" style="margin-bottom:16px">
      <div class="card-header"><span style="font-weight:700">Live Market Snapshot</span></div>
      <div class="card-body" id="market-table-wrap">
        <div class="loader"><div class="spinner"></div></div>
      </div>
    </div>

    <!-- Macro news -->
    <div class="card">
      <div class="card-header" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span style="font-weight:700">Macro &amp; Geopolitical News</span>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="filter-btn active" onclick="macroNewsFilter(null,this)">All</button>
          <button class="filter-btn" onclick="macroNewsFilter('macro',this)">Macro</button>
          <button class="filter-btn" onclick="macroNewsFilter('geopolitics',this)">Geopolitics</button>
          <button class="filter-btn" onclick="macroNewsFilter('etf',this)">ETF</button>
          <button class="filter-btn" onclick="macroNewsFilter('commodities',this)">Gold/Oil</button>
          <button class="filter-btn" onclick="macroNewsFilter('regulation',this)">Regulation</button>
          <button class="filter-btn" onclick="macroNewsFilter('monetary',this)">Central Banks</button>
        </div>
      </div>
      <div class="card-body" id="macro-news-list">
        <div class="loader"><div class="spinner"></div></div>
      </div>
    </div>
  </div>
</div>

<script>'''

html = html.replace(reddit_page_end, markets_page, 1)

# ── 4. Add JS stats update for new fields ────────────────────────────────────
old_stat_js = "document.getElementById('s-total-news').textContent = s.total_news || 0;"
new_stat_js = old_stat_js + """
    if(s.market_symbols !== undefined) document.getElementById('s-market-symbols').textContent = s.market_symbols;
    if(s.fear_greed_today !== null && s.fear_greed_today !== undefined) {
      var fg = s.fear_greed_today;
      var fgColor = fg < 25 ? 'var(--red)' : fg < 45 ? 'var(--orange)' : fg < 55 ? 'var(--yellow)' : fg < 75 ? 'var(--green)' : '#00ff88';
      document.getElementById('s-fear-greed').textContent = fg;
      document.getElementById('s-fear-greed').style.color = fgColor;
    }"""
html = html.replace(old_stat_js, new_stat_js, 1)

# ── 5. Add Markets JS at the end before </script> ────────────────────────────
insert_before = '</script>\n</body>'

markets_js = """

// ══════════════════════════════════════════════════════════════
// MARKETS PAGE
// ══════════════════════════════════════════════════════════════
var fgChartObj = null, corrChart2Obj = null, etfChartObj = null;
var activeCorrSeries = ['GOLD','SP500','NASDAQ','DXY'];
var macroNewsCategory = null;

function showPage(name) {
  document.querySelectorAll('.page').forEach(function(p){p.style.display='none';});
  var el = document.getElementById('page-'+name);
  if(el) el.style.display='block';
  document.querySelectorAll('.nav-tab').forEach(function(t){t.classList.remove('active');});
  document.querySelectorAll('.nav-tab').forEach(function(t){if(t.textContent.toLowerCase().startsWith(name.substring(0,3)))t.classList.add('active');});
  if(name==='markets') loadMarketsPage();
}

function loadMarketsPage() {
  loadFearGreed();
  loadCorrelations();
  loadEtfFlows();
  loadMarketTable();
  loadMacroNews();
}

function loadFearGreed() {
  fetch('/api/market/fear-greed?days=365').then(function(r){return r.json();}).then(function(d) {
    if(!d.dates || !d.dates.length) return;
    var latest = d.values[d.values.length-1];
    var latestCls = d.classifications[d.classifications.length-1];
    var badge = document.getElementById('fg-badge');
    badge.textContent = latest + ' — ' + latestCls;
    var clsColors = {'Extreme Fear':'#DC143C','Fear':'#FF6347','Neutral':'#FFD700','Greed':'#90EE90','Extreme Greed':'#228B22'};
    badge.style.background = clsColors[latestCls] || '#888';
    badge.style.color = '#000';
    if(fgChartObj) fgChartObj.destroy();
    fgChartObj = new Chart(document.getElementById('fgChart').getContext('2d'), {
      type: 'bar',
      data: {
        labels: d.dates,
        datasets: [{
          label: 'Fear & Greed',
          data: d.values,
          backgroundColor: d.values.map(function(v){
            return v < 25 ? '#DC143C' : v < 45 ? '#FF6347' : v < 55 ? '#FFD700' : v < 75 ? '#90EE90' : '#228B22';
          }),
          borderWidth: 0
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: {display:false}, tooltip: {
          callbacks: { label: function(ctx) { return ctx.parsed.y + ' — ' + d.classifications[ctx.dataIndex]; } }
        }},
        scales: {
          x: { display: false },
          y: { min:0, max:100, ticks:{color:'#888'}, grid:{color:'#333'} }
        }
      }
    });
  }).catch(function(e){ console.log('F&G error',e); });
}

function loadCorrelations() {
  var days = document.getElementById('corr-days').value;
  fetch('/api/market/correlations?days='+days).then(function(r){return r.json();}).then(function(d) {
    var btcData = d.BTC;
    var btcBase = btcData[0] || 1;

    // Rebuild toggles
    var toggles = document.getElementById('corr-toggles');
    toggles.innerHTML = '';
    Object.keys(d.markets).forEach(function(sym) {
      var meta = d.markets[sym];
      var isActive = activeCorrSeries.indexOf(sym) >= 0;
      var btn = document.createElement('button');
      btn.className = 'filter-btn' + (isActive ? ' active' : '');
      btn.style.borderColor = meta.color;
      if(isActive) btn.style.backgroundColor = meta.color + '33';
      btn.textContent = sym;
      btn.onclick = function() {
        var idx = activeCorrSeries.indexOf(sym);
        if(idx>=0) activeCorrSeries.splice(idx,1); else activeCorrSeries.push(sym);
        loadCorrelations();
      };
      toggles.appendChild(btn);
    });

    // Build normalized datasets
    var datasets = [{
      label: 'BTC',
      data: btcData.map(function(v){ return v ? Math.round(v/btcBase*100*100)/100 : null; }),
      borderColor: '#F7931A',
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.1
    }];

    activeCorrSeries.forEach(function(sym) {
      var meta = d.markets[sym];
      if(!meta) return;
      var vals = d.dates.map(function(dt){ return meta.data[dt] || null; });
      var base = null;
      for(var i=0;i<vals.length;i++){if(vals[i]){base=vals[i];break;}}
      if(!base) return;
      datasets.push({
        label: sym,
        data: vals.map(function(v){ return v ? Math.round(v/base*100*100)/100 : null; }),
        borderColor: meta.color,
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.1,
        borderDash: sym==='DXY' ? [4,4] : []
      });
    });

    if(corrChart2Obj) corrChart2Obj.destroy();
    corrChart2Obj = new Chart(document.getElementById('corrChart2').getContext('2d'), {
      type: 'line',
      data: { labels: d.dates, datasets: datasets },
      options: {
        responsive:true,
        interaction:{mode:'index',intersect:false},
        plugins:{
          legend:{position:'top',labels:{color:'#ccc',boxWidth:12,font:{size:11}}},
          tooltip:{mode:'index'}
        },
        scales:{
          x:{ticks:{color:'#888',maxTicksLimit:12},grid:{color:'#333'}},
          y:{ticks:{color:'#888',callback:function(v){return v+'%'}},grid:{color:'#333'}}
        }
      }
    });
  }).catch(function(e){ console.log('Correlations error',e); });
}

function loadEtfFlows() {
  var days = document.getElementById('etf-days').value;
  fetch('/api/market/etf-flows?days='+days).then(function(r){return r.json();}).then(function(d) {
    var keys = Object.keys(d);
    if(!keys.length) { document.getElementById('etfChart').parentNode.innerHTML='<div style="color:var(--text3);padding:20px">Bitcoin ETF data available from Jan 2024 (IBIT, FBTC, ARKB, BITB) and GBTC from earlier.</div>'; return; }
    var labels = d[keys[0]].dates;
    var datasets = keys.map(function(sym){
      return {
        label: sym,
        data: d[sym].prices,
        borderColor: d[sym].color,
        backgroundColor: 'transparent',
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.1
      };
    });
    if(etfChartObj) etfChartObj.destroy();
    etfChartObj = new Chart(document.getElementById('etfChart').getContext('2d'), {
      type: 'line',
      data: { labels: labels, datasets: datasets },
      options: {
        responsive:true,
        interaction:{mode:'index',intersect:false},
        plugins:{legend:{position:'top',labels:{color:'#ccc',boxWidth:12,font:{size:11}}}},
        scales:{
          x:{ticks:{color:'#888',maxTicksLimit:10},grid:{color:'#333'}},
          y:{ticks:{color:'#888',callback:function(v){return '$'+v.toFixed(2)}},grid:{color:'#333'}}
        }
      }
    });
  }).catch(function(e){ console.log('ETF error',e); });
}

function loadMarketTable() {
  fetch('/api/market/latest').then(function(r){return r.json();}).then(function(data) {
    if(!data.length){ document.getElementById('market-table-wrap').innerHTML='<div style="color:var(--text3)">Market data loading — backfill in progress...</div>'; return; }
    var cats = {'commodity':'Commodities','equity':'Equities','currency':'Currency','volatility':'Volatility','bonds':'Bonds','etf':'ETFs','btc_etf':'Bitcoin ETFs'};
    var byCat = {};
    data.forEach(function(r){
      var cat = (r.symbol==='IBIT'||r.symbol==='FBTC'||r.symbol==='GBTC'||r.symbol==='ARKB'||r.symbol==='BITB') ? 'btc_etf' : (r.symbol==='GLD'?'etf':(r.symbol==='SP500'||r.symbol==='NASDAQ'?'equity':(r.symbol==='DXY'||r.symbol==='TREASURY10Y'?'currency':(r.symbol==='VIX'?'volatility':(r.symbol==='GOLD'||r.symbol==='SILVER'||r.symbol==='OIL'?'commodity':'other')))));
      if(!byCat[cat]) byCat[cat]=[];
      byCat[cat].push(r);
    });
    var html = '<table class="price-table"><thead><tr><th style="text-align:left">Symbol</th><th style="text-align:left">Name</th><th>Price</th><th>Day Change</th><th>Date</th></tr></thead><tbody>';
    Object.keys(cats).forEach(function(cat){
      if(!byCat[cat]) return;
      html += '<tr><td colspan="5" style="font-size:10px;color:var(--text3);padding:8px 10px;text-transform:uppercase;letter-spacing:1px;background:var(--bg3)">'+cats[cat]+'</td></tr>';
      byCat[cat].forEach(function(r){
        var chg = r.change_pct;
        var chgColor = chg>0?'var(--green)':chg<0?'var(--red)':'var(--text3)';
        var chgStr = chg>0?'+'+chg+'%':(chg+'%');
        html += '<tr><td style="text-align:left;font-weight:600">'+r.symbol+'</td>';
        html += '<td style="text-align:left;color:var(--text2);font-size:12px">'+r.name+'</td>';
        html += '<td>'+( r.close_price > 1000 ? r.close_price.toLocaleString('en',{maximumFractionDigits:2}) : r.close_price > 10 ? r.close_price.toFixed(2) : r.close_price.toFixed(4))+'</td>';
        html += '<td style="color:'+chgColor+'">'+chgStr+'</td>';
        html += '<td style="color:var(--text3);font-size:11px">'+r.date+'</td></tr>';
      });
    });
    html += '</tbody></table>';
    document.getElementById('market-table-wrap').innerHTML = html;
  }).catch(function(e){ console.log('Market table error',e); });
}

function macroNewsFilter(cat, btn) {
  macroNewsCategory = cat;
  document.querySelectorAll('#page-markets .filter-btn').forEach(function(b){b.classList.remove('active');});
  if(btn) btn.classList.add('active');
  loadMacroNews();
}

function loadMacroNews() {
  var url = '/api/market/macro-news?days=60&limit=50';
  if(macroNewsCategory) url += '&category='+macroNewsCategory;
  fetch(url).then(function(r){return r.json();}).then(function(items) {
    var el = document.getElementById('macro-news-list');
    if(!items.length){ el.innerHTML='<div style="color:var(--text3);padding:20px">No macro news yet — historical collection in progress (check back in ~2 hours).</div>'; return; }
    var catColors = {macro:'#1E90FF',geopolitics:'#DC143C',etf:'#F7931A',commodities:'#FFD700',monetary:'#9370DB',regulation:'#FF6347',general:'#888'};
    el.innerHTML = items.map(function(a){
      var sColor = a.sentiment_label==='positive'?'var(--green)':a.sentiment_label==='negative'?'var(--red)':'var(--text3)';
      var catColor = catColors[a.category]||'#888';
      var dt = a.published_at ? new Date(a.published_at).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}) : '';
      return '<div style="display:flex;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);align-items:flex-start">'
        +'<div style="flex-shrink:0;width:70px;text-align:center">'
        +'<span style="font-size:10px;background:'+catColor+'22;color:'+catColor+';padding:2px 6px;border-radius:4px;white-space:nowrap">'+(a.category||'').toUpperCase()+'</span></div>'
        +'<div style="flex:1">'
        +'<div style="font-size:13px;font-weight:600;margin-bottom:3px"><a href="'+a.url+'" target="_blank" style="color:var(--text);text-decoration:none">'+a.title+'</a></div>'
        +'<div style="font-size:11px;color:var(--text3)">'+a.source+' · '+dt+' <span style="color:'+sColor+'">'+a.sentiment_label+'</span></div>'
        +'</div></div>';
    }).join('');
  }).catch(function(e){ console.log('Macro news error',e); });
}
"""

html = html.replace('</script>\n</body>', markets_js + '\n</script>\n</body>', 1)

with open(FRONTEND, 'w') as f:
    f.write(html)

print("Frontend updated with Markets tab!")
