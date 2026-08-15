"""
Módulo de Gestión de Riesgo Dinámica y Protección de Capital
Implementa dimensionamiento por % de balance, Criterio de Kelly Fraccional,
reducción automática tras rachas de pérdidas y límites de protección de cuenta.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional, Tuple


class RiskMode(str, Enum):
    FIXED_AMOUNT = "fixed_amount"          # Monto fijo estático ($10)
    FIXED_PERCENTAGE = "fixed_percentage"  # % fijo del balance actual (ej. 1.5%)
    FRACTIONAL_KELLY = "fractional_kelly"  # Kelly fraccional adaptativo


@dataclass
class RiskCheckResult:
    allowed: bool
    amount: float
    reason: str
    risk_mode: str
    is_halted: bool = False


class RiskManager:
    def __init__(
        self,
        mode: RiskMode = RiskMode.FRACTIONAL_KELLY,
        fixed_amount: float = 10.0,
        fixed_percentage: float = 1.5,      # 1.5% del balance
        kelly_fraction: float = 0.25,       # Quarter Kelly (0.25) para máxima seguridad
        max_stake_percent: float = 2.5,     # Tope máximo absoluto: 2.5% del balance
        min_stake_amount: float = 1.0,      # Mínimo permitido por el broker: $1.00 USD
        max_stake_amount: float = 50.0,     # Tope absoluto en dólares
        daily_stop_loss: float = -50.0,     # Stop loss diario ($ o valor neto)
        daily_take_profit: float = 100.0,   # Meta diaria ($ o valor neto)
        max_consecutive_losses: int = 4,    # Pausa de seguridad tras N derrotas seguidas
        loss_streak_reduction: bool = True  # Reducir stake al 50% tras 2+ pérdidas
    ):
        self.mode = mode
        self.fixed_amount = fixed_amount
        self.fixed_percentage = fixed_percentage
        self.kelly_fraction = kelly_fraction
        self.max_stake_percent = max_stake_percent
        self.min_stake_amount = min_stake_amount
        self.max_stake_amount = max_stake_amount
        self.daily_stop_loss = daily_stop_loss
        self.daily_take_profit = daily_take_profit
        self.max_consecutive_losses = max_consecutive_losses
        self.loss_streak_reduction = loss_streak_reduction

    def calculate_stake(
        self,
        current_balance: float,
        consecutive_losses: int = 0,
        consecutive_wins: int = 0,
        estimated_win_rate: float = 0.56,
        payout_percent: float = 82.0
    ) -> Tuple[float, str]:
        """
        Calcula el tamaño de la posición óptimo y seguro según el balance y la racha actual.
        """
        if current_balance <= 0:
            return self.min_stake_amount, "Balance no disponible; usando monto mínimo"

        b = payout_percent / 100.0  # Ganancia neta por dólar arriesgado (ej. 0.82)
        base_stake = self.min_stake_amount

        if self.mode == RiskMode.FIXED_AMOUNT:
            base_stake = self.fixed_amount
            mode_desc = f"Monto Fijo (${self.fixed_amount:.2f})"

        elif self.mode == RiskMode.FIXED_PERCENTAGE:
            pct = self.fixed_percentage / 100.0
            base_stake = current_balance * pct
            mode_desc = f"{self.fixed_percentage:.1f}% del Balance"

        elif self.mode == RiskMode.FRACTIONAL_KELLY:
            # Fórmula de Kelly para Opciones Binarias: f* = (p * (b + 1) - 1) / b
            p = max(0.01, min(0.99, estimated_win_rate))
            kelly_f = (p * (b + 1.0) - 1.0) / b

            if kelly_f <= 0:
                # Sin edge teórico estimado: usar tamaño mínimo de seguridad
                base_stake = self.min_stake_amount
                mode_desc = f"Kelly (Sin Edge: {kelly_f:.3f}) -> Mínimo"
            else:
                # Aplicar fracción de Kelly (Quarter Kelly)
                stake_frac = kelly_f * self.kelly_fraction
                # Limitar al tope máximo por operación (ej. max 2.5% del balance)
                max_frac = self.max_stake_percent / 100.0
                effective_frac = min(stake_frac, max_frac)
                base_stake = current_balance * effective_frac
                mode_desc = f"Kelly Fraccional ({self.kelly_fraction*100:.0f}% de Kelly: {effective_frac*100:.2f}%)"

        # Aplicar amortiguador de rachas de pérdidas
        streak_note = ""
        if self.loss_streak_reduction and consecutive_losses >= 2:
            base_stake = base_stake * 0.50
            streak_note = f" [Reducción -50% por racha de {consecutive_losses}L]"

        # Aplicar cotas duras (Mínimo $1.00 USD, Máximo configurado)
        max_allowed = min(self.max_stake_amount, current_balance * (self.max_stake_percent / 100.0))
        final_stake = max(self.min_stake_amount, min(base_stake, max_allowed))
        final_stake = round(final_stake, 2)

        reason = f"{mode_desc}{streak_note} -> Stake: ${final_stake:.2f} USD"
        return final_stake, reason

    def check_trade_permission(
        self,
        session_pnl: float,
        current_balance: float,
        consecutive_losses: int,
        consecutive_wins: int,
        estimated_win_rate: float = 0.56,
        payout_percent: float = 82.0
    ) -> RiskCheckResult:
        """
        Verifica todos los filtros de seguridad y límites antes de autorizar una operación.
        """
        # 1. Verificación de Stop Loss Diario
        if session_pnl <= self.daily_stop_loss:
            return RiskCheckResult(
                allowed=False,
                amount=0.0,
                reason=f"🛑 STOP LOSS DIARIO ALCANZADO (${session_pnl:.2f} <= ${self.daily_stop_loss:.2f}). Operaciones bloqueadas.",
                risk_mode=str(self.mode),
                is_halted=True
            )

        # 2. Verificación de Take Profit Diario
        if session_pnl >= self.daily_take_profit:
            return RiskCheckResult(
                allowed=False,
                amount=0.0,
                reason=f"🎯 TAKE PROFIT DIARIO ALCANZADO (+${session_pnl:.2f} >= +${self.daily_take_profit:.2f}). Meta cumplida.",
                risk_mode=str(self.mode),
                is_halted=True
            )

        # 3. Verificación de Máximas Pérdidas Consecutivas
        if consecutive_losses >= self.max_consecutive_losses:
            return RiskCheckResult(
                allowed=False,
                amount=0.0,
                reason=f"⚠️ LÍMITE DE RACHA NEGATIVA ({consecutive_losses} pérdidas seguidas >= {self.max_consecutive_losses}). Pausa de seguridad activa.",
                risk_mode=str(self.mode),
                is_halted=True
            )

        # 4. Cálculo de Posición Dinámica
        stake, reason = self.calculate_stake(
            current_balance=current_balance,
            consecutive_losses=consecutive_losses,
            consecutive_wins=consecutive_wins,
            estimated_win_rate=estimated_win_rate,
            payout_percent=payout_percent
        )

        return RiskCheckResult(
            allowed=True,
            amount=stake,
            reason=reason,
            risk_mode=str(self.mode),
            is_halted=False
        )
