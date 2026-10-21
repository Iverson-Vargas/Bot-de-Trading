import WebSocket from 'ws';
import fs from 'fs';
import path from 'path';

try {
    process.loadEnvFile('.env');
} catch (e) {}

// ==============================================================================
// CONFIGURACION GENERAL
// ==============================================================================
const WS_BROKER_URL = 'wss://ws.iqoption.com/echo/websocket';
const ACCOUNT_MODE = (process.env.ACCOUNT_MODE || 'demo').toLowerCase();
const SSID = process.env.SSID || '';
const USER_BALANCE_ID = parseInt(process.env.USER_BALANCE_ID || '0', 10);
const ACTIVE_ID = parseInt(process.env.ACTIVE_ID || '76', 10);
const OPTION_TYPE_ID = parseInt(process.env.OPTION_TYPE_ID || '12', 10);
const EXPIRATION_SECONDS = parseInt(process.env.EXPIRATION_SECONDS || '60', 10);
const PROFIT_PERCENT = parseInt(process.env.PROFIT_PERCENT || '82', 10);
const PYTHON_API_URL = process.env.PYTHON_API_URL || 'http://127.0.0.1:8000/analizar';
const REGISTRO_FILE = path.resolve(process.cwd(), '../registro_operaciones.txt');

// Validar SSID al inicio
if (!SSID || SSID.length < 10) {
    console.error('\n[ERROR CRITICO] No se encontro un SSID valido.');
    console.error('  Configura tu SSID en el archivo transito-broker/.env');
    console.error('  Ejemplo: SSID=tu_ssid_de_iqoption\n');
    console.error('  El bot se ejecutara pero no podra autenticarse con el broker.\n');
}

let ws = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_DELAY_MS = 30000;
const MAX_RECONNECT_ATTEMPTS = 50;
let reconnectTimer = null;
let heartbeatInterval = null;
let isShuttingDown = false;

let requestIdCounter = 1;
let lastKnownPrice = 0;
let serverTimeOffset = 0;
let isAuthenticated = false;
let hayOperacionEnCurso = false;
let operationWatchdogTimer = null;

const fechaInicio = new Date();
let saldoInicial = 0;
let saldoActual = 0;
let balanceDetectadoBroker = false;

const estadisticas = {
    ganadas: 0,
    perdidas: 0,
    empates: 0,
    balanceNeto: 0.0
};

const ordenesActivas = new Map();

function obtenerHoraLocal() {
    return new Date().toLocaleTimeString('es-ES', { hour12: false });
}

function obtenerFechaHoraLocal() {
    const ahora = new Date();
    return `${ahora.toISOString().split('T')[0]} ${ahora.toLocaleTimeString('es-ES', { hour12: false })}`;
}

let activeBalanceId = USER_BALANCE_ID;
let activeAccountType = 'DEMO';
let sessionStartedTxt = false;

// ==============================================================================
// REGISTRO EN ARCHIVO TXT
// ==============================================================================
function registrarInicioSesionTxt() {
    if (sessionStartedTxt) return;
    sessionStartedTxt = true;

    const encabezado = `
================================================================================
  INICIO DE SESION DE TRADING - BOT IQ OPTION
  Fecha y Hora de Inicio: ${obtenerFechaHoraLocal()}
  Par Operado: EUR/USD Blitz (ID: ${ACTIVE_ID}) | Expiracion: ${EXPIRATION_SECONDS}s
  Tipo de Cuenta: ${activeAccountType} (ID Balance: ${activeBalanceId})
  Balance Inicial Real en Broker: $${saldoInicial.toFixed(2)} USD
  Payout Configurado: ${PROFIT_PERCENT}%
================================================================================
`;
    try {
        fs.appendFileSync(REGISTRO_FILE, encabezado, 'utf8');
        fetch('http://127.0.0.1:8000/reiniciar-sesion', { method: 'POST' }).catch(() => {});
    } catch (e) {
        console.error('[TXT] Error escribiendo encabezado:', e.message);
    }
}

