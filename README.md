# 🤖 Bot Cuantitativo de Trading Algorítmico (EUR/USD Blitz 60s)

Ecosistema cuantitativo modular para el análisis técnico, simulación de backtesting con validación *Out-of-Sample*, calibración estadística de indicadores, gestión de riesgo dinámica y ejecución automatizada para opciones binarias Blitz (60s).

---

## 📐 Arquitectura del Sistema

```
bot_trading/
├── data/
│   ├── historical/             # Datasets de velas OHLC para backtesting (EUR/USD)
│   ├── trades.db               # Base de datos SQLite estructurada con snapshots de indicadores
│   └── trades.csv              # Exportación CSV sincronizada para análisis en Python/Excel
├── motor-logico/
│   ├── indicators.py           # Indicadores técnicos matemáticos puros (zero-dependency)
│   ├── strategy.py             # Estrategias (LegacyConfluence vs CalibratedStatistical)
│   ├── risk_manager.py         # Dimensionamiento dinámico (% Balance, Kelly, límites de rachas)
│   ├── storage.py              # Almacenamiento estructurado dual en SQLite y CSV
│   ├── backtester.py           # Motor de backtesting con métricas financieras y Walk-Forward
│   ├── calibrator.py           # Calibrador estadístico y análisis de poder predictivo
│   ├── data_fetcher.py         # Importador y generador de benchmarks de mercado
│   ├── dashboard.html          # Panel web interactivo con medidor de Win Rate vs Break-Even
│   └── main.py                 # Servicio FastAPI con endpoints en vivo
├── transito-broker/
│   ├── transit.js              # Conector WebSocket Node.js robustecido (Auto-reconexión, Failsafe)
│   ├── package.json
│   └── .env (Credenciales excluidas de Git)
├── scripts/
│   ├── run_backtest.py         # CLI de simulación y comparativa de estrategias
│   ├── calibrate_strategy.py   # CLI de análisis de correlación de indicadores
│   ├── migrate_txt_to_db.py    # Migrador del log histórico a SQLite/CSV
│   └── fetch_market_data.py    # Gestor de datasets de velas
└── tests/
    └── test_trading_system.py  # Suite de pruebas unitarias
```

---

## 📊 Diagnóstico Cuantitativo y Resultados del Backtest

### 1. ¿Por qué perdía dinero la estrategia Legacy original?
* **Punto de Equilibrio Matemático (Payout 82%)**: Se requiere un **54.95% de acierto** mínimo:
  $$\text{Break-Even Win Rate} = \frac{1}{1 + \text{payout}} = \frac{1}{1 + 0.82} = 54.95\%$$
* **Rendimiento Histórico del Log (88 operaciones)**: **50.00% Win Rate** (41 Ganadas / 41 Perdidas / 6 Empates), resultando en un **Edge negativo de -4.95%** y una pérdida neta de **-$73.80 USD**.
* **Causa Raíz Descubierta en Calibración**: En temporalidades ultracortas (velas de 10s), operar contra-tendencia simplemente porque el precio toca la banda de Bollinger o el RSI está en sobreventa (<30) tiene una tasa de acierto de solo el **12% - 18%**, ya que el precio suele continuar en expansión tendencial los siguientes 60 segundos.

### 2. Comparativa de Rendimiento (Walk-Forward / Out-of-Sample)

| Métrica | Estrategia Legacy (Original) | Estrategia Calibrada (Optimizada) | Umbral Break-Even (82% Payout) |
| :--- | :---: | :---: | :---: |
| **In-Sample (70% Entrenamiento)** | 50.00% (-4.95% Edge) | **64.18% (+9.23% Edge)** | 54.95% |
| **Out-of-Sample (30% Validación Ciega)** | 21.43% (-33.52% Edge) | **66.67% (+11.72% Edge)** | 54.95% |
| **Profit Factor (Out-of-Sample)** | 0.19 | **1.59** | > 1.00 |
| **PnL Neto Simulado (Out-of-Sample)** | -$68.60 USD | **+$53.50 USD** | Positivo |
| **Máximo Drawdown** | $68.60 (0.7%) | **$20.00 (0.2%)** | Controlado |

---

## 🚀 Guía de Uso

### 1. Ejecutar Pruebas Unitarias
```bash
python tests/test_trading_system.py
```

### 2. Ejecutar Simulación de Backtesting Histórico
```bash
# Comparación directa de ambas estrategias vs Break-Even (54.95%)
python scripts/run_backtest.py --strategy compare

# Validación cruzada Out-of-Sample (70% In-Sample / 30% Out-of-Sample)
python scripts/run_backtest.py --walk-forward
```

### 3. Calibración Estadística de Indicadores
```bash
python scripts/calibrate_strategy.py
```

### 4. Migrar Registro TXT a Base de Datos Estructurada
```bash
python scripts/migrate_txt_to_db.py
```

### 5. Iniciar el Motor Lógico (FastAPI) y Panel en Vivo
```bash
# Iniciar servidor backend
python -m uvicorn motor-logico.main:app --reload --port 8000
```
* **Panel de Control en Vivo**: Abre en tu navegador `http://127.0.0.1:8000/dashboard`
* **Métricas en Tiempo Real**: `GET http://127.0.0.1:8000/metrics/live`
* **Historial Estructurado**: `GET http://127.0.0.1:8000/trades`

### 6. Iniciar el Conector Broker en Modo Demo (Node.js)
```bash
cd transito-broker
node transit.js
```

---

## 🛡️ Checklist de Seguridad antes de Operar con Dinero Real

Antes de considerar arriesgar capital real, se debe completar rigurosamente esta lista de verificación:

- [ ] **Muestra Estadística Significativa**: Acumular un mínimo de **150 a 200 operaciones en cuenta DEMO** bajo condiciones de mercado en vivo para confirmar que el Win Rate real se mantiene por encima del **56.0%**.
- [ ] **Medición de Latencia y Slippage**: Validar que la ejecución entre la señal de Python y la confirmación del broker tome menos de **300ms** para evitar desplazamientos de precio adversos en opciones de 60 segundos.
- [ ] **Filtro de Horarios y Payouts**: Operar únicamente en ventanas horarias donde el broker garantiza un payout $\ge 80\%$ (habitualmente durante solapamientos de sesiones Londres/Nueva York) y evitar horarios de noticias de alto impacto (NFP, IPC, decisiones de tipos de interés).
- [ ] **Gestión de Capital Estricta**: Mantener el dimensionamiento de posición en **Quarter Kelly** (máximo 1.5% - 2.0% del balance por operación) y nunca desactivar los límites de **Stop Loss diario (-5%)** ni la reducción de stake tras rachas negativas.
- [ ] **Vigilancia de Credenciales**: Asegurar que el SSID de IQ Option sea renovado periódicamente y que el archivo `.env` jamás sea compartido ni subido a repositorios públicos.
