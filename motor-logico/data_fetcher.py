"""
Módulo de Gestión y Carga de Datos Históricos de Mercado.
Permite importar archivos CSV de velas reales (HistData, MetaTrader, Yahoo Finance, Investing),
generar series de referencia para pruebas de estrés, y consultar velas a IQ Option.
"""

import os
import csv
import json
import time
import math
import random
from datetime import datetime
from typing import List, Dict, Any, Optional


def parse_timestamp(val: Any) -> int:
    """Convierte diferentes representaciones de tiempo a timestamp UNIX (segundos)."""
    if isinstance(val, (int, float)):
        # Si está en milisegundos
        if val > 1e11:
            return int(val / 1000)
        return int(val)
    
    val_str = str(val).strip()
    # Intentar entero directo
    try:
        ts = int(val_str)
        if ts > 1e11:
            return int(ts / 1000)
        return ts
    except ValueError:
        pass

    # Intentar formatos de fecha estándar
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y.%m.%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y.%m.%d %H:%M",
        "%Y%m%d %H%M%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ"
    ]
    for fmt in formats:
        try:
            return int(datetime.strptime(val_str, fmt).timestamp())
        except ValueError:
            continue

    return int(time.time())


def load_candles_from_csv(file_path: str) -> List[Dict[str, float]]:
    """
    Carga velas OHLC desde un archivo CSV.
    Detecta automáticamente encabezados y orden de columnas.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

    candles: List[Dict[str, float]] = []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        # Detectar delimitador
        sample = f.read(2048)
        f.seek(0)
        delimiter = ","
        if ";" in sample and sample.count(";") > sample.count(","):
            delimiter = ";"
        elif "\t" in sample and sample.count("\t") > sample.count(","):
            delimiter = "\t"

        reader = csv.reader(f, delimiter=delimiter)
        header = next(reader, None)
        if not header:
            return []

        # Normalizar encabezado a minúsculas
        header_map = {}
        for idx, col in enumerate(header):
            clean_col = col.strip().lower().replace("<", "").replace(">", "")
            if clean_col in ("time", "timestamp", "datetime", "date", "gmt time"):
                header_map["time"] = idx
            elif clean_col in ("open", "open_price", "o"):
                header_map["open"] = idx
            elif clean_col in ("high", "max", "h"):
                header_map["high"] = idx
            elif clean_col in ("low", "min", "l"):
                header_map["low"] = idx
            elif clean_col in ("close", "close_price", "c", "price"):
                header_map["close"] = idx
            elif clean_col in ("vol", "volume", "v"):
                header_map["volume"] = idx

        # Si no hubo encabezados claros con nombres estándar, asumir formato clásico OHLC (Date/Time, Open, High, Low, Close, Vol)
        if "close" not in header_map:
            # Rebobinar si el header era en realidad la primera fila de datos
            try:
                float(header[1])
                f.seek(0)
                header_map = {"time": 0, "open": 1, "high": 2, "low": 3, "close": 4}
            except (ValueError, IndexError):
                pass

        for row in reader:
            if not row or len(row) < 4:
                continue
            try:
                t_idx = header_map.get("time", 0)
                o_idx = header_map.get("open", 1)
                h_idx = header_map.get("high", 2)
                l_idx = header_map.get("low", 3)
                c_idx = header_map.get("close", 4)
                v_idx = header_map.get("volume", -1)

                t_val = parse_timestamp(row[t_idx])
                open_val = float(row[o_idx])
                high_val = float(row[h_idx])
                low_val = float(row[l_idx])
                close_val = float(row[c_idx])
                vol_val = float(row[v_idx]) if v_idx != -1 and v_idx < len(row) else 0.0

                candles.append({
                    "timestamp": t_val,
                    "open": round(open_val, 6),
                    "max": round(high_val, 6),
                    "min": round(low_val, 6),
                    "close": round(close_val, 6),
                    "volume": vol_val
                })
            except (ValueError, IndexError):
                continue

    # Ordenar cronológicamente
    candles.sort(key=lambda x: x["timestamp"])
    return candles


def save_candles_to_csv(candles: List[Dict[str, float]], file_path: str):
    """Guarda una lista de velas en formato CSV estandarizado."""
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "date_time", "open", "high", "low", "close", "volume"])
        for c in candles:
            dt = datetime.fromtimestamp(c.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([
                c.get("timestamp", 0),
                dt,
                c.get("open", 0.0),
                c.get("max", c.get("high", 0.0)),
                c.get("min", c.get("low", 0.0)),
                c.get("close", 0.0),
                c.get("volume", 0.0)
            ])


def generate_benchmark_eurusd_candles(
    count: int = 3600, # 3600 velas de 10s = 10 horas de mercado continuo
    timeframe_seconds: int = 10,
    base_price: float = 1.08500,
    start_timestamp: Optional[int] = None,
    seed: int = 42
) -> List[Dict[str, float]]:
    """
    Genera un dataset de velas realistas de EUR/USD con microestructura de precios,
    volatilidad estocástica (proceso Ornstein-Uhlenbeck + saltos) para benchmarking y pruebas unitarias.
    """
    rng = random.Random(seed)
    current_ts = start_timestamp or int(datetime(2026, 8, 1, 8, 0, 0).timestamp())
    current_price = base_price
    
    candles = []
    # Parámetros de volatilidad para EUR/USD en 10s
    daily_vol = 0.0060 # 60 pips de rango diario
    dt_seconds = timeframe_seconds
    sigma_10s = (daily_vol / math.sqrt(86400 / dt_seconds)) * base_price

    trend_bias = 0.0
    volatility_regime = 1.0

    for i in range(count):
        # Cambios de régimen periódicos
        if i % 180 == 0: # Cada media hora
            trend_bias = rng.gauss(0, 0.00002)
            volatility_regime = rng.uniform(0.6, 1.8)

        op = current_price
        
        # Generar micro ticks dentro de la vela
        ticks = [op]
        tick_price = op
        for _ in range(4):
            delta = rng.gauss(trend_bias, sigma_10s * volatility_regime * 0.5)
            tick_price += delta
            ticks.append(tick_price)

        hi = max(ticks)
        lo = min(ticks)
        cl = ticks[-1]
        
        # Añadir mechas ocasionales de rechazo
        if rng.random() < 0.15:
            if rng.random() < 0.5:
                hi += rng.uniform(0.00003, 0.00008)
            else:
                lo -= rng.uniform(0.00003, 0.00008)

        hi = max(hi, op, cl)
        lo = min(lo, op, cl)

        candles.append({
            "timestamp": current_ts,
            "open": round(op, 6),
            "max": round(hi, 6),
            "min": round(lo, 6),
            "close": round(cl, 6),
            "volume": round(rng.uniform(10, 150), 1)
        })

        current_price = cl
        current_ts += timeframe_seconds

    return candles
