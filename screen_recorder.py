"""Grabador de examen: ventana pequeña con matrícula, aviso de grabación y botón Detener.

La captura, el troceado y la subida a Drive están en recorder_core.py.
Configuración: config.json junto a este archivo (o junto al .exe).
"""
import os
import queue
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from recorder_core import Recorder, load_config, sanitize_id

RED, GREEN, ORANGE, GREY = "#b00020", "#1b7f3b", "#b35c00", "#444444"


def base_dir():
    if getattr(sys, "frozen", False):  # empaquetado con PyInstaller
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def open_folder(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


class App:
    def __init__(self, root):
        self.root = root
        root.title("Grabador de examen")
        root.resizable(False, False)

        try:
            self.cfg = load_config(base_dir())
        except Exception as e:
            messagebox.showerror("Configuración", f"No se pudo leer config.json:\n{e}")
            root.destroy()
            raise SystemExit(1)

        self.events = queue.Queue()
        self.rec = None
        self.state = "idle"
        self.t0 = None
        self.warning = ""
        self.pending_dir = None

        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold", size=11)

        frm = ttk.Frame(root, padding=16)
        frm.grid()
        ttk.Label(frm, text="Matrícula:").grid(row=0, column=0, sticky="w")
        self.id_var = tk.StringVar()
        self.entry = ttk.Entry(frm, textvariable=self.id_var, width=28)
        self.entry.grid(row=1, column=0, pady=(2, 10), sticky="ew")
        self.entry.bind("<Return>", lambda _e: self.start())
        self.btn = ttk.Button(frm, text="Iniciar grabación", command=self.start)
        self.btn.grid(row=2, column=0, sticky="ew")
        self.status = tk.Label(frm, text="", font=bold, wraplength=330, justify="left", anchor="w")
        self.status.grid(row=3, column=0, pady=(14, 0), sticky="w")
        self.detail = tk.Label(frm, text="", wraplength=330, justify="left", anchor="w", fg=GREY)
        self.detail.grid(row=4, column=0, pady=(4, 0), sticky="w")
        self.folder_btn = ttk.Button(frm, text="Abrir carpeta", command=self.show_folder)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.entry.focus()
        self.root.after(200, self.poll)

    # ---- estados -------------------------------------------------------
    def set_state(self, state):
        self.state = state
        self.folder_btn.grid_remove()
        if state == "idle":
            self.entry.config(state="normal")
            self.btn.config(text="Iniciar grabación", command=self.start, state="normal")
        elif state == "starting":
            self.entry.config(state="disabled")
            self.btn.config(state="disabled")
            self.set_status("Iniciando…", GREY, "Preparando la grabación.")
        elif state == "recording":
            self.btn.config(text="Detener grabación", command=self.confirm_stop, state="normal")
        elif state == "finishing":
            self.btn.config(state="disabled")
            self.set_status("Subiendo los últimos fragmentos…", ORANGE,
                            "No cierres esta ventana ni apagues la computadora.")
        elif state == "done":
            self.btn.config(text="Cerrar", command=self.root.destroy, state="normal")

    def set_status(self, text, color, detail=""):
        self.status.config(text=text, fg=color)
        self.detail.config(text=detail)

    def refresh_recording_labels(self):
        elapsed = int(time.monotonic() - self.t0)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        rec = self.rec
        text = f"●  GRABANDO   {h:02d}:{m:02d}:{s:02d}"
        if not rec.cfg["upload_url"]:
            detail = "Modo local: la subida a Drive está desactivada."
        elif self.warning:
            detail = self.warning
        else:
            detail = f"Fragmentos subidos: {rec.chunks_uploaded} de {rec.chunks_total}"
        color = RED if not self.warning else ORANGE
        self.set_status(text, RED, "")
        self.detail.config(text=detail, fg=color if self.warning else GREY)

    # ---- acciones ------------------------------------------------------
    def start(self):
        if self.state != "idle":
            return
        student_id = self.id_var.get().strip()
        if not sanitize_id(student_id):
            messagebox.showwarning("Matrícula", "Escribe tu matrícula antes de iniciar.")
            return
        try:
            self.rec = Recorder(self.cfg, student_id,
                                on_event=lambda kind, data: self.events.put((kind, data)))
            self.rec.start()
        except Exception as e:
            messagebox.showerror("No se pudo iniciar", str(e))
            self.rec = None
            return
        self.warning = ""
        self.set_state("starting")

    def confirm_stop(self):
        if self.state != "recording":
            return
        if messagebox.askyesno("Detener grabación",
                               "¿Seguro que quieres terminar la grabación?\n"
                               "Hazlo solo cuando hayas entregado tu examen.",
                               icon="warning"):
            self.rec.stop()
            self.set_state("finishing")

    def on_close(self):
        if self.state in ("idle", "done"):
            self.root.destroy()
        elif self.state == "recording":
            self.confirm_stop()
        else:
            messagebox.showinfo("Espera", "Espera a que termine de subir los archivos.")

    def show_folder(self):
        if self.pending_dir:
            open_folder(self.pending_dir)

    # ---- eventos del grabador -----------------------------------------
    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                self.handle(kind, data)
        except queue.Empty:
            pass
        if self.state == "recording":
            self.refresh_recording_labels()
        self.root.after(200, self.poll)

    def handle(self, kind, data):
        if kind == "started":
            self.t0 = time.monotonic()
            self.set_state("recording")
        elif kind == "uploaded":
            self.warning = ""
        elif kind == "upload_failed":
            n = data.get("pending", 0)
            self.warning = (f"⚠ Sin conexión con el servidor: {n} fragmento(s) pendientes. "
                            "Reintentando… revisa tu internet.")
        elif kind == "error":
            if self.state in ("starting", "recording"):
                self.set_state("finishing")  # la captura ya se detuvo; solo falta subir
            messagebox.showerror("Error de grabación", data.get("message", "Error desconocido"))
        elif kind == "finished":
            self.finish(data)

    def finish(self, s):
        self.set_state("done")
        if s["chunks_total"] == 0:
            self.set_state("idle")
            self.set_status("No se grabó nada.", RED, "Vuelve a intentarlo.")
            self.rec = None
            return
        pending = s["pending"]
        if not pending:
            detail = f"Todos los fragmentos ({s['chunks_total']}) se subieron correctamente."
            if s["upload_enabled"] and not s["final_marker"]:
                detail += "\nNo se pudo enviar la marca final; avisa a tu profesor."
            self.set_status("✔ Grabación terminada", GREEN, detail + "\nYa puedes cerrar esta ventana.")
            return
        self.pending_dir = s["work_dir"]
        if not s["upload_enabled"]:
            why = "La subida está desactivada en config.json (modo local)."
        else:
            why = "No se pudieron subir (revisa tu internet)."
        self.set_status(f"⚠ ATENCIÓN: {len(pending)} fragmento(s) sin subir", RED,
                        f"{why}\nSe conservaron en:\n{s['work_dir']}\n"
                        "NO borres esa carpeta y avisa a tu profesor.")
        self.folder_btn.grid(row=5, column=0, pady=(10, 0), sticky="w")


def main():
    try:  # pantallas con escala (125 %, 150 %): capturar a resolución real
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
