"""Core of the exam screen recorder (no GUI).

Takes a screenshot every `interval_s` seconds and saves them at `fps` frames per
second into independent MP4 (H.264) chunks of `chunk_frames` frames (240 frames at
4 fps = 60 s of video = 4 min of real screen time). Encoding is done by an ffmpeg
executable that receives raw frames through a pipe (it is bundled into the .exe, see
README). Each chunk is uploaded to Google Drive
through the Apps Script receiver (drive_receiver.gs) and is deleted from disk only
after the server confirms the size and MD5. Anything that could not be uploaded is
kept in `work_dir` and reported at the end.

The GUI lives in screen_recorder.py; this module does not import tkinter.
"""
import base64
import collections
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

DEFAULT_CONFIG = {
    "upload_url": "",            # Apps Script web app URL; empty = do not upload
    "token": "",                 # shared secret, same as in drive_receiver.gs
    "fps": 4,                    # frames per second of the resulting video
    "interval_s": 1.0,           # seconds between screenshots
    "chunk_frames": 240,         # frames per chunk (240 / 4 fps = 60 s of video)
    "scale": 1.0,                # 0.5 shrinks the files to ~1/4 of the size
    "ffmpeg_path": None,         # None = bundled ffmpeg next to the program, then PATH
    "crf": 28,                   # H.264 quality: lower = better and bigger (23-32 is sensible)
    "preset": "veryfast",        # x264 speed/size trade-off
    "gop_frames": 16,            # keyframe every N frames (an interrupted MP4 stays playable up to the last one)
    "work_dir": None,            # None = Downloads\ExamRecording (see resolve_work_dir)
    "retry_delay_s": 20,        # wait between retries while recording
    "final_attempts": 3,         # upload passes when stopping
    "final_wait_s": 5,           # wait between passes when stopping
    "final_max_s": 180,          # maximum total upload time when stopping
    "request_timeout_s": 120,    # read timeout for the server response
}

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IP_SERVICES = (
    "https://api.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://ident.me",
)


APP_FOLDER = "Screen Recorder"


def _is_writable_dir(path):
    try:
        if not path.is_dir():
            return False
        with tempfile.TemporaryFile(dir=path):
            return True
    except OSError:
        return False


