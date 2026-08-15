"""
Script de Verificación y Auditoría de Autenticidad de Datos Históricos.
Calcula SHA-256 del archivo, extrae metadatos (rango de fechas, velas, fuente),
rechaza archivos sintéticos a menos que se pase --allow-synthetic-for-testing-only,
y genera DATA_PROVENANCE.md.

NUNCA usar datos generados artificialmente para métricas de rendimiento real.
"""

import os
import sys
import hashlib
import csv
import argparse
from datetime import datetime, timezone

# Asegurar codificación UTF-8 en consola de Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Lista de palabras clave que identifican archivos de datos NO reales (sintéticos)
SYNTHETIC_FILENAME_KEYWORDS = [
    "benchmark",
    "sample",
    "synthetic",
    "generated",
    "simulated",
    "test",
    "fake",
    "dummy",
    "mock"
]


def sha256_of_file(file_path: str) -> str:
    """Calcula el hash SHA-256 de un archivo para verificación de integridad."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_synthetic_name(filename: str) -> bool:
    """Retorna True si el nombre del archivo contiene palabras clave de datos sintéticos."""
    lower = os.path.basename(filename).lower()
    for kw in SYNTHETIC_FILENAME_KEYWORDS:
        if kw in lower:
            return True
    return False


def parse_candle_csv(file_path: str) -> dict:
    """Lee el CSV y extrae metadatos: número de velas, rango de fechas, precio inicial/final."""
    timestamps = []
    open_prices = []
    close_prices = []
    total_rows = 0

    DATE_FMTS = [
        "%Y%m%d %H%M%S",       # 20230101 170400  (Dukascopy sin header)
        "%Y%m%d %H:%M:%S",     # 20230101 17:04:00
        "%Y.%m.%d %H:%M:%S",   # 2023.01.01 17:04:00 (MT5)
        "%Y/%m/%d %H:%M:%S",   # 2023/01/01 17:04:00
        "%Y-%m-%d %H:%M:%S",   # 2023-01-01 17:04:00
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",   # 01.01.2023 17:04:00 (otro Dukascopy)
        "%m/%d/%Y %H:%M",
    ]

    def try_parse_ts(val):
        val = val.strip()
        # Intentar unix timestamp
        try:
            ts = int(float(val))
            if ts > 1_000_000:
                return ts if ts < 1e11 else int(ts / 1000)
        except ValueError:
            pass
        # Intentar formatos de fecha
        for fmt in DATE_FMTS:
            try:
                dt = datetime.strptime(val, fmt)
                return int(dt.replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                pass
        return None

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        # Detectar delimitador con muestra de los primeros 4096 bytes
        sample = f.read(4096)
        f.seek(0)
        delimiter = ","
        if ";" in sample and sample.count(";") > sample.count(","):
            delimiter = ";"
        elif "\t" in sample and sample.count("\t") > sample.count(","):
            delimiter = "\t"

        reader = csv.reader(f, delimiter=delimiter)
        first_row = next(reader, None)
        if not first_row:
            return {}

        # Determinar si primera fila es header o datos
        first_cell = first_row[0].strip()
        first_ts = try_parse_ts(first_cell)

        if first_ts is not None:
            ts_col, open_col, close_col = 0, 1, 4
            # Sin header: procesar primera fila como dato
            sample_rows = [first_row]
        else:
            # Primera fila es header
            header_lower = [c.strip().lower().replace("<","").replace(">","") for c in first_row]
            ts_col, open_col, close_col = None, None, None
            for idx, col in enumerate(header_lower):
                if col in ("time", "timestamp", "datetime", "date", "gmt time"):
                    ts_col = idx
                elif col in ("open", "open_price", "o") and open_col is None:
                    open_col = idx
                elif col in ("close", "close_price", "c", "price") and close_col is None:
                    close_col = idx
            if ts_col is None:
                ts_col, open_col, close_col = 0, 1, 4
            sample_rows = []

        # Solo leer las primeras 200 filas para timestamps/precios de muestra
        for i, row in enumerate(reader):
            if i >= 199:
                break
            sample_rows.append(row)

        for row in sample_rows:
            if not row or len(row) < 4:
                continue
            total_rows += 1

            if ts_col is not None and ts_col < len(row):
                ts = try_parse_ts(row[ts_col])
                if ts is not None:
                    timestamps.append(ts)

            if open_col is not None and open_col < len(row):
                try:
                    open_prices.append(float(row[open_col]))
                except ValueError:
                    pass

            if close_col is not None and close_col < len(row):
                try:
                    close_prices.append(float(row[close_col]))
                except ValueError:
                    pass

    # Contar total de filas de forma eficiente (sin cargar todo en memoria)
    total_rows_real = 0
    has_header_offset = 0 if (first_ts is not None) else 1
    with open(file_path, "rb") as fb:
        for _ in fb:
            total_rows_real += 1
    total_rows = max(total_rows_real - has_header_offset, total_rows)

    # Leer la ÚLTIMA línea para obtener el timestamp final
    last_ts = None
    with open(file_path, "rb") as fb:
        # Buscar la última línea no vacía eficientemente
        try:
            fb.seek(-4096, 2)  # Saltar al final menos 4KB
        except OSError:
            fb.seek(0)
        last_chunk = fb.read().decode("utf-8", errors="ignore")
        last_lines = [l.strip() for l in last_chunk.strip().splitlines() if l.strip()]
        if last_lines:
            last_line = last_lines[-1]
            last_cols = last_line.split(delimiter)
            if ts_col is not None and ts_col < len(last_cols):
                last_ts = try_parse_ts(last_cols[ts_col])

    if last_ts and timestamps:
        timestamps.append(last_ts)


    result = {"total_candles": total_rows}

    if timestamps:
        ts_min = min(timestamps)
        ts_max = max(timestamps)
        result["first_timestamp"] = ts_min
        result["last_timestamp"] = ts_max
        result["date_from"] = datetime.fromtimestamp(ts_min, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        result["date_to"] = datetime.fromtimestamp(ts_max, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Calcular timeframe inferido (mediana de diferencias entre timestamps)
        if len(timestamps) > 10:
            sorted_ts = sorted(timestamps)
            diffs = [sorted_ts[i+1] - sorted_ts[i] for i in range(min(100, len(sorted_ts)-1)) if sorted_ts[i+1] > sorted_ts[i]]
            if diffs:
                diffs.sort()
                median_diff = diffs[len(diffs) // 2]
                result["inferred_timeframe_seconds"] = median_diff

    if open_prices:
        result["open_first"] = open_prices[0]
    if close_prices:
        result["close_last"] = close_prices[-1]

    return result


def verify_data_source(
    file_path: str,
    source_description: str = "",
    allow_synthetic: bool = False
) -> dict:
    """
    Verifica y documenta la fuente de datos.
    Lanza ValueError si los datos son sintéticos y no se ha pasado allow_synthetic.
    """
    abs_path = os.path.abspath(file_path)

    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Archivo de datos no encontrado: {abs_path}")

    # Verificar si el nombre del archivo indica datos sintéticos
    is_synthetic_name = check_synthetic_name(abs_path)
    if is_synthetic_name and not allow_synthetic:
        raise ValueError(
            f"\n\n{'='*70}\n"
            f"❌  DATOS SINTÉTICOS RECHAZADOS — BACKTEST BLOQUEADO\n"
            f"{'='*70}\n"
            f"El archivo '{os.path.basename(abs_path)}' contiene una palabra clave\n"
            f"que indica datos sintéticos o de prueba:\n"
            f"  Palabras clave prohibidas: {', '.join(SYNTHETIC_FILENAME_KEYWORDS)}\n\n"
            f"NO se puede usar este archivo como fuente para métricas de rendimiento.\n"
            f"Proporciona un archivo CSV con datos reales de EUR/USD descargados de\n"
            f"una fuente de mercado verificable (Dukascopy, HistData, MT5, etc.).\n\n"
            f"Si quieres correr el motor para tests internos del backtester,\n"
            f"usa el flag: --allow-synthetic-for-testing-only\n"
            f"{'='*70}\n"
        )

    # Calcular hash SHA-256
    file_hash = sha256_of_file(abs_path)
    file_size = os.path.getsize(abs_path)

    # Parsear metadatos del CSV
    print(f"[VERIFY] Leyendo metadatos de {abs_path}...", flush=True)
    metadata = parse_candle_csv(abs_path)

    report = {
        "file_path": abs_path,
        "file_name": os.path.basename(abs_path),
        "file_size_bytes": file_size,
        "sha256": file_hash,
        "is_synthetic_name": is_synthetic_name,
        "allow_synthetic_override": allow_synthetic,
        "source_description": source_description or "No especificado",
        "verified_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        **metadata
    }

    return report


def print_report(report: dict):
    """Imprime el reporte de verificación en formato legible."""
    print("\n" + "="*70)
    print("  VERIFICACIÓN DE FUENTE DE DATOS HISTÓRICOS")
    print("="*70)
    print(f"  Archivo:       {report['file_name']}")
    print(f"  Ruta:          {report['file_path']}")
    print(f"  Tamaño:        {report['file_size_bytes']:,} bytes")
    print(f"  SHA-256:       {report['sha256']}")
    print(f"  Fuente:        {report['source_description']}")
    print(f"  Verificado:    {report['verified_at']}")
    print("-"*70)
    if "total_candles" in report:
        print(f"  Total Velas:   {report['total_candles']:,}")
    if "date_from" in report:
        print(f"  Desde:         {report['date_from']}")
    if "date_to" in report:
        print(f"  Hasta:         {report['date_to']}")
    if "inferred_timeframe_seconds" in report:
        tf = report["inferred_timeframe_seconds"]
        tf_str = f"{tf}s" if tf < 60 else (f"{tf//60}m" if tf < 3600 else f"{tf//3600}h")
        print(f"  Timeframe:     {tf_str} (inferido del CSV)")
    if report.get("is_synthetic_name"):
        print("  ⚠️  ADVERTENCIA: Nombre de archivo sugiere datos SINTÉTICOS.")
        if report.get("allow_synthetic_override"):
            print("      Flag --allow-synthetic-for-testing-only activo.")
    print("="*70 + "\n")


def save_provenance_md(report: dict, output_dir: str):
    """Escribe DATA_PROVENANCE.md en el directorio de datos históricos."""
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "DATA_PROVENANCE.md")

    tf_line = ""
    if "inferred_timeframe_seconds" in report:
        tf = report["inferred_timeframe_seconds"]
        tf_str = f"{tf}s" if tf < 60 else (f"{tf//60}m" if tf < 3600 else f"{tf//3600}h")
        tf_line = f"- **Timeframe Inferido:** `{tf_str}`\n"

    synthetic_warning = ""
    if report.get("is_synthetic_name"):
        synthetic_warning = "\n> [!CAUTION]\n> El nombre de archivo sugiere datos **SINTÉTICOS**. Las métricas producidas con estos datos NO son válidas como evidencia de estrategia real.\n"

    content = f"""# Registro de Procedencia de Datos Históricos