function registrarOperacionTxt(orden, resultado, gananciaNeta, precioSalida, horaCierre) {
    const pnlSigno = gananciaNeta >= 0 ? `+$${gananciaNeta.toFixed(2)}` : `-$${Math.abs(gananciaNeta).toFixed(2)}`;
    const netoSigno = estadisticas.balanceNeto >= 0 ? `+$${estadisticas.balanceNeto.toFixed(2)}` : `-$${Math.abs(estadisticas.balanceNeto).toFixed(2)}`;

    const registro = `
----------------------------------------------------------------------------------
[${horaCierre}] ORDEN #${orden.id} - RESULTADO: ${resultado} (${pnlSigno} USD)
  Cuenta: ${activeAccountType} (ID: ${activeBalanceId})
  Direccion: ${orden.direccion === 'CALL' ? 'CALL (COMPRA)' : 'PUT (VENTA)'}
  Monto Invertido: $${orden.monto.toFixed(2)} USD
  Precio Entrada: ${orden.precioEntrada.toFixed(6)}  -->  Precio Salida: ${precioSalida.toFixed(6)}
  Saldo Real Cuenta Post-Operacion: $${saldoActual.toFixed(2)} USD
  Rendimiento Neto Sesion: ${netoSigno} USD | Marcador: ${estadisticas.ganadas}W - ${estadisticas.perdidas}L - ${estadisticas.empates}D
----------------------------------------------------------------------------------`;
    try {
        fs.appendFileSync(REGISTRO_FILE, registro, 'utf8');
    } catch (e) {
        console.error('[TXT] Error guardando operacion:', e.message);
    }
}

let resumenGuardado = false;
function guardarResumenCierreSesion() {
    if (resumenGuardado) return;
    resumenGuardado = true;

    const fechaFin = new Date();
    const minutosDuracion = ((fechaFin - fechaInicio) / 1000 / 60).toFixed(1);
    const totalOps = estadisticas.ganadas + estadisticas.perdidas + estadisticas.empates;
    if (isNaN(saldoActual)) {
        saldoActual = (saldoInicial || 0) + (estadisticas.balanceNeto || 0);
    }
    const winRate = totalOps > 0 ? ((estadisticas.ganadas / (estadisticas.ganadas + estadisticas.perdidas || 1)) * 100).toFixed(1) : '0.0';
    const rentabilidad = saldoInicial > 0 ? ((estadisticas.balanceNeto / saldoInicial) * 100).toFixed(2) : '0.0';
    const pnlFinalSigno = estadisticas.balanceNeto >= 0 ? `+$${estadisticas.balanceNeto.toFixed(2)}` : `-$${Math.abs(estadisticas.balanceNeto).toFixed(2)}`;

    const resumenTexto = `
================================================================================
  RESUMEN FINAL DE LA SESION DE TRADING
  Fecha y Hora de Cierre: ${obtenerFechaHoraLocal()}
  Cuenta Operada: ${activeAccountType} (ID: ${activeBalanceId})
  Duracion de la Sesion: ${minutosDuracion} minutos
----------------------------------------------------------------------------------
  Balance Inicial Real en Broker: $${saldoInicial.toFixed(2)} USD
  Balance Final Real en Broker:   $${saldoActual.toFixed(2)} USD
  Beneficio / Perdida Neta:       ${pnlFinalSigno} USD (${rentabilidad}%)
  Total de Operaciones:           ${totalOps}
  Desglose de Resultados:         ${estadisticas.ganadas} Ganadas | ${estadisticas.perdidas} Perdidas | ${estadisticas.empates} Empates
  Tasa de Acierto (Win Rate):     ${winRate}%
================================================================================
\n`;

    try {
        fs.appendFileSync(REGISTRO_FILE, resumenTexto, 'utf8');
    } catch (e) {}
    console.log(resumenTexto);
}