def _windows_downloads_dir():
    """The user's real Downloads folder as reported by Windows (may be redirected)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    # FOLDERID_Downloads = {374DE290-123F-4565-9164-39C4925E467B}
    folder_id = GUID(0x374DE290, 0x123F, 0x4565,
                     (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                             wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.HRESULT  # raises OSError on failure
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ptr = ctypes.c_void_p()
    KF_FLAG_DONT_VERIFY = 0x00004000  # do not touch disk/network while resolving; _is_writable_dir checks it
    shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), KF_FLAG_DONT_VERIFY, None, ctypes.byref(ptr))
    try:
        return Path(ctypes.wstring_at(ptr.value))
    finally:
        ole32.CoTaskMemFree(ptr)


def resolve_work_dir():
    """Current user's Downloads\\ExamRecording, with fallbacks if it is unusable.

    Order: Downloads folder from Windows -> ~/Downloads -> C:\\Users\\Public.
    An ASCII-only path is preferred as a precaution (some tools mishandle accents
    on Windows); if no ASCII path works, the first writable one is used.
    """
    windows = sys.platform.startswith("win")
    candidates = []
    if windows:
        try:
            candidates.append(_windows_downloads_dir())
        except Exception:
            pass
    candidates.append(Path.home() / "Downloads")
    candidates.append(Path(os.environ.get("PUBLIC", r"C:\Users\Public")) if windows else Path.home())

    usable = [c for c in candidates if _is_writable_dir(c)]
    for base in usable:
        if str(base).isascii():
            return base / APP_FOLDER
    if usable:
        return usable[0] / APP_FOLDER
    raise RuntimeError("No writable folder was found to store the recording.")


def _apply(cfg, values, origin):
    for key, value in values.items():
        if key not in DEFAULT_CONFIG:
            raise ValueError(f"Unknown key in {origin}: {key!r}")
        cfg[key] = value


def load_config(overrides=None):
    """Defaults + exam_settings.py (compiled into the .exe) + overrides.

    Reads no external file: the .exe is distributed on its own.
    """
    cfg = dict(DEFAULT_CONFIG)
    try:
        import exam_settings
    except ImportError:
        exam_settings = None
    if exam_settings is not None:
        cfg["upload_url"] = getattr(exam_settings, "UPLOAD_URL", "")
        cfg["token"] = getattr(exam_settings, "TOKEN", "")
        _apply(cfg, getattr(exam_settings, "OVERRIDES", {}), "exam_settings.OVERRIDES")
    _apply(cfg, overrides or {}, "overrides")
    cfg["work_dir"] = resolve_work_dir() if cfg["work_dir"] is None else Path(cfg["work_dir"])
    return cfg


def sanitize_id(text):
    """Keep only letters and digits (same as the original script)."""
    return re.sub(r"[^a-zA-Z0-9]", "", text or "")


def _valid_ipv4(text):
    try:
        parts = [int(p) for p in (text or "").split(".")]
    except ValueError:
        return None
    if len(parts) == 4 and all(0 <= p <= 255 for p in parts):
        return ".".join(str(p) for p in parts)
    return None


def get_public_ip(timeout=3.0):
    """Address the internet sees (the school/home router's, shared by many PCs), or None."""
    for url in _IP_SERVICES:
        try:
            r = requests.get(url, timeout=timeout)
            m = _IPV4.search(r.text)
            if r.ok and m and _valid_ipv4(m.group(0)):
                return m.group(0)
        except requests.RequestException:
            continue
    return None


def get_local_ip():
    """This PC's address on the local network, or None. Never returns loopback."""
    candidates = []
    try:  # UDP "connect" sends nothing; it only asks the OS which interface it would use
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            candidates.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.append(socket.gethostbyname(socket.gethostname()))
    except OSError:
        pass
    for ip in candidates:
        ip = _valid_ipv4(ip)
        if ip and not ip.startswith("127.") and ip != "0.0.0.0":
            return ip
    return None


def get_ips(timeout=3.0):
    """(public_ip, local_ip); either may be None."""
    return get_public_ip(timeout), get_local_ip()


def ip_to_hex(ip):
    """IPv4 as 8 hexadecimal characters; 00000000 if unknown."""
    ip = _valid_ipv4(ip)
    return "".join(f"{int(p):02X}" for p in ip.split(".")) if ip else "00000000"


class Uploader:
    """Client for the Apps Script receiver. One attempt per call."""

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
        # Apps Script answers with a 302 to another URL; requests follows it with GET,
        # which is exactly what that service expects.
        r = self._session.post(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "text/plain;charset=utf-8"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def upload_chunk(self, folder, path):
        """Returns (ok, detail). ok only if the server confirms size and MD5."""
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
            return False, f"server: {error or 'invalid response'}"
        if resp.get("md5") != md5 or resp.get("size") != len(data):
            return False, "the server stored a different file (MD5/size mismatch)"
        return True, "ok"

    def send_final(self, folder, info):
        try:
            resp = self._post({"action": "final", "folder": folder, "info": info})
        except (requests.RequestException, ValueError) as e:
            return False, f"{type(e).__name__}: {e}"
        ok = isinstance(resp, dict) and bool(resp.get("ok"))
        return ok, "ok" if ok else "invalid response"


def find_ffmpeg(configured=None):
    """Locate ffmpeg: configured path -> next to this module -> next to the program -> PATH."""
    name = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path(__file__).resolve().parent / name)
    if sys.argv and sys.argv[0]:
        candidates.append(Path(sys.argv[0]).resolve().parent / name)
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise RuntimeError("ffmpeg was not found (it should be bundled inside the program).")


class _FfmpegWriter:
    """Pipes raw RGB frames into ffmpeg, which writes a fragmented H.264 MP4."""

    def __init__(self, ffmpeg, path, size, cfg):
        w, h = size
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
            "-r", f"{float(cfg['fps']):g}", "-i", "-", "-an",
            "-c:v", "libx264", "-preset", str(cfg["preset"]), "-crf", str(cfg["crf"]),
            "-g", str(int(cfg["gop_frames"])), "-pix_fmt", "yuv420p",
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            str(path),
        ]
        kwargs = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console flash
        self._tail = collections.deque(maxlen=20)
        try:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, **kwargs)
        except OSError as e:
            raise RuntimeError(f"Could not start ffmpeg: {e}")
        self._drain = threading.Thread(target=self._read_stderr, name="ffmpeg-log", daemon=True)
        self._drain.start()

    def _read_stderr(self):
        try:
            for line in self._proc.stderr:
                self._tail.append(line.decode("utf-8", "replace").strip())
        except (OSError, ValueError):
            pass

    @property
    def error_tail(self):
        return " | ".join(t for t in self._tail if t)[-400:]

    def write(self, image):
        try:
            self._proc.stdin.write(image.tobytes())
        except (BrokenPipeError, OSError, ValueError):
            raise RuntimeError(f"ffmpeg stopped unexpectedly: {self.error_tail or 'no details'}")

    def close(self):
        """Finish the file. Returns ffmpeg's exit code."""
        try:
            self._proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass
        try:
            code = self._proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            code = self._proc.wait()
        self._drain.join(timeout=2)
        return code