> [!IMPORTANT]
> Este archivo documenta el origen y la integridad del dataset usado en el backtest y la calibración.
> Cualquier resultado de rendimiento reportado sin un DATA_PROVENANCE.md correspondiente
> debe considerarse inválido.

## Datos Verificados

- **Archivo:** `{report['file_name']}`
- **Ruta Completa:** `{report['file_path']}`
- **Tamaño:** `{report['file_size_bytes']:,} bytes`
- **Hash SHA-256:** `{report['sha256']}`
- **Fuente Declarada:** {report['source_description']}
- **Total de Velas:** `{report.get('total_candles', 'N/A'):,}`
- **Rango Fecha Inicio:** `{report.get('date_from', 'N/A')}`
- **Rango Fecha Fin:** `{report.get('date_to', 'N/A')}`
{tf_line}- **Verificado el:** `{report['verified_at']}`
{synthetic_warning}
## Instrucciones de Reproducción

Para reproducir cualquier backtest o calibración reportado en este proyecto,
descarga el mismo archivo CSV desde la fuente declarada arriba y verifica que
el hash SHA-256 coincida con el registrado aquí antes de ejecutar los scripts.

```bash
python scripts/verify_data_source.py --data data/historical/TU_ARCHIVO.csv --source "Fuente de los datos"
```

---
*Generado automáticamente por `scripts/verify_data_source.py` el {report['verified_at']}*
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[VERIFY] DATA_PROVENANCE.md guardado en: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Verifica la autenticidad y metadatos de un archivo de datos históricos."
    )
    parser.add_argument("--data", required=True, help="Ruta al archivo CSV de datos históricos")
    parser.add_argument("--source", type=str, default="", help="Descripción de la fuente (ej: 'Dukascopy, EURUSD, M1, 2024-01 a 2024-08')")
    parser.add_argument("--allow-synthetic-for-testing-only", action="store_true",
                        help="Permite datos sintéticos pero solo para tests internos del motor. NUNCA para métricas de rendimiento.")

    args = parser.parse_args()

    allow_syn = getattr(args, "allow_synthetic_for_testing_only", False)

    try:
        report = verify_data_source(
            file_path=args.data,
            source_description=args.source,
            allow_synthetic=allow_syn
        )
        print_report(report)

        # Guardar provenance en el mismo directorio que el archivo de datos
        hist_dir = os.path.dirname(os.path.abspath(args.data))
        save_provenance_md(report, hist_dir)

        return report

    except (ValueError, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
