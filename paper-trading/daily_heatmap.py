#!/usr/bin/env python3
"""Daily heatmap — binaryTreemap + finvizColor, LONG/WATCH groups, reads swing-portfolio.json.

Data source: Finnhub quote API (primary, dp field = daily %, weekend-safe)
             → yfinance 5d-close fallback (per-ticker) → N/A
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import json, sys, os, time, urllib.request, urllib.error
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Load FINNHUB_API_KEY from .env (cron child env strips secrets by design) ──
FINNHUB_KEY = os.environ.get('FINNHUB_API_KEY', '')
if not FINNHUB_KEY:
    for env_path in (os.path.join(SCRIPT_DIR, '.env'), '/home/ubuntu/marsAI/.env'):
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('FINNHUB') and '=' in line:
                        k, v = line.split('=', 1)
                        if 'KEY' in k.upper() or 'TOKEN' in k.upper():
                            FINNHUB_KEY = v.strip().strip('"\'')
                            break
        except OSError:
            continue
        if FINNHUB_KEY:
            break

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
    if pct is None:
        return '#333333'
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
# Primary: Finnhub /quote → dp is ALREADY a percent (do NOT multiply by 100).
# Weekend-safe: no calendar-window math involved. Fallback: yfinance 5d closes.
all_tickers = [p['ticker'] for p in positions_data] + [w['ticker'] for w in watchlist_data]
daily = {t: None for t in all_tickers}  # None = data unavailable (renders as N/A)

def finnhub_daily_pct(symbol):
    if not FINNHUB_KEY:
        return None
    url = f'https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_KEY}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode())
        dp = j.get('dp')
        if dp is not None and j.get('pc'):  # pc=prev close must be nonzero
            return float(dp)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return None

if all_tickers:
    for t in all_tickers:
        daily[t] = finnhub_daily_pct(t)
        time.sleep(0.15)  # free tier: 60 calls/min — 18 tickers stays well under

# Fallback: yfinance (5d window, last 2 valid closes) for whatever Finnhub missed
missing = [t for t in all_tickers if daily[t] is None]
if missing:
    try:
        import yfinance as yf
        for t in missing:
            try:
                h = yf.Ticker(t).history(period='5d')
                closes = h['Close'].dropna()
                if len(closes) >= 2:
                    prev, curr = float(closes.iloc[-2]), float(closes.iloc[-1])
                    daily[t] = (curr - prev) / prev * 100
            except Exception:
                pass
    except ImportError:
        pass

# ── Build groups ──
long_items, watch_items = [], []
for p in positions_data:
    it = {'key': p['ticker'], 'value': p['weight']**1.5, 'side': 'LONG',
          'daily': daily.get(p['ticker'])}
    long_items.append(it)

for w in watchlist_data:
    it = {'key': w['ticker'], 'value': w['weight']**1.5, 'side': 'LONG',
          'daily': daily.get(w['ticker'])}
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
            if pct is None:
                ds = 'N/A'
            else:
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

n_finnhub = sum(1 for t in all_tickers if daily[t] is not None and t not in missing)
n_yf = sum(1 for t in missing if daily[t] is not None)
print(f"[data] finnhub={n_finnhub} yfinance_fallback={n_yf} na={len(all_tickers)-n_finnhub-n_yf} (key={'yes' if FINNHUB_KEY else 'NO'})")
print(f"MEDIA:{out}")
print(f"**Daily Heatmap** ({today})")
for g in groups:
    print(f"  [{g['label']}]")
    for it in g['items']:
        d = it['daily']
        print(f"    {it['key']:5s} {'N/A' if d is None else f'{d:+.2f}%'}")
