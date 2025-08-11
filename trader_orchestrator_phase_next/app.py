import os, json, time, threading, datetime as dt
from flask import Flask, jsonify, Response, request, abort
from dotenv import load_dotenv
from core import data_hub as DH
from core import order_router as OR
from core.risk import RiskManager, size_position, sector_of
from strategies.balanced_trend import BalancedTrend, Config as CBalanced
from strategies.smallcap_scalper import SmallCapScalper, Config as CScalp
from strategies.aggr_momentum import AggressiveMomentum, Config as CMomo

load_dotenv()
STATE_DIR = os.getenv("STATE_DIR","./state")
os.makedirs(STATE_DIR, exist_ok=True)
with open("config.json","r") as f: CFG = json.load(f)
PIN_HASH = os.getenv("APP_PIN_SHA256","")

def sha256(s: str):
    import hashlib; return hashlib.sha256(s.encode("utf-8")).hexdigest()
def require_pin():
    if not PIN_HASH: return
    pin = request.headers.get("X-PIN") or request.args.get("pin") or request.cookies.get("pin")
    if not pin or sha256(pin)!=PIN_HASH: abort(401)
LOG_PATH = os.path.join(STATE_DIR,"events.log")
def log(level, event, details=None):
    line = json.dumps({"ts": dt.datetime.utcnow().isoformat()+"Z", "level":level, "event":event, "details":details or {}})
    with open(LOG_PATH,"a") as f: f.write(line+"\n")
def notify(event, payload):
    OR.alert(event, payload)

class StrategyWrapper:
    def __init__(self, name, impl, cfg, alloc):
        self.name=name; self.impl=impl; self.cfg=cfg; self.alloc=alloc
        self.enabled=True; self.shadow=False; self.trades=0; self.wins=0; self.losses=0
        self.max_positions=getattr(impl, "cfg", None).max_positions if hasattr(impl,"cfg") else 3
