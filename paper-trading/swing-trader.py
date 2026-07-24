#!/usr/bin/env python3
import sys, json, urllib.request, math, os, datetime, time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── CONFIG ───
SEED = 100_000.0
PORTFOLIO_PATH = "/home/ubuntu/marsAI/paper-trading/swing-portfolio.json"
MAX_POSITIONS = 10          # 유동적: 신호 강하면 최대 10개 까지 허용
MAX_HOLD_DAYS = 14
# STOP_LOSS / TARGET_GAIN / BASE_POSITION 은 시장 상황에 따라 동적 계산
DEFAULT_BASE_POSITION = 10_000.0

# Discord webhook from existing .env
ENV_PATH = "/home/ubuntu/marsAI/etf-alarm/.env"
WEBHOOK_URL = None
if os.path.exists(ENV_PATH):
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line.startswith("DISCORD_WEBHOOK"):
                WEBHOOK_URL = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

# ─── UNIVERSE: Top ~200 US large-caps with sectors ───
UNIVERSE = {
    # S&P 500 Large-Cap
    "AAPL":"Technology","MSFT":"Technology","NVDA":"Technology","GOOGL":"Technology",
    "META":"Technology","AVGO":"Technology","AMD":"Technology","ORCL":"Technology",
    "ADBE":"Technology","CRM":"Technology","ACN":"Technology","CSCO":"Technology",
    "INTU":"Technology","TXN":"Technology","QCOM":"Technology","AMAT":"Technology",
    "MU":"Technology","NOW":"Technology","PANW":"Technology","ADP":"Technology",
    "IBM":"Technology","LRCX":"Technology","KLAC":"Technology","ANSS":"Technology",
    "CDNS":"Technology","SNPS":"Technology","FTNT":"Technology","ROP":"Technology",
    "MSI":"Technology","GLW":"Technology","NTAP":"Technology","PAYX":"Technology",
    "TEL":"Technology","HPQ":"Technology","HPE":"Technology",
    "FN":"Technology","ACLS":"Technology","FORM":"Technology",
    "NFLX":"Communication Services","DIS":"Communication Services","CMCSA":"Communication Services",
    "VZ":"Communication Services","T":"Communication Services","TMUS":"Communication Services",
    "EA":"Communication Services","TTWO":"Communication Services","CHTR":"Communication Services",
    "FOXA":"Communication Services","LYV":"Communication Services","MTCH":"Communication Services",
    "OMC":"Communication Services","IPG":"Communication Services","NWSA":"Communication Services",
    "AMZN":"Consumer Discretionary","HD":"Consumer Discretionary","MCD":"Consumer Discretionary",
    "NKE":"Consumer Discretionary","LOW":"Consumer Discretionary","SBUX":"Consumer Discretionary",
    "TGT":"Consumer Discretionary","BKNG":"Consumer Discretionary","TJX":"Consumer Discretionary",
    "GM":"Consumer Discretionary","F":"Consumer Discretionary","MAR":"Consumer Discretionary",
    "YUM":"Consumer Discretionary","DPZ":"Consumer Discretionary","DHI":"Consumer Discretionary",
    "LEN":"Consumer Discretionary","NVR":"Consumer Discretionary","PHM":"Consumer Discretionary",
    "AZO":"Consumer Discretionary","ORLY":"Consumer Discretionary","AAP":"Consumer Discretionary",
    "GPC":"Consumer Discretionary","BBY":"Consumer Discretionary","DG":"Consumer Discretionary",
    "DLTR":"Consumer Discretionary","ROST":"Consumer Discretionary","ULTA":"Consumer Discretionary",
    "PG":"Consumer Staples","KO":"Consumer Staples","PEP":"Consumer Staples",
    "WMT":"Consumer Staples","COST":"Consumer Staples","MDLZ":"Consumer Staples",
    "KHC":"Consumer Staples","GIS":"Consumer Staples","SYY":"Consumer Staples",
    "ADM":"Consumer Staples","STZ":"Consumer Staples","EL":"Consumer Staples",
    "CL":"Consumer Staples","KMB":"Consumer Staples","CHD":"Consumer Staples",
    "HSY":"Consumer Staples","CPB":"Consumer Staples","CAG":"Consumer Staples",
    "MKC":"Consumer Staples","K":"Consumer Staples","KR":"Consumer Staples",
    "XOM":"Energy","CVX":"Energy","COP":"Energy","EOG":"Energy",
    "SLB":"Energy","OXY":"Energy","MPC":"Energy","VLO":"Energy",
    "PSX":"Energy","WMB":"Energy","DVN":"Energy","MRO":"Energy",
    "JPM":"Financials","BAC":"Financials","WFC":"Financials","GS":"Financials",
    "MS":"Financials","SCHW":"Financials","BLK":"Financials","SPGI":"Financials",
    "AXP":"Financials","CB":"Financials","PNC":"Financials","USB":"Financials",
    "TFC":"Financials","COF":"Financials","AFL":"Financials","MET":"Financials",
    "PRU":"Financials","AIG":"Financials","ALL":"Financials","TRV":"Financials",
    "MMC":"Financials","AON":"Financials","BRO":"Financials","RJF":"Financials",
    "KEY":"Financials","RF":"Financials","C":"Financials","CFG":"Financials",
    "FITB":"Financials","HBAN":"Financials","ZION":"Financials",
    "JNJ":"Health Care","UNH":"Health Care","LLY":"Health Care","PFE":"Health Care",
    "ABT":"Health Care","ABBV":"Health Care","TMO":"Health Care","DHR":"Health Care",
    "MRK":"Health Care","AMGN":"Health Care","GILD":"Health Care","VRTX":"Health Care",
    "REGN":"Health Care","BIIB":"Health Care","ISRG":"Health Care","ZTS":"Health Care",
    "SYK":"Health Care","BDX":"Health Care","EW":"Health Care","MDT":"Health Care",
    "BSX":"Health Care","DXCM":"Health Care","IDXX":"Health Care","WAT":"Health Care",
    "IQV":"Health Care","LH":"Health Care","CRL":"Health Care","VTRS":"Health Care",
    "HUM":"Health Care","ELV":"Health Care","CI":"Health Care","CNC":"Health Care",
    "MOH":"Health Care","COR":"Health Care",
    "HON":"Industrials","UPS":"Industrials","BA":"Industrials","RTX":"Industrials",
    "LMT":"Industrials","NOC":"Industrials","GE":"Industrials","CAT":"Industrials",
    "DE":"Industrials","MMM":"Industrials","ITW":"Industrials","GD":"Industrials",
    "CSX":"Industrials","UNP":"Industrials","NSC":"Industrials","WM":"Industrials",
    "RSG":"Industrials","CARR":"Industrials","OTIS":"Industrials","TT":"Industrials",
    "IR":"Industrials","PH":"Industrials","FTV":"Industrials","DOV":"Industrials",
    "IEX":"Industrials","NDSN":"Industrials","SWK":"Industrials","TXT":"Industrials",
    "POWL":"Industrials","AEIS":"Industrials","MOD":"Industrials",
    "LIN":"Materials","SHW":"Materials","FCX":"Materials","NEM":"Materials",
    "DOW":"Materials","DD":"Materials","ECL":"Materials","IFF":"Materials",
    "NUE":"Materials","VMC":"Materials","AMT":"Real Estate","PLD":"Real Estate",
    "CCI":"Real Estate","EQIX":"Real Estate","PSA":"Real Estate","O":"Real Estate",
    "WELL":"Real Estate","SBAC":"Real Estate","EXR":"Real Estate","VTR":"Real Estate",
    "NEE":"Utilities","DUK":"Utilities","SO":"Utilities","D":"Utilities",
    "AEP":"Utilities","EXC":"Utilities","SRE":"Utilities","XEL":"Utilities",
    "ED":"Utilities","WEC":"Utilities","CNP":"Utilities","FE":"Utilities",
    "PEG":"Utilities","ES":"Utilities","AWK":"Utilities","EIX":"Utilities",
    # S&P 400 Mid-Cap
    "SMCI":"Technology","ENPH":"Technology","RBLX":"Communication Services","U":"Technology",
    "HOOD":"Financials","SOFI":"Financials","AFRM":"Financials","RKT":"Financials",
    "ONON":"Consumer Discretionary","CAVA":"Consumer Discretionary","APP":"Technology",
    "VSTS":"Industrials","COHR":"Technology","LITE":"Technology","RUN":"Utilities",
    "ARRY":"Technology","SHLS":"Technology","SEDG":"Technology","NXT":"Utilities",
    "AESI":"Utilities","VST":"Utilities","CWEN":"Utilities","AY":"Utilities",
    "BEP":"Utilities","NEP":"Utilities","HASI":"Real Estate","BKR":"Energy",
    "BXP":"Real Estate","CBOE":"Financials","ATO":"Utilities","AOS":"Industrials",
    "ALLE":"Industrials","AME":"Industrials","AIT":"Industrials","AIZ":"Financials",
    "BEN":"Financials","BRO":"Financials","CPT":"Real Estate","ESS":"Real Estate",
    "EXR":"Real Estate","FDS":"Financials","FFIV":"Technology","FRT":"Real Estate",
    "HST":"Real Estate","JLL":"Real Estate","KIM":"Real Estate","LAMR":"Real Estate",
    "LNC":"Financials","MAA":"Real Estate","NFG":"Utilities","PKG":"Materials",
    "RL":"Consumer Discretionary","TRMB":"Industrials","TYL":"Technology","UDR":"Real Estate",
    "WPC":"Real Estate","ZWS":"Industrials","OGS":"Utilities","NYCB":"Financials",
    "SIGI":"Financials","RHI":"Industrials","MAN":"Industrials","TNET":"Industrials",
    "BRKR":"Health Care","DYN":"Health Care","TKO":"Communication Services","CE":"Materials",
    "ASH":"Materials","BC":"Consumer Discretionary","JBL":"Technology","SAIC":"Technology",
    "TTEK":"Industrials","ENS":"Industrials","ATKR":"Industrials","MTRN":"Materials",
    # Russell 2000 Small-Cap
    "TGLS":"Materials","ASTS":"Technology","NOVA":"Utilities","SPWR":"Technology",
    "NARI":"Health Care","CERT":"Technology","S":"Technology","DDOG":"Technology",
    "OKTA":"Technology","SNOW":"Technology","MDB":"Technology","NET":"Technology",
    "FSLY":"Technology","TWLO":"Technology","ZI":"Technology","DOCU":"Technology",
    "PLTR":"Technology","DASH":"Consumer Discretionary","ABNB":"Consumer Discretionary",
    "ALKT":"Health Care","ARQT":"Health Care","AXSM":"Health Care","BTAI":"Health Care",
    "CRNX":"Health Care","DVAX":"Health Care","EYPT":"Health Care","FATE":"Health Care",
    "GLUE":"Health Care","HALO":"Health Care","IMGN":"Health Care","KROS":"Health Care",
    "LYRA":"Health Care","MIRM":"Health Care","NBIX":"Health Care","OMCL":"Health Care",
    "PTCT":"Health Care","RCKT":"Health Care","SANA":"Health Care","TNGX":"Health Care",
    "URGN":"Health Care","VCEL":"Health Care","XENE":"Health Care","YMAB":"Health Care",
    "ZNTL":"Health Care","ACAD":"Health Care","AGIO":"Health Care","AMPH":"Health Care",
    "ANIP":"Health Care","ARWR":"Health Care","BPMC":"Health Care","CERE":"Health Care",
    "CYTK":"Health Care","DAWN":"Health Care","ENTA":"Health Care","GTX":"Health Care",
    "IOVA":"Health Care","KDNY":"Health Care","LPTX":"Health Care","MGNX":"Health Care",
    "NUVL":"Health Care","OPCH":"Health Care","PRTA":"Health Care","RAPT":"Health Care",
    "RNA":"Health Care","SGMO":"Health Care","TALS":"Health Care","THRD":"Health Care",
    "VKTX":"Health Care","XERS":"Health Care","ZEAL":"Health Care",
    "AEO":"Consumer Discretionary","ANF":"Consumer Discretionary","BURL":"Consumer Discretionary",
    "CHS":"Consumer Discretionary","CROX":"Consumer Discretionary","FL":"Consumer Discretionary",
    "GME":"Consumer Discretionary","GCO":"Consumer Discretionary","GES":"Consumer Discretionary",
    "JWN":"Consumer Discretionary","LE":"Consumer Discretionary","M":"Consumer Discretionary",
    "MOV":"Consumer Discretionary","PIPR":"Consumer Discretionary","PLCE":"Consumer Discretionary",
    "RL":"Consumer Discretionary","SCVL":"Consumer Discretionary","SFIX":"Consumer Discretionary",
    "SHOO":"Consumer Discretionary","SKX":"Consumer Discretionary","TSCO":"Consumer Discretionary",
    "UA":"Consumer Discretionary","WSM":"Consumer Discretionary","ZUMZ":"Consumer Discretionary",
    "AA":"Materials","ATW":"Energy","BOOM":"Energy","BRS":"Energy","CKH":"Energy",
    "CRK":"Energy","DLPX":"Energy","DTM":"Energy","ESTE":"Energy","GPOR":"Energy",
    "HP":"Energy","ICD":"Energy","MTDR":"Energy","NEX":"Energy","NR":"Energy",
    "PARR":"Energy","PTEN":"Energy","RRC":"Energy","SD":"Energy","SM":"Energy",
    "STR":"Energy","TALO":"Energy","TTI":"Energy","WHD":"Energy","XPRO":"Energy",
    # Leveraged/Sector ETFs (non-index-tracking)
    "TQQQ":"Leveraged ETF","SOXL":"Leveraged ETF","UPRO":"Leveraged ETF",
    "FNGU":"Leveraged ETF","TECL":"Leveraged ETF","SPXL":"Leveraged ETF",
    "NVDL":"Leveraged ETF","AMZU":"Leveraged ETF","METU":"Leveraged ETF",
    "GGLL":"Leveraged ETF","BITX":"Leveraged ETF","YINN":"Leveraged ETF",
    "NVDX":"Leveraged ETF","AAPU":"Leveraged ETF","TSLL":"Leveraged ETF",
    "LABU":"Leveraged ETF","DFEN":"Leveraged ETF","CURE":"Leveraged ETF",
    "SMH":"Sector ETF","XLF":"Sector ETF","XLE":"Sector ETF",
    "XBI":"Sector ETF","XHB":"Sector ETF","XRT":"Sector ETF",
    "IBB":"Sector ETF","KRE":"Sector ETF","XME":"Sector ETF",
    "XOP":"Sector ETF","GDX":"Sector ETF","GDXJ":"Sector ETF",
    "URA":"Sector ETF","ICLN":"Sector ETF","TAN":"Sector ETF",
    "ARKK":"Thematic ETF","ARKG":"Thematic ETF","ARKW":"Thematic ETF",
    "QTEX":"Thematic ETF","QTUM":"Thematic ETF","AIQ":"Thematic ETF"
}

