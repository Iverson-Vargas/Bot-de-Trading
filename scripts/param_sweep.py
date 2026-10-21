"""
Barrido rapido de parametros para encontrar la configuracion mas rentable.
Prueba multiples combinaciones de min_score, min_conditions, y filtros.
"""
import os, sys
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass

current_dir = os.path.dirname(os.path.abspath(__file__))
motor_dir = os.path.join(os.path.dirname(current_dir), "motor-logico")
sys.path.append(motor_dir)

from backtester import Backtester
from strategy import LegacyConfluenceStrategy, AdaptiveEdgeStrategy
from risk_manager import RiskManager, RiskMode
from data_fetcher import load_candles_from_csv

root_dir = os.path.dirname(current_dir)
data_file = os.path.join(root_dir, "data", "historical", "eurusd_m1_dukascopy_2023.csv")

print("Cargando datos...")
candles = load_candles_from_csv(data_file)
n = len(candles)
split = int(n * 0.70)
print(f"Total: {n:,} | Train: {split:,} | Test: {n-split:,}\n")

risk = RiskManager(mode=RiskMode.FIXED_AMOUNT, fixed_amount=10.0,
                   daily_stop_loss=-500.0, daily_take_profit=1000.0, max_consecutive_losses=6)

configs = [
    # Legacy con diferentes umbrales
    ("Legacy_65", LegacyConfluenceStrategy(min_score=65.0)),
    ("Legacy_70", LegacyConfluenceStrategy(min_score=70.0)),
    ("Legacy_75", LegacyConfluenceStrategy(min_score=75.0)),
    ("Legacy_80", LegacyConfluenceStrategy(min_score=80.0)),
    ("Legacy_85", LegacyConfluenceStrategy(min_score=85.0)),
    ("Legacy_90", LegacyConfluenceStrategy(min_score=90.0)),
    ("Legacy_95", LegacyConfluenceStrategy(min_score=95.0)),

    # Adaptive con diferentes min_conditions y umbrales, sin filtros
    ("Adapt_50_2c_NF", AdaptiveEdgeStrategy(min_score=50.0, min_conditions=2, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_55_2c_NF", AdaptiveEdgeStrategy(min_score=55.0, min_conditions=2, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_60_2c_NF", AdaptiveEdgeStrategy(min_score=60.0, min_conditions=2, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_65_2c_NF", AdaptiveEdgeStrategy(min_score=65.0, min_conditions=2, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_70_2c_NF", AdaptiveEdgeStrategy(min_score=70.0, min_conditions=2, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_50_3c_NF", AdaptiveEdgeStrategy(min_score=50.0, min_conditions=3, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_55_3c_NF", AdaptiveEdgeStrategy(min_score=55.0, min_conditions=3, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_60_3c_NF", AdaptiveEdgeStrategy(min_score=60.0, min_conditions=3, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_65_3c_NF", AdaptiveEdgeStrategy(min_score=65.0, min_conditions=3, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_70_3c_NF", AdaptiveEdgeStrategy(min_score=70.0, min_conditions=3, use_session_filter=False, use_volatility_filter=False)),

    # Con filtro de sesion solamente
    ("Adapt_50_2c_SF", AdaptiveEdgeStrategy(min_score=50.0, min_conditions=2, use_session_filter=True, use_volatility_filter=False)),
    ("Adapt_55_2c_SF", AdaptiveEdgeStrategy(min_score=55.0, min_conditions=2, use_session_filter=True, use_volatility_filter=False)),
    ("Adapt_60_2c_SF", AdaptiveEdgeStrategy(min_score=60.0, min_conditions=2, use_session_filter=True, use_volatility_filter=False)),
    ("Adapt_50_3c_SF", AdaptiveEdgeStrategy(min_score=50.0, min_conditions=3, use_session_filter=True, use_volatility_filter=False)),
    ("Adapt_55_3c_SF", AdaptiveEdgeStrategy(min_score=55.0, min_conditions=3, use_session_filter=True, use_volatility_filter=False)),
    ("Adapt_60_3c_SF", AdaptiveEdgeStrategy(min_score=60.0, min_conditions=3, use_session_filter=True, use_volatility_filter=False)),

    # Con ambos filtros
    ("Adapt_50_2c_AF", AdaptiveEdgeStrategy(min_score=50.0, min_conditions=2, use_session_filter=True, use_volatility_filter=True)),
    ("Adapt_55_2c_AF", AdaptiveEdgeStrategy(min_score=55.0, min_conditions=2, use_session_filter=True, use_volatility_filter=True)),
    ("Adapt_60_2c_AF", AdaptiveEdgeStrategy(min_score=60.0, min_conditions=2, use_session_filter=True, use_volatility_filter=True)),

    # RSI mas extremos
    ("Adapt_50_2c_RSI20", AdaptiveEdgeStrategy(min_score=50.0, min_conditions=2, rsi_extreme_low=20.0, rsi_extreme_high=80.0, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_45_2c_RSI20", AdaptiveEdgeStrategy(min_score=45.0, min_conditions=2, rsi_extreme_low=20.0, rsi_extreme_high=80.0, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_40_2c_RSI20", AdaptiveEdgeStrategy(min_score=40.0, min_conditions=2, rsi_extreme_low=20.0, rsi_extreme_high=80.0, use_session_filter=False, use_volatility_filter=False)),

    # BB mas extremo
    ("Adapt_50_2c_BB5", AdaptiveEdgeStrategy(min_score=50.0, min_conditions=2, bb_extreme_pctb=0.05, use_session_filter=False, use_volatility_filter=False)),
    ("Adapt_45_2c_BB5", AdaptiveEdgeStrategy(min_score=45.0, min_conditions=2, bb_extreme_pctb=0.05, use_session_filter=False, use_volatility_filter=False)),

    # Sin conflicto margin
    ("Adapt_50_2c_NC", AdaptiveEdgeStrategy(min_score=50.0, min_conditions=2, conflict_margin=0.0, use_session_filter=False, use_volatility_filter=False)),
]

print(f"Probando {len(configs)} configuraciones...\n")
print(f"{'Config':<25} | {'Train WR%':>9} {'Edge%':>7} {'N':>5} {'PnL':>10} | {'Test WR%':>9} {'Edge%':>7} {'N':>5} {'PnL':>10}")
print("-" * 105)

results = []
for name, strat in configs:
    bt = Backtester(strategy=strat, risk_manager=risk, candle_timeframe_seconds=60,
                    expiration_seconds=60, payout_percent=82.0, initial_balance=10000.0)
    train = bt.run(candles, 0, split)
    test = bt.run(candles, split, n)

    results.append((name, train, test))

    tr_marker = "+" if train.edge > 0 else " "
    te_marker = "+" if test.edge > 0 else " "
    both = " <<<" if train.edge > 0 and test.edge > 0 and train.total_trades >= 20 and test.total_trades >= 10 else ""

    print(f"{name:<25} | {train.win_rate:>8.2f}% {train.edge:>+6.2f}% {train.total_trades:>4} ${train.net_profit:>+8.2f} | {test.win_rate:>8.2f}% {test.edge:>+6.2f}% {test.total_trades:>4} ${test.net_profit:>+8.2f}{both}")

# Sort by out-of-sample edge
print(f"\n{'='*105}")
print(f"TOP 10 POR EDGE OUT-OF-SAMPLE:")
print(f"{'='*105}")
results.sort(key=lambda x: x[2].edge, reverse=True)
for name, train, test in results[:10]:
    print(f"{name:<25} | Train: {train.win_rate:.2f}% (Edge {train.edge:+.2f}%, N={train.total_trades}) | Test: {test.win_rate:.2f}% (Edge {test.edge:+.2f}%, N={test.total_trades})")

# Sort by consistency (minimum edge between train/test)
print(f"\nTOP 10 POR CONSISTENCIA (min edge entre Train y Test):")
results.sort(key=lambda x: min(x[1].edge, x[2].edge), reverse=True)
for name, train, test in results[:10]:
    min_edge = min(train.edge, test.edge)
    print(f"{name:<25} | Train: {train.win_rate:.2f}% (Edge {train.edge:+.2f}%) | Test: {test.win_rate:.2f}% (Edge {test.edge:+.2f}%) | Min: {min_edge:+.2f}%")
