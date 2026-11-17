"""Núcleo del grabador de exámenes (sin interfaz gráfica).

Toma una captura de pantalla cada `interval_s` segundos y las guarda a `fps`
cuadros por segundo en fragmentos AVI independientes de `chunk_frames` cuadros
(240 cuadros a 4 fps = 60 s de video = 4 min de pantalla real). Cada fragmento
se sube a Google Drive mediante el receptor de Apps Script (receptor_drive.gs)
y solo se borra del disco cuando el servidor confirma el tamaño y el MD5.
Lo que no se logra subir se conserva en `work_dir` y se reporta al final.

La interfaz vive en screen_recorder.py; este módulo no importa tkinter.
"""
import base64
import hashlib
import json
import re
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import requests

DEFAULT_CONFIG = {
    "upload_url": "",            # URL de la aplicación web de Apps Script; vacío = no subir
    "token": "",                 # secreto compartido con receptor_drive.gs
    "fps": 4,                    # cuadros por segundo del video resultante
    "interval_s": 1.0,           # segundos entre capturas de pantalla
    "chunk_frames": 240,         # cuadros por fragmento (240 / 4 fps = 60 s de video)
    "scale": 1.0,                # 0.5 reduce el tamaño de los archivos a ~1/4
    "codec": "XVID",
    "work_dir": "grabaciones_pendientes",
    "retry_delay_s": 20,         # espera entre reintentos mientras se graba
    "final_attempts": 3,         # pasadas de subida al detener
    "final_wait_s": 5,           # espera entre pasadas al detener
    "final_max_s": 180,          # tiempo máximo total de subida al detener
    "request_timeout_s": 120,    # lectura de la respuesta del servidor
}

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IP_SERVICES = (
    "https://api.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://ident.me",
)


def load_config(base_dir):
    """Lee config.json junto al programa y lo mezcla con los valores por defecto."""
    base_dir = Path(base_dir)
    cfg = dict(DEFAULT_CONFIG)
    path = base_dir / "config.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        for key, value in user.items():
            if key.startswith("_"):
                continue
            if key not in DEFAULT_CONFIG:
                raise ValueError(f"Clave desconocida en config.json: {key!r}")
            cfg[key] = value
    work = Path(cfg["work_dir"])
    cfg["work_dir"] = work if work.is_absolute() else base_dir / work
    return cfg


def sanitize_id(text):
    """Deja solo letras y números (igual que el script original)."""
    return re.sub(r"[^a-zA-Z0-9]", "", text or "")


def get_ip_hex(timeout=3.0):
    """IP externa (o interna si no hay red) como 8 caracteres hexadecimales."""
    ip = None
    for url in _IP_SERVICES:
        try:
            r = requests.get(url, timeout=timeout)
            m = _IPV4.search(r.text)
            if r.ok and m:
                ip = m.group(0)
                break
        except requests.RequestException:
            continue
    if ip is None:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = None
    try:
        parts = [int(p) for p in (ip or "").split(".")]
        if len(parts) == 4 and all(0 <= p <= 255 for p in parts):
            return "".join(f"{p:02X}" for p in parts)
    except ValueError:
        pass
    return "00000000"