CAP_TIER = {
    # Large-Cap (>=$73B, 100조원+)
    "AAPL":"large",
    "MSFT":"large",
    "NVDA":"large",
    "GOOGL":"large",
    "META":"large",
    "AVGO":"large",
    "AMD":"large",
    "ORCL":"large",
    "ADBE":"large",
    "CRM":"large",
    "ACN":"large",
    "CSCO":"large",
    "INTU":"large",
    "TXN":"large",
    "QCOM":"large",
    "AMAT":"large",
    "MU":"large",
    "NOW":"large",
    "PANW":"large",
    "ADP":"large",
    "IBM":"large",
    "LRCX":"large",
    "KLAC":"large",
    "CDNS":"large",
    "SNPS":"large",
    "FTNT":"large",
    "GLW":"large",
    "NFLX":"large",
    "DIS":"large",
    "CMCSA":"large",
    "VZ":"large",
    "T":"large",
    "TMUS":"large",
    "AMZN":"large",
    "HD":"large",
    "MCD":"large",
    "LOW":"large",
    "SBUX":"large",
    "BKNG":"large",
    "TJX":"large",
    "GM":"large",
    "MAR":"large",
    "ORLY":"large",
    "ROST":"large",
    "PG":"large",
    "KO":"large",
    "PEP":"large",
    "WMT":"large",
    "COST":"large",
    "MDLZ":"large",
    "XOM":"large",
    "CVX":"large",
    "COP":"large",
    "EOG":"large",
    "SLB":"large",
    "MPC":"large",
    "VLO":"large",
    "PSX":"large",
    "WMB":"large",
    "JPM":"large",
    "BAC":"large",
    "WFC":"large",
    "GS":"large",
    "MS":"large",
    "SCHW":"large",
    "BLK":"large",
    "SPGI":"large",
    "AXP":"large",
    "CB":"large",
    "PNC":"large",
    "USB":"large",
    "COF":"large",
    "C":"large",
    "JNJ":"large",
    "UNH":"large",
    "LLY":"large",
    "PFE":"large",
    "ABT":"large",
    "ABBV":"large",
    "TMO":"large",
    "DHR":"large",
    "MRK":"large",
    "AMGN":"large",
    "GILD":"large",
    "VRTX":"large",
    "ISRG":"large",
    "SYK":"large",
    "MDT":"large",
    "ELV":"large",
    "CI":"large",
    "HON":"large",
    "UPS":"large",
    "BA":"large",
    "RTX":"large",
    "LMT":"large",
    "NOC":"large",
    "GE":"large",
    "CAT":"large",
    "DE":"large",
    "MMM":"large",
    "GD":"large",
    "CSX":"large",
    "UNP":"large",
    "WM":"large",
    "TT":"large",
    "PH":"large",
    "LIN":"large",
    "SHW":"large",
    "FCX":"large",
    "NEM":"large",
    "AMT":"large",
    "PLD":"large",
    "EQIX":"large",
    "WELL":"large",
    "NEE":"large",
    "DUK":"large",
    "SO":"large",
    "HOOD":"large",
    "APP":"large",
    "COHR":"large",
    "DDOG":"large",
    "SNOW":"large",
    "NET":"large",
    "PLTR":"large",
    "ABNB":"large",
    # Mid-Cap ($7.3B~$73B, 10조~100조원)
    "ROP":"mid",
    "MSI":"mid",
    "NTAP":"mid",
    "PAYX":"mid",
    "TEL":"mid",
    "HPQ":"mid",
    "HPE":"mid",
    "FN":"mid",
    "ACLS":"mid",
    "FORM":"mid",
    "EA":"mid",
    "TTWO":"mid",
    "CHTR":"mid",
    "FOXA":"mid",
    "LYV":"mid",
    "MTCH":"mid",
    "OMC":"mid",
    "NWSA":"mid",
    "NKE":"mid",
    "TGT":"mid",
    "F":"mid",
    "YUM":"mid",
    "DPZ":"mid",
    "DHI":"mid",
    "LEN":"mid",
    "NVR":"mid",
    "PHM":"mid",
    "AZO":"mid",
    "GPC":"mid",
    "BBY":"mid",
    "DG":"mid",
    "DLTR":"mid",
    "ULTA":"mid",
    "KHC":"mid",
    "GIS":"mid",
    "SYY":"mid",
    "ADM":"mid",
    "STZ":"mid",
    "EL":"mid",
    "CL":"mid",
    "KMB":"mid",
    "CHD":"mid",
    "HSY":"mid",
    "MKC":"mid",
    "KR":"mid",
    "OXY":"mid",
    "DVN":"mid",
    "TFC":"mid",
    "AFL":"mid",
    "MET":"mid",
    "PRU":"mid",
    "AIG":"mid",
    "ALL":"mid",
    "TRV":"mid",
    "AON":"mid",
    "BRO":"mid",
    "RJF":"mid",
    "KEY":"mid",
    "RF":"mid",
    "CFG":"mid",
    "FITB":"mid",
    "HBAN":"mid",
    "ZION":"mid",
    "REGN":"mid",
    "BIIB":"mid",
    "ZTS":"mid",
    "BDX":"mid",
    "EW":"mid",
    "BSX":"mid",
    "DXCM":"mid",
    "IDXX":"mid",
    "WAT":"mid",
    "IQV":"mid",
    "LH":"mid",
    "CRL":"mid",
    "VTRS":"mid",
    "HUM":"mid",
    "CNC":"mid",
    "MOH":"mid",
    "COR":"mid",
    "ITW":"mid",
    "NSC":"mid",
    "RSG":"mid",
    "CARR":"mid",
    "OTIS":"mid",
    "IR":"mid",
    "FTV":"mid",
    "DOV":"mid",
    "IEX":"mid",
    "NDSN":"mid",
    "SWK":"mid",
    "TXT":"mid",
    "DOW":"mid",
    "DD":"mid",
    "ECL":"mid",
    "IFF":"mid",
    "NUE":"mid",
    "VMC":"mid",
    "CCI":"mid",
    "PSA":"mid",
    "O":"mid",
    "SBAC":"mid",
    "EXR":"mid",
    "VTR":"mid",
    "D":"mid",
    "AEP":"mid",
    "EXC":"mid",
    "SRE":"mid",
    "XEL":"mid",
    "ED":"mid",
    "WEC":"mid",
    "CNP":"mid",
    "FE":"mid",
    "PEG":"mid",
    "ES":"mid",
    "AWK":"mid",
    "EIX":"mid",
    "SMCI":"mid",
    "ENPH":"mid",
    "RBLX":"mid",
    "U":"mid",
    "SOFI":"mid",
    "AFRM":"mid",
    "RKT":"mid",
    "ONON":"mid",
    "CAVA":"mid",
    "LITE":"mid",
    "NXT":"mid",
    "VST":"mid",
    "CWEN":"mid",
    "BEP":"mid",
    "BKR":"mid",
    "BXP":"mid",
    "CBOE":"mid",
    "ATO":"mid",
    "AOS":"mid",
    "ALLE":"mid",
    "AME":"mid",
    "AIT":"mid",
    "AIZ":"mid",
    "BEN":"mid",
    "CPT":"mid",
    "ESS":"mid",
    "FDS":"mid",
    "FFIV":"mid",
    "FRT":"mid",
    "HST":"mid",
    "JLL":"mid",
    "KIM":"mid",
    "LAMR":"mid",
    "MAA":"mid",
    "NFG":"mid",
    "PKG":"mid",
    "RL":"mid",
    "TRMB":"mid",
    "TYL":"mid",
    "UDR":"mid",
    "WPC":"mid",
    "ZWS":"mid",
    "BRKR":"mid",
    "TKO":"mid",
    "JBL":"mid",
    "ENS":"mid",
    "ASTS":"mid",
    "OKTA":"mid",
    "MDB":"mid",
    "TWLO":"mid",
    "DOCU":"mid",
    "DASH":"mid",
    "AXSM":"mid",
    "HALO":"mid",
    "NBIX":"mid",
    "ARWR":"mid",
    "CYTK":"mid",
    "BURL":"mid",
    "GME":"mid",
    "TSCO":"mid",
    "WSM":"mid",
    "AA":"mid",
    "DTM":"mid",
    "RRC":"mid",
    "SM":"mid",
    # Small-Cap (<$7.3B, <10조원)
    "AAP":"small",
    "CPB":"small",
    "CAG":"small",
    "VSTS":"small",
    "RUN":"small",
    "ARRY":"small",
    "SHLS":"small",
    "SEDG":"small",
    "AESI":"small",
    "HASI":"small",
    "LNC":"small",
    "OGS":"small",
    "SIGI":"small",
    "RHI":"small",
    "MAN":"small",
    "TNET":"small",
    "DYN":"small",
    "CE":"small",
    "ASH":"small",
    "BC":"small",
    "SAIC":"small",
    "TTEK":"small",
    "ATKR":"small",
    "MTRN":"small",
    "TGLS":"small",
    "SPWR":"small",
    "CERT":"small",
    "S":"small",
    "FSLY":"small",
    "ALKT":"small",
    "ARQT":"small",
    "BTAI":"small",
    "CRNX":"small",
    "EYPT":"small",
    "FATE":"small",
    "GLUE":"small",
    "KROS":"small",
    "LYRA":"small",
    "MIRM":"small",
    "OMCL":"small",
    "PTCT":"small",
    "RCKT":"small",
    "SANA":"small",
    "TNGX":"small",
    "URGN":"small",
    "VCEL":"small",
    "XENE":"small",
    "ZNTL":"small",
    "ACAD":"small",
    "AGIO":"small",
    "AMPH":"small",
    "ANIP":"small",
    "DAWN":"small",
    "ENTA":"small",
    "GTX":"small",
    "IOVA":"small",
    "MGNX":"small",
    "NUVL":"small",
    "OPCH":"small",
    "PRTA":"small",
    "RNA":"small",
    "SGMO":"small",
    "VKTX":"small",
    "XERS":"small",
    "AEO":"small",
    "ANF":"small",
    "CROX":"small",
    "GCO":"small",
    "LE":"small",
    "M":"small",
    "MOV":"small",
    "PIPR":"small",
    "PLCE":"small",
    "SCVL":"small",
    "SFIX":"small",
    "SHOO":"small",
    "UA":"small",
    "ZUMZ":"small",
    "BOOM":"small",
    "CRK":"small",
    "DLPX":"small",
    "GPOR":"small",
    "HP":"small",
    "MTDR":"small",
    "PARR":"small",
    "PTEN":"small",
    "SD":"small",
    "TALO":"small",
    "TTI":"small",
    "WHD":"small",
    "XPRO":"small",
    # Unknown (default large)
    "ANSS":"large",
    "IPG":"large",
    "K":"large",
    "MRO":"large",
    "MMC":"large",
    "AY":"large",
    "NEP":"large",
    "NYCB":"large",
    "NOVA":"large",
    "NARI":"large",
    "ZI":"large",
    "DVAX":"large",
    "IMGN":"large",
    "YMAB":"large",
    "BPMC":"large",
    "CERE":"large",
    "KDNY":"large",
    "LPTX":"large",
    "RAPT":"large",
    "TALS":"large",
    "THRD":"large",
    "ZEAL":"large",
    "CHS":"large",
    "FL":"large",
    "GES":"large",
    "JWN":"large",
    "SKX":"large",
    "ATW":"large",
    "BRS":"large",
    "CKH":"large",
    "ESTE":"large",
    "ICD":"large",
    "NEX":"large",
    "NR":"large",
    "STR":"large",
    # Leveraged/Sector ETFs
    "TQQQ":"large","SOXL":"large","UPRO":"large",
    "FNGU":"large","TECL":"large","SPXL":"large",
    "NVDL":"large","AMZU":"large","METU":"large",
    "GGLL":"large","BITX":"large","YINN":"large",
    "NVDX":"large","AAPU":"large","TSLL":"large",
    "LABU":"large","DFEN":"large","CURE":"large",
    "SMH":"large","XLF":"large","XLE":"large",
    "XBI":"large","XHB":"large","XRT":"large",
    "IBB":"large","KRE":"large","XME":"large",
    "XOP":"large","GDX":"large","GDXJ":"large",
    "URA":"large","ICLN":"large","TAN":"large",
    "ARKK":"large","ARKG":"large","ARKW":"large",
    "QTEX":"large","QTUM":"large","AIQ":"large",
}

