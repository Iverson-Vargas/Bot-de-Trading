import WebSocket from 'ws';
import fs from 'fs';
import path from 'path';

// Cargar variables de entorno nativamente en Node.js
try {
    process.loadEnvFile('.env');
} catch (e) {
    // Si no encuentra el archivo .env usa valores por defecto
}

// ==============================================================================
// CONFIGURACIÓN GENERAL Y VARIABLES DE ENTORNO
// ==============================================================================
const WS_BROKER_URL = 'wss://ws.iqoption.com/echo/websocket';
const ACCOUNT_MODE = (process.env.ACCOUNT_MODE || 'demo').toLowerCase(); // 'demo' o 'real'
const SSID = process.env.SSID || 'abe621c09e62ec71ba1e237dfbbb11ax';
const USER_BALANCE_ID = parseInt(process.env.USER_BALANCE_ID || '1247876399', 10);
const ACTIVE_ID = parseInt(process.env.ACTIVE_ID || '76', 10); // 76 = EUR/USD Blitz
const OPTION_TYPE_ID = parseInt(process.env.OPTION_TYPE_ID || '12', 10); // 12 = Blitz/Turbo
const EXPIRATION_SECONDS = parseInt(process.env.EXPIRATION_SECONDS || '60', 10);
const PROFIT_PERCENT = parseInt(process.env.PROFIT_PERCENT || '82', 10);
const PYTHON_API_URL = process.env.PYTHON_API_URL || 'http://127.0.0.1:8000/analizar';

// Ruta del archivo de registro (en la raíz del proyecto para compatibilidad hacia atrás)
const REGISTRO_FILE = path.resolve(process.cwd(), '../registro_operaciones.txt');

let ws = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_DELAY_MS = 30000;
let reconnectTimer = null;
let heartbeatInterval = null;

let requestIdCounter = 1;
let lastKnownPrice = 1.1577;
let serverTimeOffset = 0;
let isAuthenticated = false;
let hayOperacionEnCurso = false; // Candado estricto para evitar operaciones simultáneas
let operationWatchdogTimer = null; // Temporizador guardián para liberar candado en caso de fallo

// Balance y Estadísticas de la Sesión
const fechaInicio = new Date();
let saldoInicial = 10000.0;
let saldoActual = 10000.0;
let balanceDetectadoBroker = false;

const estadisticas = {
    ganadas: 0,
    perdidas: 0,
    empates: 0,
    balanceNeto: 0.0
};

// Mapa para rastrear órdenes activas en memoria
const ordenesActivas = new Map();

function obtenerHoraLocal() {
    return new Date().toLocaleTimeString('es-ES', { hour12: false });
}

function obtenerFechaHoraLocal() {
    const ahora = new Date();
    return `${ahora.toISOString().split('T')[0]} ${ahora.toLocaleTimeString('es-ES', { hour12: false })}`;
}

let activeBalanceId = USER_BALANCE_ID;
let activeAccountType = 'PRÁCTICA / DEMO';
let sessionStartedTxt = false;

// ==============================================================================
// REGISTRO EN ARCHIVO TXT (Compatibilidad hacia atrás)
// ==============================================================================
function registrarInicioSesionTxt() {
    if (sessionStartedTxt) return;
    sessionStartedTxt = true;

    const encabezado = `
══════════════════════════════════════════════════════════════════════════════════
📊 INICIO DE SESIÓN DE TRADING - BOT IQ OPTION
📅 Fecha y Hora de Inicio: ${obtenerFechaHoraLocal()}
🎯 Par Operado: EUR/USD Blitz (ID: ${ACTIVE_ID}) | ⏱️ Expiración: ${EXPIRATION_SECONDS}s
💼 Tipo de Cuenta: ${activeAccountType} (ID Balance: ${activeBalanceId})
💰 Balance Inicial Real en Broker: $${saldoInicial.toFixed(2)} USD
📈 Payout Configurado: ${PROFIT_PERCENT}%
══════════════════════════════════════════════════════════════════════════════════
`;
    try {
        fs.appendFileSync(REGISTRO_FILE, encabezado, 'utf8');
        fetch('http://127.0.0.1:8000/reiniciar-sesion', { method: 'POST' }).catch(() => {});
    } catch (e) {
        console.error('[TXT] Error escribiendo encabezado en TXT:', e.message);
    }
}

