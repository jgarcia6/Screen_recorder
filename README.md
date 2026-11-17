# Exam screen recorder

Small window (student ID field → "recording" indicator → Stop button). It takes
one screenshot per second, writes them as 4 fps AVI chunks and uploads each
chunk to your Google Drive while the exam is running.

## Files

| File | Role |
|---|---|
| `screen_recorder.py` | The window. Run this. |
| `recorder_core.py` | Capture, chunking, upload and verification (no GUI). |
| `receptor_drive.gs` | Google Apps Script that receives the chunks into your Drive. |
| `config.json` | Settings, kept next to the program (or next to the `.exe`). |
| `requirements.txt` | `pip install -r requirements.txt` (the script no longer installs packages by itself). |

## 1. Set up the Drive receiver (once, with your Google account)

1. Create a folder in Drive and copy its ID (the part after `/folders/` in its URL).
2. Go to script.google.com → New project → paste `receptor_drive.gs`.
3. Set `TOKEN` to a long random string and `ROOT_FOLDER_ID` to the folder ID.
4. Deploy → New deployment → type **Web app** → *Execute as*: **Me**,
   *Who has access*: **Anyone**. Authorize when asked.
5. Open the `/exec` URL in a browser. You should see `{"ok":true,"msg":"receptor activo"}`.
6. Put that URL in `upload_url` and the same string in `token` (`config.json`).

Each recording gets a subfolder `ID_date_time_IPHEX/` with `..._parte001.avi`,
`..._parte002.avi`, … and, if everything finished cleanly, `..._FINAL.txt`
with the chunk count (so you can spot a missing part).

## 2. Run

```
pip install -r requirements.txt
python screen_recorder.py
```

Windows executable (put `config.json` next to it):

```
pip install pyinstaller
pyinstaller --onefile --windowed screen_recorder.py
```

## How uploads and local copies work

- One chunk = `chunk_frames` frames (240 at 4 fps = 60 s of video = 4 min of screen).
- A chunk is deleted locally **only** after the server returns the same size and MD5.
- While recording, failed uploads are retried every `retry_delay_s`; the window warns
  the student that chunks are pending.
- On Stop, remaining chunks are uploaded (up to `final_attempts` passes / `final_max_s`).
  If all are uploaded, no local files remain. Otherwise the leftovers stay in
  `grabaciones_pendientes/` and the window tells the student to warn you.
- If the program is killed or the computer loses power, at most the chunk being
  recorded (up to 4 min) is lost; closed chunks are already uploaded or kept on disk.
- Leave `upload_url` empty to test without uploading: everything stays local.

## Caveats

- The token lives in `config.json` / next to the `.exe`, so a student could read it.
  The worst case is someone posting junk files to your folder; they cannot read or
  delete anything. Use a random token and change it per course.
- I could not verify Apps Script's maximum request size. If a chunk is rejected as too
  large, lower `scale` (0.5 cuts file size to about a quarter) or `chunk_frames`.
- Tell students the recording is running; the window shows it at all times.
