"""
Optimizador Exhaustivo de Estrategia para Opciones Binarias.
Pre-computa indicadores una sola vez y prueba cientos de combinaciones
de condiciones con validacion Walk-Forward (70/30) para encontrar
la configuracion mas robusta y rentable.
"""

import os
import sys
import math
import time
from datetime import datetime, timezone
from itertools import combinations

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

current_dir = os.path.dirname(os.path.abspath(__file__))
motor_dir = os.path.join(os.path.dirname(current_dir), "motor-logico")
sys.path.append(motor_dir)

from data_fetcher import load_candles_from_csv
from indicators import (
    calcular_rsi, calcular_bandas_bollinger, calcular_estocastico,
    analizar_patron_vela, calcular_soportes_resistencias, calcular_atr,
    calcular_ema, calcular_sma
)

PAYOUT = 82.0
BREAK_EVEN = (1.0 / (1.0 + PAYOUT / 100.0)) * 100.0
EXPIRE = 1
COOLDOWN = 1
WARMUP = 55
WINDOW = 200


def precompute(candles):
    n = len(candles)
    prices = [c["close"] for c in candles]

    d = {
        "n": n,
        "price": prices,
        "open": [c["open"] for c in candles],
        "high": [c.get("max", c.get("high", c["close"])) for c in candles],
        "low": [c.get("min", c.get("low", c["close"])) for c in candles],
        "rsi": [None] * n,
        "bb": [None] * n,
        "stoch": [None] * n,
        "wick": [None] * n,
        "sr": [None] * n,
        "atr": [None] * n,
        "ema50": [None] * n,
        "sma20": [None] * n,
        "hour": [None] * n,
    }

    hp = []
    hc = []
    t0 = time.time()
    step = max(1, n // 20)

    for i in range(n):
        hp.append(prices[i])
        hc.append(candles[i])
        if len(hp) > WINDOW:
            hp.pop(0)
        if len(hc) > WINDOW:
            hc.pop(0)

        if i > 0 and i % step == 0:
            pct = i * 100 / n
            print(f"  {pct:.0f}% ({i:,}/{n:,}) - {time.time()-t0:.1f}s")

        if len(hp) < WARMUP:
            continue

        d["rsi"][i] = calcular_rsi(hp, 14)
        d["bb"][i] = calcular_bandas_bollinger(hp, 20, 2.0)
        d["stoch"][i] = calcular_estocastico(hc, 14, 3, 3)
        d["wick"][i] = analizar_patron_vela(candles[i])
        d["sr"][i] = calcular_soportes_resistencias(hc, 30)
        d["atr"][i] = calcular_atr(hc, 14)
        d["ema50"][i] = calcular_ema(hp, 50)
        d["sma20"][i] = calcular_sma(hp, 20)

        ts = candles[i].get("timestamp", 0)
        if ts > 0:
            try:
                d["hour"][i] = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            except Exception:
                pass

    print(f"  Pre-computacion completa en {time.time()-t0:.1f}s\n")
    return d


def test_condition(d, cond_fn, direction, start, end):
    n = d["n"]
    wins = losses = ties = 0
    last = -COOLDOWN - 1
    end_limit = min(end, n - EXPIRE)
    for i in range(start, end_limit):
        if i - last < COOLDOWN:
            continue
        if not cond_fn(d, i):
            continue
        entry = d["price"][i]
        exit_p = d["price"][i + EXPIRE]
        if direction == "call":
            if exit_p > entry: wins += 1
            elif exit_p < entry: losses += 1
            else: ties += 1
        else:
            if exit_p < entry: wins += 1
            elif exit_p > entry: losses += 1
            else: ties += 1
        last = i
    return wins, losses, ties


def stats(w, l, t):
    dec = w + l
    if dec < 5:
        return {"wr": 0, "edge": -99, "n": dec, "se": 99, "sig": False, "w": w, "l": l}
    wr = w / dec * 100.0
    edge = wr - BREAK_EVEN
    p = wr / 100.0
    se = math.sqrt(p * (1 - p) / dec) * 100.0
    return {"wr": round(wr, 2), "edge": round(edge, 2), "n": dec,
            "se": round(se, 2), "sig": dec >= 30 and edge > 0, "w": w, "l": l}


# ======= CONDITION FACTORIES =======

def c_rsi_below(th):
    def fn(d, i):
        r = d["rsi"][i]
        return r is not None and r <= th
    fn.__name__ = f"RSI<={th}"
    return fn

def c_rsi_above(th):
    def fn(d, i):
        r = d["rsi"][i]
        return r is not None and r >= th
    fn.__name__ = f"RSI>={th}"
    return fn

def c_bb_pctb_low(th):
    def fn(d, i):
        b = d["bb"][i]
        return b is not None and b["percent_b"] <= th
    fn.__name__ = f"BB%B<={th}"
    return fn

def c_bb_pctb_high(th):
    def fn(d, i):
        b = d["bb"][i]
        return b is not None and b["percent_b"] >= th
    fn.__name__ = f"BB%B>={th}"
    return fn

def c_bb_touch_lower():
    def fn(d, i):
        b = d["bb"][i]
        return b is not None and d["price"][i] <= b["lower"]
    fn.__name__ = "BB_Touch_Lower"
    return fn

def c_bb_touch_upper():
    def fn(d, i):
        b = d["bb"][i]
        return b is not None and d["price"][i] >= b["upper"]
    fn.__name__ = "BB_Touch_Upper"
    return fn

def c_bb_bandwidth_range(lo, hi):
    def fn(d, i):
        b = d["bb"][i]
        return b is not None and lo <= b["bandwidth"] <= hi
    fn.__name__ = f"BB_BW_{lo}-{hi}"
    return fn

def c_stoch_cross_call(th):
    def fn(d, i):
        s = d["stoch"][i]
        return s is not None and s["cruce_alcista"] and s["k"] <= th
    fn.__name__ = f"Stoch_Call_K<={th}"
    return fn

def c_stoch_cross_put(th):
    def fn(d, i):
        s = d["stoch"][i]
        return s is not None and s["cruce_bajista"] and s["k"] >= th
    fn.__name__ = f"Stoch_Put_K>={th}"
    return fn

def c_stoch_k_below(th):
    def fn(d, i):
        s = d["stoch"][i]
        return s is not None and s["k"] <= th
    fn.__name__ = f"StochK<={th}"
    return fn

def c_stoch_k_above(th):
    def fn(d, i):
        s = d["stoch"][i]
        return s is not None and s["k"] >= th
    fn.__name__ = f"StochK>={th}"
    return fn

def c_wick_call(pct=25):
    def fn(d, i):
        w = d["wick"][i]
        return w is not None and w["rechazo_alcista"] and w["pct_mecha_inf"] >= pct
    fn.__name__ = f"Wick_Call_{pct}%"
    return fn

def c_wick_put(pct=25):
    def fn(d, i):
        w = d["wick"][i]
        return w is not None and w["rechazo_bajista"] and w["pct_mecha_sup"] >= pct
    fn.__name__ = f"Wick_Put_{pct}%"
    return fn

def c_wick_bullish():
    def fn(d, i):
        w = d["wick"][i]
        return w is not None and w["rechazo_alcista"] and w["es_alcista"]
    fn.__name__ = "Wick_Bullish"
    return fn

def c_wick_bearish():
    def fn(d, i):
        w = d["wick"][i]
        return w is not None and w["rechazo_bajista"] and w["es_bajista"]
    fn.__name__ = "Wick_Bearish"
    return fn

def c_near_support(dist=0.00010):
    def fn(d, i):
        sr = d["sr"][i]
        return sr is not None and sr["soporte"] > 0 and abs(d["price"][i] - sr["soporte"]) <= dist
    fn.__name__ = f"Support_{int(dist*100000)}pip"
    return fn

def c_near_resistance(dist=0.00010):
    def fn(d, i):
        sr = d["sr"][i]
        return sr is not None and sr["resistencia"] < 999999 and abs(d["price"][i] - sr["resistencia"]) <= dist
    fn.__name__ = f"Resist_{int(dist*100000)}pip"
    return fn

def c_trend_up():
    def fn(d, i):
        e = d["ema50"][i]
        return e is not None and d["price"][i] > e
    fn.__name__ = "Trend_Up"
    return fn

def c_trend_down():
    def fn(d, i):
        e = d["ema50"][i]
        return e is not None and d["price"][i] < e
    fn.__name__ = "Trend_Down"
    return fn

def c_bullish_candle():
    def fn(d, i):
        return d["price"][i] > d["open"][i]
    fn.__name__ = "Bullish_Candle"
    return fn

def c_bearish_candle():
    def fn(d, i):
        return d["price"][i] < d["open"][i]
    fn.__name__ = "Bearish_Candle"
    return fn

def c_momentum_up(n_back=3):
    def fn(d, i):
        return i >= n_back and d["price"][i] > d["price"][i - n_back]
    fn.__name__ = f"Mom_Up_{n_back}"
    return fn

def c_momentum_down(n_back=3):
    def fn(d, i):
        return i >= n_back and d["price"][i] < d["price"][i - n_back]
    fn.__name__ = f"Mom_Down_{n_back}"
    return fn

def c_atr_range(lo, hi):
    def fn(d, i):
        a = d["atr"][i]
        return a is not None and lo <= a <= hi
    fn.__name__ = f"ATR_{lo}-{hi}"
    return fn

def c_hour_range(h_start, h_end):
    def fn(d, i):
        h = d["hour"][i]
        if h is None:
            return True
        if h_start <= h_end:
            return h_start <= h < h_end
        return h >= h_start or h < h_end
    fn.__name__ = f"Hour_{h_start}-{h_end}"
    return fn

def c_prev_candle_bullish():
    def fn(d, i):
        return i >= 1 and d["price"][i-1] > d["open"][i-1]
    fn.__name__ = "Prev_Bullish"
    return fn

def c_prev_candle_bearish():
    def fn(d, i):
        return i >= 1 and d["price"][i-1] < d["open"][i-1]
    fn.__name__ = "Prev_Bearish"
    return fn

def c_price_below_sma20():
    def fn(d, i):
        s = d["sma20"][i]
        return s is not None and d["price"][i] < s
    fn.__name__ = "Price<SMA20"
    return fn

def c_price_above_sma20():
    def fn(d, i):
        s = d["sma20"][i]
        return s is not None and d["price"][i] > s
    fn.__name__ = "Price>SMA20"
    return fn

def c_rsi_divergence_call():
    """RSI rising while price falling (bullish divergence proxy)"""
    def fn(d, i):
        if i < 3:
            return False
        r_now = d["rsi"][i]
        r_prev = d["rsi"][i-3]
        if r_now is None or r_prev is None:
            return False
        return r_now > r_prev and d["price"][i] < d["price"][i-3]
    fn.__name__ = "RSI_Div_Call"
    return fn

def c_rsi_divergence_put():
    """RSI falling while price rising (bearish divergence proxy)"""
    def fn(d, i):
        if i < 3:
            return False
        r_now = d["rsi"][i]
        r_prev = d["rsi"][i-3]
        if r_now is None or r_prev is None:
            return False
        return r_now < r_prev and d["price"][i] > d["price"][i-3]
    fn.__name__ = "RSI_Div_Put"
    return fn


def combine(*conds):
    def fn(d, i):
        return all(c(d, i) for c in conds)
    fn.__name__ = " + ".join(c.__name__ for c in conds)
    return fn


def main():
    root_dir = os.path.dirname(current_dir)
    data_file = os.path.join(root_dir, "data", "historical", "eurusd_m1_dukascopy_2023.csv")

    print("=" * 90)
    print("  OPTIMIZADOR EXHAUSTIVO DE ESTRATEGIA - OPCIONES BINARIAS EUR/USD")
    print("=" * 90)
    print(f"  Payout: {PAYOUT}% | Break-Even: {BREAK_EVEN:.2f}% | Expiracion: {EXPIRE} vela (60s)")
    print(f"  Datos: {data_file}\n")

    candles = load_candles_from_csv(data_file)
    n = len(candles)
    split = int(n * 0.70)
    print(f"  Total velas: {n:,}")
    print(f"  Train: {split:,} (70%) | Test: {n-split:,} (30%)\n")

    print("[1/4] Pre-computando indicadores...")
    d = precompute(candles)

    # ======= PHASE 1: INDIVIDUAL CONDITIONS =======
    print("=" * 90)
    print("[2/4] ANALISIS INDIVIDUAL DE CONDICIONES")
    print("=" * 90)

    call_conds = {
        "RSI<=20": c_rsi_below(20),
        "RSI<=25": c_rsi_below(25),
        "RSI<=30": c_rsi_below(30),
        "RSI<=35": c_rsi_below(35),
        "RSI<=40": c_rsi_below(40),
        "BB%B<=0.00": c_bb_pctb_low(0.00),
        "BB%B<=0.05": c_bb_pctb_low(0.05),
        "BB%B<=0.10": c_bb_pctb_low(0.10),
        "BB%B<=0.15": c_bb_pctb_low(0.15),
        "BB%B<=0.20": c_bb_pctb_low(0.20),
        "BB_Touch_Lower": c_bb_touch_lower(),
        "Stoch_Cross_K<=15": c_stoch_cross_call(15),
        "Stoch_Cross_K<=20": c_stoch_cross_call(20),
        "Stoch_Cross_K<=25": c_stoch_cross_call(25),
        "Stoch_Cross_K<=30": c_stoch_cross_call(30),
        "StochK<=15": c_stoch_k_below(15),
        "StochK<=20": c_stoch_k_below(20),
        "StochK<=25": c_stoch_k_below(25),
        "Wick_Call_25%": c_wick_call(25),
        "Wick_Call_30%": c_wick_call(30),
        "Wick_Call_40%": c_wick_call(40),
        "Wick_Bullish": c_wick_bullish(),
        "Support_10pip": c_near_support(0.00010),
        "Support_5pip": c_near_support(0.00005),
        "Support_15pip": c_near_support(0.00015),
        "Trend_Up": c_trend_up(),
        "Bullish_Candle": c_bullish_candle(),
        "Mom_Up_3": c_momentum_up(3),
        "Mom_Up_5": c_momentum_up(5),
        "Prev_Bullish": c_prev_candle_bullish(),
        "Price<SMA20": c_price_below_sma20(),
        "RSI_Div_Call": c_rsi_divergence_call(),
    }

    put_conds = {
        "RSI>=80": c_rsi_above(80),
        "RSI>=75": c_rsi_above(75),
        "RSI>=70": c_rsi_above(70),
        "RSI>=65": c_rsi_above(65),
        "RSI>=60": c_rsi_above(60),
        "BB%B>=1.00": c_bb_pctb_high(1.00),
        "BB%B>=0.95": c_bb_pctb_high(0.95),
        "BB%B>=0.90": c_bb_pctb_high(0.90),
        "BB%B>=0.85": c_bb_pctb_high(0.85),
        "BB%B>=0.80": c_bb_pctb_high(0.80),
        "BB_Touch_Upper": c_bb_touch_upper(),
        "Stoch_Cross_K>=85": c_stoch_cross_put(85),
        "Stoch_Cross_K>=80": c_stoch_cross_put(80),
        "Stoch_Cross_K>=75": c_stoch_cross_put(75),
        "Stoch_Cross_K>=70": c_stoch_cross_put(70),
        "StochK>=85": c_stoch_k_above(85),
        "StochK>=80": c_stoch_k_above(80),
        "StochK>=75": c_stoch_k_above(75),
        "Wick_Put_25%": c_wick_put(25),
        "Wick_Put_30%": c_wick_put(30),
        "Wick_Put_40%": c_wick_put(40),
        "Wick_Bearish": c_wick_bearish(),
        "Resist_10pip": c_near_resistance(0.00010),
        "Resist_5pip": c_near_resistance(0.00005),
        "Resist_15pip": c_near_resistance(0.00015),
        "Trend_Down": c_trend_down(),
        "Bearish_Candle": c_bearish_candle(),
        "Mom_Down_3": c_momentum_down(3),
        "Mom_Down_5": c_momentum_down(5),
        "Prev_Bearish": c_prev_candle_bearish(),
        "Price>SMA20": c_price_above_sma20(),
        "RSI_Div_Put": c_rsi_divergence_put(),
    }

    results_individual = []

    for name, cond in call_conds.items():
        w, l, t = test_condition(d, cond, "call", WARMUP, split)
        tr = stats(w, l, t)
        w2, l2, t2 = test_condition(d, cond, "call", split, n)
        te = stats(w2, l2, t2)
        results_individual.append({"name": name, "dir": "CALL", "tr": tr, "te": te, "cond": cond})

    for name, cond in put_conds.items():
        w, l, t = test_condition(d, cond, "put", WARMUP, split)
        tr = stats(w, l, t)
        w2, l2, t2 = test_condition(d, cond, "put", split, n)
        te = stats(w2, l2, t2)
        results_individual.append({"name": name, "dir": "PUT", "tr": tr, "te": te, "cond": cond})

    results_individual.sort(key=lambda x: x["tr"]["edge"], reverse=True)

    print(f"\n{'Condicion':<30} {'Dir':<5} | {'Train WR%':>9} {'Edge%':>7} {'N':>6} | {'Test WR%':>9} {'Edge%':>7} {'N':>6} | {'Robusto'}")
    print("-" * 105)

    for r in results_individual:
        tr, te = r["tr"], r["te"]
        robust = "***" if tr["edge"] > 0 and te["edge"] > 0 and tr["n"] >= 30 and te["n"] >= 10 else ""
        print(f"{r['name']:<30} {r['dir']:<5} | {tr['wr']:>8.2f}% {tr['edge']:>+6.2f}% {tr['n']:>5} | {te['wr']:>8.2f}% {te['edge']:>+6.2f}% {te['n']:>5} | {robust}")

    # ======= PHASE 2: PRE-BUILT EXTREME CONFLUENCES =======
    print("\n" + "=" * 90)
    print("[3/4] CONFLUENCIAS EXTREMAS Y COMBINACIONES")
    print("=" * 90)

    combo_results = []

    # Pre-built extreme confluence conditions (the ones most likely to have edge)
    extreme_calls = [
        ("RSI<=25+BB_Low+Wick", combine(c_rsi_below(25), c_bb_pctb_low(0.10), c_wick_call(25))),
        ("RSI<=20+BB_Low", combine(c_rsi_below(20), c_bb_pctb_low(0.10))),
        ("RSI<=25+BB_Touch", combine(c_rsi_below(25), c_bb_touch_lower())),
        ("RSI<=30+BB_Touch+Wick", combine(c_rsi_below(30), c_bb_touch_lower(), c_wick_call(25))),
        ("RSI<=25+StochK<=20", combine(c_rsi_below(25), c_stoch_k_below(20))),
        ("RSI<=25+StochK<=20+Wick", combine(c_rsi_below(25), c_stoch_k_below(20), c_wick_call(25))),
        ("RSI<=30+BB_Touch", combine(c_rsi_below(30), c_bb_touch_lower())),
        ("RSI<=30+StochK<=25", combine(c_rsi_below(30), c_stoch_k_below(25))),
        ("BB_Touch+Wick+Bullish", combine(c_bb_touch_lower(), c_wick_call(25), c_bullish_candle())),
        ("BB_Touch+StochK<=20", combine(c_bb_touch_lower(), c_stoch_k_below(20))),
        ("BB_Touch+Wick", combine(c_bb_touch_lower(), c_wick_call(25))),
        ("RSI<=20+Wick", combine(c_rsi_below(20), c_wick_call(25))),
        ("RSI<=25+Wick_Bullish", combine(c_rsi_below(25), c_wick_bullish())),
        ("RSI<=30+BB%B<=0.05", combine(c_rsi_below(30), c_bb_pctb_low(0.05))),
        ("RSI<=25+BB%B<=0.05", combine(c_rsi_below(25), c_bb_pctb_low(0.05))),
        ("BB%B<=0.05+Wick+StochK<=25", combine(c_bb_pctb_low(0.05), c_wick_call(25), c_stoch_k_below(25))),
        ("RSI<=30+Support_10pip", combine(c_rsi_below(30), c_near_support(0.00010))),
        ("RSI<=25+Support_5pip", combine(c_rsi_below(25), c_near_support(0.00005))),
        ("BB_Touch+Support_10pip", combine(c_bb_touch_lower(), c_near_support(0.00010))),
        ("RSI<=25+BB_Touch+StochK<=20", combine(c_rsi_below(25), c_bb_touch_lower(), c_stoch_k_below(20))),
        ("Triple_Ext: RSI<=20+BB<=0.05+StochK<=15", combine(c_rsi_below(20), c_bb_pctb_low(0.05), c_stoch_k_below(15))),
        ("Quad: RSI<=25+BB_Touch+Wick+StochK<=25", combine(c_rsi_below(25), c_bb_touch_lower(), c_wick_call(25), c_stoch_k_below(25))),
        # Contrarian: after bearish → CALL
        ("Contrarian: PrevBearish+RSI<=30", combine(c_prev_candle_bearish(), c_rsi_below(30))),
        ("Contrarian: MomDown3+RSI<=25", combine(c_momentum_down(3), c_rsi_below(25))),
        ("Contrarian: MomDown3+BB_Touch", combine(c_momentum_down(3), c_bb_touch_lower())),
        # Trend + Mean Reversion
        ("TrendUp+RSI<=35+Wick", combine(c_trend_up(), c_rsi_below(35), c_wick_call(25))),
        ("TrendUp+BB_Touch", combine(c_trend_up(), c_bb_touch_lower())),
        ("TrendUp+RSI<=30", combine(c_trend_up(), c_rsi_below(30))),
        ("Price<SMA20+RSI<=30+Wick", combine(c_price_below_sma20(), c_rsi_below(30), c_wick_call(25))),
    ]

    extreme_puts = [
        ("RSI>=75+BB_High+Wick", combine(c_rsi_above(75), c_bb_pctb_high(0.90), c_wick_put(25))),
        ("RSI>=80+BB_High", combine(c_rsi_above(80), c_bb_pctb_high(0.90))),
        ("RSI>=75+BB_Touch", combine(c_rsi_above(75), c_bb_touch_upper())),
        ("RSI>=70+BB_Touch+Wick", combine(c_rsi_above(70), c_bb_touch_upper(), c_wick_put(25))),
        ("RSI>=75+StochK>=80", combine(c_rsi_above(75), c_stoch_k_above(80))),
        ("RSI>=75+StochK>=80+Wick", combine(c_rsi_above(75), c_stoch_k_above(80), c_wick_put(25))),
        ("RSI>=70+BB_Touch", combine(c_rsi_above(70), c_bb_touch_upper())),
        ("RSI>=70+StochK>=75", combine(c_rsi_above(70), c_stoch_k_above(75))),
        ("BB_Touch+Wick+Bearish", combine(c_bb_touch_upper(), c_wick_put(25), c_bearish_candle())),
        ("BB_Touch+StochK>=80", combine(c_bb_touch_upper(), c_stoch_k_above(80))),
        ("BB_Touch+Wick", combine(c_bb_touch_upper(), c_wick_put(25))),
        ("RSI>=80+Wick", combine(c_rsi_above(80), c_wick_put(25))),
        ("RSI>=75+Wick_Bearish", combine(c_rsi_above(75), c_wick_bearish())),
        ("RSI>=70+BB%B>=0.95", combine(c_rsi_above(70), c_bb_pctb_high(0.95))),
        ("RSI>=75+BB%B>=0.95", combine(c_rsi_above(75), c_bb_pctb_high(0.95))),
        ("BB%B>=0.95+Wick+StochK>=75", combine(c_bb_pctb_high(0.95), c_wick_put(25), c_stoch_k_above(75))),
        ("RSI>=70+Resist_10pip", combine(c_rsi_above(70), c_near_resistance(0.00010))),
        ("RSI>=75+Resist_5pip", combine(c_rsi_above(75), c_near_resistance(0.00005))),
        ("BB_Touch+Resist_10pip", combine(c_bb_touch_upper(), c_near_resistance(0.00010))),
        ("RSI>=75+BB_Touch+StochK>=80", combine(c_rsi_above(75), c_bb_touch_upper(), c_stoch_k_above(80))),
        ("Triple_Ext: RSI>=80+BB>=0.95+StochK>=85", combine(c_rsi_above(80), c_bb_pctb_high(0.95), c_stoch_k_above(85))),
        ("Quad: RSI>=75+BB_Touch+Wick+StochK>=75", combine(c_rsi_above(75), c_bb_touch_upper(), c_wick_put(25), c_stoch_k_above(75))),
        # Contrarian: after bullish → PUT
        ("Contrarian: PrevBullish+RSI>=70", combine(c_prev_candle_bullish(), c_rsi_above(70))),
        ("Contrarian: MomUp3+RSI>=75", combine(c_momentum_up(3), c_rsi_above(75))),
        ("Contrarian: MomUp3+BB_Touch", combine(c_momentum_up(3), c_bb_touch_upper())),
        # Trend + Mean Reversion
        ("TrendDown+RSI>=65+Wick", combine(c_trend_down(), c_rsi_above(65), c_wick_put(25))),
        ("TrendDown+BB_Touch", combine(c_trend_down(), c_bb_touch_upper())),
        ("TrendDown+RSI>=70", combine(c_trend_down(), c_rsi_above(70))),
        ("Price>SMA20+RSI>=70+Wick", combine(c_price_above_sma20(), c_rsi_above(70), c_wick_put(25))),
    ]

    print(f"\n  Probando {len(extreme_calls)} confluencias CALL + {len(extreme_puts)} confluencias PUT...")
    t0 = time.time()

    for name, cond in extreme_calls:
        w, l, t = test_condition(d, cond, "call", WARMUP, split)
        tr = stats(w, l, t)
        w2, l2, t2 = test_condition(d, cond, "call", split, n)
        te = stats(w2, l2, t2)
        combo_results.append({"name": name, "dir": "CALL", "tr": tr, "te": te, "nc": name.count("+")+1})

    for name, cond in extreme_puts:
        w, l, t = test_condition(d, cond, "put", WARMUP, split)
        tr = stats(w, l, t)
        w2, l2, t2 = test_condition(d, cond, "put", split, n)
        te = stats(w2, l2, t2)
        combo_results.append({"name": name, "dir": "PUT", "tr": tr, "te": te, "nc": name.count("+")+1})

    print(f"  Confluencias extremas: {len(combo_results)} combos en {time.time()-t0:.1f}s")

    # Also auto-generate 2-way combos from top individual signals
    good_call = [(r["name"], r["cond"]) for r in results_individual
                 if r["dir"] == "CALL" and r["tr"]["n"] >= 15]
    good_put = [(r["name"], r["cond"]) for r in results_individual
                if r["dir"] == "PUT" and r["tr"]["n"] >= 15]

    nc_auto = len(combo_results)
    t0 = time.time()
    print(f"\n  Auto-generando 2-way (CALL: C({len(good_call)},2)={len(good_call)*(len(good_call)-1)//2}, PUT: C({len(good_put)},2)={len(good_put)*(len(good_put)-1)//2})...")

    for conds_list, direction in [(good_call, "call"), (good_put, "put")]:
        for (n1, c1), (n2, c2) in combinations(conds_list, 2):
            combined = combine(c1, c2)
            w, l, t = test_condition(d, combined, direction, WARMUP, split)
            tr = stats(w, l, t)
            if tr["n"] >= 8:
                w2, l2, t2 = test_condition(d, combined, direction, split, n)
                te = stats(w2, l2, t2)
                combo_results.append({
                    "name": f"{n1} + {n2}",
                    "dir": direction.upper(), "tr": tr, "te": te, "nc": 2
                })

    print(f"  Auto 2-way: {len(combo_results)-nc_auto} combos en {time.time()-t0:.1f}s")

    # 3-way from top 12 per direction
    top_call = good_call[:12]
    top_put = good_put[:12]
    nc3_before = len(combo_results)
    t0 = time.time()
    print(f"  Auto-generando 3-way (top 12 each)...")

    for conds_list, direction in [(top_call, "call"), (top_put, "put")]:
        for (n1, c1), (n2, c2), (n3, c3) in combinations(conds_list, 3):
            combined = combine(c1, c2, c3)
            w, l, t = test_condition(d, combined, direction, WARMUP, split)
            tr = stats(w, l, t)
            if tr["n"] >= 5:
                w2, l2, t2 = test_condition(d, combined, direction, split, n)
                te = stats(w2, l2, t2)
                combo_results.append({
                    "name": f"{n1} + {n2} + {n3}",
                    "dir": direction.upper(), "tr": tr, "te": te, "nc": 3
                })

    print(f"  Auto 3-way: {len(combo_results)-nc3_before} combos en {time.time()-t0:.1f}s")

    # Sort by OUT-OF-SAMPLE edge (what matters for real trading)
    combo_results.sort(key=lambda x: x["te"]["edge"], reverse=True)

    print(f"\n  TOP 40 COMBINACIONES POR EDGE OUT-OF-SAMPLE:")
    print(f"  {'Combinacion':<70} {'Dir':<5} | {'Train WR%':>9} {'Edge%':>7} {'N':>5} | {'Test WR%':>9} {'Edge%':>7} {'N':>5}")
    print("  " + "-" * 120)

    for r in combo_results[:40]:
        tr, te = r["tr"], r["te"]
        marker = " <<<" if tr["edge"] > 2 and te["edge"] > 2 and tr["n"] >= 20 and te["n"] >= 10 else ""
        print(f"  {r['name'][:69]:<70} {r['dir']:<5} | {tr['wr']:>8.2f}% {tr['edge']:>+6.2f}% {tr['n']:>4} | {te['wr']:>8.2f}% {te['edge']:>+6.2f}% {te['n']:>4}{marker}")

    # Also show top by CONSISTENT edge (positive in both)
    consistent = [r for r in combo_results if r["tr"]["edge"] > 0 and r["te"]["edge"] > 0 and r["tr"]["n"] >= 15 and r["te"]["n"] >= 8]
    consistent.sort(key=lambda x: min(x["tr"]["edge"], x["te"]["edge"]), reverse=True)

    print(f"\n  TOP COMBINACIONES CONSISTENTES (Edge>0 en Train Y Test):")
    print(f"  {'Combinacion':<70} {'Dir':<5} | {'Train WR%':>9} {'Edge%':>7} {'N':>5} | {'Test WR%':>9} {'Edge%':>7} {'N':>5}")
    print("  " + "-" * 120)

    for r in consistent[:30]:
        tr, te = r["tr"], r["te"]
        print(f"  {r['name'][:69]:<70} {r['dir']:<5} | {tr['wr']:>8.2f}% {tr['edge']:>+6.2f}% {tr['n']:>4} | {te['wr']:>8.2f}% {te['edge']:>+6.2f}% {te['n']:>4}")

    # ======= PHASE 3: FILTERS =======
    print("\n" + "=" * 90)
    print("[4/4] FILTROS DE VOLATILIDAD Y SESION")
    print("=" * 90)

    # Analyze ATR distribution to set good thresholds
    atr_vals = [d["atr"][i] for i in range(n) if d["atr"][i] is not None]
    if atr_vals:
        atr_vals_sorted = sorted(atr_vals)
        p10 = atr_vals_sorted[len(atr_vals_sorted) // 10]
        p25 = atr_vals_sorted[len(atr_vals_sorted) // 4]
        p50 = atr_vals_sorted[len(atr_vals_sorted) // 2]
        p75 = atr_vals_sorted[3 * len(atr_vals_sorted) // 4]
        p90 = atr_vals_sorted[9 * len(atr_vals_sorted) // 10]
        print(f"\n  Distribucion ATR: P10={p10:.6f} P25={p25:.6f} P50={p50:.6f} P75={p75:.6f} P90={p90:.6f}")

    # Test ATR filters on the top-5 consistent combos
    atr_filters = [
        ("ATR_P10-P90", c_atr_range(p10, p90)),
        ("ATR_P25-P75", c_atr_range(p25, p75)),
        ("ATR_P25-P90", c_atr_range(p25, p90)),
        ("ATR_P10-P75", c_atr_range(p10, p75)),
        ("ATR>P25", c_atr_range(p25, 1.0)),
        ("ATR>P50", c_atr_range(p50, 1.0)),
    ]

    hour_filters = [
        ("London_7-16", c_hour_range(7, 16)),
        ("NY_12-21", c_hour_range(12, 21)),
        ("LonNY_7-21", c_hour_range(7, 21)),
        ("Overlap_12-16", c_hour_range(12, 16)),
        ("Asian_23-7", c_hour_range(23, 7)),
        ("NoFilter", None),
    ]

    # Use extreme confluences + best individuals for filter testing
    top_signals = []
    for name, cond in extreme_calls[:15]:
        top_signals.append((name, cond, "call"))
    for name, cond in extreme_puts[:15]:
        top_signals.append((name, cond, "put"))
    for r in results_individual[:8]:
        top_signals.append((r["name"], r["cond"], r["dir"].lower()))

    print(f"\n  Probando filtros sobre {len(top_signals)} senales...")

    filter_results = []

    for sig_name, sig_cond, sig_dir in top_signals:
        for filt_name, filt_cond in [*atr_filters, *hour_filters]:
            if filt_cond is None:
                filtered = sig_cond
            else:
                filtered = combine(sig_cond, filt_cond)

            w, l, t = test_condition(d, filtered, sig_dir, WARMUP, split)
            tr = stats(w, l, t)
            w2, l2, t2 = test_condition(d, filtered, sig_dir, split, n)
            te = stats(w2, l2, t2)

            if tr["n"] >= 8:
                filter_results.append({
                    "signal": sig_name, "filter": filt_name,
                    "dir": sig_dir.upper(), "tr": tr, "te": te
                })

    filter_results.sort(key=lambda x: x["te"]["edge"], reverse=True)

    print(f"\n  {'Signal + Filter':<55} {'Dir':<5} | {'Train WR%':>9} {'Edge%':>7} {'N':>5} | {'Test WR%':>9} {'Edge%':>7} {'N':>5}")
    print("  " + "-" * 110)

    for r in filter_results[:30]:
        tr, te = r["tr"], r["te"]
        label = f"{r['signal']} | {r['filter']}"
        print(f"  {label[:54]:<55} {r['dir']:<5} | {tr['wr']:>8.2f}% {tr['edge']:>+6.2f}% {tr['n']:>4} | {te['wr']:>8.2f}% {te['edge']:>+6.2f}% {te['n']:>4}")

    # ======= SUMMARY =======
    print("\n" + "=" * 90)
    print("  RESUMEN DE HALLAZGOS")
    print("=" * 90)
    print(f"  Break-Even requerido: {BREAK_EVEN:.2f}%")
    print(f"  Condiciones individuales positivas (Train): {sum(1 for r in results_individual if r['tr']['edge'] > 0)}/{len(results_individual)}")
    print(f"  Condiciones robustas (Edge>0 Train+Test):   {sum(1 for r in results_individual if r['tr']['edge'] > 0 and r['te']['edge'] > 0 and r['tr']['n'] >= 20)}/{len(results_individual)}")
    print(f"  Combinaciones consistentes encontradas:     {len(consistent)}")

    if consistent:
        best = consistent[0]
        print(f"\n  MEJOR COMBINACION ROBUSTA:")
        print(f"    {best['name']}")
        print(f"    Dir: {best['dir']} | Train: {best['tr']['wr']}% (Edge {best['tr']['edge']:+.2f}%, N={best['tr']['n']})")
        print(f"                      | Test:  {best['te']['wr']}% (Edge {best['te']['edge']:+.2f}%, N={best['te']['n']})")

    print("\n" + "=" * 90)


if __name__ == "__main__":
    main()
