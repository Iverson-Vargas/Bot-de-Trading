"""
Motor Lógico Cuantitativo de Trading - Servicio FastAPI
Procesa datos de mercado, evalúa estrategias cuantitativas, calcula dimensionamiento dinámico
por riesgo, almacena operaciones estructuradas y expone panel de métricas en tiempo real.
"""

import os
import sys
import time
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

# Añadir directorio actual al path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from indicators import (
    calcular_rsi,
    calcular_bandas_bollinger,
    calcular_estocastico,
    analizar_patron_vela,
    calcular_soportes_resistencias,
    calcular_atr,
    calcular_ema
)
from strategy import BaseStrategy, LegacyConfluenceStrategy, CalibratedStatisticalStrategy, StrategyDecision
from risk_manager import RiskManager, RiskMode
from storage import TradeStorage

app = FastAPI(
    title="Motor Lógico Cuantitativo de Trading",
    description="Motor de señales de alta confluencia y gestión de riesgo para EUR/USD Blitz"
)

# Modelos Pydantic
class MarketData(BaseModel):
    active_id: int
    price: float
    timestamp: Optional[int] = None
    open: Optional[float] = None
    close: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    volume: Optional[float] = None
    session_stats: Optional[Dict[str, Any]] = None

class ResultadoOperacion(BaseModel):
    id: str
    resultado: str # 'GANADA', 'PERDIDA', 'EMPATE'
    ganancia_neta: float
    direccion: str # 'CALL', 'PUT'
    precio_entrada: float
    precio_salida: float
    balance_actual: Optional[float] = None
    order_id: Optional[str] = None
    payout_percent: Optional[float] = 82.0
    account_type: Optional[str] = "DEMO"
    account_id: Optional[str] = ""

class StrategySwitchRequest(BaseModel):
    strategy_name: str # 'calibrated' o 'legacy'
    risk_mode: Optional[str] = None # 'fixed_amount', 'fixed_percentage', 'fractional_kelly'

# ==========================================
# ESTADO GLOBAL DEL MOTOR
# ==========================================
# Inicializar almacenamiento estructurado
storage = TradeStorage()

# Inicializar gestor de riesgo dinámico (Quarter Kelly con límites seguros)
risk_manager = RiskManager(
    mode=RiskMode.FRACTIONAL_KELLY,
    fixed_amount=10.0,
    fixed_percentage=1.5,
    kelly_fraction=0.25,
    max_stake_percent=2.5,
    min_stake_amount=1.0,
    max_stake_amount=50.0,
    daily_stop_loss=-50.0,
    daily_take_profit=100.0,
    max_consecutive_losses=4,
    loss_streak_reduction=True
)

# Estrategias disponibles (por defecto: Calibrada Cuantitativa)
strategies: Dict[str, BaseStrategy] = {
    "calibrated": CalibratedStatisticalStrategy(min_score=65.0),
    "legacy": LegacyConfluenceStrategy(min_score=65.0)
}
active_strategy_name = "calibrated"
active_strategy: BaseStrategy = strategies[active_strategy_name]

# Memoria de mercado
historial_precios: List[float] = []
historial_velas: List[Dict[str, float]] = []
ultima_operacion_timestamp: float = 0.0
COOLDOWN_SECONDS: int = 40
calentamiento_anunciado: bool = False

# Sesión en curso
session_id: str = f"session_{int(time.time())}"
session_stats = {
    "ganadas": 0,
    "perdidas": 0,
    "empates": 0,
    "balance_neto": 0.0,
    "consecutive_wins": 0,
    "consecutive_losses": 0,
    "saldo_actual": 10000.0
}

# Snapshot de última decisión para asociar indicadores con resultados
ultimos_indicadores_por_precio: Dict[str, Any] = {}


# ==========================================
# ENDPOINTS PRINCIPALES
# ==========================================

@app.get("/")
def health_check():
    metrics = storage.get_metrics_summary(payout_percent=82.0)
    return {
        "status": "online",
        "service": "Motor Lógico Cuantitativo de Trading (FastAPI)",
        "active_strategy": active_strategy_name,
        "risk_mode": str(risk_manager.mode),
        "candles_in_memory": len(historial_precios),
        "session_summary": {
            "ganadas": session_stats["ganadas"],
            "perdidas": session_stats["perdidas"],
            "empates": session_stats["empates"],
            "balance_neto": round(session_stats["balance_neto"], 2),
            "racha_perdidas": session_stats["consecutive_losses"],
            "racha_ganadas": session_stats["consecutive_wins"]
        },
        "all_time_metrics": metrics
    }


