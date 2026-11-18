# Exam screen recorder (single .exe)

Small window: student ID field → "recording" indicator → Stop button. It takes one
screenshot per second, writes 4 fps AVI chunks (240 frames = 60 s of video = 4 min of
screen) and uploads each chunk to your Google Drive while the exam runs.

The `.exe` is a single file. Settings are compiled into it; there is no config file to ship.

## Files

| File | Role |
|---|---|
| `screen_recorder.py` | The window (entry point). |
| `recorder_core.py` | Capture, chunking, upload, verification, Downloads-folder resolution. |
| `exam_settings.py` | Your Drive URL and token. **Compiled into the exe. Not for public repos** (it is in `.gitignore`). |
| `receptor_drive.gs` | Google Apps Script that receives the chunks into your Drive. |
| `requirements.txt` | Runtime packages. The script no longer installs anything by itself. |

## 1. Drive receiver (once)

1. Create a Drive folder and copy its ID (the part after `/folders/` in its URL).
2. script.google.com → New project → paste `receptor_drive.gs`.
3. Set `TOKEN` (long random string) and `ROOT_FOLDER_ID`.
4. Deploy → New deployment → **Web app** → *Execute as*: **Me**, *Who has access*: **Anyone**.
5. Open the `/exec` URL in a browser; you should see `{"ok":true,"msg":"receptor activo"}`.
6. Put that URL in `UPLOAD_URL` and the same string in `TOKEN` inside `exam_settings.py`.

If you edit the script later, deploy a **new version** (Deploy → Manage deployments → edit →
New version); otherwise the `/exec` URL keeps running the old code.

Each recording gets a subfolder `ID_date_time_IPHEX/` with `..._parte001.avi`, `..._parte002.avi`, …
and, if it finished cleanly, `..._FINAL.txt` with the chunk count (so you can spot a missing part).

What the receiver enforces (tested with the real code under Node, with Drive mocked):
- It only **adds** files. It never trashes or overwrites. A retry of an identical chunk is
  recognized and not duplicated; a different chunk with an existing name is saved as `_dup2`, `_dup3`…
- Folder and file names must have exactly the shape the recorder generates (IDs up to 64
  letters/digits), `.avi` only, at most 45 MB per chunk.
- Anyone holding the token can still add junk files or fake chunks; see Caveats.

## 2. Build the single exe (on Windows, with Python installed)

```
pip install -r requirements.txt nuitka
python -m nuitka --onefile --windows-console-mode=disable ^
  --enable-plugin=tk-inter --assume-yes-for-downloads ^
  --output-filename=GrabadorExamen.exe screen_recorder.py
```

Run it from the folder that contains `screen_recorder.py`, `recorder_core.py` and your edited
`exam_settings.py`. Notes (I could not run Nuitka here, so check these on your machine):
- If Nuitka rejects `--windows-console-mode=disable`, your version is older: use `--disable-console`.
- Newer Nuitka versions often enable tkinter support on their own; `python -m nuitka --plugin-list`
  shows the plugin names. If the exe fails with an error about `cv2`, add `--include-package=cv2`.
- `--assume-yes-for-downloads` lets Nuitka fetch a C compiler (MinGW) if none is installed.
- Onefile unpacks itself into a temp folder on each launch, so the first seconds are slower.
  Nothing the program needs to keep lives there.
- **Smoke test the exe:** start a recording and read the line under "GRABANDO". It must say
  `Fragmentos subidos: 0 de 0` (or similar). If it says `Modo local`, `exam_settings.py` was not
  compiled in or `UPLOAD_URL` is empty.

To run from source instead: `pip install -r requirements.txt` then `python screen_recorder.py`.

## 3. How uploads and local copies work

- A chunk is deleted from disk **only** after the server returns the same size and MD5.
- While recording, failed uploads are retried every 20 s and the window warns the student.
- On Stop, remaining chunks are uploaded (3 passes, at most 180 s). If all arrive, no local files
  remain. Otherwise the leftovers stay on disk and the window shows a red warning with the folder
  path, an "Abrir carpeta" button, and tells the student to inform you before logging off.
- If the program is killed or the power fails, at most the chunk being recorded (up to 4 min) is lost.
- Leftover chunks go to `Downloads\GrabacionExamen`, resolved in this order:
  1. the real Downloads folder from Windows (follows redirection),
  2. `~\Downloads`,
  3. `C:\Users\Public`.

  A plain-ASCII path is preferred, because OpenCV can fail to write video to paths with accents.
  If only accented paths are writable, the first one is used. The folder is created when recording
  starts and removed if empty at the end; `Downloads` itself is never touched.

## Caveats

- **Not run on Windows:** the Known Folder API call (checked against how `platformdirs` makes the
  same call, and the folder ID checked against its source) and the Nuitka build were not run here.
  If the API call fails for any reason, it falls back to `~\Downloads` silently, so test with the
  warning case once: start a recording, disconnect the network, stop, and confirm the path shown.
- **Not run for real:** the window (only its logic, with a mocked tkinter) and the Apps Script
  deployment (only its code, with a mocked Drive). Do a short two-minute run before an exam.
- The token is compiled into the exe, so a determined student can extract it. The append-only
  receiver limits the damage to junk or forged chunks; it cannot make the recording tamper-proof
  (virtual displays, a second device, killing the process).
- I could not verify Apps Script's maximum request size. If a chunk is rejected as too large,
  set `OVERRIDES = {"scale": 0.5}` in `exam_settings.py` (about a quarter of the size) and rebuild.
- Tell students the recording is running; the window shows it at all times.
