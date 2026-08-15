"""
Script de Migración Histórica
Parsea las operaciones registradas en 'registro_operaciones.txt' e inserta los datos
estructurados en SQLite (data/trades.db) y CSV (data/trades.csv).
"""

import os
import sys
import re
from datetime import datetime

# Añadir motor-logico al path
current_dir = os.path.dirname(os.path.abspath(__file__))
motor_dir = os.path.join(os.path.dirname(current_dir), "motor-logico")
sys.path.append(motor_dir)

from storage import TradeStorage


def migrate_txt_log():
    root_dir = os.path.dirname(current_dir)
    txt_file = os.path.join(root_dir, "registro_operaciones.txt")

    if not os.path.exists(txt_file):
        print(f"[MIGRATION] Archivo {txt_file} no encontrado.")
        return

    print(f"[MIGRATION] Leyendo historial desde: {txt_file}")
    with open(txt_file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # Dividir por sesiones
    sessions = re.split(r'📊 INICIO DE SESIÓN DE TRADING - BOT IQ OPTION', content)
    storage = TradeStorage()

    total_migrated = 0
    ganadas = 0
    perdidas = 0
    empates = 0

    order_regex = re.compile(
        r'\[(\d{2}:\d{2}:\d{2})\]\s+ORDEN\s+#(\d+)\s+-\s+RESULTADO:\s+(GANADA|PERDIDA|EMPATE)\s+\((.*?)\)'
        r'(.*?)'
        r'----------------------------------------------------------------------------------',
        re.DOTALL
    )

    for s_idx, session_text in enumerate(sessions):
        if not session_text.strip():
            continue

        # Extraer fecha de inicio de sesión
        date_match = re.search(r'📅 Fecha y Hora de Inicio:\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})', session_text)
        session_date = date_match.group(1) if date_match else "2026-08-14"
        session_id = f"session_{s_idx}_{session_date}"

        # Extraer operaciones de la sesión
        for match in order_regex.finditer(session_text):
            hora_cierre, order_num, outcome, pnl_str, body = match.groups()

            # Extraer campos detallados del cuerpo
            dir_match = re.search(r'Dirección:\s+(CALL|PUT)', body)
            direction = dir_match.group(1) if dir_match else "CALL"

            monto_match = re.search(r'Monto Invertido:\s+\$([\d\.]+)', body)
            monto = float(monto_match.group(1)) if monto_match else 10.0

            precios_match = re.search(r'Precio Entrada:\s+([\d\.]+)\s+.*?Precio Salida:\s+([\d\.]+)', body)
            if precios_match:
                entry_price = float(precios_match.group(1))
                exit_price = float(precios_match.group(2))
            else:
                entry_price = 0.0
                exit_price = 0.0

            saldo_match = re.search(r'Saldo Real Cuenta Post-Operación:\s+\$([\d\.]+)', body)
            saldo_post = float(saldo_match.group(1)) if saldo_match else 0.0

            cuenta_match = re.search(r'Cuenta:\s+(.*?)\s+\(ID:\s+(\d+)\)', body)
            account_type = cuenta_match.group(1) if cuenta_match else "DEMO"
            account_id = cuenta_match.group(2) if cuenta_match else ""

            # Calcular ganancia neta
            pnl_clean = pnl_str.replace("$", "").replace("USD", "").replace("+", "").strip()
            try:
                net_profit = float(pnl_clean)
            except ValueError:
                net_profit = 8.2 if outcome == "GANADA" else (-10.0 if outcome == "PERDIDA" else 0.0)

            # Construir fecha y hora
            dt_str = f"{session_date} {hora_cierre}"
            try:
                ts = int(datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").timestamp())
            except Exception:
                ts = int(datetime.now().timestamp())

            trade_id = f"{session_id}_ord_{order_num}_{total_migrated+1}"

            storage.save_trade(
                trade_id=trade_id,
                order_id=order_num,
                session_id=session_id,
                timestamp=ts,
                date_time=dt_str,
                account_type=account_type,
                account_id=account_id,
                direction=direction,
                amount=monto,
                entry_price=entry_price,
                exit_price=exit_price,
                payout_percent=82.0,
                expiration_seconds=60,
                outcome=outcome,
                net_profit=net_profit,
                balance_post=saldo_post,
                strategy_name="LegacyConfluence",
                signal_reasons="Migrado desde registro_operaciones.txt"
            )

            total_migrated += 1
            if outcome == "GANADA":
                ganadas += 1
            elif outcome == "PERDIDA":
                perdidas += 1
            else:
                empates += 1

    print(f"\n[MIGRATION] Migración completada con éxito.")
    print(f"            Total de operaciones: {total_migrated}")
    print(f"            Ganadas: {ganadas} | Perdidas: {perdidas} | Empates: {empates}")
    decisivas = ganadas + perdidas
    if decisivas > 0:
        win_rate = (ganadas / decisivas) * 100.0
        print(f"            Win Rate Histórico: {win_rate:.2f}% (Break-even necesario: 54.95%)")
    print(f"            Base de datos SQLite: {storage.db_path}")
    print(f"            Archivo CSV: {storage.csv_path}\n")


if __name__ == "__main__":
    migrate_txt_log()