// ==============================================================================
// SHUTDOWN GRACEFUL
// ==============================================================================
function gracefulShutdown(signal) {
    if (isShuttingDown) return;
    isShuttingDown = true;
    console.log(`\n[${obtenerHoraLocal()}] [NODE] Recibida senal ${signal}. Cerrando conexiones...`);

    if (heartbeatInterval) clearInterval(heartbeatInterval);
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (operationWatchdogTimer) clearTimeout(operationWatchdogTimer);

    guardarResumenCierreSesion();

    if (ws) {
        try {
            ws.removeAllListeners();
            ws.close(1000, 'Shutdown');
        } catch (e) {}
    }

    console.log(`[${obtenerHoraLocal()}] [NODE] Bot detenido correctamente.`);
    setTimeout(() => process.exit(0), 500);
}

process.on('SIGINT', () => gracefulShutdown('SIGINT'));
process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('exit', () => { if (!resumenGuardado) guardarResumenCierreSesion(); });

// ==============================================================================
// GESTION DE BALANCES Y PERFILES
// ==============================================================================
function extraerBalancesDeMensaje(mensaje) {
    if (!['profile', 'balances', 'get-balances', 'balance-changed'].includes(mensaje.name)) {
        return;
    }

    if (mensaje.name === 'balance-changed') {
        const item = mensaje.msg;
        if (item && item.id === activeBalanceId) {
            const nuevoSaldo = item.current_balance !== undefined ? item.current_balance : item.amount;
            if (nuevoSaldo !== undefined) {
                saldoActual = parseFloat(nuevoSaldo);
            }
        }
        return;
    }

    let balancesArray = null;
    let balanceIdPrincipal = null;

    if (Array.isArray(mensaje.msg)) {
        balancesArray = mensaje.msg;
    } else if (mensaje.msg && Array.isArray(mensaje.msg.balances)) {
        balancesArray = mensaje.msg.balances;
        balanceIdPrincipal = mensaje.msg.balance_id;
    } else if (mensaje.msg?.result && Array.isArray(mensaje.msg.result.balances)) {
        balancesArray = mensaje.msg.result.balances;
        balanceIdPrincipal = mensaje.msg.result.balance_id;
    }

    if (!balancesArray || balancesArray.length === 0) return;

    const balancesValidos = balancesArray.filter(b => b && (b.type !== undefined || b.currency !== undefined));
    if (balancesValidos.length === 0) return;

    let balanceObj = null;
    if (ACCOUNT_MODE === 'demo') {
        balanceObj = balancesValidos.find(b => b.type === 4 || b.id === USER_BALANCE_ID) || balancesValidos.find(b => b.type === 4);
    } else if (ACCOUNT_MODE === 'real') {
        balanceObj = balancesValidos.find(b => b.type === 1);
    }

    if (!balanceObj && USER_BALANCE_ID) {
        balanceObj = balancesValidos.find(b => b.id === USER_BALANCE_ID);
    }
    if (!balanceObj && balanceIdPrincipal) {
        balanceObj = balancesValidos.find(b => b.id === balanceIdPrincipal);
    }

    if (balanceObj) {
        const rawAmount = balanceObj.amount ?? balanceObj.current_balance ?? balanceObj.value;
        if (rawAmount !== undefined && rawAmount !== null) {
            const monto = parseFloat(rawAmount);
            activeBalanceId = balanceObj.id;
            activeAccountType = balanceObj.type === 1 ? 'REAL' : (balanceObj.type === 4 ? 'DEMO' : `CUENTA #${balanceObj.id}`);

            if (!balanceDetectadoBroker) {
                saldoInicial = monto;
                saldoActual = monto;
                balanceDetectadoBroker = true;
                console.log(`[${obtenerHoraLocal()}] [NODE] Modo: ${ACCOUNT_MODE.toUpperCase()} -> Cuenta: ${activeAccountType} (ID: ${activeBalanceId})`);
                console.log(`[${obtenerHoraLocal()}] [NODE] Saldo Real en Broker: $${saldoActual.toFixed(2)} USD`);
                registrarInicioSesionTxt();
            } else {
                saldoActual = monto;
            }
        }
    }
}

