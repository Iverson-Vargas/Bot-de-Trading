"""
Optimizacion final: estrategias ultra-selectivas que combinan
Legacy alto umbral + confirmaciones adicionales (MACD, EMA trend, momentum).
Tambien prueba Martingale recovery y selectividad temporal.
"""
import os, sys, math, time
from datetime import datetime, timezone

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass

current_dir = os.path.dirname(os.path.abspath(__file__))
motor_dir = os.path.join(os.path.dirname(current_dir), "motor-logico")
sys.path.append(motor_dir)

from data_fetcher import load_candles_from_csv
from indicators import (
    calcular_rsi, calcular_bandas_bollinger, calcular_estocastico,
    analizar_patron_vela, calcular_soportes_resistencias, calcular_atr,
    calcular_ema, calcular_sma, calcular_macd, extraer_hora_utc
)

PAYOUT = 82.0
BE = (1.0 / (1.0 + PAYOUT / 100.0)) * 100.0
EXP = 1
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
    highs = [c.get("high", c.get("max", 0)) for c in candles]
    lows = [c.get("low", c.get("min", 0)) for c in candles]

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
    ema50_v = [None]*n
    ema20_v = [None]*n
    momentum_v = [None]*n

    hp, hc = [], []
    WU = 55
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
        ema50_v[i] = calcular_ema(hp, 50) if len(hp) >= 50 else None
        ema20_v[i] = calcular_ema(hp, 20)

        ts = candles[i].get("timestamp", 0)
        if ts > 0:
            try: hour_v[i] = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            except: pass

        # Momentum: price change over last 5 candles
        if i >= 5:
            momentum_v[i] = prices[i] - prices[i-5]

    print(f"Pre-comp: {time.time()-t0:.0f}s\n")
    sys.stdout.flush()

    def simulate(check_fn, start, end, cooldown=1, use_martingale=False):
        w = l = t = 0
        last = -cooldown - 1
        lim = min(end, n - EXP)
        balance = 10000.0
        stake = 10.0
        base_stake = 10.0
        consec_loss = 0
        max_dd = 0
        peak = balance

        for i in range(start, lim):
            if i - last < cooldown: continue
            sig = check_fn(i)
            if sig is None: continue
            direction = sig

            entry = prices[i]
            exit_p = prices[i + EXP]

            won = False
            if direction == "C":
                won = exit_p > entry
                lost = exit_p < entry
            else:
                won = exit_p < entry
                lost = exit_p > entry

            if won:
                w += 1
                balance += stake * (PAYOUT / 100.0)
                consec_loss = 0
                if use_martingale:
                    stake = base_stake
            elif lost:
                l += 1
                balance -= stake
                consec_loss += 1
                if use_martingale and consec_loss <= 3:
                    stake = base_stake * (2 ** consec_loss)
                    if stake > balance * 0.05:
                        stake = base_stake
                        consec_loss = 0
                elif use_martingale:
                    stake = base_stake
                    consec_loss = 0
            else:
                t += 1

            if balance > peak: peak = balance
            dd = peak - balance
            if dd > max_dd: max_dd = dd

            last = i

        dec = w + l
        wr = w / dec * 100.0 if dec > 0 else 0
        edge = wr - BE
        pnl = balance - 10000.0
        return w, l, t, dec, round(wr, 2), round(edge, 2), round(pnl, 2), round(max_dd, 2)

    # Strategy functions that return "C", "P", or None
    def legacy_enhanced(min_sc, macd_confirm=False, ema_trend=False, momentum_confirm=False,
                        session_hours=None, min_atr=None, max_atr=None):
        def fn(i):
            r = rsi_v[i]; b = bb_v[i]; s = stk_v[i]; w = wck_v[i]; sr = sr_v[i]
            if r is None or b is None: return None
            p = prices[i]

            # Filters
            if session_hours:
                h = hour_v[i]
                if h is not None and h not in session_hours: return None
            if min_atr or max_atr:
                a = atr_v[i]
                if a is not None:
                    if min_atr and a < min_atr: return None
                    if max_atr and a > max_atr: return None

            # CALL score
            sc_c = 0
            if p <= b["lower"]: sc_c += 30
            elif b["std"] > 0 and p <= b["lower"] + b["std"]*0.25: sc_c += 15
            if r <= 32: sc_c += 25
            elif r <= 38: sc_c += 10
            if s and (s["cruce_alcista"] or s["k"] <= 25): sc_c += 20
            if w and w["rechazo_alcista"]: sc_c += 20
            if sr and sr["soporte"] > 0 and abs(p - sr["soporte"]) <= 0.00015: sc_c += 10

            # PUT score
            sc_p = 0
            if p >= b["upper"]: sc_p += 30
            elif b["std"] > 0 and p >= b["upper"] - b["std"]*0.25: sc_p += 15
            if r >= 68: sc_p += 25
            elif r >= 62: sc_p += 10
            if s and (s["cruce_bajista"] or s["k"] >= 75): sc_p += 20
            if w and w["rechazo_bajista"]: sc_p += 20
            if sr and sr["resistencia"] < 999999 and abs(p - sr["resistencia"]) <= 0.00015: sc_p += 10

            direction = None
            if sc_c >= min_sc and sc_c > sc_p:
                direction = "C"
            elif sc_p >= min_sc and sc_p > sc_c:
                direction = "P"
            else:
                return None

            # MACD confirmation
            if macd_confirm:
                mc = macd_v[i]
                if mc:
                    if direction == "C" and not mc["hist_rising"]: return None
                    if direction == "P" and mc["hist_rising"]: return None

            # EMA trend confirmation
            if ema_trend:
                e50 = ema50_v[i]
                if e50 is not None:
                    if direction == "C" and p > e50: return None  # Mean reversion: price should be below EMA for CALL
                    if direction == "P" and p < e50: return None

            # Momentum confirmation (counter-trend)
            if momentum_confirm:
                mom = momentum_v[i]
                if mom is not None:
                    if direction == "C" and mom > 0: return None  # Only CALL after downward momentum
                    if direction == "P" and mom < 0: return None

            return direction
        return fn

    # Configs to test
    configs = []

    # Pure Legacy at high thresholds with different cooldowns
    for ms in [80, 85, 90, 95]:
        for cd in [1, 2, 3, 5]:
            configs.append((f"L{ms}_cd{cd}", legacy_enhanced(ms), cd, False))

    # Legacy + MACD
    for ms in [75, 80, 85, 90]:
        configs.append((f"L{ms}_MACD", legacy_enhanced(ms, macd_confirm=True), 1, False))
        configs.append((f"L{ms}_MACD_cd3", legacy_enhanced(ms, macd_confirm=True), 3, False))

    # Legacy + EMA trend (mean reversion with trend filter)
    for ms in [75, 80, 85]:
        configs.append((f"L{ms}_EMA", legacy_enhanced(ms, ema_trend=True), 1, False))

    # Legacy + Momentum
    for ms in [75, 80, 85]:
        configs.append((f"L{ms}_MOM", legacy_enhanced(ms, momentum_confirm=True), 1, False))

    # Combined: Legacy + MACD + session filter
    london_ny = set(range(7, 21))
    for ms in [80, 85, 90]:
        configs.append((f"L{ms}_MACD_S", legacy_enhanced(ms, macd_confirm=True, session_hours=london_ny), 1, False))

    # ATR filtered Legacy
    for ms in [80, 85]:
        configs.append((f"L{ms}_ATR", legacy_enhanced(ms, min_atr=0.00005, max_atr=0.00025), 1, False))
        configs.append((f"L{ms}_MACD_ATR", legacy_enhanced(ms, macd_confirm=True, min_atr=0.00005, max_atr=0.00025), 1, False))

    # With Martingale (limited to 3 steps)
    for ms in [80, 85, 90]:
        configs.append((f"L{ms}_MART", legacy_enhanced(ms), 1, True))
        configs.append((f"L{ms}_MACD_MART", legacy_enhanced(ms, macd_confirm=True), 1, True))

    # Ultra selective: all confirmations
    for ms in [75, 80, 85]:
        configs.append((f"L{ms}_ALL", legacy_enhanced(ms, macd_confirm=True, ema_trend=True, momentum_confirm=True), 1, False))
        configs.append((f"L{ms}_ALL_S", legacy_enhanced(ms, macd_confirm=True, ema_trend=True, momentum_confirm=True, session_hours=london_ny), 1, False))

    print(f"Probando {len(configs)} configs...\n")
    sys.stdout.flush()
    print(f"{'Config':<20} | {'Tr WR%':>7} {'Edge%':>7} {'N':>5} | {'Te WR%':>7} {'Edge%':>7} {'N':>5} | {'OOS PnL':>9} {'MaxDD':>8}")
    print("-" * 100)
    sys.stdout.flush()

    all_results = []
    WU2 = 55

    for name, fn, cd, mart in configs:
        w1, l1, t1, n1, wr1, e1, pnl1, dd1 = simulate(fn, WU2, sp, cd, mart)
        w2, l2, t2, n2, wr2, e2, pnl2, dd2 = simulate(fn, sp, n, cd, mart)

        mark = ""
        if e1 > 0 and e2 > 0 and n1 >= 15 and n2 >= 8:
            mark = " <<<"
        elif e2 > -1.0 and n2 >= 20:
            mark = " *"

        print(f"{name:<20} | {wr1:>6.2f}% {e1:>+6.2f}% {n1:>4} | {wr2:>6.2f}% {e2:>+6.2f}% {n2:>4} | ${pnl2:>+8.2f} ${dd2:>7.2f}{mark}")
        sys.stdout.flush()

        all_results.append((name, wr1, e1, n1, wr2, e2, n2, pnl2, dd2, mart))

    # Rankings
    print(f"\n{'='*100}")
    print(f"TOP 20 POR EDGE OUT-OF-SAMPLE (N>=5):")
    print(f"{'='*100}")
    filtered = [r for r in all_results if r[6] >= 5]
    filtered.sort(key=lambda x: x[5], reverse=True)
    for name, tr_wr, tr_e, tr_n, te_wr, te_e, te_n, pnl, dd, mart in filtered[:20]:
        m = " [MART]" if mart else ""
        print(f"  {name:<20} Train: {tr_wr:.2f}% ({tr_e:+.2f}%, N={tr_n}) | Test: {te_wr:.2f}% ({te_e:+.2f}%, N={te_n}) | PnL: ${pnl:+.2f} DD: ${dd:.2f}{m}")
    sys.stdout.flush()

    print(f"\nCONSISTENTES (Edge>0 en ambos periodos):")
    consistent = [r for r in all_results if r[2] > 0 and r[5] > 0 and r[3] >= 10 and r[6] >= 5]
    consistent.sort(key=lambda x: min(x[2], x[5]), reverse=True)
    if consistent:
        for name, tr_wr, tr_e, tr_n, te_wr, te_e, te_n, pnl, dd, mart in consistent:
            m = " [MART]" if mart else ""
            print(f"  {name:<20} Train: {tr_wr:.2f}% ({tr_e:+.2f}%, N={tr_n}) | Test: {te_wr:.2f}% ({te_e:+.2f}%, N={te_n}) | PnL: ${pnl:+.2f}{m}")
    else:
        print("  Ninguna configuracion logro edge positivo consistente.")
    sys.stdout.flush()

    # Best for Martingale (PnL based)
    print(f"\nMEJORES CON MARTINGALE (por PnL OOS):")
    mart_results = [r for r in all_results if r[9] and r[6] >= 5]
    mart_results.sort(key=lambda x: x[7], reverse=True)
    for name, tr_wr, tr_e, tr_n, te_wr, te_e, te_n, pnl, dd, mart in mart_results[:10]:
        print(f"  {name:<20} Test: {te_wr:.2f}% ({te_e:+.2f}%, N={te_n}) | PnL: ${pnl:+.2f} | MaxDD: ${dd:.2f}")
    sys.stdout.flush()


if __name__ == "__main__":
    run()
