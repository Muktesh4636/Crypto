#!/usr/bin/env python3
"""Replace the dashboard section with a clean stats-only view."""

TEMPLATE = '/var/www/crypto.pravoo.in/current/templates/dashboard.html'

with open(TEMPLATE, 'r') as f:
    html = f.read()

# ── Remove header search/nav bar (Refresh, Trades, Reports links) ─────────────
old_header_bar = '''          <label class="inline">
            Search markets
            <input type="search" id="q" placeholder="Search BTC, BTCUSDT, SOL..." autocomplete="off" aria-label="Filter symbols">
          </label>
          <button type="button" class="primary" id="refresh">Refresh data</button>
          <a href="/trades/" class="secondary-link">Trades</a>
          <a href="/reports/" class="secondary-link">Reports</a>'''

new_header_bar = '''          <span style="font-size:14px;color:var(--muted)">Live trading performance overview</span>
          <a href="/trades/" class="secondary-link">All Trades</a>
          <a href="/reports/" class="secondary-link">Reports</a>'''

html = html.replace(old_header_bar, new_header_bar, 1)

# ── Replace the entire dashboard section + coins table with new stats UI ──────
old_section_start = '    {% if active_module == \'dashboard\' %}'
old_section_end   = '  </div>\n    </main>\n  </div>'

# Find and replace everything from the dashboard section to end of main content
start_idx = html.find(old_section_start)
end_idx   = html.find(old_section_end, start_idx)

if start_idx == -1 or end_idx == -1:
    print("Could not find section markers!")
    exit(1)

new_section = '''    {% if active_module == 'dashboard' %}
    <!-- ── KPI bar ─────────────────────────────────────────── -->
    <div class="kpi-bar" id="kpiBar">
      <div class="kpi-card">
        <span class="kpi-label">Coins Tracked</span>
        <span class="kpi-value" id="kpiCoins">—</span>
        <span class="kpi-sub">Binance USDT pairs</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Active Trades</span>
        <span class="kpi-value" id="kpiActive">—</span>
        <span class="kpi-sub" id="kpiActiveSymbols">open positions</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Portfolio Equity</span>
        <span class="kpi-value" id="kpiEquity">—</span>
        <span class="kpi-sub" id="kpiEquityInr">—</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">All-Time Win Rate</span>
        <span class="kpi-value" id="kpiWinRate">—</span>
        <span class="kpi-sub" id="kpiWinRateSub">closed trades</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">All-Time P&amp;L</span>
        <span class="kpi-value" id="kpiPnl">—</span>
        <span class="kpi-sub" id="kpiPnlInr">—</span>
      </div>
    </div>

    <!-- ── Period performance cards ────────────────────────── -->
    <div class="period-grid" id="periodGrid">
      <div class="period-card" id="period-today">
        <div class="period-head">Today</div>
        <div class="period-body">
          <div class="period-row"><span class="period-label">Total Trades</span><span class="period-val" data-key="trades">—</span></div>
          <div class="period-row win"><span class="period-label">✓ Wins</span><span class="period-val" data-key="wins">—</span></div>
          <div class="period-row loss"><span class="period-label">✗ Losses</span><span class="period-val" data-key="losses">—</span></div>
          <div class="period-row"><span class="period-label">Win Rate</span><span class="period-val" data-key="win_rate">—</span></div>
          <div class="period-row pnl"><span class="period-label">P&amp;L (USDT)</span><span class="period-val" data-key="pnl_usdt">—</span></div>
          <div class="period-row pnl"><span class="period-label">P&amp;L (INR)</span><span class="period-val" data-key="pnl_inr">—</span></div>
        </div>
      </div>
      <div class="period-card" id="period-weekly">
        <div class="period-head">This Week</div>
        <div class="period-body">
          <div class="period-row"><span class="period-label">Total Trades</span><span class="period-val" data-key="trades">—</span></div>
          <div class="period-row win"><span class="period-label">✓ Wins</span><span class="period-val" data-key="wins">—</span></div>
          <div class="period-row loss"><span class="period-label">✗ Losses</span><span class="period-val" data-key="losses">—</span></div>
          <div class="period-row"><span class="period-label">Win Rate</span><span class="period-val" data-key="win_rate">—</span></div>
          <div class="period-row pnl"><span class="period-label">P&amp;L (USDT)</span><span class="period-val" data-key="pnl_usdt">—</span></div>
          <div class="period-row pnl"><span class="period-label">P&amp;L (INR)</span><span class="period-val" data-key="pnl_inr">—</span></div>
        </div>
      </div>
      <div class="period-card" id="period-monthly">
        <div class="period-head">This Month</div>
        <div class="period-body">
          <div class="period-row"><span class="period-label">Total Trades</span><span class="period-val" data-key="trades">—</span></div>
          <div class="period-row win"><span class="period-label">✓ Wins</span><span class="period-val" data-key="wins">—</span></div>
          <div class="period-row loss"><span class="period-label">✗ Losses</span><span class="period-val" data-key="losses">—</span></div>
          <div class="period-row"><span class="period-label">Win Rate</span><span class="period-val" data-key="win_rate">—</span></div>
          <div class="period-row pnl"><span class="period-label">P&amp;L (USDT)</span><span class="period-val" data-key="pnl_usdt">—</span></div>
          <div class="period-row pnl"><span class="period-label">P&amp;L (INR)</span><span class="period-val" data-key="pnl_inr">—</span></div>
        </div>
      </div>
      <div class="period-card" id="period-yearly">
        <div class="period-head">All Time</div>
        <div class="period-body">
          <div class="period-row"><span class="period-label">Total Trades</span><span class="period-val" data-key="trades">—</span></div>
          <div class="period-row win"><span class="period-label">✓ Wins</span><span class="period-val" data-key="wins">—</span></div>
          <div class="period-row loss"><span class="period-label">✗ Losses</span><span class="period-val" data-key="losses">—</span></div>
          <div class="period-row"><span class="period-label">Win Rate</span><span class="period-val" data-key="win_rate">—</span></div>
          <div class="period-row"><span class="period-label">Best Trade</span><span class="period-val" data-key="best">—</span></div>
          <div class="period-row"><span class="period-label">Worst Trade</span><span class="period-val" data-key="worst">—</span></div>
        </div>
      </div>
    </div>
    {% endif %}
  </div>
    </main>
  </div>'''