// ==============================================================================
// CONEXION WEBSOCKET CON AUTO-RECONEXION
// ==============================================================================
let connectionOpenedAt = 0;
let subscriptionSent = false;

function conectarWebSocket() {
    if (isShuttingDown) return;

    if (ws) {
        try {
            ws.removeAllListeners();
            ws.terminate();
        } catch (e) {}
    }

    isAuthenticated = false;
    subscriptionSent = false;

    console.log(`\n[${obtenerHoraLocal()}] [NODE] Conectando con servidor WebSocket IQ Option...`);

    ws = new WebSocket(WS_BROKER_URL, {
        origin: 'https://iqoption.com',
        headers: {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    });

    ws.on('open', () => {
        connectionOpenedAt = Date.now();
        console.log(`[${obtenerHoraLocal()}] [NODE] Conexion establecida.`);

        console.log(`[${obtenerHoraLocal()}] [NODE] Enviando SSID para autenticacion...`);
        ws.send(JSON.stringify({
            name: "ssid",
            msg: SSID,
            request_id: `ssid_${Date.now()}`
        }));

        if (heartbeatInterval) clearInterval(heartbeatInterval);
        heartbeatInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ name: "heartbeat", userTime: Date.now() }));
            }
        }, 15000);
    });

    ws.on('message', async (data) => {
        if (isShuttingDown) return;

        let mensaje;
        try {
            mensaje = JSON.parse(data.toString());
        } catch {
            return;
        }

        if (mensaje.name === 'timeSync') {
            serverTimeOffset = mensaje.msg - Math.floor(Date.now() / 1000);
            return;
        }

        // Debug: log non-trivial messages during connection setup
        if (!isAuthenticated && mensaje.name !== 'timeSync') {
            console.log(`[${obtenerHoraLocal()}] [NODE] [DEBUG] Mensaje: ${mensaje.name} (req_id: ${mensaje.request_id || '-'})`);
        }

        if (mensaje.name === 'auth-error' || (mensaje.msg?.is_successful === false && mensaje.request_id?.startsWith('ssid_'))) {
            console.error(`\n[${obtenerHoraLocal()}] ERROR CRITICO: SSID INVALIDO O EXPIRADO.`);
            console.error('  Obtiene un nuevo SSID desde las cookies de tu navegador en iqoption.com');
            console.error('  y actualiza el archivo .env con: SSID=tu_nuevo_ssid\n');
            isShuttingDown = true;
            return;
        }

        extraerBalancesDeMensaje(mensaje);

        if (mensaje.name === 'profile' && !isAuthenticated) {
            isAuthenticated = true;
            console.log(`[${obtenerHoraLocal()}] [NODE] Autenticado exitosamente en IQ Option!`);

            // Wait briefly before subscribing to let the server finish processing
            setTimeout(() => {
                if (!ws || ws.readyState !== WebSocket.OPEN || subscriptionSent) return;
                subscriptionSent = true;

                console.log(`[${obtenerHoraLocal()}] [NODE] Suscribiendose a velas de 10s del activo ${ACTIVE_ID}...`);
                ws.send(JSON.stringify({
                    name: "subscribeMessage",
                    request_id: `sub_candles_${Date.now()}`,
                    msg: {
                        name: "candle-generated",
                        params: {
                            routingFilters: {
                                active_id: ACTIVE_ID,
                                size: 10
                            }
                        }
                    }
                }));

                // Request initial candles history for faster warmup
                ws.send(JSON.stringify({
                    name: "sendMessage",
                    request_id: `get_candles_${Date.now()}`,
                    msg: {
                        name: "get-candles",
                        version: "2.0",
                        body: {
                            active_id: ACTIVE_ID,
                            size: 60,
                            count: 200,
                            to: Math.floor(Date.now() / 1000)
                        }
                    }
                }));

                setTimeout(() => {
                    if (!sessionStartedTxt) registrarInicioSesionTxt();
                }, 3000);
            }, 500);
            return;
        }

        if (mensaje.name === 'candles' && Array.isArray(mensaje.msg?.candles)) {
            const velas = mensaje.msg.candles;
            console.log(`[${obtenerHoraLocal()}] [NODE] Recibidas ${velas.length} velas historicas M1 para warmup.`);
            for (const v of velas) {
                lastKnownPrice = v.close || v.value || lastKnownPrice;
                await procesarDatosConPython({
                    active_id: ACTIVE_ID,
                    price: lastKnownPrice,
                    open: v.open,
                    close: v.close,
                    min: v.min,
                    max: v.max,
                    volume: v.volume,
                    timestamp: v.to || v.from || Math.floor(Date.now() / 1000)
                });
            }
            return;
        }

        if (mensaje.name === 'candle-generated') {
            const vela = mensaje.msg;
            if (vela.active_id === ACTIVE_ID) {
                lastKnownPrice = vela.close || vela.value || lastKnownPrice;
                await procesarDatosConPython({
                    active_id: vela.active_id,
                    price: lastKnownPrice,
                    open: vela.open,
                    close: vela.close,
                    min: vela.min,
                    max: vela.max,
                    volume: vela.volume,
                    timestamp: vela.to || Math.floor(Date.now() / 1000)
                });
            }
        } else if (mensaje.name === 'quote' || mensaje.name === 'instrument-quotes') {
            const tick = mensaje.msg;
            const precio = tick.value || tick.price || null;
            if (precio) lastKnownPrice = precio;
        }

        if (mensaje.name === 'option-opened' || mensaje.msg?.name === 'option-opened') {
            const body = mensaje.msg?.body || mensaje.msg || mensaje;
            const brokerId = body.id || body.option_id;
            console.log(`[${obtenerHoraLocal()}] [NODE] Operacion ACEPTADA (ID Broker: ${brokerId || 'OK'})`);
            for (const [, o] of ordenesActivas.entries()) {
                if (!o.resuelto && !o.brokerId) {
                    o.brokerId = brokerId;
                    break;
                }
            }
        }

        if (mensaje.name === 'socket-option-rejected' || (mensaje.name === 'sendMessage' && mensaje.msg?.is_successful === false)) {
            console.error(`[${obtenerHoraLocal()}] [NODE] RECHAZO:`, mensaje.msg?.message || 'Sin detalle');
            hayOperacionEnCurso = false;
            if (operationWatchdogTimer) clearTimeout(operationWatchdogTimer);
        }

        if (['option-closed', 'socket-option-closed', 'position-changed'].includes(mensaje.name) ||
            ['option-closed', 'socket-option-closed'].includes(mensaje.msg?.name)) {
            const res = mensaje.msg?.body || mensaje.msg || mensaje;
            finalizarOrdenDesdeBroker(res);
        }
    });

    ws.on('close', (codigo, razon) => {
        const aliveMs = Date.now() - connectionOpenedAt;
        console.warn(`[${obtenerHoraLocal()}] [NODE] Conexion cerrada. Codigo: ${codigo} (estuvo ${(aliveMs/1000).toFixed(1)}s activa)`);
        isAuthenticated = false;
        if (heartbeatInterval) clearInterval(heartbeatInterval);

        // Only reset reconnect counter if connection was stable (>10s)
        if (aliveMs > 10000) {
            reconnectAttempts = 0;
        }

        if (!isShuttingDown) programarReconexion();
    });

    ws.on('error', (err) => {
        console.error(`[${obtenerHoraLocal()}] [NODE] Error en WebSocket:`, err.message);
    });
}

