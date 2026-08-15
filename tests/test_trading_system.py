"""
Suite de Pruebas Unitarias e Integración para el Bot de Trading Cuantitativo.
Prueba indicadores, gestión de riesgo, almacenamiento estructurado, estrategias y backtesting.
"""

import os
import sys
import unittest

# Añadir motor-logico al path
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
motor_dir = os.path.join(root_dir, "motor-logico")
sys.path.append(motor_dir)

from indicators import (
    calcular_sma, calcular_ema, calcular_desviacion_estandar,
    calcular_bandas_bollinger, calcular_rsi, calcular_estocastico,
    analizar_patron_vela, calcular_soportes_resistencias, calcular_atr
)
from risk_manager import RiskManager, RiskMode
from storage import TradeStorage
from strategy import LegacyConfluenceStrategy, CalibratedStatisticalStrategy
from backtester import Backtester
from data_fetcher import generate_benchmark_eurusd_candles


class TestIndicators(unittest.TestCase):
    def test_sma(self):
        prices = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(calcular_sma(prices, 5), 3.0)
        self.assertEqual(calcular_sma(prices, 3), 4.0)
        self.assertIsNone(calcular_sma(prices, 10))

    def test_ema(self):
        prices = [10.0] * 20 + [20.0]
        ema = calcular_ema(prices, 10)
        self.assertIsNotNone(ema)
        self.assertGreater(ema, 10.0)

    def test_bollinger_bands(self):
        prices = [1.1000 + (0.0010 * (i % 5)) for i in range(30)]
        bb = calcular_bandas_bollinger(prices, 20, 2.0)
        self.assertIsNotNone(bb)
        self.assertIn("middle", bb)
        self.assertIn("upper", bb)
        self.assertIn("lower", bb)
        self.assertGreater(bb["upper"], bb["middle"])
        self.assertLess(bb["lower"], bb["middle"])
        self.assertGreater(bb["bandwidth"], 0)

    def test_rsi(self):
        # Serie en alza continua -> RSI muy alto
        prices_up = [1.1000 + (0.0005 * i) for i in range(30)]
        rsi_up = calcular_rsi(prices_up, 14)
        self.assertIsNotNone(rsi_up)
        self.assertGreater(rsi_up, 70.0)

        # Serie en caída continua -> RSI muy bajo
        prices_down = [1.1000 - (0.0005 * i) for i in range(30)]
        rsi_down = calcular_rsi(prices_down, 14)
        self.assertIsNotNone(rsi_down)
        self.assertLess(rsi_down, 30.0)

    def test_stochastic(self):
        velas = [
            {"open": 1.10, "close": 1.10 + 0.001*i, "min": 1.09 + 0.001*i, "max": 1.11 + 0.001*i}
            for i in range(30)
        ]
        stoch = calcular_estocastico(velas, 14, 3, 3)
        self.assertIsNotNone(stoch)
        self.assertIn("k", stoch)
        self.assertIn("d", stoch)

    def test_candlestick_pattern(self):
        # Martillo con mecha inferior larga (rechazo alcista)
        hammer = {"open": 1.1050, "close": 1.1060, "min": 1.1000, "max": 1.1065}
        patron = analizar_patron_vela(hammer)
        self.assertTrue(patron["rechazo_alcista"])
        self.assertFalse(patron["rechazo_bajista"])
        self.assertGreater(patron["pct_mecha_inf"], 50.0)


class TestRiskManager(unittest.TestCase):
    def test_fixed_stake(self):
        rm = RiskManager(mode=RiskMode.FIXED_AMOUNT, fixed_amount=15.0)
        stake, _ = rm.calculate_stake(current_balance=5000.0)
        self.assertEqual(stake, 15.0)

    def test_fractional_kelly_with_edge(self):
        rm = RiskManager(mode=RiskMode.FRACTIONAL_KELLY, kelly_fraction=0.25, max_stake_percent=2.5)
        # Con 60% win rate y 82% payout: f* = (0.60 * 1.82 - 1) / 0.82 = 0.1122 -> Quarter Kelly = ~2.8% -> Cap a 2.5%
        stake, reason = rm.calculate_stake(current_balance=1000.0, estimated_win_rate=0.60, payout_percent=82.0)
        self.assertGreaterEqual(stake, 1.0)
        self.assertLessEqual(stake, 25.0) # max 2.5% de 1000 = $25

    def test_daily_stop_loss_trigger(self):
        rm = RiskManager(daily_stop_loss=-50.0, daily_take_profit=100.0)
        check = rm.check_trade_permission(
            session_pnl=-55.0,
            current_balance=9945.0,
            consecutive_losses=3,
            consecutive_wins=0
        )
        self.assertFalse(check.allowed)
        self.assertTrue(check.is_halted)
        self.assertIn("STOP LOSS", check.reason)

    def test_loss_streak_reduction(self):
        rm = RiskManager(mode=RiskMode.FIXED_AMOUNT, fixed_amount=20.0, loss_streak_reduction=True)
        stake_normal, _ = rm.calculate_stake(current_balance=5000.0, consecutive_losses=0)
        stake_reduced, _ = rm.calculate_stake(current_balance=5000.0, consecutive_losses=2)
        self.assertEqual(stake_normal, 20.0)
        self.assertEqual(stake_reduced, 10.0) # 50% de reducción


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.test_db = os.path.join(root_dir, "data", "test_trades.db")
        self.test_csv = os.path.join(root_dir, "data", "test_trades.csv")
        self.storage = TradeStorage(db_path=self.test_db, csv_path=self.test_csv)

    def tearDown(self):
        if os.path.exists(self.test_db):
            try: os.remove(self.test_db)
            except Exception: pass
        if os.path.exists(self.test_csv):
            try: os.remove(self.test_csv)
            except Exception: pass

    def test_save_and_retrieve_trade(self):
        self.storage.save_trade(
            trade_id="test_001",
            direction="CALL",
            amount=10.0,
            entry_price=1.15000,
            exit_price=1.15020,
            outcome="GANADA",
            net_profit=8.20,
            balance_post=10008.20,
            indicators={"rsi": 28.5, "bollinger": {"middle": 1.1500, "upper": 1.1510, "lower": 1.1490, "std": 0.0005, "bandwidth": 0.17}}
        )
        recent = self.storage.get_recent_trades(limit=10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["id"], "test_001")
        self.assertEqual(recent[0]["outcome"], "GANADA")

        metrics = self.storage.get_metrics_summary(payout_percent=82.0)
        self.assertEqual(metrics["total_trades"], 1)
        self.assertEqual(metrics["win_rate"], 100.0)
        self.assertEqual(metrics["pnl_neto"], 8.20)


class TestBacktesterAndStrategies(unittest.TestCase):
    def test_backtest_run(self):
        candles = generate_benchmark_eurusd_candles(count=500, timeframe_seconds=10, seed=123)
        strat = LegacyConfluenceStrategy(min_score=65.0)
        rm = RiskManager(mode=RiskMode.FIXED_AMOUNT, fixed_amount=10.0, daily_stop_loss=-500.0, daily_take_profit=1000.0)
        bt = Backtester(strat, risk_manager=rm, candle_timeframe_seconds=10, expiration_seconds=60)
        
        result = bt.run(candles)
        self.assertEqual(result.total_candles, 500)
        self.assertGreaterEqual(result.break_even_win_rate, 54.0)
        self.assertIsInstance(result.win_rate, float)


if __name__ == "__main__":
    unittest.main()
