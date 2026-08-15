# Instrucciones para Descargar Datos Reales de EUR/USD

> [!IMPORTANT]
> El backtest **NO aceptará** ningún archivo sintético, generado artificialmente, o con nombres
> como `sample`, `benchmark`, `synthetic`, `test`, etc.
> Debes descargar datos reales de una de las fuentes listadas abajo y colocarlos en esta carpeta.

---

## Fuente 1: Dukascopy (Recomendado — Gratuito, tick + OHLC, desde 2003)

### Opción A: Descarga manual vía Web

**Página principal de exportación:**
🔗 https://www.dukascopy.com/swiss/english/marketwatch/historical/

**Acceso directo a la herramienta de datos históricos:**
🔗 https://www.dukascopy.com/swiss/english/fx-market-tools/historical-data/

1. Abre el link directo de la herramienta
2. En **Instrument**, selecciona: `EUR/USD`
3. En **Time interval**, selecciona: `M1` (1 minuto)
4. Establece el rango de fechas (ej. 01/01/2023 a 31/12/2023)
5. En **Format**, selecciona: `CSV`
6. Clic en **Download**

El archivo descargado tendrá este formato:
```
Gmt time,Open,High,Low,Close,Volume
01.01.2024 00:01:00.000,1.10450,1.10470,1.10440,1.10460,12.34
```

6. **Renombra el archivo** a: `eurusd_m1_dukascopy_2024.csv`
7. **Colócalo en esta carpeta**: `data/historical/`

---

### Opción B: Descarga programática con duka-py

Instala la librería:
```bash
pip install duka
```

Descarga datos directamente:
```bash
python -m duka.app --instrument EURUSD --startDate 2024-01-01 --endDate 2024-08-01 --timeframe M1
```

---

## Fuente 2: HistData.com (Gratuito, OHLC mensual, desde 2000)

1. Ve a **https://www.histdata.com/download-free-forex-historical-data/?/metatrader/1-minute-bar-quotes/EURUSD/**
2. Selecciona el año y mes que quieres (ej. 2024 / Enero)
3. Descarga el archivo `.zip`
4. Extrae el `.csv` contenido en el zip
5. El formato es:
   ```
   20240101 000100,1.10450,1.10470,1.10440,1.10460,0
   ```
   (columnas: `DateTime,Open,High,Low,Close,Volume`, sin header)

6. **Añade manualmente la primera línea de encabezado**:
   ```
   DateTime,Open,High,Low,Close,Volume
   ```
7. **Renombra el archivo** a: `eurusd_m1_histdata_2024.csv`
8. **Colócalo en esta carpeta**: `data/historical/`

---

## Fuente 3: MetaTrader 5 (si tienes MT5 instalado)

1. Abre MetaTrader 5
2. En la pestaña **Symbols**, busca `EURUSD`
3. Clic derecho → **History Center**
4. Selecciona timeframe M1
5. Botón **Export** → guarda como CSV
6. Renombra a: `eurusd_m1_mt5.csv` y colócalo en `data/historical/`

---

## Verificar que los datos son auténticos

Después de descargar y colocar el archivo, ejecuta:

```bash
python scripts/verify_data_source.py \
  --data data/historical/eurusd_m1_dukascopy_2024.csv \
  --source "Dukascopy Historical Data Feed, EURUSD, M1, 2024-01-01 a 2024-08-01"
```

El script:
- Calculará el **hash SHA-256** del archivo
- Mostrará el rango de fechas y número de velas
- Rechazará el archivo si el nombre parece sintético
- Generará `DATA_PROVENANCE.md` con el registro de integridad

---

## Ejecutar el Backtest con Datos Reales

```bash
python scripts/run_backtest.py \
  --data data/historical/eurusd_m1_dukascopy_2024.csv \
  --source "Dukascopy EURUSD M1 2024-01 a 2024-08" \
  --timeframe 60 \
  --payout 82 \
  --walk-forward \
  --strategy compare
```

> [!NOTE]
> El timeframe de velas de 1 minuto (M1) = 60 segundos.
> La expiración de las opciones Blitz es también de 60 segundos.
> Por tanto, con velas M1, **cada vela = 1 operación potencial**.

---

## Nota sobre Velas de 10 Segundos

El bot opera en IQ Option con expiración de 60 segundos sobre velas de 10s (6 velas por expiración).
Para replicar exactamente esa resolución necesitas **datos de tick de Dukascopy** + re-muestreo.

Sin embargo, **velas de 1 minuto son suficientes** para validar el edge estadístico:
- Cada cierre de vela M1 = señal de entrada potencial
- La siguiente vela M1 = resultado de la operación (cierre de 60s)
- El sistema de puntaje de confluencia funciona correctamente sobre M1

---

*Archivo generado por el sistema de trading algorítmico.*
*No usar datos sintéticos para backtest de rendimiento.*
