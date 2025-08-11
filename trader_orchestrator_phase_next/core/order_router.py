import os, requests
from dotenv import load_dotenv
load_dotenv()
ALPACA_KEY = os.getenv("ALPACA_KEY_ID","")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY","")
ALPACA_PAPER_BASE = os.getenv("ALPACA_PAPER_BASE","https://paper-api.alpaca.markets")
ALPACA_LIVE_BASE  = os.getenv("ALPACA_LIVE_BASE","https://api.alpaca.markets")
ALERT_WEBHOOK = os.getenv("ALERT_WEBHOOK","")
def headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
def base(paper=True):
    return ALPACA_PAPER_BASE if paper else ALPACA_LIVE_BASE
def account(paper=True):
    r = requests.get(f"{base(paper)}/v2/account", headers=headers(), timeout=15)
    r.raise_for_status(); return r.json()
def positions(paper=True):
    r = requests.get(f"{base(paper)}/v2/positions", headers=headers(), timeout=15)
    r.raise_for_status(); return r.json()
def submit_bracket(symbol, qty, side, tp_price, sl_price, paper=True):
    data = {"symbol":symbol,"qty":str(qty),"side":side,"type":"market","time_in_force":"day",
            "order_class":"bracket","take_profit":{"limit_price": round(tp_price, 2)},
            "stop_loss":{"stop_price": round(sl_price, 2)}}
    r = requests.post(f"{base(paper)}/v2/orders", headers=headers(), json=data, timeout=20)
    if r.status_code >= 300: raise RuntimeError(r.text)
    return r.json()
def submit_split_brackets(symbol, qty, side, tp1, sl1, tp2, sl2, paper=True):
    q1 = max(1, int(qty*0.5)); q2 = max(1, qty - q1)
    o1 = submit_bracket(symbol, q1, side, tp1, sl1, paper)
    o2 = submit_bracket(symbol, q2, side, tp2, sl2, paper)
    return [o1,o2]
def cancel_all(paper=True):
    try: requests.delete(f"{base(paper)}/v2/orders", headers=headers(), timeout=20)
    except Exception: pass
def close_all(paper=True):
    try: requests.delete(f"{base(paper)}/v2/positions", headers=headers(), timeout=20)
    except Exception: pass
def alert(event, payload):
    if not ALERT_WEBHOOK: return
    try: requests.post(ALERT_WEBHOOK, json={"event":event,"payload":payload}, timeout=10)
    except Exception: pass