function programarReconexion() {
    if (isShuttingDown) return;
    if (reconnectTimer) clearTimeout(reconnectTimer);

    reconnectAttempts++;
    if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
        console.error(`[${obtenerHoraLocal()}] [NODE] Se alcanzo el maximo de reintentos (${MAX_RECONNECT_ATTEMPTS}). Deteniendo.`);
        gracefulShutdown('MAX_RECONNECT');
        return;
    }

    // Exponential backoff: 2s, 4s, 8s, 16s, 30s max
    const delayMs = Math.min(Math.pow(2, Math.min(reconnectAttempts, 15)) * 1000, MAX_RECONNECT_DELAY_MS);
    console.log(`[${obtenerHoraLocal()}] [NODE] Reintentando en ${(delayMs / 1000).toFixed(0)}s (Intento #${reconnectAttempts} de ${MAX_RECONNECT_ATTEMPTS})...`);

    reconnectTimer = setTimeout(() => conectarWebSocket(), delayMs);
}

// ==============================================================================
// COMUNICACION CON MOTOR PYTHON
// ==============================================================================
async function procesarDatosConPython(datosMercado) {
    if (isShuttingDown) return;

    try {
        const payload = {
            ...datosMercado,
            session_stats: {
                ganadas: estadisticas.ganadas,
                perdidas: estadisticas.perdidas,
                empates: estadisticas.empates,
                balance_neto: estadisticas.balanceNeto,
                saldo_actual: saldoActual,
                en_curso: hayOperacionEnCurso
            }
        };

        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);

        const respuesta = await fetch(PYTHON_API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller.signal
        });
        clearTimeout(timeout);

        if (!respuesta.ok) return;

        const decision = await respuesta.json();
        const hora = obtenerHoraLocal();

        if (decision.warmup && decision.status) {
            console.log(`[${hora}] [BOT] ${decision.status}`);
        } else if (decision.rsi !== null && decision.rsi !== undefined) {
            const scoreInfo = decision.score_call !== undefined ? ` | [CALL:${decision.score_call} PUT:${decision.score_put}]` : '';
            console.log(`[${hora}] [BOT] Vela 10s -> Precio: ${datosMercado.price.toFixed(6)} | RSI: ${decision.rsi}${scoreInfo}`);
            if (decision.motivo && !decision.motivo.startsWith('Sin senal')) {
                console.log(`[${hora}] [BOT] ${decision.motivo}`);
            }
        }

        if ((decision.action === 'call' || decision.action === 'put') && !hayOperacionEnCurso) {
            ejecutarOperacion(activeBalanceId, decision.active_id || ACTIVE_ID, decision.action, decision.amount || 10.0, datosMercado.price);
        }

    } catch (error) {
        if (error.name !== 'AbortError') {
            console.error(`[${obtenerHoraLocal()}] [NODE] Motor Python no disponible:`, error.message);
        }
    }
}