function registrarOperacionTxt(orden, resultado, gananciaNeta, precioSalida, horaCierre) {
    const pnlSigno = gananciaNeta >= 0 ? `+$${gananciaNeta.toFixed(2)}` : `-$${Math.abs(gananciaNeta).toFixed(2)}`;
    const netoSigno = estadisticas.balanceNeto >= 0 ? `+$${estadisticas.balanceNeto.toFixed(2)}` : `-$${Math.abs(estadisticas.balanceNeto).toFixed(2)}`;
    
    const registro = `
----------------------------------------------------------------------------------
[${horaCierre}] ORDEN #${orden.id} - RESULTADO: ${resultado} (${pnlSigno} USD)
• Cuenta: ${activeAccountType} (ID: ${activeBalanceId})
• Dirección: ${orden.direccion === 'CALL' ? 'CALL (COMPRA / ALZA)' : 'PUT (VENTA / BAJA)'}
• Monto Invertido: $${orden.monto.toFixed(2)} USD
• Precio Entrada: ${orden.precioEntrada.toFixed(6)}  ──▶  Precio Salida: ${precioSalida.toFixed(6)}

• Horario: ${orden.horaApertura} a ${horaCierre} (${orden.expiracionSegundos}s)
• Saldo Real Cuenta Post-Operación: $${saldoActual.toFixed(2)} USD
• Rendimiento Neto Sesión: ${netoSigno} USD | Marcador: ${estadisticas.ganadas}W - ${estadisticas.perdidas}L - ${estadisticas.empates}D
----------------------------------------------------------------------------------`;
    try {
        fs.appendFileSync(REGISTRO_FILE, registro, 'utf8');
    } catch (e) {
        console.error('[TXT] Error guardando operación en TXT:', e.message);
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
══════════════════════════════════════════════════════════════════════════════════
🏁 RESUMEN FINAL DE LA SESIÓN DE TRADING
📅 Fecha y Hora de Cierre: ${obtenerFechaHoraLocal()}
💼 Cuenta Operada: ${activeAccountType} (ID: ${activeBalanceId})
⏳ Duración de la Sesión: ${minutosDuracion} minutos
----------------------------------------------------------------------------------
📈 Balance Inicial Real en Broker: $${saldoInicial.toFixed(2)} USD
📉 Balance Final Real en Broker:   $${saldoActual.toFixed(2)} USD
💰 Beneficio / Pérdida Neta:       ${pnlFinalSigno} USD (${rentabilidad}%)
🎯 Total de Operaciones:           ${totalOps}
🏆 Desglose de Resultados:         ${estadisticas.ganadas} Ganadas | ${estadisticas.perdidas} Perdidas | ${estadisticas.empates} Empates
📊 Tasa de Acierto (Win Rate):     ${winRate}%
══════════════════════════════════════════════════════════════════════════════════
\n`;

    try {
        fs.appendFileSync(REGISTRO_FILE, resumenTexto, 'utf8');
    } catch (e) {}

    console.log(resumenTexto);
    console.log(`[TXT] 💾 Historial y resumen guardados exitosamente en: ${REGISTRO_FILE}\n`);
}

process.on('SIGINT', () => { guardarResumenCierreSesion(); process.exit(0); });
process.on('SIGTERM', () => { guardarResumenCierreSesion(); process.exit(0); });
process.on('exit', () => { guardarResumenCierreSesion(); });

// ==============================================================================
// GESTIÓN DE BALANCES Y PERFILES
// ==============================================================================
function extraerBalancesDeMensaje(mensaje) {
    if (mensaje.name !== 'profile' && mensaje.name !== 'balances' && mensaje.name !== 'get-balances' && mensaje.name !== 'balance-changed') {
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
    } else if (mensaje.msg && mensaje.msg.result && Array.isArray(mensaje.msg.result.balances)) {
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
        const rawAmount = balanceObj.amount !== undefined ? balanceObj.amount : (balanceObj.current_balance !== undefined ? balanceObj.current_balance : balanceObj.value);
        if (rawAmount !== undefined && rawAmount !== null) {
            const monto = parseFloat(rawAmount);
            activeBalanceId = balanceObj.id;
            activeAccountType = balanceObj.type === 1 ? 'REAL (DINERO REAL)' : (balanceObj.type === 4 ? 'PRÁCTICA / DEMO' : `CUENTA #${balanceObj.id}`);

            if (!balanceDetectadoBroker) {
                saldoInicial = monto;
                saldoActual = monto;
                balanceDetectadoBroker = true;
                console.log(`[${obtenerHoraLocal()}] [NODE] 💼 Modo: ${ACCOUNT_MODE.toUpperCase()} -> Cuenta: ${activeAccountType} (ID: ${activeBalanceId})`);
                console.log(`[${obtenerHoraLocal()}] [NODE] 💰 Saldo Real en Broker: $${saldoActual.toFixed(2)} USD`);
                registrarInicioSesionTxt();
            } else {
                saldoActual = monto;
            }
        }
    }
}