TICKERS = list(UNIVERSE.keys())

# ─── Helpers ───
def fetch_chart(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=3mo&includePrePost=true"
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        result = data["chart"]["result"][0]
        close = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
        return close
    except Exception:
        return None

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

def calc_ma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def calc_atr(prices, period=14):
    if len(prices) < period + 1:
        return None
    trs = []
    for i in range(1, len(prices)):
        trs.append(abs(prices[i] - prices[i-1]))
    return sum(trs[-period:]) / period

def score_long(prices, rsi, ma20, ma50, atr):
    if not prices or rsi is None or ma20 is None or ma50 is None:
        return 0
    score = 0
    # RSI oversold
    if rsi < 30: score += 3
    elif rsi < 40: score += 2
    elif rsi < 50: score += 1
    # Trend
    if ma20 > ma50: score += 2
    elif prices[-1] > ma50: score += 1
    # Pullback proximity to MA20
    dist_ma20 = abs(prices[-1] - ma20) / prices[-1] * 100
    if dist_ma20 < 3: score += 2
    elif dist_ma20 < 6: score += 1
    # Volatility filter
    if atr and atr / prices[-1] * 100 < 3: score += 1
    return score

def score_short(prices, rsi, ma20, ma50, atr):
    if not prices or rsi is None or ma20 is None or ma50 is None:
        return 0
    score = 0
    if rsi > 70: score += 3
    elif rsi > 65: score += 2
    elif rsi > 60: score += 1
    if ma20 < ma50: score += 2
    elif prices[-1] < ma50: score += 1
    dist_ma20 = abs(prices[-1] - ma20) / prices[-1] * 100
    if dist_ma20 < 3: score += 2
    elif dist_ma20 < 6: score += 1
    if atr and atr / prices[-1] * 100 < 3: score += 1
    return score

def get_scan_type():
    now = datetime.datetime.utcnow()
    t = (now.hour, now.minute)
    mapping = {
        (9, 0): "pre_market_1",
        (13, 30): "pre_market_2",
        (15, 30): "regular_1",
        (17, 30): "regular_2",
        (20, 0): "regular_3",
        (21, 30): "after_hours",
    }
    return mapping.get(t, "after_hours")

def load_portfolio():
    default = {"cash": SEED, "positions": [], "watchlist": [], "history": [], "total_invested": 0.0}
    if os.path.exists(PORTFOLIO_PATH):
        with open(PORTFOLIO_PATH) as f:
            return json.load(f)
    return default

def save_portfolio(p):
    os.makedirs(os.path.dirname(PORTFOLIO_PATH), exist_ok=True)
    with open(PORTFOLIO_PATH, "w") as f:
        json.dump(p, f, indent=2, ensure_ascii=False)

def market_aggression(ctx):
    """시장 미온에 따라 0.0(최대 방어) ~ 1.0(최대 공격) 반환"""
    spy = ctx.get("S&P 500", {})
    vix = ctx.get("VIX", {})
    qqq = ctx.get("Nasdaq 100", {})
    score = 0.5
    
    if spy.get("5d") is not None:
        c5 = spy["5d"]
        if c5 > 3: score += 0.3
        elif c5 > 1: score += 0.15
        elif c5 < -3: score -= 0.3
        elif c5 < -1: score -= 0.15
    
    if qqq.get("5d") is not None:
        n5 = qqq["5d"]
        if n5 > 4: score += 0.1
        elif n5 < -5: score -= 0.1
    
    if vix.get("price") is not None:
        vp = vix["price"]
        if vp < 15: score += 0.2
        elif vp < 18: score += 0.1
        elif vp > 30: score -= 0.3
        elif vp > 25: score -= 0.2
    
    return max(0.0, min(1.0, score))

def market_context():
    ctx = {}
    for t, name in [("SPY","S&P 500"), ("QQQ","Nasdaq 100"), ("%5EVIX","VIX"), ("DIA","Dow Jones"), ("IWM","Russell 2000")]:
        prices = fetch_chart(t)
        if prices and len(prices) >= 6:
            c5 = (prices[-1] - prices[-6]) / prices[-6] * 100
            c20 = (prices[-1] - prices[-21]) / prices[-21] * 100 if len(prices) >= 21 else 0.0
            ctx[name] = {"price": round(prices[-1], 2), "5d": round(c5, 2), "20d": round(c20, 2)}
        else:
            ctx[name] = {"price": None, "5d": None, "20d": None}
        time.sleep(0.2)
    return ctx

def _scan_one(ticker):
    '''Scan a single ticker, returns dict or None.'''
    prices = fetch_chart(ticker)
    if not prices or len(prices) < 50:
        return None
    rsi = calc_rsi(prices)
    ma20 = calc_ma(prices, 20)
    ma50 = calc_ma(prices, 50)
    atr = calc_atr(prices)
    if rsi is None or ma20 is None or ma50 is None:
        return None
    lscore = score_long(prices, rsi, ma20, ma50, atr)
    if lscore < 4:
        return None
    price = round(prices[-1], 2)
    if price < 2.0:
        return None
    return {
        "ticker": ticker, "sector": UNIVERSE.get(ticker, "Unknown"),
        "price": price, "rsi": round(rsi, 1),
        "ma20": round(ma20, 2), "ma50": round(ma50, 2),
        "side": "LONG", "score": lscore,
        "dist_ma20": round(abs(prices[-1]-ma20)/prices[-1]*100, 2),
        "atr_pct": round(atr/prices[-1]*100, 2) if atr else 0
    }

def scan_tickers(tickers, is_full=False):
    '''Parallel scan using ThreadPoolExecutor.'''
    results = []
    workers = 12 if is_full else 6
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_scan_one, t): t for t in tickers}
        for future in as_completed(futures):
            try:
                res = future.result(timeout=15)
                if res:
                    results.append(res)
            except Exception:
                pass
    return results

