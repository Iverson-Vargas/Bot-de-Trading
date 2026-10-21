"""
Barrido ultrarapido: pre-computa indicadores UNA VEZ y prueba
diferentes umbrales/condiciones instantaneamente con arrays.
"""
import os, sys, math, time
from datetime import datetime, timezone

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass

current_dir = os.path.dirname(os.path.abspath(__file__))
motor_dir = os.path.join(os.path.dirname(current_dir), "motor-logico")
sys.path.append(motor_dir)
sys.stdout.flush()

from data_fetcher import load_candles_from_csv
from indicators import (
    calcular_rsi, calcular_bandas_bollinger, calcular_estocastico,
    analizar_patron_vela, calcular_soportes_resistencias, calcular_atr,
    calcular_ema, calcular_sma, calcular_macd, extraer_hora_utc
)

PAYOUT = 82.0
BE = (1.0 / (1.0 + PAYOUT / 100.0)) * 100.0
EXP = 1
CD = 1
WU = 55
WIN = 200


def run():
    root = os.path.dirname(current_dir)
    f = os.path.join(root, "data", "historical", "eurusd_m1_dukascopy_2023.csv")
    print(f"Cargando {f}...")
    sys.stdout.flush()
    candles = load_candles_from_csv(f)
    n = len(candles)
    sp = int(n * 0.70)
    print(f"N={n:,} Train={sp:,} Test={n-sp:,} BE={BE:.2f}%")
    sys.stdout.flush()

    prices = [c["close"] for c in candles]
    opens = [c["open"] for c in candles]

    print("Pre-computando indicadores...")
    sys.stdout.flush()
    t0 = time.time()

    rsi_v = [None]*n
    bb_v = [None]*n
    stk_v = [None]*n
    wck_v = [None]*n
    sr_v = [None]*n
    atr_v = [None]*n
    macd_v = [None]*n
    hour_v = [None]*n

    hp, hc = [], []
    step = max(1, n // 10)
    for i in range(n):
        hp.append(prices[i])
        hc.append(candles[i])
        if len(hp) > WIN: hp.pop(0)
        if len(hc) > WIN: hc.pop(0)
        if i > 0 and i % step == 0:
            print(f"  {i*100//n}% - {time.time()-t0:.0f}s")
            sys.stdout.flush()
        if len(hp) < WU:
            continue
        rsi_v[i] = calcular_rsi(hp, 14)
        bb_v[i] = calcular_bandas_bollinger(hp, 20, 2.0)
        stk_v[i] = calcular_estocastico(hc, 14, 3, 3)
        wck_v[i] = analizar_patron_vela(candles[i])
        sr_v[i] = calcular_soportes_resistencias(hc, 30)
        atr_v[i] = calcular_atr(hc, 14)
        macd_v[i] = calcular_macd(hp, 12, 26, 9)
        ts = candles[i].get("timestamp", 0)
        if ts > 0:
            try: hour_v[i] = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            except: pass

    print(f"Pre-comp: {time.time()-t0:.0f}s\n")
    sys.stdout.flush()

    def simulate(check_fn, direction, start, end):
        w = l = t = 0
        last = -CD - 1
        lim = min(end, n - EXP)
        for i in range(start, lim):
            if i - last < CD: continue
            if not check_fn(i): continue
            entry = prices[i]
            exit_p = prices[i + EXP]
            if direction == "C":
                if exit_p > entry: w += 1
                elif exit_p < entry: l += 1
                else: t += 1
            else:
                if exit_p < entry: w += 1
                elif exit_p > entry: l += 1
                else: t += 1
            last = i
        dec = w + l
        wr = w / dec * 100.0 if dec > 0 else 0
        edge = wr - BE
        return w, l, t, dec, round(wr, 2), round(edge, 2)

    # Build strategy-like conditions using pre-computed arrays
    def legacy_check(min_sc, direction):
        def fn(i):
            r = rsi_v[i]; b = bb_v[i]; s = stk_v[i]; w = wck_v[i]; sr = sr_v[i]
            if r is None or b is None: return False
            p = prices[i]
            sc = 0
            if direction == "C":
                if p <= b["lower"]: sc += 30
                elif b["std"] > 0 and p <= b["lower"] + b["std"]*0.25: sc += 15
                if r <= 32: sc += 25
                elif r <= 38: sc += 10
                if s and (s["cruce_alcista"] or s["k"] <= 25): sc += 20
                if w and w["rechazo_alcista"]: sc += 20
                if sr and sr["soporte"] > 0 and abs(p - sr["soporte"]) <= 0.00015: sc += 10
            else:
                if p >= b["upper"]: sc += 30
                elif b["std"] > 0 and p >= b["upper"] - b["std"]*0.25: sc += 15
                if r >= 68: sc += 25
                elif r >= 62: sc += 10
                if s and (s["cruce_bajista"] or s["k"] >= 75): sc += 20
                if w and w["rechazo_bajista"]: sc += 20
                if sr and sr["resistencia"] < 999999 and abs(p - sr["resistencia"]) <= 0.00015: sc += 10
            return sc >= min_sc
        return fn

    def adaptive_check(min_sc, min_conds, rsi_lo, rsi_hi, bb_th, stoch_th,
                       wick_pct, session_filt, vol_filt, conflict_mg):
        def fn(i):
            r = rsi_v[i]; b = bb_v[i]; s = stk_v[i]; w = wck_v[i]
            sr = sr_v[i]; a = atr_v[i]; mc = macd_v[i]
            if r is None or b is None: return False
            p = prices[i]

            if vol_filt and a is not None and (a < 0.00004 or a > 0.00030): return False
            if session_filt:
                h = hour_v[i]
                if h is not None and not (7 <= h < 21): return False

            sc_c = sc_p = 0
            cc = cp = 0

            # CALL
            if r <= rsi_lo:
                sc_c += 25 if r <= 20 else 20; cc += 1
            if b["percent_b"] <= bb_th:
                sc_c += 25 if b["percent_b"] <= 0.05 else 20; cc += 1
            elif p <= b["lower"]:
                sc_c += 20; cc += 1
            if s and s["k"] <= stoch_th:
                sc_c += 20; cc += 1
                if s["cruce_alcista"]: sc_c += 5
            if w and w["rechazo_alcista"] and w["pct_mecha_inf"] >= wick_pct:
                sc_c += 15; cc += 1
                if w["es_alcista"]: sc_c += 5
            if sr and sr["soporte"] > 0 and abs(p - sr["soporte"]) <= 0.00010:
                sc_c += 10; cc += 1
            if mc and mc["hist_rising"]: sc_c += 5

            # PUT
            if r >= rsi_hi:
                sc_p += 25 if r >= 80 else 20; cp += 1
            if b["percent_b"] >= (1.0 - bb_th):
                sc_p += 25 if b["percent_b"] >= 0.95 else 20; cp += 1
            elif p >= b["upper"]:
                sc_p += 20; cp += 1
            if s and s["k"] >= (100 - stoch_th):
                sc_p += 20; cp += 1
                if s["cruce_bajista"]: sc_p += 5
            if w and w["rechazo_bajista"] and w["pct_mecha_sup"] >= wick_pct:
                sc_p += 15; cp += 1
                if w["es_bajista"]: sc_p += 5
            if sr and sr["resistencia"] < 999999 and abs(p - sr["resistencia"]) <= 0.00010:
                sc_p += 10; cp += 1
            if mc and not mc["hist_rising"]: sc_p += 5

            # Conflict check
            if sc_c >= min_sc and sc_p >= min_sc and abs(sc_c - sc_p) < conflict_mg:
                return False

            return sc_c >= min_sc and cc >= min_conds and sc_c > sc_p

        def fn_put(i):
            r = rsi_v[i]; b = bb_v[i]; s = stk_v[i]; w = wck_v[i]
            sr = sr_v[i]; a = atr_v[i]; mc = macd_v[i]
            if r is None or b is None: return False
            p = prices[i]

            if vol_filt and a is not None and (a < 0.00004 or a > 0.00030): return False
            if session_filt:
                h = hour_v[i]
                if h is not None and not (7 <= h < 21): return False

            sc_c = sc_p = 0
            cc = cp = 0

            if r <= rsi_lo:
                sc_c += 25 if r <= 20 else 20; cc += 1
            if b["percent_b"] <= bb_th:
                sc_c += 25 if b["percent_b"] <= 0.05 else 20; cc += 1
            elif p <= b["lower"]:
                sc_c += 20; cc += 1

            if r >= rsi_hi:
                sc_p += 25 if r >= 80 else 20; cp += 1
            if b["percent_b"] >= (1.0 - bb_th):
                sc_p += 25 if b["percent_b"] >= 0.95 else 20; cp += 1
            elif p >= b["upper"]:
                sc_p += 20; cp += 1
            if s and s["k"] >= (100 - stoch_th):
                sc_p += 20; cp += 1
                if s["cruce_bajista"]: sc_p += 5
            if w and w["rechazo_bajista"] and w["pct_mecha_sup"] >= wick_pct:
                sc_p += 15; cp += 1
                if w["es_bajista"]: sc_p += 5
            if sr and sr["resistencia"] < 999999 and abs(p - sr["resistencia"]) <= 0.00010:
                sc_p += 10; cp += 1
            if mc and not mc["hist_rising"]: sc_p += 5

            if sc_c >= min_sc and sc_p >= min_sc and abs(sc_c - sc_p) < conflict_mg:
                return False

            return sc_p >= min_sc and cp >= min_conds and sc_p > sc_c

        return fn, fn_put

    # Test configs
    configs = []

    # Legacy variants
    for ms in [55, 60, 65, 70, 75, 80, 85, 90, 95]:
        configs.append((f"L_{ms}", "legacy", ms, None))

    # Adaptive: (name, min_sc, min_conds, rsi_lo, rsi_hi, bb_th, stoch_th, wick_pct, sess, vol, conflict)
    for ms in [40, 45, 50, 55, 60, 65, 70]:
        for mc in [2, 3]:
            configs.append((f"A_{ms}_{mc}c", "adaptive", ms, (mc, 25, 75, 0.10, 20, 25, False, False, 15)))

    # With filters
    for ms in [40, 45, 50, 55, 60]:
        configs.append((f"A_{ms}_2c_S", "adaptive", ms, (2, 25, 75, 0.10, 20, 25, True, False, 15)))
        configs.append((f"A_{ms}_2c_V", "adaptive", ms, (2, 25, 75, 0.10, 20, 25, False, True, 15)))
        configs.append((f"A_{ms}_2c_SV", "adaptive", ms, (2, 25, 75, 0.10, 20, 25, True, True, 15)))

    # More extreme RSI
    for rlo, rhi in [(20, 80), (22, 78), (28, 72), (30, 70)]:
        for ms in [40, 45, 50]:
            configs.append((f"A_{ms}_2c_R{rlo}", "adaptive", ms, (2, rlo, rhi, 0.10, 20, 25, False, False, 15)))

    # No conflict filter
    for ms in [40, 45, 50, 55]:
        configs.append((f"A_{ms}_2c_NC", "adaptive", ms, (2, 25, 75, 0.10, 20, 25, False, False, 0)))

    print(f"Probando {len(configs)} configs...\n")
    sys.stdout.flush()
    print(f"{'Config':<20} | {'Train WR%':>9} {'Edge%':>7} {'N':>5} | {'Test WR%':>9} {'Edge%':>7} {'N':>5} | {'OOS PnL/trade':>12}")
    print("-" * 95)
    sys.stdout.flush()

    all_results = []

    for name, stype, min_sc, params in configs:
        if stype == "legacy":
            fn_c = legacy_check(min_sc, "C")
            fn_p = legacy_check(min_sc, "P")
        else:
            mc2, rlo, rhi, bbth, sth, wpct, sf, vf, cf = params
            fn_c, fn_p = adaptive_check(min_sc, mc2, rlo, rhi, bbth, sth, wpct, sf, vf, cf)

        # Train CALL
        wc, lc, tc, nc, wrc, ec = simulate(fn_c, "C", WU, sp)
        # Train PUT
        wp, lp, tp, np2, wrp, ep = simulate(fn_p, "P", WU, sp)
        # Merged train
        tr_w = wc + wp; tr_l = lc + lp; tr_n = nc + np2
        tr_wr = tr_w / (tr_w + tr_l) * 100 if (tr_w + tr_l) > 0 else 0
        tr_edge = tr_wr - BE

        # Test CALL
        wc2, lc2, tc2, nc2, wrc2, ec2 = simulate(fn_c, "C", sp, n)
        # Test PUT
        wp2, lp2, tp2, np22, wrp2, ep2 = simulate(fn_p, "P", sp, n)
        # Merged test
        te_w = wc2 + wp2; te_l = lc2 + lp2; te_n = nc2 + np22
        te_wr = te_w / (te_w + te_l) * 100 if (te_w + te_l) > 0 else 0
        te_edge = te_wr - BE

        pnl_per_trade = 0
        if te_n > 0:
            pnl_per_trade = ((te_w * 10 * 0.82) - (te_l * 10)) / te_n

        mark = " <<<" if tr_edge > 0 and te_edge > 0 and tr_n >= 20 and te_n >= 10 else ""
        print(f"{name:<20} | {tr_wr:>8.2f}% {tr_edge:>+6.2f}% {tr_n:>4} | {te_wr:>8.2f}% {te_edge:>+6.2f}% {te_n:>4} | ${pnl_per_trade:>+8.4f}{mark}")
        sys.stdout.flush()

        all_results.append((name, tr_wr, tr_edge, tr_n, te_wr, te_edge, te_n, pnl_per_trade))

    # Rankings
    print(f"\n{'='*95}")
    print(f"TOP 15 POR EDGE OUT-OF-SAMPLE (con N>=10):")
    print(f"{'='*95}")
    filtered = [r for r in all_results if r[6] >= 10]
    filtered.sort(key=lambda x: x[5], reverse=True)
    for name, tr_wr, tr_e, tr_n, te_wr, te_e, te_n, pnl in filtered[:15]:
        print(f"  {name:<20} Train: {tr_wr:.2f}% ({tr_e:+.2f}%, N={tr_n}) | Test: {te_wr:.2f}% ({te_e:+.2f}%, N={te_n}) | PnL/trade: ${pnl:+.4f}")
    sys.stdout.flush()

    print(f"\nCONSISTENTES (Edge>0 en ambos, N>=15 train, N>=8 test):")
    consistent = [r for r in all_results if r[2] > 0 and r[5] > 0 and r[3] >= 15 and r[6] >= 8]
    consistent.sort(key=lambda x: min(x[2], x[5]), reverse=True)
    if consistent:
        for name, tr_wr, tr_e, tr_n, te_wr, te_e, te_n, pnl in consistent:
            print(f"  {name:<20} Train: {tr_wr:.2f}% ({tr_e:+.2f}%, N={tr_n}) | Test: {te_wr:.2f}% ({te_e:+.2f}%, N={te_n})")
    else:
        print("  Ninguna configuracion logro edge positivo consistente en ambos periodos.")
    sys.stdout.flush()


if __name__ == "__main__":
    run()