// ==============================================================================
// CONEXIÓN WEBSOCKET Y AUTO-RECONEXIÓN CON BACKOFF EXPONENCIAL
// ==============================================================================
function conectarWebSocket() {
    if (ws) {
        try {
            ws.removeAllListeners();
            ws.terminate();
        } catch (e) {}
    }

    console.log(`\n[${obtenerHoraLocal()}] [NODE] 🔌 Conectando con servidor WebSocket IQ Option (${WS_BROKER_URL})...`);
    
    ws = new WebSocket(WS_BROKER_URL, {
        origin: 'https://iqoption.com',
        headers: {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
        }
    });

    ws.on('open', () => {
        console.log(`[${obtenerHoraLocal()}] [NODE] 🟢 Conexión establecida exitosamente.`);
        reconnectAttempts = 0; // Resetear contador de reintentos
        
        // 1. Autenticación con el SSID
        console.log(`[${obtenerHoraLocal()}] [NODE] 🔐 Enviando credenciales de autenticación...`);
        ws.send(JSON.stringify({
            name: "authenticate",
            request_id: `auth_${Date.now()}`,
            local_time: Math.floor(Date.now() / 1000),
            msg: {
                ssid: SSID,
                protocol: 3,
                session_id: "",
                client_session_id: ""
            }
        }));

        ws.send(JSON.stringify({
            name: "ssid",
            msg: SSID,
            request_id: `ssid_${Date.now()}`
        }));

        // Heartbeat cada 20 segundos para mantener la conexión viva
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        heartbeatInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ name: "heartbeat", userTime: Date.now() }));
            }
        }, 20000);
    });

    ws.on('message', async (data) => {
        try {
            const mensaje = JSON.parse(data.toString());

            // A. Sincronización de hora del servidor
            if (mensaje.name === 'timeSync') {
                const serverTime = mensaje.msg;
                serverTimeOffset = serverTime - Math.floor(Date.now() / 1000);
            }

            // B. Manejo explícito de errores de autenticación
            if (mensaje.name === 'auth-error' || (mensaje.msg && mensaje.msg.is_successful === false && mensaje.request_id?.startsWith('auth_'))) {
                console.error('\n═══════════════════════════════════════════════════════════════');
                console.error(`[${obtenerHoraLocal()}] ❌ ERROR CRÍTICO DE AUTENTICACIÓN: SSID INVÁLIDO O EXPIRADO.`);
                console.error('   Por favor obtén un nuevo SSID desde las cookies de tu navegador en iqoption.com');
                console.error('   y actualiza el archivo .env con: SSID=tu_nuevo_ssid');
                console.error('═══════════════════════════════════════════════════════════════\n');
                return;
            }

            // C. Extraer balances
            extraerBalancesDeMensaje(mensaje);

            // D. Respuesta de Autenticación Exitosa
            if (mensaje.name === 'authenticated' || mensaje.name === 'profile') {
                isAuthenticated = true;
                console.log(`[${obtenerHoraLocal()}] [NODE] ✅ ¡Autenticado exitosamente en IQ Option!`);
                
                // Suscribirse a las velas de 10 segundos del activo (EUR/USD Blitz)
                console.log(`[${obtenerHoraLocal()}] [NODE] 📡 Suscribiéndose a velas de 10s del activo ${ACTIVE_ID}...`);
                ws.send(JSON.stringify({
                    name: "subscribeMessage",
                    request_id: `sub_candles_${Date.now()}`,
                    msg: {
                        name: "candle-generated",
                        params: {
                            routingFilters: {
                                active_id: ACTIVE_ID,
                                size: 10 // Velas de 10 segundos
                            }
                        }
                    }
                }));

                ws.send(JSON.stringify({
                    name: "subscribeMessage",
                    request_id: `sub_quotes_${Date.now()}`,
                    msg: {
                        name: "quote",
                        params: {
                            routingFilters: {
                                active_id: ACTIVE_ID
                            }
                        }
                    }
                }));

                setTimeout(() => {
                    if (!sessionStartedTxt) registrarInicioSesionTxt();
                }, 3000);
                return;
            }

            // E. Recepción de Velas de 10s completas
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
                const precio = tick.value || (tick.price ? tick.price : null);
                if (precio) {
                    lastKnownPrice = precio;
                }
            }

            // F. Confirmación de orden abierta por el broker
            if (mensaje.name === 'option-opened' || (mensaje.msg && mensaje.msg.name === 'option-opened')) {
                const body = mensaje.msg?.body || mensaje.msg || mensaje;
                const brokerId = body.id || body.option_id;
                console.log(`[${obtenerHoraLocal()}] [NODE] 🎉 ¡Operación ACEPTADA en IQ Option! (ID Broker: ${brokerId || 'OK'})`);
                for (const [reqId, o] of ordenesActivas.entries()) {
                    if (!o.resuelto && !o.brokerId) {
                        o.brokerId = brokerId;
                        break;
                    }
                }
            }

            // G. Rechazo de orden
            if (mensaje.name === 'socket-option-rejected' || (mensaje.name === 'sendMessage' && mensaje.msg?.is_successful === false)) {
                console.error(`[${obtenerHoraLocal()}] [NODE] ❌ RECHAZO DE IQ OPTION:`, mensaje.msg?.message || JSON.stringify(mensaje.msg));
                // Liberar candado ante rechazo para no bloquear el sistema
                hayOperacionEnCurso = false;
                if (operationWatchdogTimer) clearTimeout(operationWatchdogTimer);
            }

            // H. Resultado oficial de operación finalizada
            if (mensaje.name === 'option-closed' || mensaje.name === 'socket-option-closed' || mensaje.name === 'position-changed' || (mensaje.msg && (mensaje.msg.name === 'option-closed' || mensaje.msg.name === 'socket-option-closed'))) {
                const res = mensaje.msg?.body || mensaje.msg || mensaje;
                finalizarOrdenDesdeBroker(res);
            }

        } catch (err) {
            // Ignorar errores de parsing menores
        }
    });

    ws.on('close', (codigo, razon) => {
        console.warn(`[${obtenerHoraLocal()}] [NODE] 🔴 Conexión cerrada. Código: ${codigo} - Motivo: ${razon || 'Desconexión de red'}`);
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        programarReconexion();
    });

    ws.on('error', (err) => {
        console.error(`[${obtenerHoraLocal()}] [NODE] ❌ Error en WebSocket:`, err.message);
    });
}