def update_positions(portfolio, today):
    """Check stops and targets. Return list of closed positions."""
    closed = []
    remaining = []
    for pos in portfolio["positions"]:
        prices = fetch_chart(pos["ticker"])
        if not prices:
            remaining.append(pos)
            continue
        current = prices[-1]
        days_held = (datetime.datetime.strptime(today, "%Y-%m-%d") - datetime.datetime.strptime(pos["date"], "%Y-%m-%d")).days
        exit_reason = None
        if current <= pos["stop_price"]:
            exit_reason = f"스톱 출도 (${current:.2f} ≤ ${pos['stop_price']:.2f})"
        elif current >= pos["target_price"]:
            exit_reason = f"목표 도달 (${current:.2f} ≥ ${pos['target_price']:.2f})"
        
        if exit_reason:
            pnl = (current - pos["entry_price"]) * pos["shares"]
            closed.append({**pos, "exit_price": round(current, 2), "exit_date": today, "pnl": round(pnl, 2), "exit_reason": exit_reason})
            portfolio["cash"] += pos["shares"] * current
        else:
            pos["current_price"] = round(current, 2)
            pos["days_held"] = days_held
            pos["unrealized"] = round((current - pos["entry_price"]) * pos["shares"], 2)
            remaining.append(pos)
    portfolio["positions"] = remaining
    portfolio["history"].extend(closed)
    return closed

