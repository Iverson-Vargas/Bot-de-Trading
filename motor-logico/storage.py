"""
Módulo de Almacenamiento Estructurado de Operaciones e Indicadores.
Implementa persistencia dual en SQLite (data/trades.db) y exportación automática a CSV (data/trades.csv).
"""

import os
import sqlite3
import csv
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple


class TradeStorage:
    def __init__(self, db_path: Optional[str] = None, csv_path: Optional[str] = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)

        self.db_path = db_path or os.path.join(data_dir, "trades.db")
        self.csv_path = csv_path or os.path.join(data_dir, "trades.csv")
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Crea las tablas e índices si no existen."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Tabla Principal de Operaciones
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                order_id TEXT,
                session_id TEXT,
                timestamp INTEGER,
                date_time TEXT,
                account_type TEXT,
                account_id TEXT,
                direction TEXT,
                amount REAL,
                entry_price REAL,
                exit_price REAL,
                payout_percent REAL,
                expiration_seconds INTEGER,
                outcome TEXT,
                net_profit REAL,
                balance_post REAL,
                strategy_name TEXT,
                score_call REAL,
                score_put REAL,
                signal_reasons TEXT
            )
            """)

            # 2. Tabla de Snapshot de Indicadores en el momento del disparo
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_indicators (
                trade_id TEXT PRIMARY KEY,
                rsi REAL,
                bollinger_middle REAL,
                bollinger_upper REAL,
                bollinger_lower REAL,
                bollinger_std REAL,
                bollinger_bandwidth REAL,
                stoch_k REAL,
                stoch_d REAL,
                stoch_cruce_alcista INTEGER,
                stoch_cruce_bajista INTEGER,
                pct_mecha_inf REAL,
                pct_mecha_sup REAL,
                rechazo_alcista INTEGER,
                rechazo_bajista INTEGER,
                soporte REAL,
                resistencia REAL,
                atr REAL,
                macd_line REAL,
                macd_signal REAL,
                macd_histogram REAL,
                macd_hist_rising INTEGER,
                FOREIGN KEY (trade_id) REFERENCES trades(id)
            )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_outcome ON trades(outcome)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)")

            # Migrate: add MACD columns if missing
            try:
                cursor.execute("SELECT macd_line FROM trade_indicators LIMIT 1")
            except sqlite3.OperationalError:
                for col in ["macd_line REAL", "macd_signal REAL", "macd_histogram REAL", "macd_hist_rising INTEGER"]:
                    try:
                        cursor.execute(f"ALTER TABLE trade_indicators ADD COLUMN {col}")
                    except sqlite3.OperationalError:
                        pass

            conn.commit()

    def save_trade(
        self,
        trade_id: str,
        direction: str,
        amount: float,
        entry_price: float,
        exit_price: float,
        outcome: str,
        net_profit: float,
        balance_post: Optional[float] = None,
        order_id: Optional[str] = None,
        session_id: Optional[str] = None,
        timestamp: Optional[int] = None,
        date_time: Optional[str] = None,
        account_type: str = "DEMO",
        account_id: str = "",
        payout_percent: float = 82.0,
        expiration_seconds: int = 60,
        strategy_name: str = "Confluence",
        score_call: float = 0.0,
        score_put: float = 0.0,
        signal_reasons: str = "",
        indicators: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Guarda o actualiza una operación completa con su snapshot de indicadores."""
        ts = timestamp or int(time.time())
        dt = date_time or datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        order_id_val = order_id or trade_id

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO trades (
                id, order_id, session_id, timestamp, date_time, account_type, account_id,
                direction, amount, entry_price, exit_price, payout_percent, expiration_seconds,
                outcome, net_profit, balance_post, strategy_name, score_call, score_put, signal_reasons
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_id, order_id_val, session_id or "", ts, dt, account_type, str(account_id),
                direction.upper(), amount, entry_price, exit_price, payout_percent, expiration_seconds,
                outcome.upper(), net_profit, balance_post or 0.0, strategy_name, score_call, score_put, signal_reasons
            ))

            if indicators:
                bollinger = indicators.get("bollinger") or {}
                estocastico = indicators.get("estocastico") or {}
                patron_vela = indicators.get("patron_vela") or {}
                niveles_sr = indicators.get("niveles_sr") or {}

                macd = indicators.get("macd") or {}

                cursor.execute("""
                INSERT OR REPLACE INTO trade_indicators (
                    trade_id, rsi, bollinger_middle, bollinger_upper, bollinger_lower,
                    bollinger_std, bollinger_bandwidth, stoch_k, stoch_d,
                    stoch_cruce_alcista, stoch_cruce_bajista, pct_mecha_inf,
                    pct_mecha_sup, rechazo_alcista, rechazo_bajista, soporte, resistencia, atr,
                    macd_line, macd_signal, macd_histogram, macd_hist_rising
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade_id,
                    indicators.get("rsi"),
                    bollinger.get("middle"),
                    bollinger.get("upper"),
                    bollinger.get("lower"),
                    bollinger.get("std"),
                    bollinger.get("bandwidth"),
                    estocastico.get("k"),
                    estocastico.get("d"),
                    1 if estocastico.get("cruce_alcista") else 0,
                    1 if estocastico.get("cruce_bajista") else 0,
                    patron_vela.get("pct_mecha_inf"),
                    patron_vela.get("pct_mecha_sup"),
                    1 if patron_vela.get("rechazo_alcista") else 0,
                    1 if patron_vela.get("rechazo_bajista") else 0,
                    niveles_sr.get("soporte"),
                    niveles_sr.get("resistencia"),
                    indicators.get("atr"),
                    macd.get("macd_line"),
                    macd.get("signal_line"),
                    macd.get("histogram"),
                    1 if macd.get("hist_rising") else 0
                ))

            conn.commit()

        # Actualizar exportación CSV de respaldo
        self.export_to_csv()
        return True

    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Obtiene las operaciones más recientes junto con sus indicadores."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT t.*, 
                   i.rsi, i.bollinger_upper, i.bollinger_lower, i.bollinger_bandwidth,
                   i.stoch_k, i.stoch_d, i.pct_mecha_inf, i.pct_mecha_sup, i.atr
            FROM trades t
            LEFT JOIN trade_indicators i ON t.id = i.trade_id
            ORDER BY t.timestamp DESC
            LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_all_trades_with_indicators(self) -> List[Dict[str, Any]]:
        """Obtiene el historial completo ordenado cronológicamente para análisis estadístico."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT t.*, i.rsi, i.bollinger_middle, i.bollinger_upper, i.bollinger_lower,
                   i.bollinger_std, i.bollinger_bandwidth, i.stoch_k, i.stoch_d,
                   i.stoch_cruce_alcista, i.stoch_cruce_bajista, i.pct_mecha_inf,
                   i.pct_mecha_sup, i.rechazo_alcista, i.rechazo_bajista, i.soporte, i.resistencia, i.atr
            FROM trades t
            LEFT JOIN trade_indicators i ON t.id = i.trade_id
            ORDER BY t.timestamp ASC
            """)
            return [dict(r) for r in cursor.fetchall()]

    def get_metrics_summary(self, payout_percent: float = 82.0) -> Dict[str, Any]:
        """Calcula métricas agregadas globales y de rendimiento."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'GANADA' THEN 1 ELSE 0 END) as ganadas,
                SUM(CASE WHEN outcome = 'PERDIDA' THEN 1 ELSE 0 END) as perdidas,
                SUM(CASE WHEN outcome = 'EMPATE' THEN 1 ELSE 0 END) as empates,
                SUM(net_profit) as pnl_neto,
                SUM(CASE WHEN direction = 'CALL' THEN 1 ELSE 0 END) as calls_total,
                SUM(CASE WHEN direction = 'CALL' AND outcome = 'GANADA' THEN 1 ELSE 0 END) as calls_ganadas,
                SUM(CASE WHEN direction = 'PUT' THEN 1 ELSE 0 END) as puts_total,
                SUM(CASE WHEN direction = 'PUT' AND outcome = 'GANADA' THEN 1 ELSE 0 END) as puts_ganadas
            FROM trades
            """)
            row = cursor.fetchone()
            
            total = row["total"] or 0
            ganadas = row["ganadas"] or 0
            perdidas = row["perdidas"] or 0
            empates = row["empates"] or 0
            pnl_neto = row["pnl_neto"] or 0.0
            calls_total = row["calls_total"] or 0
            calls_ganadas = row["calls_ganadas"] or 0
            puts_total = row["puts_total"] or 0
            puts_ganadas = row["puts_ganadas"] or 0

            decisivas = ganadas + perdidas
            win_rate = (ganadas / decisivas * 100.0) if decisivas > 0 else 0.0
            call_win_rate = (calls_ganadas / calls_total * 100.0) if calls_total > 0 else 0.0
            put_win_rate = (puts_ganadas / puts_total * 100.0) if puts_total > 0 else 0.0

            # Punto de Equilibrio Matemático: 1 / (1 + Payout)
            payout_frac = payout_percent / 100.0
            break_even_win_rate = (1.0 / (1.0 + payout_frac)) * 100.0
            edge = win_rate - break_even_win_rate

            # Profit Factor
            cursor.execute("SELECT SUM(net_profit) FROM trades WHERE outcome = 'GANADA'")
            ganancias_brutas = cursor.fetchone()[0] or 0.0
            cursor.execute("SELECT SUM(ABS(net_profit)) FROM trades WHERE outcome = 'PERDIDA'")
            perdidas_brutas = cursor.fetchone()[0] or 0.0
            profit_factor = (ganancias_brutas / perdidas_brutas) if perdidas_brutas > 0 else (999.0 if ganancias_brutas > 0 else 0.0)

            return {
                "total_trades": total,
                "ganadas": ganadas,
                "perdidas": perdidas,
                "empates": empates,
                "win_rate": round(win_rate, 2),
                "call_win_rate": round(call_win_rate, 2),
                "put_win_rate": round(put_win_rate, 2),
                "break_even_win_rate": round(break_even_win_rate, 2),
                "edge": round(edge, 2),
                "has_edge": edge > 0,
                "pnl_neto": round(pnl_neto, 2),
                "profit_factor": round(profit_factor, 2),
                "ganancias_brutas": round(ganancias_brutas, 2),
                "perdidas_brutas": round(perdidas_brutas, 2),
                "payout_percent": payout_percent
            }

    def export_to_csv(self):
        """Exporta la base de datos a un archivo CSV estructurado."""
        trades = self.get_all_trades_with_indicators()
        if not trades:
            return

        fieldnames = list(trades[0].keys())
        try:
            with open(self.csv_path, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(trades)
        except Exception as e:
            print(f"[STORAGE] Error exportando CSV: {e}")