@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard():
    """Sirve el panel de control interactivo en vivo."""
    html_path = os.path.join(current_dir, "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Dashboard template no encontrado")


@app.get("/metrics/live")
def get_live_metrics(payout: float = Query(82.0, description="Payout configurado")):
    """
    Retorna métricas completas en tiempo real:
    - Win rate de la sesión vs global
    - Punto de equilibrio matemático (54.95% con 82% payout)
    - Diferencial de ventaja (Edge)
    - PnL y estado de gestión de riesgo
    """
    global_metrics = storage.get_metrics_summary(payout_percent=payout)
    
    # Calcular métricas específicas de la sesión actual
    session_decisivas = session_stats["ganadas"] + session_stats["perdidas"]
    session_win_rate = (session_stats["ganadas"] / session_decisivas * 100.0) if session_decisivas > 0 else 0.0
    
    break_even_wr = (1.0 / (1.0 + (payout / 100.0))) * 100.0
    session_edge = session_win_rate - break_even_wr if session_decisivas > 0 else 0.0
    
    # Cálculo de stake sugerido actual
    current_stake, stake_reason = risk_manager.calculate_stake(
        current_balance=session_stats["saldo_actual"],
        consecutive_losses=session_stats["consecutive_losses"],
        consecutive_wins=session_stats["consecutive_wins"],
        estimated_win_rate=0.56,
        payout_percent=payout
    )

    return {
        "status": "active",
        "timestamp": int(time.time()),
        "active_strategy": active_strategy_name,
        "payout_percent": payout,
        "break_even_win_rate": round(break_even_wr, 2),
        
        # Métricas de Sesión
        "session": {
            "total_trades": session_decisivas + session_stats["empates"],
            "ganadas": session_stats["ganadas"],
            "perdidas": session_stats["perdidas"],
            "empates": session_stats["empates"],
            "win_rate": round(session_win_rate, 2),
            "edge": round(session_edge, 2),
            "has_edge": session_edge > 0 and session_decisivas >= 5,
            "pnl_neto": round(session_stats["balance_neto"], 2),
            "racha_perdidas": session_stats["consecutive_losses"],
            "racha_ganadas": session_stats["consecutive_wins"]
        },
        
        # Métricas Históricas Globales
        "global": global_metrics,
        "win_rate_global": global_metrics["win_rate"],
        "edge_vs_breakeven": global_metrics["edge"],
        "pnl_neto": global_metrics["pnl_neto"],
        "profit_factor": global_metrics["profit_factor"],
        "total_trades": global_metrics["total_trades"],
        "ganadas": global_metrics["ganadas"],
        "perdidas": global_metrics["perdidas"],
        "empates": global_metrics["empates"],
        
        # Parámetros de Riesgo
        "risk": {
            "mode": str(risk_manager.mode),
            "current_stake": current_stake,
            "stake_reason": stake_reason,
            "saldo_actual": session_stats["saldo_actual"],
            "daily_stop_loss": risk_manager.daily_stop_loss,
            "daily_take_profit": risk_manager.daily_take_profit
        }
    }


@app.get("/trades")
def get_trades(limit: int = Query(50, ge=1, le=200)):
    """Retorna las operaciones más recientes con sus indicadores."""
    return storage.get_recent_trades(limit=limit)


