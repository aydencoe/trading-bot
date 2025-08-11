import os, datetime as dt, requests, pandas as pd
from dotenv import load_dotenv
load_dotenv()
ALPACA_KEY = os.getenv("ALPACA_KEY_ID","")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY","")
DATA_BASE = "https://data.alpaca.markets"
def alpaca_headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
def bars(symbol, start, end, timeframe="5Min", limit=1000):
    url = f"{DATA_BASE}/v2/stocks/{symbol}/bars"
    r = requests.get(url, headers=alpaca_headers(), params={
        "timeframe": timeframe, "start": start, "end": end, "limit": limit, "adjustment":"all"
    }, timeout=20)
    r.raise_for_status()
    js = r.json().get("bars", [])
    if not js: return pd.DataFrame()
    df = pd.DataFrame(js)
    df["t"] = pd.to_datetime(df["t"]); df.set_index("t", inplace=True)
    df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"}, inplace=True)
    return df[["open","high","low","close","volume"]]
def last_close_and_vol(symbol):
    end = dt.datetime.utcnow().isoformat()+"Z"
    start = (dt.datetime.utcnow()-dt.timedelta(days=5)).isoformat()+"Z"
    df = bars(symbol, start, end, "1Day", 5)
    if df is None or df.empty: return None, None
    return float(df["close"].iloc[-1]), float(df["volume"].iloc[-1])
def is_open_now(now=None):
    now = now or dt.datetime.utcnow()
    if now.weekday() >= 5: return False
    t = now.time()
    return (t >= dt.time(13,30)) and (t <= dt.time(20,0))
def minutes_since_open(now=None):
    now = now or dt.datetime.utcnow()
    open_dt = now.replace(hour=13, minute=30, second=0, microsecond=0)
    return (now - open_dt).total_seconds()/60.0
def add_features(df):
    import numpy as np
    if df.empty or len(df)<60: return df
    df = df.copy()
    df["ret1"] = df["close"].pct_change()
    df["ret5"] = df["close"].pct_change(5)
    df["vol"] = df["ret1"].rolling(20).std()
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    df["macd"] = macd; df["macd_sig"] = signal; df["macd_hist"] = macd - signal
    delta = df["close"].diff()
    gain = (delta.clip(lower=0)).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss.replace(0, np.nan))
    df["rsi"] = 100 - (100/(1+rs))
    df.dropna(inplace=True)
    return df