function programarReconexion() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectAttempts++;
    
    // Backoff exponencial: 2s, 4s, 8s, 16s, máx 30s
    const delayMs = Math.min(Math.pow(2, reconnectAttempts) * 1000, MAX_RECONNECT_DELAY_MS);
    console.log(`[${obtenerHoraLocal()}] [NODE] 🔄 Reintentando reconexión en ${(delayMs/1000).toFixed(0)} segundos (Intento #${reconnectAttempts})...`);
    
    reconnectTimer = setTimeout(() => {
        conectarWebSocket();
    }, delayMs);
}

// ==============================================================================
// COMUNICACIÓN CON MOTOR PYTHON Y EJECUCIÓN
// ==============================================================================
async function procesarDatosConPython(datosMercado) {
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

        const respuesta = await fetch(PYTHON_API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!respuesta.ok) return;

        const decision = await respuesta.json();
        const hora = obtenerHoraLocal();

        if (decision.warmup && decision.status) {
            console.log(`[${hora}] [BOT] ${decision.status}`);
        } else if (decision.bollinger && decision.rsi !== null) {
            const scoreInfo = decision.score_call !== undefined ? ` | Confluencia [CALL:${decision.score_call}/100 PUT:${decision.score_put}/100]` : '';
            console.log(`[${hora}] [BOT] 🎯 Vela 10s -> Precio: ${datosMercado.price.toFixed(6)} | RSI: ${decision.rsi}${scoreInfo}`);
            if (decision.motivo && !decision.motivo.startsWith('Sin señal')) {
                console.log(`[${hora}] [BOT] 💡 ${decision.motivo}`);
            }
        }

        // Si Python decide comprar (CALL) o vender (PUT) y NO hay otra operación en curso
        if ((decision.action === 'call' || decision.action === 'put') && !hayOperacionEnCurso) {
            ejecutarOperacion(activeBalanceId, decision.active_id || ACTIVE_ID, decision.action, decision.amount || 10.0, datosMercado.price);
        }

    } catch (error) {
        console.error(`[${obtenerHoraLocal()}] [NODE] ⚠️ Motor de Python no disponible:`, error.message);
    }
}