function ejecutarOperacion(balanceId, idActivo, direccion, montoInversion, precioActual) {
    if (hayOperacionEnCurso || isShuttingDown) return;
    hayOperacionEnCurso = true;

    const reqId = `${requestIdCounter++}`;
    const horaApertura = obtenerHoraLocal();
    const tiempoServidor = Math.floor(Date.now() / 1000) + serverTimeOffset;
    const tiempoExpiracion = tiempoServidor + EXPIRATION_SECONDS;
    const horaExpiracion = new Date(Date.now() + (EXPIRATION_SECONDS * 1000)).toLocaleTimeString('es-ES', { hour12: false });
    const valorStrike = Math.round(precioActual * 1000000);

    const orden = {
        name: "sendMessage",
        request_id: reqId,
        local_time: Math.floor(Date.now() / 1000),
        msg: {
            name: "binary-options.open-option",
            version: "2.0",
            body: {
                user_balance_id: balanceId,
                active_id: idActivo,
                option_type_id: OPTION_TYPE_ID,
                direction: direccion.toLowerCase(),
                expired: tiempoExpiracion,
                refund_value: 0,
                price: montoInversion,
                value: valorStrike,
                profit_percent: PROFIT_PERCENT,
                expiration_size: EXPIRATION_SECONDS
            }
        }
    };

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(orden));
    } else {
        console.error(`[${horaApertura}] [NODE] WebSocket desconectado al enviar orden #${reqId}`);
        hayOperacionEnCurso = false;
        return;
    }

    const infoOrden = {
        id: reqId,
        direccion: direccion.toUpperCase(),
        monto: montoInversion,
        precioEntrada: precioActual,
        horaApertura,
        horaExpiracion,
        expiracionSegundos: EXPIRATION_SECONDS,
        resuelto: false
    };
    ordenesActivas.set(reqId, infoOrden);

    console.log(`\n===============================================================`);
    console.log(`[${horaApertura}] [ORDEN #${reqId}] ENVIADA AL BROKER`);
    console.log(`  Direccion: ${direccion.toUpperCase() === 'CALL' ? 'CALL (COMPRA)' : 'PUT (VENTA)'}`);
    console.log(`  Monto: $${montoInversion.toFixed(2)} USD`);
    console.log(`  Precio Entrada: ${precioActual.toFixed(6)}`);
    console.log(`  Expiracion: ${horaExpiracion} (${EXPIRATION_SECONDS}s)`);
    console.log(`===============================================================\n`);

    if (operationWatchdogTimer) clearTimeout(operationWatchdogTimer);
    operationWatchdogTimer = setTimeout(() => {
        evaluarResultadoLocal(reqId);
    }, (EXPIRATION_SECONDS + 8) * 1000);
}