@app.post("/analizar")
async def analizar_mercado(data: MarketData):
    """
    Evalúa la vela entrante contra la estrategia activa y calcula la orden con gestión de riesgo.
    """
    global ultima_operacion_timestamp, calentamiento_anunciado, ultimos_indicadores_por_precio

    # 1. Actualizar balance si viene del broker
    if data.session_stats:
        saldo = data.session_stats.get("saldo_actual")
        if saldo is not None and saldo > 0:
            session_stats["saldo_actual"] = float(saldo)

    # 2. Almacenar vela y precio
    precio_actual = data.close if data.close is not None else data.price
    historial_precios.append(precio_actual)

    nueva_vela = {
        "open": data.open if data.open is not None else precio_actual,
        "close": data.close if data.close is not None else precio_actual,
        "min": data.min if data.min is not None else min(precio_actual, data.open if data.open else precio_actual),
        "max": data.max if data.max is not None else max(precio_actual, data.open if data.open else precio_actual),
        "volume": data.volume if data.volume is not None else 0.0,
        "timestamp": data.timestamp or int(time.time())
    }
    historial_velas.append(nueva_vela)

    if len(historial_precios) > 200:
        historial_precios.pop(0)
    if len(historial_velas) > 200:
        historial_velas.pop(0)

    # 3. Evaluar Calentamiento de Indicadores
    min_warmup = active_strategy.min_candles_warmup
    if len(historial_precios) < min_warmup:
        progreso = len(historial_precios)
        porcentaje = int((progreso / min_warmup) * 100)
        return {
            "action": "hold",
            "amount": 0.0,
            "active_id": data.active_id,
            "status": f"⏳ Calibrando indicadores ({progreso}/{min_warmup} velas - {porcentaje}%)...",
            "warmup": True
        }

    if not calentamiento_anunciado:
        calentamiento_anunciado = True
        print(f"\n[PYTHON] 🚀 ¡CALIBRACIÓN COMPLETADA ({min_warmup} velas)!")
        print(f"         Estrategia Activa: {active_strategy.name} (Puntaje Requerido: >={active_strategy.min_score:.0f} pts)\n")

    # 4. Evaluar Cooldown
    tiempo_actual = time.time()
    segundos_desde_ultima = tiempo_actual - ultima_operacion_timestamp
    en_cooldown = segundos_desde_ultima < COOLDOWN_SECONDS

    # 5. Evaluar Estrategia Cuantitativa
    decision: StrategyDecision = active_strategy.evaluate(
        prices=historial_precios,
        candles=historial_velas,
        session_stats=session_stats
    )

    # Guardar snapshot de indicadores
    ultimos_indicadores_por_precio = {
        "timestamp": tiempo_actual,
        "price": precio_actual,
        "decision": decision,
        "indicators": decision.indicators
    }

    # 6. Evaluar Permiso y Dimensionamiento de Riesgo
    risk_check = risk_manager.check_trade_permission(
        session_pnl=session_stats["balance_neto"],
        current_balance=session_stats["saldo_actual"],
        consecutive_losses=session_stats["consecutive_losses"],
        consecutive_wins=session_stats["consecutive_wins"],
        estimated_win_rate=0.56,
        payout_percent=82.0
    )

    if not risk_check.allowed:
        return {
            "action": "hold",
            "amount": 0.0,
            "active_id": data.active_id,
            "motivo": risk_check.reason,
            "is_halted": risk_check.is_halted,
            "cooldown_active": True,
            "score_call": decision.score_call,
            "score_put": decision.score_put
        }

    # 7. Determinar Acción Final
    accion_final = "hold"
    motivo_final = decision.reason_summary

    if decision.action in ("call", "put"):
        if not en_cooldown:
            accion_final = decision.action
            ultima_operacion_timestamp = tiempo_actual
            tag_color = "🟢 CALL" if accion_final == "call" else "🔴 PUT"
            print(f"\n[PYTHON] {tag_color} DISPARADO [{decision.score_call:.0f}C / {decision.score_put:.0f}P]")
            print(f"         Monto Dinámico: ${risk_check.amount:.2f} USD ({risk_check.reason})")
            print(f"         {decision.reason_summary}\n")
        else:
            motivo_final = f"Señal {decision.action.upper()} ignorada por Cooldown ({int(COOLDOWN_SECONDS - segundos_desde_ultima)}s restantes)"

    # Extraer indicadores para telemetría
    ind = decision.indicators
    return {
        "action": accion_final,
        "amount": risk_check.amount if accion_final != "hold" else 0.0,
        "active_id": data.active_id,
        "price": precio_actual,
        "rsi": ind.get("rsi"),
        "bollinger": ind.get("bollinger"),
        "estocastico": ind.get("estocastico"),
        "patron_vela": ind.get("patron_vela"),
        "score_call": decision.score_call,
        "score_put": decision.score_put,
        "motivo": motivo_final,
        "strategy": active_strategy.name,
        "racha_perdidas": session_stats["consecutive_losses"],
        "racha_ganadas": session_stats["consecutive_wins"],
        "cooldown_active": en_cooldown
    }


