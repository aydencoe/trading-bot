import datetime as dt
SECTOR_MAP = {
  "AAPL":"tech","MSFT":"tech","NVDA":"tech","AMD":"tech","META":"tech","GOOGL":"tech","TSLA":"tech","QQQ":"tech","XLK":"tech",
  "BAC":"finance","XLF":"finance",
  "XLE":"energy",
  "SPY":"broad","IWM":"broad"
}
def sector_of(sym):
    return SECTOR_MAP.get(sym, "broad")
class RiskManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.daily_dd_hit = False
    def reset_day(self):
        self.daily_dd_hit = False
    def hit_daily_dd(self, equity, last_equity):
        dd = (last_equity - equity) / max(1e-9, last_equity) * 100
        self.daily_dd_hit = dd >= self.cfg["risk"]["max_daily_drawdown_pct"]
        return self.daily_dd_hit
    def can_enter_symbol(self, symbol, open_positions, sector_counts):
        if symbol in {p.get("symbol") for p in open_positions or []}: return False
        sec = sector_of(symbol)
        max_per = self.cfg["exposure_limits"]["sector_max_positions"].get(sec, 2)
        return sector_counts.get(sec,0) < max_per
    def on_loss_cooldown(self, symbol, cooldowns):
        until = cooldowns.get(symbol)
        if not until: return False
        return dt.datetime.utcnow() < until
def size_position(price, confidence, equity_cap, risk_frac):
    conf = max(0.2, min(1.0, float(confidence)))
    dollar_risk = equity_cap * risk_frac * conf
    qty = max(1, int(dollar_risk / max(0.5, price*0.01)))
    return qty
