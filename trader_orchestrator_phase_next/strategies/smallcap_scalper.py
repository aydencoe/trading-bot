from dataclasses import dataclass
FEATURES = ["ret1","ret5","vol","rsi","macd","macd_sig","macd_hist"]
@dataclass
class Config:
    timeframe: str = "2Min"
    tp_pct: float = 0.012
    sl_pct: float = 0.006
    max_positions: int = 5
    shadow: bool = False
class SmallCapScalper:
    name = "smallcap_scalper"
    def __init__(self, cfg: Config):
        self.cfg = cfg
    def symbols(self, smallcap_list):
        return smallcap_list
    def generate_signal(self, df_features):
        if df_features is None or len(df_features)<40: return (None, 0.0)
        row = df_features.iloc[-1]
        if row["rsi"] > 55 and row["macd_hist"] > 0: return ("buy", 0.6)
        if row["rsi"] < 45 and row["macd_hist"] < 0: return ("sell", 0.55)
        return (None, 0.0)