@app.post("/resultado-operacion")
async def registrar_resultado(trade: ResultadoOperacion):
    """
    Recibe el veredicto del broker, actualiza estadísticas en memoria y guarda
    en SQLite y CSV con su snapshot completo de indicadores.
    """
    global session_stats

    # Actualizar rachas y PnL de sesión
    if trade.resultado == "GANADA":
        session_stats["ganadas"] += 1
        session_stats["consecutive_wins"] += 1
        session_stats["consecutive_losses"] = 0
        print(f"\n[PYTHON] 🧠 RESULTADO: Operación #{trade.id} GANADA (+${trade.ganancia_neta:.2f}). Racha: {session_stats['consecutive_wins']}W.\n")
    elif trade.resultado == "PERDIDA":
        session_stats["perdidas"] += 1
        session_stats["consecutive_losses"] += 1
        session_stats["consecutive_wins"] = 0
        print(f"\n[PYTHON] 🧠 RESULTADO: Operación #{trade.id} PERDIDA (-${abs(trade.ganancia_neta):.2f}). Racha: {session_stats['consecutive_losses']}L.\n")
    else:
        session_stats["empates"] += 1
        print(f"\n[PYTHON] 🧠 RESULTADO: Operación #{trade.id} en EMPATE ($0.00).\n")

    session_stats["balance_neto"] += trade.ganancia_neta
    if trade.balance_actual:
        session_stats["saldo_actual"] = trade.balance_actual

    # Obtener snapshot de indicadores correspondiente
    indicators_to_save = ultimos_indicadores_por_precio.get("indicators")
    decision_obj = ultimos_indicadores_por_precio.get("decision")
    score_call = decision_obj.score_call if decision_obj else 0.0
    score_put = decision_obj.score_put if decision_obj else 0.0
    signal_reasons = decision_obj.reason_summary if decision_obj else ""

    # Persistencia Estructurada (SQLite + CSV)
    trade_db_id = f"{session_id}_ord_{trade.id}_{int(time.time())}"
    storage.save_trade(
        trade_id=trade_db_id,
        order_id=trade.order_id or trade.id,
        session_id=session_id,
        direction=trade.direccion,
        amount=abs(trade.ganancia_neta) if trade.resultado == "PERDIDA" else (trade.ganancia_neta / 0.82 if trade.resultado == "GANADA" else 10.0),
        entry_price=trade.precio_entrada,
        exit_price=trade.precio_salida,
        outcome=trade.resultado,
        net_profit=trade.ganancia_neta,
        balance_post=trade.balance_actual or session_stats["saldo_actual"],
        account_type=trade.account_type or "DEMO",
        account_id=trade.account_id or "",
        payout_percent=trade.payout_percent or 82.0,
        strategy_name=active_strategy.name,
        score_call=score_call,
        score_put=score_put,
        signal_reasons=signal_reasons,
        indicators=indicators_to_save
    )

    return {
        "status": "ok",
        "session_summary": session_stats,
        "total_recorded_trades": storage.get_metrics_summary()["total_trades"]
    }


@app.post("/reiniciar-sesion")
async def reiniciar_sesion():
    """Reinicia contadores de rachas y abre una nueva sesión de registro."""
    global session_id, session_stats
    session_id = f"session_{int(time.time())}"
    session_stats = {
        "ganadas": 0,
        "perdidas": 0,
        "empates": 0,
        "balance_neto": 0.0,
        "consecutive_wins": 0,
        "consecutive_losses": 0,
        "saldo_actual": session_stats["saldo_actual"]
    }
    print(f"\n[PYTHON] 🔄 SESIÓN REINICIADA: Rachas y contadores puestos a cero. Nueva sesión: {session_id}\n")
    return {"status": "ok", "session_id": session_id}


@app.post("/cambiar-estrategia")
async def cambiar_estrategia(req: StrategySwitchRequest):
    """Permite alternar en caliente entre la estrategia Calibrada y la Legacy."""
    global active_strategy_name, active_strategy
    strat_key = req.strategy_name.lower().strip()
    if strat_key not in strategies:
        raise HTTPException(status_code=400, detail=f"Estrategia inválida. Opciones: {list(strategies.keys())}")

    active_strategy_name = strat_key
    active_strategy = strategies[strat_key]

    if req.risk_mode:
        try:
            risk_manager.mode = RiskMode(req.risk_mode.lower())
        except ValueError:
            pass

    print(f"\n[PYTHON] ⚙️ CONFIGURACIÓN ACTUALIZADA: Estrategia = {active_strategy.name} | Riesgo = {risk_manager.mode}\n")
    return {
        "status": "ok",
        "active_strategy": active_strategy.name,
        "risk_mode": str(risk_manager.mode)
    }