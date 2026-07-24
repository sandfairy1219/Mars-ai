import urllib.request, json, os, time, re
from bs4 import BeautifulSoup

UNIVERSE_PATH = "/home/ubuntu/marsAI/paper-trading/universe.json"

def fetch_sp500_from_stockanalysis():
    url = "https://stockanalysis.com/list/sp-500-stocks/"
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    rows = table.find_all("tr")[1:] if table else []
    stocks = []
    for row in rows:
        cols = row.find_all(["td","th"])
        if len(cols) < 5:
            continue
        ticker = cols[0].get_text(strip=True)
        name   = cols[1].get_text(strip=True)
        market_cap_text = cols[2].get_text(strip=True)
        sector = cols[3].get_text(strip=True) if len(cols) > 3 else "Unknown"
        # Parse market cap (e.g. 3.42T, 850.5B, 45.2M)
        cap = 0
        try:
            num = float(re.sub(r'[^0-9\\.]', '', market_cap_text))
            if 'T' in market_cap_text.upper():
                cap = int(num * 1e12)
            elif 'B' in market_cap_text.upper():
                cap = int(num * 1e9)
            elif 'M' in market_cap_text.upper():
                cap = int(num * 1e6)
        except:
            pass
        if ticker and cap > 0:
            stocks.append({"ticker": ticker, "name": name, "market_cap": cap, "sector": sector})
    return stocks

def fetch_yahoo_price(ticker):
    """Fetch latest price from Yahoo Finance v8 chart (lightweight)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice") or meta.get("previousClose", 0)
        return round(price, 2) if price else None
    except Exception as e:
        print(f"[WARN] Price fetch failed for {ticker}: {e}")
        return None

print("Fetching S&P 500 list from stockanalysis.com...")
stocks = fetch_sp500_from_stockanalysis()
print(f"Parsed {len(stocks)} stocks with market cap")

# Sort by market cap desc, keep top 200
stocks.sort(key=lambda x: x["market_cap"], reverse=True)
top200 = stocks[:200]

# Enrich with current price from Yahoo
print("Fetching current prices from Yahoo Finance...")
for s in top200:
    price = fetch_yahoo_price(s["ticker"])
    if price:
        s["price"] = price
    else:
        s["price"] = 0.0
    time.sleep(0.15)  # be polite

# Sector stats
sector_counts = {}
for s in top200:
    sec = s["sector"]
    sector_counts[sec] = sector_counts.get(sec, 0) + 1

print("\nTop 200 sector distribution:")
for sec, cnt in sorted(sector_counts.items(), key=lambda x: -x[1]):
    print(f"  {sec}: {cnt}")

# Save
os.makedirs(os.path.dirname(UNIVERSE_PATH), exist_ok=True)
with open(UNIVERSE_PATH, "w") as f:
    json.dump(top200, f, indent=2, ensure_ascii=False)

print(f"\n✅ Universe saved: {UNIVERSE_PATH} ({len(top200)} stocks)")
print(f"Top 10 tickers: {[s['ticker'] for s in top200[:10]]}")