html = html[:start_idx] + new_section + html[end_idx + len(old_section_end):]

# ── Inject CSS for new components ─────────────────────────────────────────────
new_css = '''
    /* ── Dashboard KPI bar ─────────────────────────────────── */
    .kpi-bar {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .kpi-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px 22px;
      display: flex;
      flex-direction: column;
      gap: 4px;
      box-shadow: var(--shadow-sm);
    }
    .kpi-label {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--muted);
    }
    .kpi-value {
      font-size: 28px;
      font-weight: 700;
      color: var(--text);
      line-height: 1.1;
    }
    .kpi-sub {
      font-size: 12px;
      color: var(--muted);
    }
    /* ── Period grid ───────────────────────────────────────── */
    .period-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
    }
    .period-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      overflow: hidden;
      box-shadow: var(--shadow-sm);
    }
    .period-head {
      font-size: 13px;
      font-weight: 700;
      padding: 14px 18px;
      background: var(--surface-alt);
      border-bottom: 1px solid var(--border);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--accent);
    }
    .period-body { padding: 4px 0; }
    .period-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 9px 18px;
      border-bottom: 1px solid var(--border);
      font-size: 13px;
    }
    .period-row:last-child { border-bottom: none; }
    .period-label { color: var(--muted); }
    .period-val { font-weight: 600; color: var(--text); }
    .period-row.win .period-val { color: var(--up); }
    .period-row.loss .period-val { color: var(--down); }
    .period-row.pnl .period-val { font-family: monospace; font-size: 12px; }
'''

html = html.replace('    *, *::before, *::after { box-sizing: border-box; }',
                    '    *, *::before, *::after { box-sizing: border-box; }' + new_css, 1)

