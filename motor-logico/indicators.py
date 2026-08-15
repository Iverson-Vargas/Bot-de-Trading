"""
Módulo de Indicadores Técnicos y Acción del Precio
Implementación matemática pura (zero-dependency) optimizada para alta velocidad.
"""

import math
from typing import List, Dict, Any, Optional, Tuple


def calcular_sma(precios: List[float], periodo: int) -> Optional[float]:
    """Calcula la Media Móvil Simple (SMA)."""
    if len(precios) < periodo or periodo <= 0:
        return None
    return sum(precios[-periodo:]) / periodo


def calcular_ema(precios: List[float], periodo: int, smoothing: float = 2.0) -> Optional[float]:
    """Calcula la Media Móvil Exponencial (EMA)."""
    if len(precios) < periodo or periodo <= 0:
        return None
    k = smoothing / (periodo + 1.0)
    ema = sum(precios[:periodo]) / periodo
    for precio in precios[periodo:]:
        ema = (precio * k) + (ema * (1.0 - k))
    return ema


def calcular_desviacion_estandar(precios: List[float], periodo: int, media: Optional[float] = None) -> Optional[float]:
    """Calcula la desviación estándar poblacional de los últimos 'periodo' precios."""
    if len(precios) < periodo or periodo <= 0:
        return None
    if media is None:
        media = sum(precios[-periodo:]) / periodo
    varianza = sum((p - media) ** 2 for p in precios[-periodo:]) / periodo
    return math.sqrt(varianza)


def calcular_bandas_bollinger(
    precios: List[float],
    periodo: int = 20,
    num_std: float = 2.0
) -> Optional[Dict[str, float]]:
    """
    Calcula las Bandas de Bollinger:
    - middle (SMA)
    - upper (SMA + k*std)
    - lower (SMA - k*std)
    - std (Desviación estándar)
    - bandwidth: ((upper - lower) / middle) * 100
    - percent_b: (precio - lower) / (upper - lower)
    """
    if len(precios) < periodo or periodo <= 0:
        return None
    sma = calcular_sma(precios, periodo)
    if sma is None or sma <= 0:
        return None
    std = calcular_desviacion_estandar(precios, periodo, sma)
    if std is None:
        return None

    upper = sma + (num_std * std)
    lower = sma - (num_std * std)
    bandwidth = ((upper - lower) / sma) * 100.0 if sma > 0 else 0.0
    
    precio_actual = precios[-1]
    ancho = upper - lower
    percent_b = ((precio_actual - lower) / ancho) if ancho > 0 else 0.5

    return {
        "middle": round(sma, 6),
        "upper": round(upper, 6),
        "lower": round(lower, 6),
        "std": round(std, 6),
        "bandwidth": round(bandwidth, 4),
        "percent_b": round(percent_b, 4)
    }


def calcular_rsi(precios: List[float], periodo: int = 14) -> Optional[float]:
    """
    Calcula el RSI (Relative Strength Index) utilizando el método de Wilder.
    Rango: 0 a 100.
    """
    if len(precios) <= periodo or periodo <= 0:
        return None

    cambios = [precios[i] - precios[i - 1] for i in range(1, len(precios))]
    
    # Inicialización con SMA de las primeras N variaciones
    ganancias_iniciales = [c for c in cambios[:periodo] if c > 0]
    perdidas_iniciales = [-c for c in cambios[:periodo] if c < 0]

    avg_gain = sum(ganancias_iniciales) / periodo if ganancias_iniciales else 0.0
    avg_loss = sum(perdidas_iniciales) / periodo if perdidas_iniciales else 0.0

    # Suavizado de Wilder a lo largo del resto de la serie
    for c in cambios[periodo:]:
        gain = c if c > 0 else 0.0
        loss = -c if c < 0 else 0.0
        avg_gain = (avg_gain * (periodo - 1) + gain) / periodo
        avg_loss = (avg_loss * (periodo - 1) + loss) / periodo

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


