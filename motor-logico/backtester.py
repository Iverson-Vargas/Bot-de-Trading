"""
Motor de Backtesting Histórico Cuantitativo para Estrategias de Trading Binario.
Permite simular velas OHLC (10s, 1m), calcular métricas completas (Win Rate vs Break-Even,
Profit Factor, Drawdown, Expectativa Matemática, Rachas) y realizar validación Out-of-Sample.
"""

import math
import sys
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from strategy import BaseStrategy, LegacyConfluenceStrategy, CalibratedStatisticalStrategy
from risk_manager import RiskManager, RiskMode


@dataclass
class BacktestTrade:
    trade_num: int
    entry_index: int
    exit_index: int
    entry_timestamp: int
    exit_timestamp: int
    direction: str
    stake: float
    entry_price: float
    exit_price: float
    outcome: str  # GANADA, PERDIDA, EMPATE
    net_profit: float
    balance_after: float
    score_call: float
    score_put: float
    reasons: str
    indicators: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    strategy_name: str
    candle_timeframe_seconds: int
    expiration_seconds: int
    payout_percent: float
    initial_balance: float
    final_balance: float
    net_profit: float
    return_percent: float
    
    total_candles: int
    total_trades: int
    wins: int
    losses: int
    ties: int
    win_rate: float
    call_win_rate: float
    put_win_rate: float
    calls_count: int
    puts_count: int
    
    break_even_win_rate: float
    edge: float
    has_statistical_edge: bool
    
    gross_profit: float
    gross_loss: float
    profit_factor: float
    expectancy_per_trade: float
    
    max_drawdown_amount: float
    max_drawdown_percent: float
    max_win_streak: int
    max_loss_streak: int
    
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)

    def print_summary(self):
        edge_sign = "+" if self.edge >= 0 else ""
        pnl_sign = "+" if self.net_profit >= 0 else ""
        status_icon = "✅" if self.has_statistical_edge else "❌"

        print("══════════════════════════════════════════════════════════════════")
        print(f"📊 REPORTE DE BACKTESTING: {self.strategy_name.upper()}")
        print("══════════════════════════════════════════════════════════════════")
        print(f"  Velas Evaluadas:        {self.total_candles} (Timeframe: {self.candle_timeframe_seconds}s)")
        print(f"  Expiración Simulada:    {self.expiration_seconds}s | Payout: {self.payout_percent:.1f}%")
        print(f"  Capital Inicial:        ${self.initial_balance:.2f} USD")
        print(f"  Capital Final:          ${self.final_balance:.2f} USD")
        print(f"  Rendimiento Neto:       {pnl_sign}${self.net_profit:.2f} USD ({pnl_sign}{self.return_percent:.2f}%)")
        print("------------------------------------------------------------------")
        print(f"  Total Operaciones:      {self.total_trades} (CALL: {self.calls_count}, PUT: {self.puts_count})")
        print(f"  Desglose:               {self.wins} Ganadas | {self.losses} Perdidas | {self.ties} Empates")
        print(f"  Win Rate Global:        {self.win_rate:.2f}% (CALL: {self.call_win_rate:.2f}% | PUT: {self.put_win_rate:.2f}%)")
        print(f"  Break-Even Necesario:   {self.break_even_win_rate:.2f}%")
        print(f"  Edge / Ventaja:         {edge_sign}{self.edge:.2f}% {status_icon} (Supera equilibrio: {self.has_statistical_edge})")
        print("------------------------------------------------------------------")
        print(f"  Profit Factor:          {self.profit_factor:.2f}")
        print(f"  Expectativa por Trade:  ${self.expectancy_per_trade:.2f} USD")
        print(f"  Máximo Drawdown:        ${self.max_drawdown_amount:.2f} USD ({self.max_drawdown_percent:.2f}%)")
        print(f"  Racha Máx. Ganadas:     {self.max_win_streak}W")
        print(f"  Racha Máx. Pérdidas:    {self.max_loss_streak}L")
        print("══════════════════════════════════════════════════════════════════\n")