# ── Inject new JS for dashboard stats (before closing </script>) ──────────────
new_js = '''
    // ── Dashboard stats loader ──────────────────────────────
    function fmtPct2(v) {
      if (v === null || v === undefined) return "—";
      return (v * 100).toFixed(1) + "%";
    }
    function fmtUsdt(v) {
      if (v === null || v === undefined) return "—";
      const sign = v >= 0 ? "+" : "";
      return sign + "$" + Math.abs(v).toFixed(2);
    }
    function fmtInr(v) {
      if (v === null || v === undefined) return "—";
      const sign = v >= 0 ? "+" : "";
      return sign + "₹" + Math.abs(v).toLocaleString("en-IN", {maximumFractionDigits: 0});
    }

    function fillPeriod(cardId, data, isAllTime) {
      const card = document.getElementById(cardId);
      if (!card) return;
      card.querySelectorAll(".period-val").forEach(function(el) {
        const key = el.dataset.key;
        if (key === "trades")    el.textContent = data.trades ?? "—";
        if (key === "wins")      el.textContent = data.wins ?? "—";
        if (key === "losses")    el.textContent = data.losses ?? "—";
        if (key === "win_rate")  el.textContent = fmtPct2(data.win_rate);
        if (key === "pnl_usdt")  { el.textContent = fmtUsdt(data.pnl_usdt ?? data.realized_pnl_usdt); el.style.color = (data.pnl_usdt ?? data.realized_pnl_usdt) >= 0 ? "var(--up)" : "var(--down)"; }
        if (key === "pnl_inr")   { el.textContent = fmtInr(data.pnl_inr   ?? data.realized_pnl_inr);   el.style.color = (data.pnl_inr   ?? data.realized_pnl_inr)   >= 0 ? "var(--up)" : "var(--down)"; }
        if (key === "best")      el.textContent = data.best_trade_usdt  != null ? fmtUsdt(data.best_trade_usdt)  : "—";
        if (key === "worst")     { el.textContent = data.worst_trade_usdt != null ? fmtUsdt(data.worst_trade_usdt) : "—"; el.style.color = "var(--down)"; }
      });
    }

    function loadDashboardStats() {
      // Coins tracked
      fetch("/api/coins/eligible/").then(function(r){return r.json();}).then(function(d){
        var el = document.getElementById("kpiCoins");
        if (el) el.textContent = d.count || (Array.isArray(d) ? d.length : "—");
      }).catch(function(){});

      // Portfolio + trades
      fetch("/api/trading/paper-portfolio/").then(function(r){return r.json();}).then(function(d){
        var p = d.portfolio || {};
        var el;
        el = document.getElementById("kpiActive");
        if (el) el.textContent = p.open_positions_count ?? "—";
        el = document.getElementById("kpiActiveSymbols");
        if (el && p.open_symbols) el.textContent = p.open_symbols.slice(0,3).join(", ") + (p.open_symbols.length > 3 ? " +" + (p.open_symbols.length-3) : "");
        el = document.getElementById("kpiEquity");
        if (el) el.textContent = p.equity_usdt != null ? "$" + p.equity_usdt.toFixed(2) : "—";
        el = document.getElementById("kpiEquityInr");
        if (el) el.textContent = p.equity_inr != null ? "₹" + p.equity_inr.toLocaleString("en-IN", {maximumFractionDigits:0}) : "—";
        el = document.getElementById("kpiWinRate");
        if (el) el.textContent = fmtPct2(p.win_rate);
        el = document.getElementById("kpiWinRateSub");
        if (el) el.textContent = (p.closed_count ?? "?") + " closed · " + (p.wins ?? 0) + "W / " + (p.losses ?? 0) + "L";
        el = document.getElementById("kpiPnl");
        if (el) { el.textContent = fmtUsdt(p.realized_pnl_usdt); el.style.color = (p.realized_pnl_usdt ?? 0) >= 0 ? "var(--up)" : "var(--down)"; }
        el = document.getElementById("kpiPnlInr");
        if (el) { el.textContent = fmtInr(p.realized_pnl_inr); el.style.color = (p.realized_pnl_inr ?? 0) >= 0 ? "var(--up)" : "var(--down)"; }
      }).catch(function(){});

      // Period performance
      fetch("/api/trading/performance-report/").then(function(r){return r.json();}).then(function(d){
        var cur = d.current || {};
        fillPeriod("period-today",   cur.daily   || {});
        fillPeriod("period-weekly",  cur.weekly  || {});
        fillPeriod("period-monthly", cur.monthly || {});
        fillPeriod("period-yearly",  d.overview  || {}, true);
      }).catch(function(){});
    }

    // Load on page open, refresh every 60s
    if (document.getElementById("kpiBar")) {
      loadDashboardStats();
      setInterval(loadDashboardStats, 60000);
    }
'''

html = html.replace('  </script>\n</body>', new_js + '  </script>\n</body>', 1)

with open(TEMPLATE, 'w') as f:
    f.write(html)

print("Dashboard patched successfully!")
