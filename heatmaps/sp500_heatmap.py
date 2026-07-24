#!/usr/bin/env python3
"""
S&P 500 Finviz-style heatmap generator.
Fetches S&P 500 components → daily change % → binary treemap → finviz-style color → PNG.
No Cloudflare issues since it uses Yahoo Finance directly.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import yfinance as yf
import json, numpy as np, sys, os, re, time
from datetime import datetime

# ── binaryTreemap (from daily_heatmap.py) ──
def binary_treemap(items, x, y, w, h):
    if not items: return []
    if len(items) == 1:
        return [{'key': items[0]['key'], 'x': x, 'y': y, 'w': w, 'h': h}]
    total = sum(it['value'] for it in items)
    s = sorted(items, key=lambda i: -i['value'])
    left, right, sl, sr = [], [], 0, 0
    for it in s:
        if sl <= sr: left.append(it); sl += it['value']
        else: right.append(it); sr += it['value']
    out = []
    if w >= h:
        wl = w * (sl / total)
        out.extend(binary_treemap(left, x, y, wl, h))
        out.extend(binary_treemap(right, x + wl, y, w - wl, h))
    else:
        ht = h * (sl / total)
        out.extend(binary_treemap(left, x, y, w, ht))
        out.extend(binary_treemap(right, x, y + ht, w, h - ht))
    return out

# ── finvizColor ──
def finviz_color(pct):
    t = max(0, min(1, (pct + 5) / 10))
    if t < 0.5:
        u = t / 0.5
        return (0.6 + (0.35-0.6)*u, 0 + (0.35-0)*u, 0 + (0.35-0)*u)
    else:
        u = (t - 0.5) / 0.5
        return (0.35 + (0-0.35)*u, 0.35 + (0.6-0.35)*u, 0.35 + (0-0.35)*u)

# ── Sector colors (finviz-inspired, GICS sectors) ──
SECTOR_COLORS = {
    'Information Technology':     '#3b82f6',  # blue
    'Financials':                 '#10b981',  # green
    'Health Care':                '#ef4444',  # red
    'Consumer Discretionary':     '#f59e0b',  # amber
    'Communication Services':     '#8b5cf6',  # purple
    'Consumer Staples':           '#ec4899',  # pink
    'Energy':                     '#f97316',  # orange
    'Industrials':                '#6366f1',  # indigo
    'Materials':                  '#14b8a6',  # teal
    'Real Estate':                '#a855f7',  # violet
    'Utilities':                  '#eab308',  # yellow
}

# ── Get S&P 500 components from Wikipedia ──
def get_sp500_components():
    """Fetch current S&P 500 constituents list."""
    import urllib.request
    import html as html_mod
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    raw_html = urllib.request.urlopen(req, timeout=20).read().decode('utf-8')
    
    # Find constituents table by id
    table_id_pos = raw_html.find('id="constituents"')
    if table_id_pos == -1:
        print("ERROR: Could not find constituents table", file=sys.stderr)
        return [], {}
    
    # Find the containing <table> tag
    table_start = raw_html.rfind('<table', 0, table_id_pos)
    if table_start == -1:
        print("ERROR: Could not find <table> tag", file=sys.stderr)
        return [], {}
    
    table_end = raw_html.find('</table>', table_id_pos)
    if table_end == -1:
        print("ERROR: Could not find </table>", file=sys.stderr)
        return [], {}
    
    table_html = raw_html[table_start:table_end + 8]
    
    # Extract tbody
    tbody_start = table_html.find('<tbody')
    tbody_end = table_html.find('</tbody>')
    if tbody_start == -1 or tbody_end == -1:
        print("ERROR: Could not find table body", file=sys.stderr)
        return [], {}
    
    tbody = table_html[tbody_start:tbody_end + 8]
    
    # Parse rows (skip header)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody, re.DOTALL)
    tickers = []
    sectors = {}
    
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(cells) < 3:
            continue
        
        # Cell 0: Symbol (ticker)
        ticker = re.sub(r'<[^>]+>', '', cells[0]).strip()
        ticker = html_mod.unescape(ticker).replace('&amp;', '&')
        
        # Cell 2: GICS Sector
        sector = re.sub(r'<[^>]+>', '', cells[2]).strip()
        sector = html_mod.unescape(sector)
        
        if ticker and ticker != 'Symbol' and not ticker.startswith('<'):
            tickers.append(ticker)
            sectors[ticker] = sector
    
    print(f"Parsed {len(tickers)} tickers from Wikipedia", file=sys.stderr)
    return tickers, sectors

# ── Fetch daily changes in batches ──
def fetch_daily_changes(tickers, batch_size=100):
    """Fetch daily % change for all tickers from Yahoo Finance."""
    daily = {}
    failed = []
    
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i+batch_size]
        try:
            data = yf.download(batch, period='2d', progress=False, timeout=30, auto_adjust=True)
            closes = data['Close']
            if len(closes) >= 2:
                prev_row = closes.iloc[-2]
                curr_row = closes.iloc[-1]
                for t in batch:
                    try:
                        if isinstance(closes.iloc[-2], (int, float)):
                            prev_val, curr_val = float(closes.iloc[-2]), float(closes.iloc[-1])
                        else:
                            prev_val = float(closes[t].iloc[-2]) if t in closes else None
                            curr_val = float(closes[t].iloc[-1]) if t in closes else None
                        
                        if prev_val and curr_val and prev_val != 0:
                            daily[t] = (curr_val - prev_val) / prev_val * 100
                        else:
                            daily[t] = 0.0
                    except:
                        daily[t] = 0.0
            else:
                for t in batch:
                    daily[t] = 0.0
        except Exception as e:
            print(f"Batch {i} failed, trying individually: {e}", file=sys.stderr)
            for t in batch:
                try:
                    hist = yf.download(t, period='2d', progress=False, timeout=15)
                    closes = hist['Close'].dropna()
                    if len(closes) >= 2:
                        prev, curr = float(closes.iloc[-2]), float(closes.iloc[-1])
                        daily[t] = (curr - prev) / prev * 100
                    else:
                        daily[t] = 0.0
                except:
                    failed.append(t)
        
        time.sleep(0.5)
    
    if failed:
        print(f"Failed to fetch {len(failed)} tickers: {failed[:10]}...", file=sys.stderr)
    
    return daily

# ── Generate heatmap ──
def generate_heatmap(daily_changes, sectors, out_path):
    """Generate treemap image in Finviz style."""
    
    # Group by sector
    sector_items = {}
    sector_total_change = {}
    
    for ticker, change in daily_changes.items():
        sector = sectors.get(ticker, 'Other')
        if sector not in sector_items:
            sector_items[sector] = []
            sector_total_change[sector] = 0
        
        # Use absolute change % as weight (higher weight = more visual space)
        weight = abs(change) + 0.5  # minimum visibility
        sector_items[sector].append({
            'key': ticker,
            'value': weight,
            'change': change
        })
        sector_total_change[sector] = sector_total_change.get(sector, 0) + change
    
    # Sort sectors by total absolute weight
    sorted_sectors = sorted(sector_items.keys(), 
                          key=lambda s: sum(abs(it['change']) for it in sector_items[s]), 
                          reverse=True)
    
    # Render
    W, H = 120, 80
    bg = '#0a0a0a'
    fig, ax = plt.subplots(figsize=(20, 12))
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    
    # Sector groups for treemap
    group_entries = []
    for sector in sorted_sectors:
        total_weight = sum(it['value'] for it in sector_items[sector])
        group_entries.append({'key': sector, 'value': total_weight})
    
    group_rects = binary_treemap(group_entries, 0, 0, W, H)
    
    for sector, grect in zip(sorted_sectors, group_rects):
        if sector not in sector_items:
            continue
        gx, gy, gw, gh = grect['x'], grect['y'], grect['w'], grect['h']
        
        sector_color = SECTOR_COLORS.get(sector, '#666666')
        
        # Group border
        border_pad = 0.3
        border = mpatches.Rectangle((gx+border_pad, gy+border_pad), gw-2*border_pad, gh-2*border_pad,
                                    linewidth=1.5, facecolor='none', edgecolor=sector_color, alpha=0.6)
        ax.add_patch(border)
        
        # Sector label area at bottom of group
        label_area_h = min(4.5, gh * 0.12)
        
        # Calculate sector average change
        sector_changes = [it['change'] for it in sector_items[sector]]
        avg_change = np.mean(sector_changes) if sector_changes else 0
        
        # Sector name
        ax.text(gx + gw/2, gy + gh - label_area_h/2, 
                f"{sector}  {avg_change:+.2f}%",
                ha='center', va='center',
                fontsize=min(14, max(6, gh * 0.20)), 
                fontweight='bold', color=sector_color, fontfamily='sans-serif',
                zorder=10)
        
        # Inner treemap
        inner_area_h = gh - label_area_h
        inner_rects = binary_treemap(sector_items[sector], gx, gy, gw, inner_area_h)
        
        for r in inner_rects:
            x, y, w, h = r['x'], r['y'], r['w'], r['h']
            it = next(item for item in sector_items[sector] if item['key'] == r['key'])
            pct = it['change']
            
            pad = 0.2
            color = finviz_color(pct)
            clip_rect = mpatches.Rectangle((x+pad, y+pad), w-2*pad, h-2*pad,
                                           facecolor=color, edgecolor='#1a1a1a', linewidth=0.3)
            ax.add_patch(clip_rect)
            
            min_dim = min(w, h)
            ticker_fs = min_dim / 0.85
            pct_fs = min_dim / 1.2
            
            if ticker_fs < 1.2:
                continue
            
            vo = min_dim * 0.25
            ax.text(x + w/2, y + h/2 - vo, it['key'], ha='center', va='center',
                    fontsize=max(1.2, ticker_fs), fontweight='bold', color='white', fontfamily='sans-serif')
            if pct_fs > 1.0:
                ds = f"{pct:+.2f}%" if abs(pct) >= 0.01 else "0.00%"
                ax.text(x + w/2, y + h/2 + vo, ds, ha='center', va='center',
                        fontsize=max(1.0, pct_fs), color='white', fontfamily='sans-serif')
    
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis('off')
    
    today = datetime.now().strftime('%Y-%m-%d')
    title_text = f'S&P 500 Heatmap — {today}'
    
    fig.text(0.5, 0.995, title_text, ha='center', va='top',
             fontsize=10, color='#666', fontfamily='sans-serif')
    
    fig.savefig(out_path, dpi=180, facecolor=bg, edgecolor='none', pad_inches=0.08,
                bbox_inches='tight')
    plt.close()
    
    return out_path

# ── Main ──
if __name__ == '__main__':
    out_path = '/tmp/sp500_heatmap.png'
    
    print("Fetching S&P 500 components...", file=sys.stderr)
    tickers, sectors = get_sp500_components()
    if not tickers:
        print(json.dumps({"error": "Failed to fetch S&P 500 components"}))
        sys.exit(1)
    
    print(f"Got {len(tickers)} tickers. Fetching prices...", file=sys.stderr)
    daily_changes = fetch_daily_changes(tickers)
    
    print(f"Got {len(daily_changes)} changes. Generating heatmap...", file=sys.stderr)
    generate_heatmap(daily_changes, sectors, out_path)
    
    # Output summary
    advancing = sum(1 for v in daily_changes.values() if v > 0)
    declining = sum(1 for v in daily_changes.values() if v < 0)
    unchanged = sum(1 for v in daily_changes.values() if v == 0)
    total = len(daily_changes)
    
    print(f"MEDIA:{out_path}")
    print(f"")
    print(f"   S&P 500 Heatmap — {datetime.now().strftime('%Y-%m-%d')}")
    print(f"   Advancing: {advancing} | Declining: {declining} | Unchanged: {unchanged}")
    print(f"   (Source: Yahoo Finance)")
