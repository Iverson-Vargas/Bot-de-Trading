"""
Motor Logico Cuantitativo de Trading - Servicio FastAPI
Procesa datos de mercado, evalua estrategias cuantitativas, calcula dimensionamiento dinamico
por riesgo, almacena operaciones estructuradas y expone panel de control con gestion del bot.
"""

import os
import sys
import time
import threading
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
from strategy import BaseStrategy, LegacyConfluenceStrategy, AdaptiveEdgeStrategy, CalibratedStatisticalStrategy, StrategyDecision
from risk_manager import RiskManager, RiskMode
from storage import TradeStorage
from process_manager import bot_manager

app = FastAPI(
    title="Motor Logico Cuantitativo de Trading",
    description="Motor de senales de alta confluencia y gestion de riesgo para EUR/USD Blitz"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# MODELOS PYDANTIC
# ==========================================

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
    resultado: str
    ganancia_neta: float
    direccion: str
    precio_entrada: float
    precio_salida: float
    balance_actual: Optional[float] = None
    order_id: Optional[str] = None
    payout_percent: Optional[float] = 82.0
    account_type: Optional[str] = "DEMO"
    account_id: Optional[str] = ""

class StrategySwitchRequest(BaseModel):
    strategy_name: str
    risk_mode: Optional[str] = None


# ==========================================
# ESTADO GLOBAL DEL MOTOR (protegido con lock)
# ==========================================

_state_lock = threading.Lock()

storage = TradeStorage()

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

strategies: Dict[str, BaseStrategy] = {
    "calibrated": AdaptiveEdgeStrategy(min_score=85.0, use_macd_confirmation=True, use_session_filter=False),
    "legacy": LegacyConfluenceStrategy(min_score=65.0)
}
active_strategy_name = "calibrated"
active_strategy: BaseStrategy = strategies[active_strategy_name]

historial_precios: List[float] = []
historial_velas: List[Dict[str, float]] = []
ultima_operacion_timestamp: float = 0.0
COOLDOWN_SECONDS: int = 40
calentamiento_anunciado: bool = False

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

ultimos_indicadores_por_precio: Dict[str, Any] = {}


# ==========================================
# ENDPOINTS DE CONTROL DEL BOT
# ==========================================

@app.get("/")
def health_check():
    bot_status = bot_manager.get_status()
    metrics = storage.get_metrics_summary(payout_percent=82.0)
    return {
        "status": "online",
        "service": "Motor Logico Cuantitativo de Trading (FastAPI)",
        "bot": bot_status,
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


@app.post("/bot/start")
async def start_bot():
    """Inicia el proceso del broker (transit.js)."""
    result = bot_manager.start()
    status_code = 200 if result["success"] else 409
    return JSONResponse(content=result, status_code=status_code)


@app.post("/bot/stop")
async def stop_bot():
    """Detiene el proceso del broker (transit.js)."""
    result = bot_manager.stop()
    return JSONResponse(content=result)


@app.post("/bot/restart")
async def restart_bot():
    """Reinicia el proceso del broker."""
    result = bot_manager.restart()
    status_code = 200 if result["success"] else 500
    return JSONResponse(content=result, status_code=status_code)


@app.get("/bot/status")
def get_bot_status():
    """Estado actual del proceso del broker."""
    return bot_manager.get_status()


@app.get("/bot/logs")
def get_bot_logs(lines: int = Query(100, ge=1, le=500)):
    """Ultimas lineas del log del broker."""
    return {"logs": bot_manager.get_logs(last_n=lines)}


# ==========================================
# PANEL DE CONTROL WEB
# ==========================================

@app.get("/panel", response_class=HTMLResponse)
def get_control_panel():
    """Sirve la interfaz web de control del bot."""
    html_path = os.path.join(current_dir, "control_panel.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Panel de control no encontrado")


@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard():
    """Sirve el panel de metricas legado."""
    html_path = os.path.join(current_dir, "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Dashboard no encontrado")


# ==========================================
# ENDPOINTS DE METRICAS Y DATOS
# ==========================================

@app.get("/metrics/live")
def get_live_metrics(payout: float = Query(82.0, description="Payout configurado")):
    global_metrics = storage.get_metrics_summary(payout_percent=payout)

    with _state_lock:
        stats_copy = dict(session_stats)

    session_decisivas = stats_copy["ganadas"] + stats_copy["perdidas"]
    session_win_rate = (stats_copy["ganadas"] / session_decisivas * 100.0) if session_decisivas > 0 else 0.0

    break_even_wr = (1.0 / (1.0 + (payout / 100.0))) * 100.0
    session_edge = session_win_rate - break_even_wr if session_decisivas > 0 else 0.0

    current_stake, stake_reason = risk_manager.calculate_stake(
        current_balance=stats_copy["saldo_actual"],
        consecutive_losses=stats_copy["consecutive_losses"],
        consecutive_wins=stats_copy["consecutive_wins"],
        estimated_win_rate=0.56,
        payout_percent=payout
    )

    return {
        "status": "active",
        "timestamp": int(time.time()),
        "active_strategy": active_strategy_name,
        "payout_percent": payout,
        "break_even_win_rate": round(break_even_wr, 2),
        "bot": bot_manager.get_status(),
        "session": {
            "total_trades": session_decisivas + stats_copy["empates"],
            "ganadas": stats_copy["ganadas"],
            "perdidas": stats_copy["perdidas"],
            "empates": stats_copy["empates"],
            "win_rate": round(session_win_rate, 2),
            "edge": round(session_edge, 2),
            "has_edge": session_edge > 0 and session_decisivas >= 5,
            "pnl_neto": round(stats_copy["balance_neto"], 2),
            "racha_perdidas": stats_copy["consecutive_losses"],
            "racha_ganadas": stats_copy["consecutive_wins"]
        },
        "global": global_metrics,
        "win_rate_global": global_metrics["win_rate"],
        "edge_vs_breakeven": global_metrics["edge"],
        "pnl_neto": global_metrics["pnl_neto"],
        "profit_factor": global_metrics["profit_factor"],
        "total_trades": global_metrics["total_trades"],
        "ganadas": global_metrics["ganadas"],
        "perdidas": global_metrics["perdidas"],
        "empates": global_metrics["empates"],
        "risk": {
            "mode": str(risk_manager.mode),
            "current_stake": current_stake,
            "stake_reason": stake_reason,
            "saldo_actual": stats_copy["saldo_actual"],
            "daily_stop_loss": risk_manager.daily_stop_loss,
            "daily_take_profit": risk_manager.daily_take_profit
        }
    }


@app.get("/trades")
def get_trades(limit: int = Query(50, ge=1, le=200)):
    return storage.get_recent_trades(limit=limit)


# ==========================================
# ENDPOINTS DE ANALISIS Y OPERACIONES
# ==========================================

@app.post("/analizar")
async def analizar_mercado(data: MarketData):
    global ultima_operacion_timestamp, calentamiento_anunciado, ultimos_indicadores_por_precio

    with _state_lock:
        if data.session_stats:
            saldo = data.session_stats.get("saldo_actual")
            if saldo is not None and saldo > 0:
                session_stats["saldo_actual"] = float(saldo)

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

    min_warmup = active_strategy.min_candles_warmup
    if len(historial_precios) < min_warmup:
        progreso = len(historial_precios)
        porcentaje = int((progreso / min_warmup) * 100)
        return {
            "action": "hold",
            "amount": 0.0,
            "active_id": data.active_id,
            "status": f"Calibrando indicadores ({progreso}/{min_warmup} velas - {porcentaje}%)...",
            "warmup": True
        }

    if not calentamiento_anunciado:
        calentamiento_anunciado = True
        print(f"\n[PYTHON] CALIBRACION COMPLETADA ({min_warmup} velas)!")
        print(f"         Estrategia Activa: {active_strategy.name} (Puntaje Requerido: >={active_strategy.min_score:.0f} pts)\n")

    tiempo_actual = time.time()
    segundos_desde_ultima = tiempo_actual - ultima_operacion_timestamp
    en_cooldown = segundos_desde_ultima < COOLDOWN_SECONDS

    decision: StrategyDecision = active_strategy.evaluate(
        prices=historial_precios,
        candles=historial_velas,
        session_stats=session_stats
    )

    ultimos_indicadores_por_precio = {
        "timestamp": tiempo_actual,
        "price": precio_actual,
        "decision": decision,
        "indicators": decision.indicators
    }

    with _state_lock:
        stats_snapshot = dict(session_stats)

    risk_check = risk_manager.check_trade_permission(
        session_pnl=stats_snapshot["balance_neto"],
        current_balance=stats_snapshot["saldo_actual"],
        consecutive_losses=stats_snapshot["consecutive_losses"],
        consecutive_wins=stats_snapshot["consecutive_wins"],
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

    accion_final = "hold"
    motivo_final = decision.reason_summary

    if decision.action in ("call", "put"):
        if not en_cooldown:
            accion_final = decision.action
            ultima_operacion_timestamp = tiempo_actual
            tag_color = "CALL" if accion_final == "call" else "PUT"
            print(f"\n[PYTHON] {tag_color} DISPARADO [{decision.score_call:.0f}C / {decision.score_put:.0f}P]")
            print(f"         Monto Dinamico: ${risk_check.amount:.2f} USD ({risk_check.reason})")
            print(f"         {decision.reason_summary}\n")
        else:
            motivo_final = f"Senal {decision.action.upper()} ignorada por Cooldown ({int(COOLDOWN_SECONDS - segundos_desde_ultima)}s restantes)"

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
        "racha_perdidas": stats_snapshot["consecutive_losses"],
        "racha_ganadas": stats_snapshot["consecutive_wins"],
        "cooldown_active": en_cooldown
    }


@app.post("/resultado-operacion")
async def registrar_resultado(trade: ResultadoOperacion):
    global session_stats

    with _state_lock:
        if trade.resultado == "GANADA":
            session_stats["ganadas"] += 1
            session_stats["consecutive_wins"] += 1
            session_stats["consecutive_losses"] = 0
        elif trade.resultado == "PERDIDA":
            session_stats["perdidas"] += 1
            session_stats["consecutive_losses"] += 1
            session_stats["consecutive_wins"] = 0
        else:
            session_stats["empates"] += 1

        session_stats["balance_neto"] += trade.ganancia_neta
        if trade.balance_actual:
            session_stats["saldo_actual"] = trade.balance_actual

        stats_snapshot = dict(session_stats)

    print(f"[PYTHON] RESULTADO: Operacion #{trade.id} {trade.resultado} (${trade.ganancia_neta:+.2f})")

    indicators_to_save = ultimos_indicadores_por_precio.get("indicators")
    decision_obj = ultimos_indicadores_por_precio.get("decision")
    score_call = decision_obj.score_call if decision_obj else 0.0
    score_put = decision_obj.score_put if decision_obj else 0.0
    signal_reasons = decision_obj.reason_summary if decision_obj else ""

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
        balance_post=trade.balance_actual or stats_snapshot["saldo_actual"],
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
        "session_summary": stats_snapshot,
        "total_recorded_trades": storage.get_metrics_summary()["total_trades"]
    }


@app.post("/reiniciar-sesion")
async def reiniciar_sesion():
    global session_id

    with _state_lock:
        session_id = f"session_{int(time.time())}"
        saldo_prev = session_stats["saldo_actual"]
        session_stats.update({
            "ganadas": 0,
            "perdidas": 0,
            "empates": 0,
            "balance_neto": 0.0,
            "consecutive_wins": 0,
            "consecutive_losses": 0,
            "saldo_actual": saldo_prev
        })

    historial_precios.clear()
    historial_velas.clear()

    print(f"\n[PYTHON] SESION REINICIADA: Nueva sesion: {session_id}\n")
    return {"status": "ok", "session_id": session_id}


@app.post("/cambiar-estrategia")
async def cambiar_estrategia(req: StrategySwitchRequest):
    global active_strategy_name, active_strategy
    strat_key = req.strategy_name.lower().strip()
    if strat_key not in strategies:
        raise HTTPException(status_code=400, detail=f"Estrategia invalida. Opciones: {list(strategies.keys())}")

    active_strategy_name = strat_key
    active_strategy = strategies[strat_key]

    if req.risk_mode:
        try:
            risk_manager.mode = RiskMode(req.risk_mode.lower())
        except ValueError:
            pass

    print(f"\n[PYTHON] CONFIGURACION ACTUALIZADA: Estrategia = {active_strategy.name} | Riesgo = {risk_manager.mode}\n")
    return {
        "status": "ok",
        "active_strategy": active_strategy.name,
        "risk_mode": str(risk_manager.mode)
    }