class Backtester:
    def __init__(
        self,
        strategy: BaseStrategy,
        risk_manager: Optional[RiskManager] = None,
        candle_timeframe_seconds: int = 10,
        expiration_seconds: int = 60,
        payout_percent: float = 82.0,
        initial_balance: float = 10000.0,
        cooldown_seconds: int = 40
    ):
        self.strategy = strategy
        self.risk_manager = risk_manager or RiskManager(mode=RiskMode.FIXED_AMOUNT, fixed_amount=10.0)
        self.candle_timeframe = max(1, candle_timeframe_seconds)
        self.expiration_seconds = expiration_seconds
        self.payout_percent = payout_percent
        self.initial_balance = initial_balance
        self.cooldown_seconds = cooldown_seconds
        
        # Velas requeridas para expiración y cooldown
        self.candles_to_expire = max(1, round(self.expiration_seconds / self.candle_timeframe))
        self.candles_cooldown = max(1, round(self.cooldown_seconds / self.candle_timeframe))

    def run(
        self,
        candles: List[Dict[str, float]],
        start_index: int = 0,
        end_index: Optional[int] = None
    ) -> BacktestResult:
        """
        Ejecuta la simulación secuencial sobre la serie temporal de velas.
        """
        total_len = len(candles)
        if end_index is None or end_index > total_len:
            end_index = total_len

        sub_candles = candles[start_index:end_index]
        if len(sub_candles) < self.strategy.min_candles_warmup + self.candles_to_expire:
            # Datos insuficientes
            return self._empty_result(len(sub_candles))

        balance = self.initial_balance
        peak_balance = balance
        max_dd_amount = 0.0
        max_dd_percent = 0.0
        
        trades: List[BacktestTrade] = []
        equity_curve: List[float] = [balance]
        
        consecutive_losses = 0
        consecutive_wins = 0
        max_win_streak = 0
        max_loss_streak = 0
        
        last_trade_candle_idx = -self.candles_cooldown

        history_prices: List[float] = []
        history_candles: List[Dict[str, float]] = []

        for i, candle in enumerate(sub_candles):
            close_price = candle.get("close", candle.get("price", 0.0))
            history_prices.append(close_price)
            history_candles.append(candle)

            # Mantener ventana en memoria de tamaño razonable (ej. 200 velas)
            if len(history_prices) > 200:
                history_prices.pop(0)
            if len(history_candles) > 200:
                history_candles.pop(0)

            # Verificar que no estemos demasiado cerca del final para poder resolver la expiración
            if i + self.candles_to_expire >= len(sub_candles):
                continue

            # Evaluar cooldown
            if (i - last_trade_candle_idx) < self.candles_cooldown:
                continue

            # Evaluar estrategia
            decision = self.strategy.evaluate(history_prices, history_candles)
            if decision.is_warmup or decision.action not in ("call", "put"):
                continue

            # Evaluar gestión de riesgo
            session_pnl = balance - self.initial_balance
            risk_check = self.risk_manager.check_trade_permission(
                session_pnl=session_pnl,
                current_balance=balance,
                consecutive_losses=consecutive_losses,
                consecutive_wins=consecutive_wins,
                estimated_win_rate=0.56,
                payout_percent=self.payout_percent
            )

            if not risk_check.allowed:
                continue

            stake = risk_check.amount
            entry_price = close_price
            entry_timestamp = candle.get("timestamp", i * self.candle_timeframe)

            # Obtener vela de salida a los T segundos de expiración
            exit_idx = i + self.candles_to_expire
            exit_candle = sub_candles[exit_idx]
            exit_price = exit_candle.get("close", exit_candle.get("price", entry_price))
            exit_timestamp = exit_candle.get("timestamp", exit_idx * self.candle_timeframe)

            # Determinar resultado de la opción binaria
            outcome = "EMPATE"
            net_profit = 0.0
            payout_mult = self.payout_percent / 100.0

            if decision.action == "call":
                if exit_price > entry_price:
                    outcome = "GANADA"
                    net_profit = stake * payout_mult
                elif exit_price < entry_price:
                    outcome = "PERDIDA"
                    net_profit = -stake
            elif decision.action == "put":
                if exit_price < entry_price:
                    outcome = "GANADA"
                    net_profit = stake * payout_mult
                elif exit_price > entry_price:
                    outcome = "PERDIDA"
                    net_profit = -stake

            balance += net_profit
            equity_curve.append(balance)
            last_trade_candle_idx = i

            # Actualizar rachas
            if outcome == "GANADA":
                consecutive_wins += 1
                consecutive_losses = 0
                max_win_streak = max(max_win_streak, consecutive_wins)
            elif outcome == "PERDIDA":
                consecutive_losses += 1
                consecutive_wins = 0
                max_loss_streak = max(max_loss_streak, consecutive_losses)

            # Actualizar Drawdown
            if balance > peak_balance:
                peak_balance = balance
            dd_amount = peak_balance - balance
            dd_pct = (dd_amount / peak_balance * 100.0) if peak_balance > 0 else 0.0
            if dd_amount > max_dd_amount:
                max_dd_amount = dd_amount
            if dd_pct > max_dd_percent:
                max_dd_percent = dd_pct

            trade_record = BacktestTrade(
                trade_num=len(trades) + 1,
                entry_index=i,
                exit_index=exit_idx,
                entry_timestamp=entry_timestamp,
                exit_timestamp=exit_timestamp,
                direction=decision.action.upper(),
                stake=stake,
                entry_price=entry_price,
                exit_price=exit_price,
                outcome=outcome,
                net_profit=round(net_profit, 2),
                balance_after=round(balance, 2),
                score_call=decision.score_call,
                score_put=decision.score_put,
                reasons=decision.reason_summary,
                indicators=decision.indicators
            )
            trades.append(trade_record)

        # Resumen Final
        wins = sum(1 for t in trades if t.outcome == "GANADA")
        losses = sum(1 for t in trades if t.outcome == "PERDIDA")
        ties = sum(1 for t in trades if t.outcome == "EMPATE")
        total_trades = len(trades)
        decisive_trades = wins + losses

        win_rate = (wins / decisive_trades * 100.0) if decisive_trades > 0 else 0.0
        
        calls = [t for t in trades if t.direction == "CALL"]
        puts = [t for t in trades if t.direction == "PUT"]
        calls_wins = sum(1 for t in calls if t.outcome == "GANADA")
        calls_decisive = sum(1 for t in calls if t.outcome in ("GANADA", "PERDIDA"))
        puts_wins = sum(1 for t in puts if t.outcome == "GANADA")
        puts_decisive = sum(1 for t in puts if t.outcome in ("GANADA", "PERDIDA"))

        call_win_rate = (calls_wins / calls_decisive * 100.0) if calls_decisive > 0 else 0.0
        put_win_rate = (puts_wins / puts_decisive * 100.0) if puts_decisive > 0 else 0.0

        payout_frac = self.payout_percent / 100.0
        break_even_win_rate = (1.0 / (1.0 + payout_frac)) * 100.0
        edge = win_rate - break_even_win_rate

        gross_profit = sum(t.net_profit for t in trades if t.net_profit > 0)
        gross_loss = sum(abs(t.net_profit) for t in trades if t.net_profit < 0)
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        
        net_profit_total = balance - self.initial_balance
        return_pct = (net_profit_total / self.initial_balance * 100.0) if self.initial_balance > 0 else 0.0
        expectancy = (net_profit_total / total_trades) if total_trades > 0 else 0.0

        return BacktestResult(
            strategy_name=self.strategy.name,
            candle_timeframe_seconds=self.candle_timeframe,
            expiration_seconds=self.expiration_seconds,
            payout_percent=self.payout_percent,
            initial_balance=round(self.initial_balance, 2),
            final_balance=round(balance, 2),
            net_profit=round(net_profit_total, 2),
            return_percent=round(return_pct, 2),
            total_candles=len(sub_candles),
            total_trades=total_trades,
            wins=wins,
            losses=losses,
            ties=ties,
            win_rate=round(win_rate, 2),
            call_win_rate=round(call_win_rate, 2),
            put_win_rate=round(put_win_rate, 2),
            calls_count=len(calls),
            puts_count=len(puts),
            break_even_win_rate=round(break_even_win_rate, 2),
            edge=round(edge, 2),
            has_statistical_edge=(edge > 0 and decisive_trades >= 20),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            profit_factor=round(profit_factor, 2),
            expectancy_per_trade=round(expectancy, 2),
            max_drawdown_amount=round(max_dd_amount, 2),
            max_drawdown_percent=round(max_dd_percent, 2),
            max_win_streak=max_win_streak,
            max_loss_streak=max_loss_streak,
            trades=trades,
            equity_curve=equity_curve
        )

    def run_walk_forward(
        self,
        candles: List[Dict[str, float]],
        train_split: float = 0.70
    ) -> Tuple[BacktestResult, BacktestResult]:
        """
        Ejecuta validación cruzada In-Sample (Entrenamiento) vs Out-of-Sample (Validación Ciega).
        """
        split_idx = int(len(candles) * train_split)
        train_result = self.run(candles, start_index=0, end_index=split_idx)
        test_result = self.run(candles, start_index=split_idx, end_index=len(candles))
        return train_result, test_result

    def _empty_result(self, total_candles: int) -> BacktestResult:
        payout_frac = self.payout_percent / 100.0
        be = (1.0 / (1.0 + payout_frac)) * 100.0
        return BacktestResult(
            strategy_name=self.strategy.name,
            candle_timeframe_seconds=self.candle_timeframe,
            expiration_seconds=self.expiration_seconds,
            payout_percent=self.payout_percent,
            initial_balance=self.initial_balance,
            final_balance=self.initial_balance,
            net_profit=0.0,
            return_percent=0.0,
            total_candles=total_candles,
            total_trades=0,
            wins=0,
            losses=0,
            ties=0,
            win_rate=0.0,
            call_win_rate=0.0,
            put_win_rate=0.0,
            calls_count=0,
            puts_count=0,
            break_even_win_rate=round(be, 2),
            edge=round(-be, 2),
            has_statistical_edge=False,
            gross_profit=0.0,
            gross_loss=0.0,
            profit_factor=0.0,
            expectancy_per_trade=0.0,
            max_drawdown_amount=0.0,
            max_drawdown_percent=0.0,
            max_win_streak=0,
            max_loss_streak=0
        )