def enter_positions(portfolio, candidates, today, aggression=0.5):
    """시장 상황(aggression)에 따라 현금 뱃스, 진입 개수, 포지션 크기를 유동적으로 조절."""
    entered = []
    sector_counts = {}
    cap_counts = {"large": 0, "mid": 0, "small": 0}
    for p in portfolio["positions"]:
        sector_counts[p["sector"]] = sector_counts.get(p["sector"], 0) + 1
        tier = CAP_TIER.get(p["ticker"], "large")
        cap_counts[tier] = cap_counts.get(tier, 0) + 1
    
    # ─── 유동적 매다러 계산 ───
    min_cash = SEED * (0.15 + (1.0 - aggression) * 0.35)   # 월활: 15%~50%
    max_new = max(4, int(4 + aggression * 6))                 # 최소 4~10개
    sector_limit = 3 if aggression > 0.65 else 2
    small_limit = 4 if aggression > 0.65 else (3 if aggression > 0.35 else 2)
    mid_limit = 5 if aggression > 0.65 else 4
    large_limit = max(1, int(MAX_POSITIONS * 0.60))
    
    # Ensure at least 1 mid and 1 small if candidates exist and aggression allows
    min_mid = 1 if aggression > 0.25 else 0
    min_small = 1 if aggression > 0.25 else 0
    
    # Split candidates by tier and sort each by score desc
    by_tier = {"large": [], "mid": [], "small": []}
    for c in candidates:
        tier = CAP_TIER.get(c["ticker"], "large")
        by_tier[tier].append(c)
    for tier in by_tier:
        by_tier[tier].sort(key=lambda x: x["score"], reverse=True)
    
    # Round-robin selection: prioritize under-represented tiers
    idx = {"large": 0, "mid": 0, "small": 0}
    
    def pick_next():
        """Return the best candidate from the most under-represented tier."""
        # Determine which tier needs filling most
        total_pos = len(portfolio["positions"]) + len(entered)
        if total_pos == 0:
            # Start with small -> mid -> large to ensure mix
            order = ["small", "mid", "large"]
        else:
            # Pick tier with lowest current %
            ratios = {}
            for t in ["small", "mid", "large"]:
                current = cap_counts.get(t, 0)
                # If we haven't met minimums for mid/small, boost priority
                if t == "mid" and current < min_mid:
                    ratios[t] = -999
                elif t == "small" and current < min_small:
                    ratios[t] = -999
                else:
                    ratios[t] = current / max(total_pos, 1)
            order = sorted(ratios, key=ratios.get)
        
        for tier in order:
            while idx[tier] < len(by_tier[tier]):
                c = by_tier[tier][idx[tier]]
                idx[tier] += 1
                
                sec = c["sector"]
                t = CAP_TIER.get(c["ticker"], "large")
                
                # Sector limit
                if sector_counts.get(sec, 0) >= sector_limit:
                    continue
                # Already in portfolio
                if any(p["ticker"] == c["ticker"] for p in portfolio["positions"]):
                    continue
                
                return c
        return None
    
    while len(entered) < max_new and len(portfolio["positions"]) + len(entered) < MAX_POSITIONS:
        c = pick_next()
        if not c:
            break
        
        sec = c["sector"]
        tier = CAP_TIER.get(c["ticker"], "large")
        
        # ─── Position size ───
        base = DEFAULT_BASE_POSITION * (0.3 + aggression * 0.8)  # $3K ~ $11K
        # Score bonus
        if c["score"] >= 8: base += 6_000
        elif c["score"] >= 7: base += 3_000
        elif c["score"] <= 4: base *= 0.6
        
        # Cap tier sizing
        tier_mult = {"large": 1.0, "mid": 0.75, "small": 0.55}[tier]
        size = base * tier_mult
        
        # Enforce dynamic minimum cash
        if portfolio["cash"] - size < min_cash:
            size = portfolio["cash"] - min_cash
        
        if size < 3_000:
            continue
        
        shares = math.floor(size / c["price"])
        if shares < 1:
            continue
        
        entry = c["price"]
        
        # Dynamic stop / target based on aggression + tier
        base_stop = {"large": 0.05, "mid": 0.07, "small": 0.10}[tier]
        base_tgt = {"large": 0.10, "mid": 0.12, "small": 0.15}[tier]
        stop_pct = base_stop * (0.8 + aggression * 0.5)   # 0.8x ~ 1.3x
        tgt_pct = base_tgt * (0.8 + aggression * 0.6)      # 0.8x ~ 1.4x
        
        stop = round(entry * (1 - stop_pct), 2)
        target = round(entry * (1 + tgt_pct), 2)
        
        pos = {
            "ticker": c["ticker"], "sector": sec, "side": c["side"],
            "cap_tier": tier, "entry_price": entry, "shares": shares, "date": today,
            "stop_price": stop, "target_price": target,
            "current_price": entry, "days_held": 0, "unrealized": 0.0
        }
        portfolio["positions"].append(pos)
        portfolio["cash"] -= shares * entry
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
        cap_counts[tier] = cap_counts.get(tier, 0) + 1
        entered.append(pos)
    return entered

