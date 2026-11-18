# Exam screen recorder (single .exe)

Small window: student ID field → "recording" indicator → Stop button. It takes one
screenshot per second, encodes 4 fps H.264 MP4 chunks (240 frames = 60 s of video = 4 min of
screen) and uploads each chunk to your Google Drive while the exam runs.

The `.exe` is a single file. Settings and `ffmpeg.exe` are compiled into it; there is nothing else to ship.

## Files

| File | Role |
|---|---|
| `screen_recorder.py` | The window (entry point). |
| `recorder_core.py` | Capture, ffmpeg encoding, chunking, upload, verification, Downloads-folder resolution. |
| `exam_settings.py` | Your Drive URL and token. **Compiled into the exe. Not for public repos** (it is in `.gitignore`). |
| `drive_receiver.gs` | Google Apps Script that receives the chunks into your Drive. |
| `ffmpeg.exe` | You add this (see section 2). Not included here. Does the H.264 encoding. |
| `requirements.txt` | Runtime packages: Pillow and requests only (no OpenCV, no NumPy). |

## 1. Drive receiver (once)

1. Create a Drive folder and copy its ID (the part after `/folders/` in its URL).
2. script.google.com → New project → paste `drive_receiver.gs`.
3. Set `TOKEN` (long random string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`) and `ROOT_FOLDER_ID`.
4. Deploy → New deployment → **Web app** → *Execute as*: **Me**, *Who has access*: **Anyone**.
5. Open the `/exec` URL in a browser; you should see `{"ok":true,"msg":"receiver active"}`.
6. Put that URL in `UPLOAD_URL` and the same string in `TOKEN` inside `exam_settings.py`.

If you edit the script later, deploy a **new version** (Deploy → Manage deployments → edit →
New version); otherwise the `/exec` URL keeps running the old code. This version expects
`.mp4` chunk names; an older receiver that expects `.avi` will reject them.

Each recording gets a subfolder `ID_date_time_IPHEX/` with `..._part001.mp4`, `..._part002.mp4`, …
and, if it finished cleanly, `..._FINAL.txt` with the chunk count (so you can spot a missing part).

What the receiver enforces (tested with the real code under Node, with Drive mocked):
- It only **adds** files. It never trashes or overwrites. A retry of an identical chunk is
  recognized and not duplicated; a different chunk with an existing name is saved as `_dup2`, `_dup3`…
- Folder and file names must have exactly the shape the recorder generates (IDs up to 64
  letters/digits), `.mp4` only, at most 45 MB per chunk.
- Anyone holding the token can still add junk files or fake chunks; see Caveats.

## 2. Build the single exe (on Windows, with Python installed)

1. Download a static Windows build of ffmpeg and copy **only `ffmpeg.exe`** next to
   `screen_recorder.py`. It must include the `libx264` encoder (the common "essentials" and
   "full" builds do; check with `ffmpeg -encoders | findstr x264`).
2. Build:

```
pip install -r requirements.txt nuitka
python -m nuitka --onefile --windows-console-mode=disable ^
  --enable-plugin=tk-inter --assume-yes-for-downloads ^
  --include-data-files=ffmpeg.exe=ffmpeg.exe ^
  --output-filename=ExamRecorder.exe screen_recorder.py
```

Run it from the folder that contains `screen_recorder.py`, `recorder_core.py`, `ffmpeg.exe` and your
edited `exam_settings.py`. Notes (I could not run Nuitka here, so check these on your machine):
- If Nuitka rejects `--windows-console-mode=disable`, your version is older: use `--disable-console`.
- `python -m nuitka --plugin-list` shows plugin names. Newer Nuitka versions often enable tkinter support on their own.
- `--assume-yes-for-downloads` lets Nuitka fetch a C compiler (MinGW) if none is installed.
- **Size:** the exe is roughly the Python part (Pillow + tkinter + requests) plus `ffmpeg.exe`, so the
  ffmpeg build you choose decides most of it. Compare builds, or compile a minimal ffmpeg with only
  raw-video input, `libx264` and the MP4 muxer if you need it smaller.
- Onefile unpacks itself (including `ffmpeg.exe`) into a temp folder on each launch, so the first
  seconds are slower, and some antivirus products dislike an exe that extracts and runs another exe.
  Nothing the program needs to keep lives there.
- **Licensing:** `libx264` is GPL. Distributing a GPL ffmpeg build to others carries GPL obligations
  (e.g. offering the source). For classroom use check that this is acceptable to you, or use an LGPL build.
- **Smoke test the exe:** start a recording and read the line under "RECORDING". It must say
  `Chunks uploaded: 0 of 0` (or similar). If it says `Local mode`, `exam_settings.py` was not
  compiled in or `UPLOAD_URL` is empty. If you get "Could not start" mentioning ffmpeg, `ffmpeg.exe`
  was not bundled.

To run from source instead: `pip install -r requirements.txt`, put `ffmpeg` on your PATH (or next
to the script) and run `python screen_recorder.py`. On Ubuntu use a virtual environment and
`sudo apt install python3-tk ffmpeg`.

## 3. How uploads and local copies work

- A chunk is deleted from disk **only** after the server returns the same size and MD5.
- While recording, failed uploads are retried every 20 s and the window warns the student.
- On Stop, remaining chunks are uploaded (3 passes, at most 180 s). If all arrive, no local files
  remain. Otherwise the leftovers stay on disk and the window shows a red warning with the folder
  path, an "Open folder" button, and tells the student to inform you before logging off.
- Chunks are fragmented MP4 (a keyframe every 16 frames), so a chunk interrupted by a crash or
  power loss is usually still playable up to the last fragment. At most the chunk being recorded
  (up to 4 min) is affected.
- Leftover chunks go to `Downloads\ExamRecording`, resolved in this order:
  1. the real Downloads folder from Windows (follows redirection),
  2. `~\Downloads`,
  3. `C:\Users\Public`.

  A plain-ASCII path is preferred as a precaution. If only accented paths are writable, the first
  one is used. The folder is created when recording starts and removed if empty at the end;
  `Downloads` itself is never touched.

## Settings you can change (`OVERRIDES` in `exam_settings.py`)

`scale` (0.5 ≈ quarter of the pixels), `crf` (H.264 quality, default 28; lower = better and bigger),
`preset` (x264 speed, default `veryfast`), `gop_frames`, `chunk_frames`, `fps`, `interval_s`,
`ffmpeg_path`. Keys are listed in `DEFAULT_CONFIG` in `recorder_core.py`; unknown keys are rejected.

## Caveats

- **Not run on Windows:** the Known Folder API call, the Nuitka build, bundled-ffmpeg lookup inside the
  onefile exe (`__file__` / `sys.argv[0]` folder), and the hidden-console flag for ffmpeg were not run here.
  The encoding itself was tested with a Linux ffmpeg (libx264): 4 fps H.264, even dimensions, chunk
  count, interrupted-file playback. A synthetic 1080p text screen gave about 5 MB per 240-frame chunk
  and about 10 ms per frame to encode; real screens (video, animations) will differ.
- If the API call for Downloads fails, it falls back to `~\Downloads` silently, so test the warning
  case once: start a recording, disconnect the network, stop, and confirm the path shown.
- **Not run for real:** the window (only its logic, with a mocked tkinter) and the Apps Script
  deployment (only its code, with a mocked Drive). Do a short two-minute run before an exam.
- The token is compiled into the exe, so a determined student can extract it. The append-only
  receiver limits the damage to junk or forged chunks; it cannot make the recording tamper-proof
  (virtual displays, a second device, killing the process).
- I could not verify Apps Script's maximum request size. MP4 chunks are much smaller than the old AVI
  ones, which helps; if a chunk is still rejected, lower `scale` or raise `crf`.
- Tell students the recording is running; the window shows it at all times.
