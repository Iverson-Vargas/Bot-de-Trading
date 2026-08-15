# Registro de Procedencia de Datos Históricos

> [!IMPORTANT]
> Este archivo documenta el origen y la integridad del dataset usado en el backtest y la calibración.
> Cualquier resultado de rendimiento reportado sin un DATA_PROVENANCE.md correspondiente
> debe considerarse inválido.

## Datos Verificados

- **Archivo:** `eurusd_m1_dukascopy_2023.csv`
- **Ruta Completa:** `C:\Users\luisi\Desktop\bot_trading\data\historical\eurusd_m1_dukascopy_2023.csv`
- **Tamaño:** `17,422,452 bytes`
- **Hash SHA-256:** `7f4bb88af747a61dce9ba21dd99f4585f3163e66a06d3f0df4c2343ecb1e9687`
- **Fuente Declarada:** Dukascopy EURUSD M1 2023
- **Total de Velas:** `322,638`
- **Rango Fecha Inicio:** `2023-01-01 17:04:00 UTC`
- **Rango Fecha Fin:** `2023-12-29 16:58:00 UTC`
- **Timeframe Inferido:** `1m`
- **Verificado el:** `2026-08-15 05:08:41 UTC`

## Instrucciones de Reproducción

Para reproducir cualquier backtest o calibración reportado en este proyecto,
descarga el mismo archivo CSV desde la fuente declarada arriba y verifica que
el hash SHA-256 coincida con el registrado aquí antes de ejecutar los scripts.

```bash
python scripts/verify_data_source.py --data data/historical/TU_ARCHIVO.csv --source "Fuente de los datos"
```

---
*Generado automáticamente por `scripts/verify_data_source.py` el 2026-08-15 05:08:41 UTC*
