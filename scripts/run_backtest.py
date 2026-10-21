"""
Script CLI de Ejecución y Comparación de Backtests Cuantitativos.
Permite evaluar la estrategia Legacy vs la Calibrada sobre series históricas,
validar si superan el 54.95% de break-even y correr validación Out-of-Sample.
"""

import os
import sys
import argparse

# Asegurar codificación UTF-8 en consola de Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Añadir motor-logico al path
current_dir = os.path.dirname(os.path.abspath(__file__))
motor_dir = os.path.join(os.path.dirname(current_dir), "motor-logico")
sys.path.append(motor_dir)

from backtester import Backtester
from strategy import LegacyConfluenceStrategy, AdaptiveEdgeStrategy
from risk_manager import RiskManager, RiskMode
from data_fetcher import load_candles_from_csv, generate_benchmark_eurusd_candles

# Importar verificador de fuente de datos (directamente desde scripts/)
scripts_dir = current_dir  # ya es scripts/
sys.path.append(scripts_dir)
from verify_data_source import verify_data_source, print_report, save_provenance_md


def print_comparison_table(res_legacy, res_calibrated):
    be = res_legacy.break_even_win_rate
    print("\n" + "═" * 78)
    print(" 🏆 TABLA COMPARATIVA DE ESTRATEGIAS VS PUNTO DE EQUILIBRIO MATEMÁTICO")
    print("═" * 78)
    print(f" {'Métrica':<28} | {'Legacy (Original)':<20} | {'Calibrada (Optimizada)':<22}")
    print("─" * 78)
    print(f" {'Velas / Timeframe':<28} | {res_legacy.total_candles} velas ({res_legacy.candle_timeframe_seconds}s)      | {res_calibrated.total_candles} velas ({res_calibrated.candle_timeframe_seconds}s)")
    print(f" {'Total Operaciones':<28} | {res_legacy.total_trades:<20} | {res_calibrated.total_trades:<22}")
    print(f" {'Operaciones Ganadas/Perd':<28} | {res_legacy.wins}W / {res_legacy.losses}L / {res_legacy.ties}D       | {res_calibrated.wins}W / {res_calibrated.losses}L / {res_calibrated.ties}D")
    
    leg_stat = "✅ EDGE" if res_legacy.has_statistical_edge else "❌ PIERDE"
    cal_stat = "✅ EDGE" if res_calibrated.has_statistical_edge else ("⚠️ NEUTRO" if res_calibrated.edge >= 0 else "❌ PIERDE")
    
    print(f" {'Tasa de Acierto (Win Rate)':<28} | {res_legacy.win_rate:.2f}% ({leg_stat})        | {res_calibrated.win_rate:.2f}% ({cal_stat})")
    print(f" {'Break-Even Requerido (82%)':<28} | {be:.2f}%                | {be:.2f}%")
    
    leg_edge_sign = "+" if res_legacy.edge >= 0 else ""
    cal_edge_sign = "+" if res_calibrated.edge >= 0 else ""
    print(f" {'Ventaja Estadística (Edge)':<28} | {leg_edge_sign}{res_legacy.edge:.2f}%               | {cal_edge_sign}{res_calibrated.edge:.2f}%")
    print(f" {'Profit Factor':<28} | {res_legacy.profit_factor:.2f}                 | {res_calibrated.profit_factor:.2f}")
    
    leg_pnl_sign = "+" if res_legacy.net_profit >= 0 else ""
    cal_pnl_sign = "+" if res_calibrated.net_profit >= 0 else ""
    print(f" {'Beneficio Neto ($)':<28} | {leg_pnl_sign}${res_legacy.net_profit:.2f} USD         | {cal_pnl_sign}${res_calibrated.net_profit:.2f} USD")
    print(f" {'Retorno sobre Capital':<28} | {leg_pnl_sign}{res_legacy.return_percent:.2f}%              | {cal_pnl_sign}{res_calibrated.return_percent:.2f}%")
    print(f" {'Máximo Drawdown':<28} | ${res_legacy.max_drawdown_amount:.2f} ({res_legacy.max_drawdown_percent:.1f}%)   | ${res_calibrated.max_drawdown_amount:.2f} ({res_calibrated.max_drawdown_percent:.1f}%)")
    print(f" {'Racha Máx. Ganadas / Perd':<28} | {res_legacy.max_win_streak}W / {res_legacy.max_loss_streak}L             | {res_calibrated.max_win_streak}W / {res_calibrated.max_loss_streak}L")
    print("═" * 78 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Ejecutar backtesting cuantitativo")
    parser.add_argument("--strategy", choices=["legacy", "calibrated", "compare"], default="compare", help="Estrategia a simular")
    parser.add_argument("--data", type=str, default=None,
                        help="Ruta al CSV de velas REALES de EUR/USD (Dukascopy, HistData, MT5, etc.).")
    parser.add_argument("--source", type=str, default="",
                        help="Descripción de la fuente de datos (ej: 'Dukascopy EURUSD M1 2024-01 a 2024-08')")
    parser.add_argument("--payout", type=float, default=82.0, help="Payout del broker (default: 82.0%%)")
    parser.add_argument("--timeframe", type=int, default=60, help="Timeframe de velas en segundos (default: 60s para M1)")
    parser.add_argument("--expiration", type=int, default=60, help="Tiempo de expiración en segundos (default: 60s)")
    parser.add_argument("--balance", type=float, default=10000.0, help="Balance inicial (default: $10,000 USD)")
    parser.add_argument("--stake", type=float, default=10.0, help="Monto de operación para modo fijo (default: $10.0 USD)")
    parser.add_argument("--stop-loss", type=float, default=-500.0, help="Stop loss diario para la simulación (default: -$500 USD)")
    parser.add_argument("--take-profit", type=float, default=1000.0, help="Take profit diario para la simulación (default: +$1000 USD)")
    parser.add_argument("--risk-mode", choices=["fixed_amount", "fixed_percentage", "fractional_kelly"], default="fixed_amount", help="Modo de gestión de riesgo")
    parser.add_argument("--walk-forward", action="store_true", help="Ejecuta split In-Sample (70%%) vs Out-of-Sample (30%%)")
    parser.add_argument("--allow-synthetic-for-testing-only", action="store_true",
                        help="Permite correr el motor sobre datos sintéticos ÚNICAMENTE para tests del backtester. NO produce métricas válidas.")

    args = parser.parse_args()
    allow_syn = getattr(args, "allow_synthetic_for_testing_only", False)
    root_dir = os.path.dirname(current_dir)

    if args.data is None:
        print("\n" + "="*70, file=sys.stderr)
        print("ERROR: No se especificó --data con un archivo de datos reales.", file=sys.stderr)
        print("\nDebería correr este comando con datos reales de EUR/USD, por ejemplo:", file=sys.stderr)
        print("  python scripts/run_backtest.py --data data/historical/eurusd_m1_real.csv --source \"Dukascopy M1 2024\"", file=sys.stderr)
        print("\nConsulta las instrucciones en data/historical/INSTRUCCIONES_DATOS_REALES.md", file=sys.stderr)
        print("para saber cómo descargar los datos desde Dukascopy o HistData.", file=sys.stderr)
        print("="*70 + "\n", file=sys.stderr)
        sys.exit(1)

    data_file = os.path.abspath(os.path.join(root_dir, args.data))

    if not os.path.exists(data_file):
        print(f"\n[ERROR] Archivo no encontrado: {data_file}", file=sys.stderr)
        print("Por favor descarga datos reales de EUR/USD y colócalos en data/historical/", file=sys.stderr)
        print("Consulta data/historical/INSTRUCCIONES_DATOS_REALES.md para instrucciones exactas.", file=sys.stderr)
        sys.exit(1)

    # --- VERIFICACIÓN OBLIGATORIA DE AUTENTICIDAD ---
    try:
        report = verify_data_source(
            file_path=data_file,
            source_description=args.source or "Fuente no especificada (usa --source para documentar)",
            allow_synthetic=allow_syn
        )
        print_report(report)
        hist_dir = os.path.dirname(data_file)
        save_provenance_md(report, hist_dir)

        if allow_syn:
            print("\n" + "!"*70)
            print("AVISO: --allow-synthetic-for-testing-only activo.")
            print("Los resultados NO son válidos como evidencia de estrategia real.")
            print("!"*70 + "\n")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"[BACKTEST] Cargando datos desde: {data_file}")
    candles = load_candles_from_csv(data_file)
    print(f"[BACKTEST] Total de velas cargadas: {len(candles)}")

    # Configuración de riesgo
    risk_mode_map = {
        "fixed_amount": RiskMode.FIXED_AMOUNT,
        "fixed_percentage": RiskMode.FIXED_PERCENTAGE,
        "fractional_kelly": RiskMode.FRACTIONAL_KELLY
    }
    risk_mgr = RiskManager(
        mode=risk_mode_map[args.risk_mode],
        fixed_amount=args.stake,
        fixed_percentage=1.5,
        kelly_fraction=0.25,
        daily_stop_loss=args.stop_loss,
        daily_take_profit=args.take_profit,
        max_consecutive_losses=6,
        loss_streak_reduction=True
    )

    strat_legacy = LegacyConfluenceStrategy(min_score=65.0)
    strat_calibrated = AdaptiveEdgeStrategy(min_score=85.0, use_macd_confirmation=True, use_session_filter=False)

    bt_legacy = Backtester(
        strategy=strat_legacy,
        risk_manager=risk_mgr,
        candle_timeframe_seconds=args.timeframe,
        expiration_seconds=args.expiration,
        payout_percent=args.payout,
        initial_balance=args.balance
    )

    bt_calibrated = Backtester(
        strategy=strat_calibrated,
        risk_manager=risk_mgr,
        candle_timeframe_seconds=args.timeframe,
        expiration_seconds=args.expiration,
        payout_percent=args.payout,
        initial_balance=args.balance
    )

    if args.walk_forward:
        print(f"\n[WALK-FORWARD] Ejecutando validación cruzada In-Sample (70%) vs Out-of-Sample (30%)...\n")
        train_leg, test_leg = bt_legacy.run_walk_forward(candles, train_split=0.70)
        train_cal, test_cal = bt_calibrated.run_walk_forward(candles, train_split=0.70)

        print("--- RESULTADOS IN-SAMPLE (70% DATOS - ENTRENAMIENTO) ---")
        print_comparison_table(train_leg, train_cal)

        print("--- RESULTADOS OUT-OF-SAMPLE (30% DATOS - VALIDACIÓN CIEGA) ---")
        print_comparison_table(test_leg, test_cal)
        return

    if args.strategy == "legacy":
        res = bt_legacy.run(candles)
        res.print_summary()
    elif args.strategy == "calibrated":
        res = bt_calibrated.run(candles)
        res.print_summary()
    elif args.strategy == "compare":
        res_leg = bt_legacy.run(candles)
        res_cal = bt_calibrated.run(candles)
        res_leg.print_summary()
        res_cal.print_summary()
        print_comparison_table(res_leg, res_cal)


if __name__ == "__main__":
    main()