def portfolio_summary(portfolio):
    invested = sum(p["shares"] * p.get("current_price", p["entry_price"]) for p in portfolio["positions"])
    total_unrealized = sum(p.get("unrealized", 0) for p in portfolio["positions"])
    realized = sum(h.get("pnl", 0) for h in portfolio["history"])
    total_value = portfolio["cash"] + invested
    total_return = total_value - SEED
    spy_return_pct = 0.0
    spy_baseline = portfolio.get("spy_baseline_price")
    if spy_baseline:
        spy_current = portfolio.get("spy_current_price", spy_baseline)
        spy_return_pct = round((spy_current - spy_baseline) / spy_baseline * 100, 2)
    dia_return_pct = 0.0
    dia_baseline = portfolio.get("dia_baseline_price")
    if dia_baseline:
        dia_current = portfolio.get("dia_current_price", dia_baseline)
        dia_return_pct = round((dia_current - dia_baseline) / dia_baseline * 100, 2)
    iwm_return_pct = 0.0
    iwm_baseline = portfolio.get("iwm_baseline_price")
    if iwm_baseline:
        iwm_current = portfolio.get("iwm_current_price", iwm_baseline)
        iwm_return_pct = round((iwm_current - iwm_baseline) / iwm_baseline * 100, 2)
    return {
        "cash": round(portfolio["cash"], 2),
        "invested": round(invested, 2),
        "total_value": round(total_value, 2),
        "unrealized": round(total_unrealized, 2),
        "realized": round(realized, 2),
        "total_return": round(total_return, 2),
        "return_pct": round(total_return / SEED * 100, 2),
        "spy_return_pct": spy_return_pct,
        "spy_baseline": spy_baseline,
        "spy_current": portfolio.get("spy_current_price"),
        "dia_return_pct": dia_return_pct,
        "dia_baseline": dia_baseline,
        "dia_current": portfolio.get("dia_current_price"),
        "iwm_return_pct": iwm_return_pct,
        "iwm_baseline": iwm_baseline,
        "iwm_current": portfolio.get("iwm_current_price"),
        "open_count": len(portfolio["positions"])
    }