function ejecutarOperacion(balanceId, idActivo, direccion, montoInversion, precioActual) {
    if (hayOperacionEnCurso) return;
    hayOperacionEnCurso = true; // Activar candado estricto

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
        console.error(`[${horaApertura}] [NODE] ❌ WebSocket desconectado al intentar enviar orden #${reqId}`);
        hayOperacionEnCurso = false;
        return;
    }

    const infoOrden = {
        id: reqId,
        direccion: direccion.toUpperCase(),
        monto: montoInversion,
        precioEntrada: precioActual,
        horaApertura: horaApertura,
        horaExpiracion: horaExpiracion,
        expiracionSegundos: EXPIRATION_SECONDS,
        resuelto: false
    };
    ordenesActivas.set(reqId, infoOrden);

    console.log(`\n═══════════════════════════════════════════════════════════════`);
    console.log(`[${horaApertura}] 🚀 [ORDEN #${reqId}] ENVIADA AL BROKER`);
    console.log(`├─ Dirección: ${direccion.toUpperCase() === 'CALL' ? '🟢 CALL (COMPRA)' : '🔴 PUT (VENTA)'}`);
    console.log(`├─ Monto Invertido: $${montoInversion.toFixed(2)} USD`);
    console.log(`├─ Precio Entrada (Strike): ${precioActual.toFixed(6)}`);
    console.log(`└─ Hora Expiración: ${horaExpiracion} (${EXPIRATION_SECONDS}s de duración)`);
    console.log(`═══════════════════════════════════════════════════════════════\n`);

    // Temporizador de respaldo y vigilancia (Watchdog failsafe a EXPIRATION_SECONDS + 8s)
    if (operationWatchdogTimer) clearTimeout(operationWatchdogTimer);
    operationWatchdogTimer = setTimeout(() => {
        evaluarResultadoLocal(reqId);
    }, (EXPIRATION_SECONDS + 8) * 1000);
}