class Uploader:
    """Cliente del receptor de Apps Script. Un solo intento por llamada."""

    def __init__(self, url, token, read_timeout=120):
        self.url = (url or "").strip()
        self.token = token
        self.timeout = (10, float(read_timeout))
        self._session = requests.Session()

    @property
    def enabled(self):
        return bool(self.url)

    def _post(self, payload):
        payload = dict(payload, token=self.token)
        # Apps Script responde con 302 a otra URL; requests lo sigue con GET,
        # que es justo lo que ese servicio espera.
        r = self._session.post(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "text/plain;charset=utf-8"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def upload_chunk(self, folder, path):
        """Devuelve (ok, detalle). ok solo si el servidor confirma tamaño y MD5."""
        try:
            data = Path(path).read_bytes()
            md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
            resp = self._post({
                "action": "chunk",
                "folder": folder,
                "name": Path(path).name,
                "md5": md5,
                "size": len(data),
                "data": base64.b64encode(data).decode("ascii"),
            })
        except (OSError, requests.RequestException, ValueError) as e:
            return False, f"{type(e).__name__}: {e}"
        if not isinstance(resp, dict) or not resp.get("ok"):
            error = resp.get("error") if isinstance(resp, dict) else None
            return False, f"servidor: {error or 'respuesta inválida'}"
        if resp.get("md5") != md5 or resp.get("size") != len(data):
            return False, "el servidor guardó un archivo distinto (MD5/tamaño no coinciden)"
        return True, "ok"

    def send_final(self, folder, info):
        try:
            resp = self._post({"action": "final", "folder": folder, "info": info})
        except (requests.RequestException, ValueError) as e:
            return False, f"{type(e).__name__}: {e}"
        ok = isinstance(resp, dict) and bool(resp.get("ok"))
        return ok, "ok" if ok else "respuesta inválida"


def _screen_frame():
    import pyautogui  # import tardío: el núcleo se puede probar sin pantalla
    return cv2.cvtColor(np.array(pyautogui.screenshot()), cv2.COLOR_RGB2BGR)


class Recorder:
    """Graba en un hilo y sube en otro. Se comunica mediante `on_event(kind, data)`.

    Eventos: started, chunk_closed, uploaded, upload_failed, error, finished.
    `on_event` se llama desde hilos de trabajo; si lo usas desde una interfaz,
    solo encola el evento y procésalo en el hilo de la interfaz.
    """

    def __init__(self, cfg, student_id, frame_source=None, on_event=None):
        self.cfg = cfg
        self.student_id = sanitize_id(student_id)
        if not self.student_id:
            raise ValueError("La matrícula debe tener al menos una letra o un número.")
        self._frame_source = frame_source or _screen_frame
        self._on_event = on_event
        self._uploader = Uploader(cfg["upload_url"], cfg["token"], cfg["request_timeout_s"])
        self._stop = threading.Event()
        self._capture_done = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._pending = []
        self.base_name = None
        self.chunks_total = 0
        self.chunks_uploaded = 0
        self.frames_total = 0
        self.skipped_frames = 0
        self.started_iso = None
        self.summary = None

    # ---- control -------------------------------------------------------
    def start(self):
        self.cfg["work_dir"].mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._capture_loop, name="captura", daemon=True).start()
        threading.Thread(target=self._upload_loop, name="subida", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    # ---- utilidades internas ------------------------------------------
    def _emit(self, kind, **data):
        if self._on_event:
            try:
                self._on_event(kind, data)
            except Exception:
                pass

    def _pending_snapshot(self):
        with self._lock:
            return list(self._pending)

    def _grab(self):
        frame = self._frame_source()
        scale = float(self.cfg["scale"])
        if scale != 1.0:
            frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        h, w = frame.shape[:2]
        if h % 2 or w % 2:  # algunos códecs exigen dimensiones pares
            frame = frame[: h // 2 * 2, : w // 2 * 2]
        return frame

    def _chunk_closed(self, path):
        with self._lock:
            self._pending.append(path)
            self.chunks_total += 1
            pending = len(self._pending)
        self._emit("chunk_closed", path=str(path), pending=pending)
        self._wake.set()

    # ---- captura -------------------------------------------------------
    def _capture_loop(self):
        writer = None
        path = None
        size = None
        frames_in_chunk = 0
        part = 0
        errors = 0

        def close_writer():
            nonlocal writer
            if writer is not None:
                try:
                    writer.release()
                finally:
                    writer = None
                    self._chunk_closed(path)

        try:
            ip_hex = get_ip_hex()
            now = datetime.now()
            self.started_iso = now.isoformat(timespec="seconds")
            self.base_name = f"{self.student_id}_{now:%Y-%m-%d_%H-%M-%S}_{ip_hex}"
            self._emit("started", base_name=self.base_name)

            fourcc = cv2.VideoWriter_fourcc(*self.cfg["codec"])
            interval = float(self.cfg["interval_s"])
            chunk_frames = int(self.cfg["chunk_frames"])
            next_t = time.monotonic()

            while not self._stop.is_set():
                frame = None
                try:
                    frame = self._grab()
                    errors = 0
                except Exception as e:  # p. ej. pantalla bloqueada un instante
                    errors += 1
                    self.skipped_frames += 1
                    if errors >= 30:
                        raise RuntimeError(f"No se pudo capturar la pantalla: {e}")

                if frame is not None:
                    if writer is None:
                        part += 1
                        path = self.cfg["work_dir"] / f"{self.base_name}_parte{part:03d}.avi"
                        size = (frame.shape[1], frame.shape[0])
                        writer = cv2.VideoWriter(str(path), fourcc, float(self.cfg["fps"]), size)
                        if not writer.isOpened():
                            writer = None
                            raise RuntimeError(
                                f"No se pudo crear el archivo de video en {path}. "
                                "Revisa que la carpeta sea escribible (rutas con acentos "
                                "pueden dar problemas en Windows)."
                            )
                        frames_in_chunk = 0
                    elif (frame.shape[1], frame.shape[0]) != size:
                        frame = cv2.resize(frame, size)
                    writer.write(frame)
                    frames_in_chunk += 1
                    self.frames_total += 1
                    if frames_in_chunk >= chunk_frames:
                        close_writer()

                # Horario fijo: no acumula retraso con el tiempo de captura.
                next_t += interval
                delay = next_t - time.monotonic()
                if delay > 0:
                    self._stop.wait(delay)
                else:
                    next_t = time.monotonic()
        except Exception as e:
            self._emit("error", message=str(e))
        finally:
            try:
                close_writer()  # conserva lo grabado hasta el último cuadro
            except Exception:
                pass
            self._stop.set()
            self._capture_done.set()
            self._wake.set()

    # ---- subida --------------------------------------------------------
    def _upload_one(self, path):
        ok, detail = self._uploader.upload_chunk(self.base_name, path)
        if ok:
            with self._lock:
                if path in self._pending:
                    self._pending.remove(path)
                self.chunks_uploaded += 1
                pending = len(self._pending)
            try:
                Path(path).unlink()
            except OSError:
                pass  # subido igualmente; solo queda una copia local
            self._emit("uploaded", path=str(path), pending=pending)
        else:
            self._emit("upload_failed", path=str(path), detail=detail,
                       pending=len(self._pending_snapshot()))
        return ok

    def _upload_loop(self):
        next_try = 0.0
        while not self._capture_done.is_set():
            pending = self._pending_snapshot()
            if (not pending or not self._uploader.enabled
                    or time.monotonic() < next_try):
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            if self._upload_one(pending[0]):
                next_try = 0.0
            else:
                next_try = time.monotonic() + float(self.cfg["retry_delay_s"])
        self._flush_and_finish()

    def _flush_and_finish(self):
        cfg = self.cfg
        attempts = int(cfg["final_attempts"])
        deadline = time.monotonic() + float(cfg["final_max_s"])

        if self._uploader.enabled and self.base_name:
            for attempt in range(attempts):
                for path in self._pending_snapshot():
                    if time.monotonic() > deadline:
                        break
                    self._upload_one(path)
                if not self._pending_snapshot() or time.monotonic() > deadline:
                    break
                if attempt < attempts - 1:
                    time.sleep(float(cfg["final_wait_s"]))

        pending = self._pending_snapshot()
        marker = False
        if self._uploader.enabled and self.base_name and self.chunks_total and not pending:
            info = {
                "alumno": self.student_id,
                "inicio": self.started_iso,
                "fin": datetime.now().isoformat(timespec="seconds"),
                "partes": self.chunks_total,
                "cuadros": self.frames_total,
                "cuadros_omitidos": self.skipped_frames,
            }
            for _ in range(2):
                marker, _detail = self._uploader.send_final(self.base_name, info)
                if marker:
                    break
                time.sleep(float(cfg["final_wait_s"]))

        if not pending:
            try:
                cfg["work_dir"].rmdir()  # solo si está vacía
            except OSError:
                pass

        self.summary = {
            "base_name": self.base_name,
            "chunks_total": self.chunks_total,
            "chunks_uploaded": self.chunks_uploaded,
            "pending": [str(p) for p in pending],
            "work_dir": str(cfg["work_dir"]),
            "final_marker": marker,
            "upload_enabled": self._uploader.enabled,
            "frames_total": self.frames_total,
            "skipped_frames": self.skipped_frames,
        }
        self._emit("finished", **self.summary)