def calcular_estocastico(
    velas: List[Dict[str, float]],
    periodo_k: int = 14,
    smooth_k: int = 3,
    periodo_d: int = 3,
    oversold: float = 25.0,
    overbought: float = 75.0
) -> Optional[Dict[str, Any]]:
    """
    Calcula el Oscilador Estocástico Lento (%K, %D).
    Retorna %K actual, %D actual, cruce alcista y cruce bajista.
    """
    min_requerido = periodo_k + smooth_k + periodo_d
    if len(velas) < min_requerido:
        return None

    fast_k_serie = []
    for i in range(len(velas) - (smooth_k + periodo_d) + 1, len(velas) + 1):
        sub_velas = velas[max(0, i - periodo_k) : i]
        if not sub_velas:
            continue
        lowest_low = min(v.get("min", v.get("low", v.get("close", 0.0))) for v in sub_velas)
        highest_high = max(v.get("max", v.get("high", v.get("close", 0.0))) for v in sub_velas)
        current_close = sub_velas[-1].get("close", 0.0)

        if highest_high == lowest_low:
            fast_k = 50.0
        else:
            fast_k = ((current_close - lowest_low) / (highest_high - lowest_low)) * 100.0
        fast_k_serie.append(fast_k)

    if len(fast_k_serie) < smooth_k + periodo_d:
        return None

    k_smooth_serie = []
    for j in range(len(fast_k_serie) - periodo_d + 1, len(fast_k_serie) + 1):
        sub_k = fast_k_serie[max(0, j - smooth_k) : j]
        k_smooth_serie.append(sum(sub_k) / len(sub_k))

    if len(k_smooth_serie) < periodo_d:
        return None

    k_actual = k_smooth_serie[-1]
    d_actual = sum(k_smooth_serie[-periodo_d:]) / periodo_d

    k_previo = k_smooth_serie[-2] if len(k_smooth_serie) >= 2 else k_actual
    d_previo = sum(k_smooth_serie[-periodo_d - 1 : -1]) / periodo_d if len(k_smooth_serie) > periodo_d else d_actual

    # Detección de cruces válidos o condición en extremos
    cruce_alcista = (k_previo <= d_previo and k_actual > d_actual) or (k_actual > d_actual and k_actual <= oversold)
    cruce_bajista = (k_previo >= d_previo and k_actual < d_actual) or (k_actual < d_actual and k_actual >= overbought)

    return {
        "k": round(k_actual, 2),
        "d": round(d_actual, 2),
        "k_prev": round(k_previo, 2),
        "d_prev": round(d_previo, 2),
        "cruce_alcista": cruce_alcista,
        "cruce_bajista": cruce_bajista
    }


def analizar_patron_vela(vela: Dict[str, float]) -> Dict[str, Any]:
    """
    Analiza la morfología de una vela:
    - Mechas superior e inferior relativas (%)
    - Tamaño del cuerpo vs rango total
    - Detección de patrones de rechazo (Pinbar, Martillo / Shooting star)
    """
    op = vela.get("open", vela.get("close", 0.0))
    cl = vela.get("close", op)
    hi = vela.get("max", vela.get("high", max(op, cl)))
    lo = vela.get("min", vela.get("low", min(op, cl)))

    rango_total = hi - lo
    if rango_total <= 1e-9:
        return {
            "rechazo_alcista": False,
            "rechazo_bajista": False,
            "pct_mecha_inf": 0.0,
            "pct_mecha_sup": 0.0,
            "pct_cuerpo": 100.0,
            "es_alcista": cl >= op,
            "es_bajista": cl < op,
            "es_doji": True
        }

    cuerpo = abs(cl - op)
    mecha_superior = hi - max(op, cl)
    mecha_inferior = min(op, cl) - lo

    pct_mecha_inf = mecha_inferior / rango_total
    pct_mecha_sup = mecha_superior / rango_total
    pct_cuerpo = cuerpo / rango_total

    # Rechazo significativo (mecha >= 25% del rango total y mecha dominante)
    rechazo_alcista = pct_mecha_inf >= 0.25 and (pct_mecha_inf > pct_mecha_sup * 1.2 or pct_mecha_inf >= 0.40)
    rechazo_bajista = pct_mecha_sup >= 0.25 and (pct_mecha_sup > pct_mecha_inf * 1.2 or pct_mecha_sup >= 0.40)

    return {
        "rechazo_alcista": rechazo_alcista,
        "rechazo_bajista": rechazo_bajista,
        "pct_mecha_inf": round(pct_mecha_inf * 100, 1),
        "pct_mecha_sup": round(pct_mecha_sup * 100, 1),
        "pct_cuerpo": round(pct_cuerpo * 100, 1),
        "es_alcista": cl > op,
        "es_bajista": cl < op,
        "es_doji": pct_cuerpo <= 0.10
    }


def calcular_soportes_resistencias(velas: List[Dict[str, float]], lookback: int = 30) -> Dict[str, float]:
    """Calcula los niveles extremos de soporte y resistencia dentro de la ventana lookback."""
    if len(velas) < 5:
        return {"soporte": 0.0, "resistencia": 999999.0}
    sub = velas[-lookback:]
    soporte = min(v.get("min", v.get("low", v.get("close", 0.0))) for v in sub)
    resistencia = max(v.get("max", v.get("high", v.get("close", 0.0))) for v in sub)
    return {
        "soporte": round(soporte, 6),
        "resistencia": round(resistencia, 6)
    }


def calcular_atr(velas: List[Dict[str, float]], periodo: int = 14) -> Optional[float]:
    """Calcula el Average True Range (ATR)."""
    if len(velas) < periodo + 1:
        return None

    true_ranges = []
    for i in range(1, len(velas)):
        hi = velas[i].get("max", velas[i].get("high", velas[i].get("close", 0.0)))
        lo = velas[i].get("min", velas[i].get("low", velas[i].get("close", 0.0)))
        prev_cl = velas[i - 1].get("close", 0.0)

        tr = max(hi - lo, abs(hi - prev_cl), abs(lo - prev_cl))
        true_ranges.append(tr)

    if len(true_ranges) < periodo:
        return None

    atr = sum(true_ranges[-periodo:]) / periodo
    return round(atr, 6)
