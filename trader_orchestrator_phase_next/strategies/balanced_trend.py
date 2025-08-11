from dataclasses import dataclass
FEATURES = ["ret1","ret5","vol","rsi","macd","macd_sig","macd_hist"]
@dataclass
class Config:
    timeframe: str = "15Min"
    tp_pct: float = 0.02
    sl_pct: float = 0.01
    max_positions: int = 3
    shadow: bool = False
class BalancedTrend:
    name = "balanced_trend"
    def __init__(self, cfg: Config):
        self.cfg = cfg
    def symbols(self, core_symbols):
        return core_symbols
    def generate_signal(self, df_features):
        if df_features is None or len(df_features)<70: return (None, 0.0)
        row = df_features.iloc[-1]
        long_bias = row["macd"] > row["macd_sig"] and row["rsi"] > 50
        short_bias = row["macd"] < row["macd_sig"] and row["rsi"] < 50
        if long_bias: return ("buy", float(min(0.9, 0.5 + (row["rsi"]-50)/100)))
        if short_bias: return ("sell", float(min(0.9, 0.5 + (50-row["rsi"])/100)))
        return (None, 0.0)
