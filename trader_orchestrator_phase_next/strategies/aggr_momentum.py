from dataclasses import dataclass
FEATURES = ["ret1","ret5","vol","rsi","macd","macd_sig","macd_hist"]
@dataclass
class Config:
    timeframe: str = "5Min"
    tp_pct: float = 0.03
    sl_pct: float = 0.015
    max_positions: int = 2
    shadow: bool = False
class AggressiveMomentum:
    name = "aggr_momentum"
    def __init__(self, cfg: Config):
        self.cfg = cfg
    def symbols(self, core_symbols):
        return core_symbols
    def generate_signal(self, df_features):
        if df_features is None or len(df_features)<60: return (None, 0.0)
        row = df_features.iloc[-1]
        if row["macd_hist"] > 0 and row["rsi"] > 60: return ("buy", 0.65)
        if row["macd_hist"] < 0 and row["rsi"] < 40: return ("sell", 0.6)
        return (None, 0.0)