def build_message(scan_type, ctx, summary, positions, watchlist, closed, entered, today, aggression=0.5, history=None):
    labels = {
        "pre_market_1": "✅ 오버나이트 스캔 1",
        "pre_market_2": "🌅 프리장 스캔 2 (오픈 준비)",
        "regular_1": "🌞 정규장 스캔 1 (오전)",
        "regular_2": "⚡ 정규장 스캔 2 (중간)",
        "regular_3": "🌙 정규장 스캔 3 (장마감)",
        "after_hours": "🌌 데이장 마감 요약"
    }
    label = labels.get(scan_type, "🟢 롱온리 스윙 트레이더")
    
    # Aggression emoji
    if aggression >= 0.75:
        ag_emoji, ag_text = "🚀", f"공격도 {aggression:.0%} (강세 추세 추경)"
    elif aggression <= 0.3:
        ag_emoji, ag_text = "🛡️", f"공격도 {aggression:.0%} (방어 우선)"
    else:
        ag_emoji, ag_text = "⚖️", f"공격도 {aggression:.0%} (중립)"
    
    lines = [f"**{label}**", f"📅 {today} UTC | {ag_emoji} {ag_text}", ""]
    
    # Market context
    lines.append("📊 **시장 환경**")
    for name, data in ctx.items():
        if data["price"]:
            lines.append(f"• {name}: ${data['price']} (5일: {data['5d']}%, 20일: {data['20d']}%)")
        else:
            lines.append(f"• {name}: 데이터 불가")
    lines.append("")
    
    # Portfolio summary
    lines.append("💼 **포트폴리오**")
    cash_ratio = summary['cash'] / SEED * 100
    invested_ratio = summary['invested'] / SEED * 100
    lines.append(f"• 총 자산: ${summary['total_value']:,} (수익률 {summary['return_pct']}%)")
    # S&P 500 benchmark comparison
    spy_ret = summary.get('spy_return_pct', 0)
    vs_spy = summary['return_pct'] - spy_ret
    vs_emoji = "🟢" if vs_spy > 0 else ("🟡" if vs_spy > -2 else "🔴")
    lines.append(f"• vs S&P 500: {vs_emoji} {vs_spy:+.1f}%p (포트 {summary['return_pct']}% vs SPY {spy_ret}%)")
    lines.append(f"• 현금: ${summary['cash']:,} ({cash_ratio:.1f}%) | 투자금: ${summary['invested']:,} ({invested_ratio:.1f}%)")
    lines.append(f"• 미실현: ${summary['unrealized']:,} | 실현수익: ${summary['realized']:,}")
    lines.append(f"• 보유 종목: {summary['open_count']}/{MAX_POSITIONS}")
    if aggression >= 0.75:
        lines.append("• 💡 **현금 전략**: 강세장 — 현금 비중 최소화, 적극 배분 중")
    elif aggression <= 0.3:
        lines.append("• 💡 **현금 전략**: 약세장 — 방어적 현금 비중 유지, 신규 진입 축소")
    else:
        lines.append("• 💡 **현금 전략**: 중립 구간 — 점수 높은 셋업 선별 진입")
    lines.append("")
    
    # Closed positions
    if closed:
        lines.append("🛑 **오늘 청산**")
        for c in closed:
            emoji = "🔴" if c["pnl"] < 0 else "🟢"
            lines.append(f"{emoji} [{c['side']}] {c['ticker']}: ${c['pnl']:+,} — {c['exit_reason']}")
        lines.append("")
    
    # New entries
    if entered:
        lines.append("🎯 **신규 진입**")
        for e in entered:
            emoji = "🟢"  # LONG only
            lines.append(f"{emoji} [{e['side']}] {e['ticker']} ${e['entry_price']} ×{e['shares']} (스톱: ${e['stop_price']}, 목표: ${e['target_price']})")
        lines.append("")
    
    # Open positions
    if positions:
        lines.append("📊 **보유 중 포지션**")
        for p in positions:
            emoji = "🟢"  # LONG only
            unreal = p.get("unrealized", 0)
            pct = unreal / (p["entry_price"] * p["shares"]) * 100 if p["entry_price"] * p["shares"] > 0 else 0
            current_price = p.get('current_price', p['entry_price'])
            notional = current_price * p['shares']
            weight = notional / summary['total_value'] * 100 if summary['total_value'] > 0 else 0
            lines.append(f"{emoji} {p['ticker']} {p['side']} | 매수가: ${p['entry_price']} 현재: ${current_price} | P&L: ${unreal:+,} ({pct:+.1f}%) | 비중 {weight:.1f}% | {p.get('days_held', 0)}일")
        lines.append("")
    
    # Watchlist
    if watchlist:
        lines.append("👁️ **워치리스트 (스윙 셋업 후보)**")
        for w in watchlist[:10]:
            emoji = "🟢"  # LONG only
            lines.append(f"{emoji} {w['ticker']} [{w['side']}] 점수 {w['score']}/8 | RSI {w['rsi']} | ${w['price']}")
        lines.append("")
    
    # Strategy note: 벤치마크 비교 & VIX
    vs_spy = summary.get('return_pct', 0) - summary.get('spy_return_pct', 0)
    vs_dia = summary.get('return_pct', 0) - summary.get('dia_return_pct', 0)
    vs_iwm = summary.get('return_pct', 0) - summary.get('iwm_return_pct', 0)
    if vs_spy > 0:
        lines.append(f"🏆 **S&P 500 대비**: {vs_spy:+.1f}%p 초과 수익 중 🔥")
    else:
        lines.append(f"📉 **S&P 500 대비**: {vs_spy:+.1f}%p — 지수 따라잡기 목표")
    lines.append(f"📊 **Dow Jones 대비**: {vs_dia:+.1f}%p | **Russell 2000 대비**: {vs_iwm:+.1f}%p")
    vix = ctx.get("VIX", {}).get("price")
    if vix and vix > 25:
        lines.append(f"⚠️ **VIX 고점** ({vix}) — 방어적 자세, 현금 비중 유지")
    elif vix and vix < 15:
        lines.append(f"🚀 **VIX 저점** ({vix}) — 적극적 매수 기회")
    else:
        lines.append("🔄 **VIX 중립** — 고점수 롱 셋업 선별 진입")
    
    # Trade history log (last 10)
    if history:
        lines.append("")
        lines.append("📜 **거래 내역 (최근 10건)**")
        for h in history[-10:]:
            emoji = "🔴" if h["pnl"] < 0 else "🟢"
            entry_notional = h["entry_price"] * h["shares"]
            ret_pct = h["pnl"] / entry_notional * 100 if entry_notional else 0
            lines.append(f"{emoji} {h['date']} [{h['side']}] {h['ticker']} | 진입 ${h['entry_price']} → 청산 ${h['exit_price']} | P&L ${h['pnl']:+,.2f} ({ret_pct:+.2f}%) | {h['exit_reason']}")
    
    return "\n".join(lines)