function procesarCierreDeOrden(orden, resultadoOficial, precioSalidaOficial, gananciaNetaOficial) {
    if (!orden || orden.resuelto) return;
    orden.resuelto = true;
    hayOperacionEnCurso = false; // Liberar candado inmediatamente
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

    console.log(`\n═══════════════════════════════════════════════════════════════`);
    console.log(`[${horaCierre}] 🏁 [RESULTADO OFICIAL ORDEN #${orden.id}]`);
    console.log(`├─ Tipo: ${orden.direccion === 'CALL' ? '🟢 CALL' : '🔴 PUT'} | Monto: $${orden.monto.toFixed(2)} USD`);
    console.log(`├─ Entrada: ${orden.precioEntrada.toFixed(6)}  ──▶  Salida: ${precioSalida.toFixed(6)}`);
    if (resultado === 'GANADA') {
        console.log(`├─ Veredicto: 🎉 ¡OPERACIÓN GANADA (ITM)! (+ $${gananciaNeta.toFixed(2)} USD)`);
    } else if (resultado === 'PERDIDA') {
        console.log(`├─ Veredicto: 💥 OPERACIÓN PERDIDA (OTM) (- $${orden.monto.toFixed(2)} USD)`);
    } else {
        console.log(`├─ Veredicto: ⚖️ OPERACIÓN EN EMPATE (ATM) ($0.00 USD)`);
    }
    console.log(`├─ Marcador Sesión: ${estadisticas.ganadas}W - ${estadisticas.perdidas}L - ${estadisticas.empates}D (Win Rate: ${winRate}%)`);
    console.log(`└─ Balance Neto: ${estadisticas.balanceNeto >= 0 ? '+' : ''}$${estadisticas.balanceNeto.toFixed(2)} USD`);
    console.log(`═══════════════════════════════════════════════════════════════\n`);

    // Escribir en archivo TXT
    registrarOperacionTxt(orden, resultado, gananciaNeta, precioSalida, horaCierre);

    // Enviar feedback al motor de Python para almacenamiento estructurado en SQLite/CSV
    fetch('http://127.0.0.1:8000/resultado-operacion', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            id: orden.id,
            order_id: orden.brokerId || orden.id,
            resultado: resultado,
            ganancia_neta: gananciaNeta,
            direccion: orden.direccion,
            precio_entrada: orden.precioEntrada,
            precio_salida: precioSalida,
            balance_actual: saldoActual,
            payout_percent: PROFIT_PERCENT,
            account_type: activeAccountType,
            account_id: String(activeBalanceId)
        })
    }).catch(() => {});
}

function evaluarResultadoLocal(reqId) {
    const orden = ordenesActivas.get(reqId);
    if (!orden || orden.resuelto) return;
    console.log(`[${obtenerHoraLocal()}] [NODE] ⏱️ Temporizador guardián activado para Orden #${reqId}.`);
    procesarCierreDeOrden(orden, null, lastKnownPrice, 0.0);
}

function finalizarOrdenDesdeBroker(res) {
    if (!res) return;
    let orden = null;
    const optionId = res.id || res.option_id;
    if (optionId) {
        for (const [id, o] of ordenesActivas.entries()) {
            if (!o.resuelto && (o.brokerId == optionId || !o.brokerId)) {
                orden = o;
                break;
            }
        }
    }
    if (!orden) {
        for (const [id, o] of ordenesActivas.entries()) {
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

// INICIAR CONEXIÓN
console.log('═══════════════════════════════════════════════════════════════');
console.log('  🤖 BOT DE TRADING CUANTITATIVO - CONEXIÓN CON BROKER IQ OPTION');
console.log(`  ⏰ Hora de inicio: ${obtenerHoraLocal()}`);
console.log(`  📊 Activo ID: ${ACTIVE_ID} (EUR/USD Blitz)`);
console.log(`  ⏱️ Expiración: ${EXPIRATION_SECONDS}s | Payout: ${PROFIT_PERCENT}%`);
console.log(`  💼 Modo Configurado: ${ACCOUNT_MODE.toUpperCase()}`);
console.log('═══════════════════════════════════════════════════════════════');

conectarWebSocket();