def _screen_frame():
    from PIL import ImageGrab  # late import: the core can be tested without a display
    return ImageGrab.grab().convert("RGB")


class Recorder:
    """Records in one thread and uploads in another. Reports via `on_event(kind, data)`.

    Events: started, chunk_closed, uploaded, upload_failed, error, finished.
    `on_event` is called from worker threads; if you use it from a GUI, just
    queue the event and handle it in the GUI thread.
    """

    def __init__(self, cfg, student_id, frame_source=None, on_event=None):
        self.cfg = cfg
        self.student_id = sanitize_id(student_id)
        if not self.student_id:
            raise ValueError("The student ID must contain at least one letter or digit.")
        self._frame_source = frame_source or _screen_frame
        self._ffmpeg = find_ffmpeg(cfg["ffmpeg_path"])
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
        self.public_ip = None
        self.local_ip = None
        self.summary = None

    # ---- control -------------------------------------------------------
    def start(self):
        self.cfg["work_dir"].mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._capture_loop, name="capture", daemon=True).start()
        threading.Thread(target=self._upload_loop, name="upload", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    # ---- internals -----------------------------------------------------
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
        frame = self._frame_source().convert("RGB")
        scale = float(self.cfg["scale"])
        w, h = frame.size
        if scale != 1.0:
            w, h = max(2, round(w * scale)), max(2, round(h * scale))
            frame = frame.resize((w, h), Image.Resampling.BOX)
        if h % 2 or w % 2:  # yuv420p requires even dimensions
            frame = frame.crop((0, 0, w // 2 * 2, h // 2 * 2))
        return frame

    def _chunk_closed(self, path):
        with self._lock:
            self._pending.append(path)
            self.chunks_total += 1
            pending = len(self._pending)
        self._emit("chunk_closed", path=str(path), pending=pending)
        self._wake.set()

    # ---- capture -------------------------------------------------------
    def _capture_loop(self):
        writer = None
        path = None
        size = None
        frames_in_chunk = 0
        part = 0
        errors = 0

        def close_writer():
            nonlocal writer
            if writer is None:
                return
            w, writer = writer, None
            code = w.close()
            if path.is_file() and path.stat().st_size > 0:
                self._chunk_closed(path)  # even if ffmpeg exited oddly: fragmented MP4 is salvageable
            else:
                raise RuntimeError(f"ffmpeg produced no file (exit {code}): {w.error_tail or 'no details'}")

        try:
            self.public_ip, self.local_ip = get_ips()
            ip_hex = ip_to_hex(self.public_ip or self.local_ip)
            now = datetime.now()
            self.started_iso = now.isoformat(timespec="seconds")
            self.base_name = f"{self.student_id}_{now:%Y-%m-%d_%H-%M-%S}_{ip_hex}"
            self._emit("started", base_name=self.base_name)

            interval = float(self.cfg["interval_s"])
            chunk_frames = int(self.cfg["chunk_frames"])
            next_t = time.monotonic()

            while not self._stop.is_set():
                frame = None
                try:
                    frame = self._grab()
                    errors = 0
                except Exception as e:  # e.g. screen briefly locked
                    errors += 1
                    self.skipped_frames += 1
                    if errors >= 30:
                        raise RuntimeError(f"Could not capture the screen: {e}")

                if frame is not None:
                    if writer is None:
                        part += 1
                        path = self.cfg["work_dir"] / f"{self.base_name}_part{part:03d}.mp4"
                        size = frame.size
                        writer = _FfmpegWriter(self._ffmpeg, path, size, self.cfg)
                        frames_in_chunk = 0
                    elif frame.size != size:
                        frame = frame.resize(size, Image.Resampling.BOX)
                    writer.write(frame)
                    frames_in_chunk += 1
                    self.frames_total += 1
                    if frames_in_chunk >= chunk_frames:
                        close_writer()

                # Fixed schedule: does not accumulate drift from capture time.
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
                close_writer()  # keep everything recorded up to the last frame
            except Exception:
                pass
            self._stop.set()
            self._capture_done.set()
            self._wake.set()

    # ---- upload --------------------------------------------------------
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
                pass  # uploaded anyway; only a local copy remains
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
                "student_id": self.student_id,
                "start": self.started_iso,
                "end": datetime.now().isoformat(timespec="seconds"),
                "parts": self.chunks_total,
                "frames": self.frames_total,
                "skipped_frames": self.skipped_frames,
                "public_ip": self.public_ip,
                "local_ip": self.local_ip,
            }
            for _ in range(2):
                marker, _detail = self._uploader.send_final(self.base_name, info)
                if marker:
                    break
                time.sleep(float(cfg["final_wait_s"]))

        if not pending:
            try:
                cfg["work_dir"].rmdir()  # only if empty
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