def send_discord(message):
    if not WEBHOOK_URL:
        return
    try:
        payload = {"content": message}
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception:
        pass

def main():
    try:
        scan_type = get_scan_type()
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        portfolio = load_portfolio()
        
        # Market context (lightweight)
        ctx = market_context()
        aggression = market_aggression(ctx)
        
        # SPY / DIA / IWM benchmark tracking (set baseline on first run)
        spy_price = ctx.get("S&P 500", {}).get("price")
        if spy_price:
            portfolio["spy_current_price"] = spy_price
            if not portfolio.get("spy_baseline_price"):
                portfolio["spy_baseline_price"] = spy_price
                portfolio["spy_baseline_date"] = today
        dia_price = ctx.get("Dow Jones", {}).get("price")
        if dia_price:
            portfolio["dia_current_price"] = dia_price
            if not portfolio.get("dia_baseline_price"):
                portfolio["dia_baseline_price"] = dia_price
                portfolio["dia_baseline_date"] = today
        iwm_price = ctx.get("Russell 2000", {}).get("price")
        if iwm_price:
            portfolio["iwm_current_price"] = iwm_price
            if not portfolio.get("iwm_baseline_price"):
                portfolio["iwm_baseline_price"] = iwm_price
                portfolio["iwm_baseline_date"] = today
        
        # Determine which tickers to scan
        if scan_type in ("pre_market_1", "after_hours"):
            # Full universe scan
            scan_targets = TICKERS
            is_full = True
        else:
            # Positions + watchlist only
            scan_targets = list(set([p["ticker"] for p in portfolio["positions"]] + [w["ticker"] for w in portfolio.get("watchlist", [])]))
            is_full = False
            if len(scan_targets) < 30:
                # Fallback: add some from full universe if watchlist is small
                scan_targets = list(dict.fromkeys(scan_targets + TICKERS[:30]))
        
        # Update existing positions (check stops/targets/time)
        closed = update_positions(portfolio, today)
        
        # Scan for setups
        results = scan_tickers(scan_targets, is_full)
        
        # For full scans, rebuild watchlist
        if is_full:
            # Filter out existing positions
            existing_tickers = {p["ticker"] for p in portfolio["positions"]}
            watchlist = [r for r in results if r["ticker"] not in existing_tickers]
            
            # 지수 약세 시 방어 선욱 주 보너스
            DEFENSIVE = {"Utilities", "Consumer Staples", "Health Care"}
            OFFENSIVE = {"Technology", "Communication Services"}
            if aggression < 0.5:
                for w in watchlist:
                    if w["sector"] in DEFENSIVE:
                        w["score"] += 2  # 방어 섹터 롱 보너스
            
            watchlist.sort(key=lambda x: x["score"], reverse=True)
            portfolio["watchlist"] = watchlist[:20]
        else:
            watchlist = portfolio.get("watchlist", [])
        
        # For after_hours and regular_1, try to enter new positions from watchlist
        entered = []
        if scan_type in ("after_hours", "regular_1"):
            candidates = [w for w in watchlist if w.get("score", 0) >= 5]
            entered = enter_positions(portfolio, candidates, today, aggression)
        
        # Recompute summary with updated prices for remaining positions
        for p in portfolio["positions"]:
            prices = fetch_chart(p["ticker"])
            if prices:
                p["current_price"] = round(prices[-1], 2)
                p["unrealized"] = round((prices[-1] - p["entry_price"]) * p["shares"], 2)
                p["days_held"] = (datetime.datetime.strptime(today, "%Y-%m-%d") - datetime.datetime.strptime(p["date"], "%Y-%m-%d")).days
        
        summary = portfolio_summary(portfolio)
        save_portfolio(portfolio)
        
        # Build and send message
        msg = build_message(scan_type, ctx, summary, portfolio["positions"], portfolio.get("watchlist", []), closed, entered, today, aggression, portfolio.get("history", []))
        print(msg)
        
    except Exception as e:
        print(f"⚠️ 스윙 트레이더 오류: {e}")

if __name__ == "__main__":
    main()
