"""
Módulo de Calibración y Análisis Estadístico de Indicadores.
Evalúa la correlación empírica y la tasa de acierto condicional de cada indicador técnico
sobre datos históricos de mercado para eliminar pesos arbitrarios y calibrar umbrales óptimos.
"""

import math
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

from indicators import (
    calcular_rsi,
    calcular_bandas_bollinger,
    calcular_estocastico,
    analizar_patron_vela,
    calcular_soportes_resistencias,
    calcular_atr
)


@dataclass
class IndicatorConditionStat:
    name: str
    direction_tested: str  # CALL o PUT
    total_occurrences: int
    wins: int
    losses: int
    ties: int
    win_rate: float
    break_even_win_rate: float
    edge: float
    is_statistically_significant: bool
    standard_error: float


class StrategyCalibrator:
    def __init__(self, payout_percent: float = 82.0, expiration_candles: int = 6):
        self.payout_percent = payout_percent
        self.expiration_candles = expiration_candles  # 6 velas de 10s = 60s
        self.break_even_win_rate = (1.0 / (1.0 + (payout_percent / 100.0))) * 100.0

    def analyze_indicators_predictive_power(
        self,
        candles: List[Dict[str, float]],
        min_warmup: int = 30
    ) -> Dict[str, Any]:
        """
        Analiza individualmente el poder predictivo a 60 segundos de cada condición técnica.
        """
        if len(candles) < min_warmup + self.expiration_candles:
            return {"error": "Insuficientes velas para calibración"}

        # Contadores de eventos por condición
        conditions = {
            "RSI_Extreme_Oversold_25": {"call_wins": 0, "call_losses": 0, "total": 0},
            "RSI_Oversold_32": {"call_wins": 0, "call_losses": 0, "total": 0},
            "RSI_Extreme_Overbought_75": {"put_wins": 0, "put_losses": 0, "total": 0},
            "RSI_Overbought_68": {"put_wins": 0, "put_losses": 0, "total": 0},
            "Bollinger_Touch_Lower": {"call_wins": 0, "call_losses": 0, "total": 0},
            "Bollinger_Touch_Upper": {"put_wins": 0, "put_losses": 0, "total": 0},
            "Bollinger_PercentB_Low_10": {"call_wins": 0, "call_losses": 0, "total": 0},
            "Bollinger_PercentB_High_90": {"put_wins": 0, "put_losses": 0, "total": 0},
            "Stoch_Cross_Oversold_25": {"call_wins": 0, "call_losses": 0, "total": 0},
            "Stoch_Cross_Overbought_75": {"put_wins": 0, "put_losses": 0, "total": 0},
            "Wick_Rejection_Lower_25": {"call_wins": 0, "call_losses": 0, "total": 0},
            "Wick_Rejection_Upper_25": {"put_wins": 0, "put_losses": 0, "total": 0},
            "Support_Zone": {"call_wins": 0, "call_losses": 0, "total": 0},
            "Resistance_Zone": {"put_wins": 0, "put_losses": 0, "total": 0},
            "Confluence_RSI30_BBLower_Wick": {"call_wins": 0, "call_losses": 0, "total": 0},
            "Confluence_RSI70_BBUpper_Wick": {"put_wins": 0, "put_losses": 0, "total": 0}
        }

        history_prices = []
        history_candles = []

        for i in range(len(candles) - self.expiration_candles):
            candle = candles[i]
            close_price = candle.get("close", 0.0)
            history_prices.append(close_price)
            history_candles.append(candle)

            if len(history_prices) < min_warmup:
                continue

            # Precio futuro de salida a los 60s
            future_close = candles[i + self.expiration_candles].get("close", close_price)
            sub_prices = history_prices[-200:]
            sub_candles = history_candles[-200:]

            # Calcular indicadores
            rsi = calcular_rsi(sub_prices, 14)
            bollinger = calcular_bandas_bollinger(sub_prices, 20, 2.0)
            estocastico = calcular_estocastico(sub_candles, 14, 3, 3)
            patron_vela = analizar_patron_vela(candle)
            niveles_sr = calcular_soportes_resistencias(sub_candles, lookback=30)

            # 1. RSI
            if rsi is not None:
                if rsi <= 25.0:
                    self._record_stat(conditions["RSI_Extreme_Oversold_25"], "call", close_price, future_close)
                if rsi <= 32.0:
                    self._record_stat(conditions["RSI_Oversold_32"], "call", close_price, future_close)
                if rsi >= 75.0:
                    self._record_stat(conditions["RSI_Extreme_Overbought_75"], "put", close_price, future_close)
                if rsi >= 68.0:
                    self._record_stat(conditions["RSI_Overbought_68"], "put", close_price, future_close)

            # 2. Bollinger
            if bollinger:
                if close_price <= bollinger["lower"]:
                    self._record_stat(conditions["Bollinger_Touch_Lower"], "call", close_price, future_close)
                if close_price >= bollinger["upper"]:
                    self._record_stat(conditions["Bollinger_Touch_Upper"], "put", close_price, future_close)
                if bollinger["percent_b"] <= 0.10:
                    self._record_stat(conditions["Bollinger_PercentB_Low_10"], "call", close_price, future_close)
                if bollinger["percent_b"] >= 0.90:
                    self._record_stat(conditions["Bollinger_PercentB_High_90"], "put", close_price, future_close)

            # 3. Estocástico
            if estocastico:
                if estocastico["cruce_alcista"] and estocastico["k"] <= 25.0:
                    self._record_stat(conditions["Stoch_Cross_Oversold_25"], "call", close_price, future_close)
                if estocastico["cruce_bajista"] and estocastico["k"] >= 75.0:
                    self._record_stat(conditions["Stoch_Cross_Overbought_75"], "put", close_price, future_close)

            # 4. Mechas
            if patron_vela["rechazo_alcista"]:
                self._record_stat(conditions["Wick_Rejection_Lower_25"], "call", close_price, future_close)
            if patron_vela["rechazo_bajista"]:
                self._record_stat(conditions["Wick_Rejection_Upper_25"], "put", close_price, future_close)

            # 5. S/R
            if niveles_sr["soporte"] > 0 and abs(close_price - niveles_sr["soporte"]) <= 0.00010:
                self._record_stat(conditions["Support_Zone"], "call", close_price, future_close)
            if niveles_sr["resistencia"] < 999999 and abs(close_price - niveles_sr["resistencia"]) <= 0.00010:
                self._record_stat(conditions["Resistance_Zone"], "put", close_price, future_close)

            # 6. Confluencia Fuerte
            if rsi and rsi <= 30.0 and bollinger and close_price <= bollinger["lower"] and patron_vela["rechazo_alcista"]:
                self._record_stat(conditions["Confluence_RSI30_BBLower_Wick"], "call", close_price, future_close)
            if rsi and rsi >= 70.0 and bollinger and close_price >= bollinger["upper"] and patron_vela["rechazo_bajista"]:
                self._record_stat(conditions["Confluence_RSI70_BBUpper_Wick"], "put", close_price, future_close)

        # Compilar estadísticas finales
        stats_list = []
        for name, data in conditions.items():
            total = data["total"]
            is_call = "call_wins" in data
            wins = data.get("call_wins", 0) if is_call else data.get("put_wins", 0)
            losses = data.get("call_losses", 0) if is_call else data.get("put_losses", 0)
            ties = total - (wins + losses)
            decisive = wins + losses

            win_rate = (wins / decisive * 100.0) if decisive > 0 else 0.0
            edge = win_rate - self.break_even_win_rate
            
            # Error estándar: SE = sqrt(p*(1-p)/n)
            p = win_rate / 100.0
            se = math.sqrt(p * (1.0 - p) / decisive) * 100.0 if decisive > 5 else 99.0
            is_significant = decisive >= 30 and (edge > 0 or edge < -5)

            stat = IndicatorConditionStat(
                name=name,
                direction_tested="CALL" if is_call else "PUT",
                total_occurrences=total,
                wins=wins,
                losses=losses,
                ties=ties,
                win_rate=round(win_rate, 2),
                break_even_win_rate=round(self.break_even_win_rate, 2),
                edge=round(edge, 2),
                is_statistically_significant=is_significant,
                standard_error=round(se, 2)
            )
            stats_list.append(stat)

        return {
            "total_candles_analyzed": len(candles),
            "break_even_win_rate": round(self.break_even_win_rate, 2),
            "payout_percent": self.payout_percent,
            "conditions": stats_list
        }

    def _record_stat(self, stat_dict: Dict[str, int], direction: str, entry_price: float, future_price: float):
        stat_dict["total"] += 1
        if direction == "call":
            if future_price > entry_price:
                stat_dict["call_wins"] += 1
            elif future_price < entry_price:
                stat_dict["call_losses"] += 1
        elif direction == "put":
            if future_price < entry_price:
                stat_dict["put_wins"] += 1
            elif future_price > entry_price:
                stat_dict["put_losses"] += 1