class Orchestrator:
    def __init__(self):
        self.running=False; self.thread=None; self.interval=CFG["interval_sec"]; self.paper=CFG["paper"]
        self.risk=RiskManager(CFG); self.last_tick=None; self.last_msg="Idle"; self.error=None
        self.cooldowns={}; self.panic=False
        self.strategies=[
            StrategyWrapper("balanced_trend", BalancedTrend(CBalanced()), CFG, CFG["allocations"]["balanced_trend"]),
            StrategyWrapper("smallcap_scalper", SmallCapScalper(CScalp()), CFG, CFG["allocations"]["smallcap_scalper"]),
            StrategyWrapper("aggr_momentum", AggressiveMomentum(CMomo()), CFG, CFG["allocations"]["aggr_momentum"]),
        ]
    def smallcap_watchlist(self):
        out=[]
        for s in CFG.get("symbols_universe", []):
            px, vol = DH.last_close_and_vol(s)
            if px is None or vol is None: continue
            if px <= CFG["watchlist"]["smallcap_max_price"] and vol >= CFG["watchlist"]["smallcap_min_vol"]:
                out.append(s)
        return out[:20]  # cap size
    def _fetch_and_feature(self, symbols, timeframe):
        end = dt.datetime.utcnow().isoformat()+"Z"
        start = (dt.datetime.utcnow()-dt.timedelta(days=10)).isoformat()+"Z"
        out={}
        for sym in symbols:
            try:
                df = DH.bars(sym, start, end, timeframe, 1000)
                f = DH.add_features(df)
                out[sym]=f
            except Exception as e:
                log("ERROR","bars_failed",{"symbol":sym,"err":str(e)})
        return out
    def _enter_trade(self, sym, side, confidence, strategy_name):
        try:
            acct = OR.account(self.paper); equity=float(acct.get("equity",0)); last_equity=float(acct.get("last_equity", equity))
        except Exception as e:
            log("ERROR","account_failed",{"err":str(e)}); notify("account_failed",{"err":str(e)}); return
        if self.risk.hit_daily_dd(equity, last_equity):
            log("WARN","daily_dd_hit",{"equity":equity,"last_equity":last_equity}); notify("daily_dd_hit",{"equity":equity}); return
        end = dt.datetime.utcnow().isoformat()+"Z"
        start = (dt.datetime.utcnow()-dt.timedelta(days=2)).isoformat()+"Z"
        df = DH.bars(sym, start, end, "1Min", 50)
        if df is None or df.empty: return
        px = float(df["close"].iloc[-1])
        qty = size_position(px, confidence, CFG["risk"]["equity_cap"], CFG["risk"]["per_trade_risk_frac"])
        # Two-bracket approach: partial take + extended runner
        tp_pct = {"balanced_trend":0.02,"smallcap_scalper":0.012,"aggr_momentum":0.03}.get(strategy_name,0.02)
        sl_pct = {"balanced_trend":0.01,"smallcap_scalper":0.006,"aggr_momentum":0.015}.get(strategy_name,0.01)
        tp1 = px*(1+tp_pct) if side=="buy" else px*(1-tp_pct)
        sl1 = px*(1-sl_pct) if side=="buy" else px*(1+sl_pct)
        # runner leg gets extra target; same stop (approx trailing concept via higher TP/keeping runner alive)
        tp2 = px*(1+tp_pct*(1+CFG["router"]["trail_extra_tp_pct"])) if side=="buy" else px*(1-tp_pct*(1+CFG["router"]["trail_extra_tp_pct"]))
        sl2 = sl1
        try:
            if self._strategy_by_name(strategy_name).shadow:
                log("INFO","shadow_signal",{"strategy":strategy_name,"symbol":sym,"side":side,"qty":qty,"tp1":tp1,"tp2":tp2,"sl":sl1})
            else:
                OR.submit_split_brackets(sym, qty, "buy" if side=="buy" else "sell", tp1, sl1, tp2, sl2, self.paper)
                log("INFO","order_submitted",{"strategy":strategy_name,"symbol":sym,"side":side,"qty":qty,"tp1":tp1,"tp2":tp2,"sl":sl1})
        except Exception as e:
            log("ERROR","order_failed",{"symbol":sym,"err":str(e)}); notify("order_failed",{"symbol":sym,"err":str(e)})
    def _strategy_by_name(self, name):
        for s in self.strategies:
            if s.name==name: return s
        return None
    def _loop(self):
        while self.running:
            try:
                if self.panic: time.sleep(1); continue
                if CFG["risk"]["skip_minutes_after_open"] and DH.is_open_now():
                    if DH.minutes_since_open() < CFG["risk"]["skip_minutes_after_open"]:
                        self.last_msg = "Skipping early session"; self.last_tick = dt.datetime.utcnow().isoformat()+"Z"; time.sleep(5); continue
                core_syms = CFG["symbols_core"]; smallcap_syms = self.smallcap_watchlist()
                cache={}; needed=set()
                for SW in self.strategies:
                    if not SW.enabled: continue
                    if SW.name=="smallcap_scalper": [needed.add((s,"2Min")) for s in smallcap_syms]
                    elif SW.name=="balanced_trend": [needed.add((s,"15Min")) for s in core_syms]
                    else: [needed.add((s,"5Min")) for s in core_syms]
                by_tf={}
                for s,tf in needed: by_tf.setdefault(tf,set()).add(s)
                for tf, syms in by_tf.items(): cache[tf]= self._fetch_and_feature(sorted(syms), tf)
                try: open_pos = OR.positions(self.paper)
                except Exception: open_pos = []
                # sector counts for exposure caps
                sector_counts = {}
                for p in open_pos or []:
                    sec = sector_of(p.get("symbol",""))
                    sector_counts[sec] = sector_counts.get(sec,0)+1
                for SW in self.strategies:
                    if not SW.enabled: continue
                    if SW.name=="smallcap_scalper": symbols=smallcap_syms; tf="2Min"; limit=SW.max_positions
                    elif SW.name=="balanced_trend": symbols=core_syms; tf="15Min"; limit=SW.max_positions
                    else: symbols=core_syms; tf="5Min"; limit=SW.max_positions
                    taken=0
                    for sym in symbols:
                        if taken>=limit: break
                        if not self.risk.can_enter_symbol(sym, open_pos, sector_counts): continue
                        if sym in self.cooldowns and self.risk.on_loss_cooldown(sym, self.cooldowns): continue
                        F = cache.get(tf,{}).get(sym)
                        side, conf = SW.impl.generate_signal(F)
                        if not side: continue
                        if side=="buy" and conf < CFG["router"]["prob_long_thresh"]: continue
                        if side=="sell" and conf < CFG["router"]["prob_short_thresh"]: continue
                        self._enter_trade(sym, side, conf, SW.name); taken+=1
                self.last_msg="ok"; self.error=None
            except Exception as e:
                self.error=str(e); log("ERROR","loop_error",{"err":str(e)}); notify("loop_error",{"err":str(e)})
            finally:
                self.last_tick = dt.datetime.utcnow().isoformat()+"Z"
                slept=0
                while self.running and slept < self.interval: time.sleep(1); slept+=1
    def start(self):
        if self.running: return False
        self.running=True; self.thread=threading.Thread(target=self._loop, daemon=True); self.thread.start(); return True
    def stop(self): self.running=False

ORCH = Orchestrator(); app = Flask(__name__)

@app.before_request
def guard():
    if request.method=="GET" and request.path in ("/","/status","/logs","/strategies"): return
    require_pin()

