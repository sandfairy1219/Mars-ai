#!/usr/bin/env python3
"""Daily heatmap — binaryTreemap + finvizColor, LONG/WATCH groups, reads swing-portfolio.json."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import yfinance as yf
import json, numpy as np
from datetime import datetime

# ── binaryTreemap ──
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

# ── Load portfolio from swing-portfolio.json ──
positions_data = []
watchlist_data = []
spy_info = {}

try:
    with open('/home/ubuntu/marsAI/paper-trading/swing-portfolio.json') as f:
        port = json.load(f)
    
    # Calculate total equity for weight %
    total_equity = port.get('cash', 100000)
    for p in port.get('positions', []):
        notional = p.get('current_price', p['entry_price']) * p['shares']
        total_equity += notional
    
    for p in port.get('positions', []):
        notional = p.get('current_price', p['entry_price']) * p['shares']
        weight = notional / total_equity * 100 if total_equity > 0 else 5.0
        positions_data.append({
            'ticker': p['ticker'],
            'side': p.get('side', 'LONG'),
            'weight': weight,
            'unrealized': p.get('unrealized', 0),
            'entry_price': p['entry_price'],
            'current_price': p.get('current_price', p['entry_price'])
        })
    
    # Watchlist (max 8 for visual clarity)
    for w in port.get('watchlist', [])[:8]:
        watchlist_data.append({
            'ticker': w['ticker'],
            'weight': 3.0,  # fixed small weight for visual
            'side': 'LONG'
        })
    
    # SPY benchmark
    spy_info = {
        'baseline': port.get('spy_baseline_price'),
        'current': port.get('spy_current_price')
    }
except Exception as e:
    pass

# Fallback if no data
if not positions_data:
    positions_data = [
        {"ticker": "N/A", "side": "LONG", "weight": 100.0}
    ]

# ── Fetch daily changes ──
all_tickers = [p['ticker'] for p in positions_data] + [w['ticker'] for w in watchlist_data]
daily = {}
try:
    if all_tickers:
        data = yf.download(all_tickers, period='5d', progress=False, timeout=20, prepost=True)
        for t in all_tickers:
            try:
                if len(all_tickers) == 1:
                    closes = data['Close'].dropna()
                else:
                    closes = data['Close'][t].dropna()
                if len(closes) >= 2:
                    prev, curr = float(closes.iloc[-2]), float(closes.iloc[-1])
                    daily[t] = (curr - prev) / prev * 100
                else:
                    daily[t] = 0.0
            except:
                daily[t] = 0.0
except:
    daily = {t: 0.0 for t in all_tickers}

# ── Build groups ──
long_items, watch_items = [], []
for p in positions_data:
    it = {'key': p['ticker'], 'value': p['weight']**1.5, 'side': 'LONG',
          'daily': daily.get(p['ticker'], 0)}
    long_items.append(it)

for w in watchlist_data:
    it = {'key': w['ticker'], 'value': w['weight']**1.5, 'side': 'LONG',
          'daily': daily.get(w['ticker'], 0)}
    watch_items.append(it)

groups = []
if long_items:
    groups.append({'items': long_items, 'label': 'LONG', 'border': '#3fb950'})
if watch_items:
    groups.append({'items': watch_items, 'label': 'WATCH', 'border': '#444'})

if not groups:
    print("No positions or watchlist data")
    sys.exit(0)

# ── Render ──
W, H = 100, 65
bg = '#0a0a0a'
fig, ax = plt.subplots(figsize=(16, 10))
fig.patch.set_facecolor(bg)
ax.set_facecolor(bg)

group_entries = [{'key': g['label'], 'value': sum(it['value'] for it in g['items'])} for g in groups]
group_rects = binary_treemap(group_entries, 0, 0, W, H)

for grp, grect in zip(groups, group_rects):
    gx, gy, gw, gh = grect['x'], grect['y'], grect['w'], grect['h']

    # Group border
    border = mpatches.Rectangle((gx, gy), gw, gh, linewidth=1.8,
                                facecolor='none', edgecolor=grp['border'], alpha=0.8)
    ax.add_patch(border)

    # Group label
    label_h = 5.5
    ax.text(gx + gw/2, gy + gh, grp['label'], ha='center', va='top',
            fontsize=22, fontweight='bold', color=grp['border'], fontfamily='sans-serif',
            zorder=10)

    inner_rects = binary_treemap(grp['items'], gx, gy, gw, gh - label_h)
    for r in inner_rects:
        x, y, w, h = r['x'], r['y'], r['w'], r['h']
        it = next(it for it in grp['items'] if it['key'] == r['key'])
        pct = it['daily']

        pad = 0.25
        clip_rect = mpatches.Rectangle((x+pad, y+pad), w-2*pad, h-2*pad,
                                       facecolor=finviz_color(pct), edgecolor='#222', linewidth=0.6)
        ax.add_patch(clip_rect)

        min_dim = min(w, h)
        ticker_fs = min_dim / 0.73
        pct_fs = min_dim / 1.0

        if ticker_fs < 1.5:
            continue

        vo = min_dim * 0.28
        ax.text(x + w/2, y + h/2 - vo, it['key'], ha='center', va='center',
                fontsize=ticker_fs, fontweight='bold', color='white', fontfamily='sans-serif',
                clip_path=clip_rect)
        if pct_fs > 1.2:
            ds = f"{pct:+.2f}%" if abs(pct) >= 0.01 else "0.00%"
            ax.text(x + w/2, y + h/2 + vo, ds, ha='center', va='center',
                    fontsize=pct_fs, color='white', fontfamily='sans-serif',
                    clip_path=clip_rect)

ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis('off')

today = datetime.now().strftime('%Y-%m-%d')
title = f'MARS AI — DAILY HEATMAP (LONG ONLY)  |  {today}'
# Add SPY benchmark if available
if spy_info.get('baseline') and spy_info.get('current'):
    spy_chg = (spy_info['current'] - spy_info['baseline']) / spy_info['baseline'] * 100
    title += f'  |  SPY {spy_chg:+.2f}%'

fig.text(0.5, 0.995, title, ha='center', va='top',
         fontsize=9, color='#555', fontfamily='sans-serif')

out = '/tmp/daily_heatmap.png'
plt.savefig(out, dpi=150, facecolor=bg, edgecolor='none', pad_inches=0.1)
plt.close()

print(f"MEDIA:{out}")
print(f"**Daily Heatmap** ({today})")
for g in groups:
    print(f"  [{g['label']}]")
    for it in g['items']:
        print(f"    {it['key']:5s} {it['daily']:+.2f}%")
