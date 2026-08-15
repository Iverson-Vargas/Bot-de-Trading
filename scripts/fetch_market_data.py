"""
Script de Gestión y Descarga de Datos de Mercado
Permite generar datasets de referencia o importar archivos externos de velas.
"""

import os
import sys
import argparse

# Añadir motor-logico al path
current_dir = os.path.dirname(os.path.abspath(__file__))
motor_dir = os.path.join(os.path.dirname(current_dir), "motor-logico")
sys.path.append(motor_dir)

from data_fetcher import generate_benchmark_eurusd_candles, save_candles_to_csv, load_candles_from_csv


def main():
    parser = argparse.ArgumentParser(description="Gestor de datos históricos para backtesting")
    parser.add_argument("--generate-sample", action="store_true", help="Genera un dataset de referencia de EUR/USD")
    parser.add_argument("--candles", type=int, default=5000, help="Número de velas a generar (default: 5000)")
    parser.add_argument("--timeframe", type=int, default=10, help="Timeframe de velas en segundos (default: 10s)")
    parser.add_argument("--output", type=str, default="data/historical/eurusd_10s_sample.csv", help="Ruta de guardado")
    parser.add_argument("--import-csv", type=str, help="Importa y normaliza un archivo CSV externo")

    args = parser.parse_args()

    root_dir = os.path.dirname(current_dir)
    out_path = os.path.join(root_dir, args.output)

    if args.import_csv:
        in_path = os.path.abspath(args.import_csv)
        print(f"[DATA] Cargando CSV desde: {in_path}")
        candles = load_candles_from_csv(in_path)
        print(f"[DATA] {len(candles)} velas cargadas exitosamente.")
        save_candles_to_csv(candles, out_path)
        print(f"[DATA] Guardado normalizado en: {out_path}")
        return

    # Por defecto o si se pide --generate-sample
    print(f"[DATA] Generando dataset de referencia EUR/USD ({args.candles} velas de {args.timeframe}s)...")
    candles = generate_benchmark_eurusd_candles(
        count=args.candles,
        timeframe_seconds=args.timeframe,
        base_price=1.08500,
        seed=12345
    )
    save_candles_to_csv(candles, out_path)
    print(f"[DATA] Dataset generado exitosamente en: {out_path}")
    print(f"       Total de velas: {len(candles)} | Cobertura temporal: {len(candles)*args.timeframe/3600:.1f} horas de mercado.")


if __name__ == "__main__":
    main()