@app.get("/")
def index():
    html = """
<!doctype html>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Orchestrator</title>
<style>
:root { font-family: -apple-system, system-ui, sans-serif; }
body { margin: 20px; }
.wrap { max-width: 1100px; margin:auto; }
.row { display:flex; gap:12px; flex-wrap:wrap; }
button { flex:1 1 200px; font-size:22px; padding:16px; border-radius:14px; border:0; }
.start{ background:#2ecc71; color:#fff; }
.stop{ background:#e74c3c; color:#fff; }
.panic{ background:#8e44ad; color:#fff; }
.card{ background:#f5f5f7; padding:16px; border-radius:12px; margin-top:14px; }
.grid{ display:grid; grid-template-columns: 1fr 1fr; gap:8px 16px; }
.pill{ display:inline-block; padding:6px 10px; border-radius:999px; background:#eee; }
.panel{ border:1px solid #ddd; border-radius:10px; padding:12px; margin-top:10px; background:#fff; }
h3{ margin:6px 0; }
</style>
<div class="wrap">
  <h1>Trading Orchestrator</h1>
  <div class="row">
    <button class="start" onclick="post('/start')">Start</button>
    <button class="stop" onclick="post('/stop')">Stop</button>
    <button class="panic" onclick="post('/panic')">PANIC CLOSE</button>
  </div>

  <div class="card">
    <div id="run" class="pill">checking...</div>
    <div class="grid" style="margin-top:10px">
      <div>Paper</div><div id="paper"></div>
      <div>Last tick</div><div id="last"></div>
      <div>Message</div><div id="msg"></div>
      <div>Error</div><div id="err"></div>
      <div>Interval</div><div id="intv"></div>
    </div>
  </div>

  <div class="card">
    <h2>Strategies</h2>
    <div id="strats"></div>
  </div>

  <div class="card">
    <h3>Logs (tail 30)</h3>
    <pre id="logs" style="white-space:pre-wrap"></pre>
  </div>
</div>
<script>
async function refresh(){
  const s = await (await fetch('/status')).json();
  document.getElementById('run').textContent = s.running ? 'RUNNING' : 'STOPPED';
  document.getElementById('paper').textContent = s.paper ? 'paper' : 'live';
  document.getElementById('last').textContent = s.last_tick || '—';
  document.getElementById('msg').textContent = s.last_msg || '—';
  document.getElementById('err').textContent = s.error || '—';
  document.getElementById('intv').textContent = s.interval + ' sec';

  const st = await (await fetch('/strategies')).json();
  const container = document.getElementById('strats');
  container.innerHTML = '';
  st.forEach(row => {
    const div = document.createElement('div');
    div.className='panel';
    div.innerHTML = `<h3>${row.name}</h3>
      <div>Enabled: ${row.enabled}</div>
      <div>Shadow: ${row.shadow}</div>
      <div>Alloc: ${(row.alloc*100).toFixed(0)}%</div>
      <div>Max positions: ${row.max_positions}</div>
      <div class="row" style="margin-top:8px">
        <button onclick="toggle('/strategy/start?name=${row.name}')">Start</button>
        <button onclick="toggle('/strategy/stop?name=${row.name}')">Stop</button>
        <button onclick="toggle('/strategy/shadow?name=${row.name}')">Toggle Shadow</button>
      </div>`;
    container.appendChild(div);
  });

  const logs = await (await fetch('/logs?n=30')).text();
  document.getElementById('logs').textContent = logs;
}
async function toggle(url){ await fetch(url, {method:'POST'}); setTimeout(refresh, 200); }
async function post(p){ await fetch(p, {method:'POST'}); setTimeout(refresh, 200); }
setInterval(refresh, 4000); refresh();
</script>
"""
    return Response(html, mimetype="text/html")

@app.get("/strategies")
def strategies():
    return jsonify([{"name":s.name,"enabled":s.enabled,"shadow":s.shadow,"alloc":s.alloc,"max_positions":s.max_positions} for s in ORCH.strategies])

@app.post("/strategy/start")
def strategy_start():
    name = request.args.get("name")
    s = ORCH._strategy_by_name(name)
    if s: s.enabled=True
    return jsonify({"ok":True})

@app.post("/strategy/stop")
def strategy_stop():
    name = request.args.get("name")
    s = ORCH._strategy_by_name(name)
    if s: s.enabled=False
    return jsonify({"ok":True})

@app.post("/strategy/shadow")
def strategy_shadow():
    name = request.args.get("name")
    s = ORCH._strategy_by_name(name)
    if s: s.shadow = not s.shadow
    return jsonify({"ok":True, "shadow": s.shadow if s else None})

@app.get("/status")
def status():
    return jsonify({
        "running": ORCH.running, "paper": ORCH.paper, "interval": ORCH.interval,
        "last_tick": ORCH.last_tick, "last_msg": ORCH.last_msg, "error": ORCH.error
    })

@app.post("/start")
def start():
    ORCH.interval = CFG["interval_sec"]
    started = ORCH.start(); return jsonify({"ok":True, "already_running": (not started)})

@app.post("/stop")
def stop():
    ORCH.stop(); return jsonify({"ok":True})

@app.post("/panic")
def panic():
    OR.cancel_all(ORCH.paper); OR.close_all(ORCH.paper); ORCH.panic=True; log("WARN","panic_close",{}); return jsonify({"ok":True})

@app.get("/logs")
def tail_logs():
    n = int(request.args.get("n","30"))
    try:
        with open(LOG_PATH,"r") as f: lines=f.readlines()[-n:]
        return "".join(lines)
    except FileNotFoundError:
        return ""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","8080")), debug=False)
