"""
Gestor de Procesos del Bot de Trading.
Controla el ciclo de vida del transit.js (Node.js broker) como subproceso,
captura logs en tiempo real y expone estado para la API.
"""

import os
import sys
import time
import subprocess
import threading
from collections import deque
from typing import Optional, Dict, Any
from enum import Enum


class BotState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class ProcessManager:
    def __init__(self, max_log_lines: int = 500):
        self._process: Optional[subprocess.Popen] = None
        self._state = BotState.STOPPED
        self._lock = threading.Lock()
        self._logs: deque = deque(maxlen=max_log_lines)
        self._log_thread: Optional[threading.Thread] = None
        self._started_at: Optional[float] = None
        self._error_message: str = ""

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._transit_dir = os.path.join(base_dir, "transito-broker")
        self._transit_script = os.path.join(self._transit_dir, "transit.js")

    @property
    def state(self) -> BotState:
        with self._lock:
            if self._state == BotState.RUNNING and self._process:
                if self._process.poll() is not None:
                    self._state = BotState.STOPPED
                    self._add_log("[MANAGER] El proceso del broker se detuvo inesperadamente.")
            return self._state

    @property
    def is_running(self) -> bool:
        return self.state == BotState.RUNNING

    def get_status(self) -> Dict[str, Any]:
        current_state = self.state
        uptime = 0.0
        if self._started_at and current_state == BotState.RUNNING:
            uptime = time.time() - self._started_at

        return {
            "state": current_state.value,
            "is_running": current_state == BotState.RUNNING,
            "uptime_seconds": round(uptime, 1),
            "error": self._error_message if current_state == BotState.ERROR else "",
            "pid": self._process.pid if self._process and current_state == BotState.RUNNING else None
        }

    def get_logs(self, last_n: int = 100) -> list:
        with self._lock:
            logs = list(self._logs)
        return logs[-last_n:]

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._state == BotState.RUNNING and self._process and self._process.poll() is None:
                return {"success": False, "message": "El bot ya esta en ejecucion."}

            if self._state in (BotState.STARTING, BotState.STOPPING):
                return {"success": False, "message": f"El bot esta en estado: {self._state.value}. Espera un momento."}

            if not os.path.exists(self._transit_script):
                self._state = BotState.ERROR
                self._error_message = f"No se encontro transit.js en: {self._transit_script}"
                return {"success": False, "message": self._error_message}

            self._state = BotState.STARTING
            self._error_message = ""
            self._logs.clear()

        self._add_log("[MANAGER] Iniciando el broker transit.js...")

        try:
            node_cmd = "node"
            env = os.environ.copy()

            env_path = os.path.join(self._transit_dir, ".env")
            if os.path.exists(env_path):
                self._add_log("[MANAGER] Archivo .env detectado.")

            self._process = subprocess.Popen(
                [node_cmd, self._transit_script],
                cwd=self._transit_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            )

            self._started_at = time.time()
            self._log_thread = threading.Thread(target=self._read_output, daemon=True)
            self._log_thread.start()

            time.sleep(1)
            if self._process.poll() is not None:
                with self._lock:
                    self._state = BotState.ERROR
                    self._error_message = "El proceso de Node.js se detuvo inmediatamente despues de iniciar."
                return {"success": False, "message": self._error_message}

            with self._lock:
                self._state = BotState.RUNNING
            self._add_log("[MANAGER] Broker transit.js iniciado correctamente.")
            return {"success": True, "message": "Bot iniciado correctamente.", "pid": self._process.pid}

        except FileNotFoundError:
            with self._lock:
                self._state = BotState.ERROR
                self._error_message = "Node.js no esta instalado o no esta en el PATH del sistema."
            return {"success": False, "message": self._error_message}
        except Exception as e:
            with self._lock:
                self._state = BotState.ERROR
                self._error_message = str(e)
            return {"success": False, "message": f"Error al iniciar: {e}"}

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            if self._state == BotState.STOPPED or not self._process:
                self._state = BotState.STOPPED
                return {"success": True, "message": "El bot ya estaba detenido."}

            if self._state == BotState.STOPPING:
                return {"success": False, "message": "El bot ya se esta deteniendo."}

            self._state = BotState.STOPPING

        self._add_log("[MANAGER] Deteniendo el broker transit.js...")

        try:
            if sys.platform == "win32":
                self._process.terminate()
            else:
                import signal
                self._process.send_signal(signal.SIGTERM)

            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._add_log("[MANAGER] El proceso no respondio a SIGTERM, forzando cierre...")
                self._process.kill()
                self._process.wait(timeout=5)

        except Exception as e:
            self._add_log(f"[MANAGER] Error al detener: {e}")
        finally:
            with self._lock:
                self._state = BotState.STOPPED
                self._started_at = None

        self._add_log("[MANAGER] Broker transit.js detenido.")
        return {"success": True, "message": "Bot detenido correctamente."}

    def restart(self) -> Dict[str, Any]:
        self.stop()
        time.sleep(1)
        return self.start()

    def _read_output(self):
        try:
            if not self._process or not self._process.stdout:
                return
            for line in self._process.stdout:
                stripped = line.rstrip("\n\r")
                if stripped:
                    self._add_log(stripped)
        except (ValueError, OSError):
            pass

    def _add_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        with self._lock:
            self._logs.append(entry)


bot_manager = ProcessManager()