function procesarCierreDeOrden(orden, resultadoOficial, precioSalidaOficial, gananciaNetaOficial) {
    if (!orden || orden.resuelto) return;
    orden.resuelto = true;
    hayOperacionEnCurso = false;
    if (operationWatchdogTimer) clearTimeout(operationWatchdogTimer);

    const horaCierre = obtenerHoraLocal();
    const precioSalida = precioSalidaOficial || lastKnownPrice;
    let resultado = resultadoOficial;
    let gananciaNeta = gananciaNetaOficial;

    if (!resultado) {
        resultado = 'EMPATE';
        gananciaNeta = 0.0;
        if (orden.direccion === 'CALL') {
            if (precioSalida > orden.precioEntrada) {
                resultado = 'GANADA';
                gananciaNeta = orden.monto * (PROFIT_PERCENT / 100);
            } else if (precioSalida < orden.precioEntrada) {
                resultado = 'PERDIDA';
                gananciaNeta = -orden.monto;
            }
        } else if (orden.direccion === 'PUT') {
            if (precioSalida < orden.precioEntrada) {
                resultado = 'GANADA';
                gananciaNeta = orden.monto * (PROFIT_PERCENT / 100);
            } else if (precioSalida > orden.precioEntrada) {
                resultado = 'PERDIDA';
                gananciaNeta = -orden.monto;
            }
        }
    }

    if (resultado === 'GANADA') estadisticas.ganadas++;
    else if (resultado === 'PERDIDA') estadisticas.perdidas++;
    else estadisticas.empates++;

    estadisticas.balanceNeto += gananciaNeta;
    saldoActual = saldoInicial + estadisticas.balanceNeto;

    const totalOperaciones = estadisticas.ganadas + estadisticas.perdidas + estadisticas.empates;
    const winRate = totalOperaciones > 0 ? ((estadisticas.ganadas / (estadisticas.ganadas + estadisticas.perdidas || 1)) * 100).toFixed(1) : '0.0';

    console.log(`\n===============================================================`);
    console.log(`[${horaCierre}] [RESULTADO ORDEN #${orden.id}]`);
    console.log(`  ${orden.direccion} | Monto: $${orden.monto.toFixed(2)} USD`);
    console.log(`  Entrada: ${orden.precioEntrada.toFixed(6)}  -->  Salida: ${precioSalida.toFixed(6)}`);
    if (resultado === 'GANADA') {
        console.log(`  GANADA (+$${gananciaNeta.toFixed(2)} USD)`);
    } else if (resultado === 'PERDIDA') {
        console.log(`  PERDIDA (-$${orden.monto.toFixed(2)} USD)`);
    } else {
        console.log(`  EMPATE ($0.00 USD)`);
    }
    console.log(`  Marcador: ${estadisticas.ganadas}W - ${estadisticas.perdidas}L - ${estadisticas.empates}D (Win Rate: ${winRate}%)`);
    console.log(`  Balance Neto: ${estadisticas.balanceNeto >= 0 ? '+' : ''}$${estadisticas.balanceNeto.toFixed(2)} USD`);
    console.log(`===============================================================\n`);

    registrarOperacionTxt(orden, resultado, gananciaNeta, precioSalida, horaCierre);

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    fetch('http://127.0.0.1:8000/resultado-operacion', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            id: orden.id,
            order_id: orden.brokerId || orden.id,
            resultado,
            ganancia_neta: gananciaNeta,
            direccion: orden.direccion,
            precio_entrada: orden.precioEntrada,
            precio_salida: precioSalida,
            balance_actual: saldoActual,
            payout_percent: PROFIT_PERCENT,
            account_type: activeAccountType,
            account_id: String(activeBalanceId)
        }),
        signal: controller.signal
    }).catch(() => {}).finally(() => clearTimeout(timeout));
}

function evaluarResultadoLocal(reqId) {
    const orden = ordenesActivas.get(reqId);
    if (!orden || orden.resuelto) return;
    console.log(`[${obtenerHoraLocal()}] [NODE] Temporizador guardian activado para Orden #${reqId}.`);
    procesarCierreDeOrden(orden, null, lastKnownPrice, 0.0);
}

function finalizarOrdenDesdeBroker(res) {
    if (!res) return;
    let orden = null;
    const optionId = res.id || res.option_id;
    if (optionId) {
        for (const [, o] of ordenesActivas.entries()) {
            if (!o.resuelto && (o.brokerId == optionId || !o.brokerId)) {
                orden = o;
                break;
            }
        }
    }
    if (!orden) {
        for (const [, o] of ordenesActivas.entries()) {
            if (!o.resuelto) {
                orden = o;
                break;
            }
        }
    }
    if (!orden) return;

    let winStatus = (res.win || res.status || '').toLowerCase();
    if (!winStatus && res.win_amount !== undefined) {
        winStatus = parseFloat(res.win_amount) > 0 ? 'win' : 'loose';
    }

    let resultado = null;
    let gananciaNeta = 0.0;

    if (winStatus === 'win') {
        resultado = 'GANADA';
        const profitPcnt = (PROFIT_PERCENT || 82) / 100;
        const profitTeorico = orden.monto * profitPcnt;
        let profitReal = profitTeorico;
        if (res.win_amount !== undefined && !isNaN(parseFloat(res.win_amount))) {
            profitReal = parseFloat(res.win_amount) - orden.monto;
        } else if (res.profit_amount !== undefined && !isNaN(parseFloat(res.profit_amount))) {
            profitReal = parseFloat(res.profit_amount);
        }
        gananciaNeta = !isNaN(profitReal) && profitReal > 0 ? profitReal : profitTeorico;
    } else if (winStatus === 'loose' || winStatus === 'lose') {
        resultado = 'PERDIDA';
        gananciaNeta = -orden.monto;
    } else if (winStatus === 'equal') {
        resultado = 'EMPATE';
        gananciaNeta = 0.0;
    }

    const rawSalida = res.close_quote || res.close_price || res.cur_price || lastKnownPrice;
    const precioSalida = !isNaN(parseFloat(rawSalida)) ? parseFloat(rawSalida) : lastKnownPrice;
    procesarCierreDeOrden(orden, resultado, precioSalida, gananciaNeta);
}

// ==============================================================================
// INICIO
// ==============================================================================
console.log('===============================================================');
console.log('  BOT DE TRADING CUANTITATIVO - BROKER IQ OPTION');
console.log(`  Hora de inicio: ${obtenerHoraLocal()}`);
console.log(`  Activo ID: ${ACTIVE_ID} (EUR/USD Blitz)`);
console.log(`  Expiracion: ${EXPIRATION_SECONDS}s | Payout: ${PROFIT_PERCENT}%`);
console.log(`  Modo: ${ACCOUNT_MODE.toUpperCase()}`);
console.log('===============================================================');

conectarWebSocket();
