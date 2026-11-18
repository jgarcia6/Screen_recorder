// Chunk receiver for Google Drive (Google Apps Script).
//
// Deployment: see README.md. Summary:
//   1. Create a folder in your Drive and copy its ID (the part after /folders/ in the URL).
//   2. script.google.com > New project > paste this file.
//   3. Set TOKEN and ROOT_FOLDER_ID (TOKEN must match the one in exam_settings.py).
//   4. Deploy > New deployment > Web app:
//        Execute as: Me      Who has access: Anyone
//   5. Copy the URL ending in /exec into exam_settings.py (UPLOAD_URL).
//
// It only ADDS files: it never deletes or replaces anything.
//  - Re-sending an identical chunk (a retry after a lost response) does not duplicate it.
//  - A chunk with the same name but different content is saved separately as
//    *_dup2, *_dup3... and the original stays untouched.
// It only accepts folders and file names with the exact shape the recorder generates.

const TOKEN = 'CHANGE_THIS_TOKEN';
const ROOT_FOLDER_ID = 'YOUR_DRIVE_FOLDER_ID';
const MAX_BYTES = 45 * 1024 * 1024;  // sanity cap per chunk

// studentid_YYYY-MM-DD_HH-MM-SS_IPHEX
const FOLDER_RE = /^[A-Za-z0-9]{1,64}_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[0-9A-F]{8}$/;

function doGet() {
  return json_({ ok: true, msg: 'receiver active' });
}

function doPost(e) {
  try {
    const req = JSON.parse(e.postData.contents);
    if (req.token !== TOKEN) return json_({ ok: false, error: 'wrong token' });

    const folderName = String(req.folder || '');
    if (!FOLDER_RE.test(folderName)) return json_({ ok: false, error: 'invalid folder' });

    if (req.action === 'chunk') {
      const name = String(req.name || '');
      if (!new RegExp('^' + folderName + '_part\\d{3}\\.mp4$').test(name)) {
        return json_({ ok: false, error: 'invalid name' });
      }
      const bytes = Utilities.base64Decode(req.data);
      if (bytes.length === 0 || bytes.length > MAX_BYTES) {
        return json_({ ok: false, error: 'invalid size' });
      }
      saveAppendOnly_(getFolder_(folderName), name, bytes, 'video/mp4');
      return json_({ ok: true, size: bytes.length, md5: md5_(bytes) });
    }

    if (req.action === 'final') {
      const bytes = Utilities.newBlob(JSON.stringify(req.info, null, 2)).getBytes();
      saveAppendOnly_(getFolder_(folderName), folderName + '_FINAL.txt', bytes, 'text/plain');
      return json_({ ok: true });
    }

    return json_({ ok: false, error: 'unknown action' });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function getFolder_(folderName) {
  const root = DriveApp.getFolderById(ROOT_FOLDER_ID);
  const found = root.getFoldersByName(folderName);
  return found.hasNext() ? found.next() : root.createFolder(folderName);
}

// Saves without touching what exists. Returns true if an identical file was already there.
function saveAppendOnly_(folder, name, bytes, mime) {
  const want = md5_(bytes);
  let candidate = name;
  let n = 1;
  for (;;) {
    let clash = false;
    const it = folder.getFilesByName(candidate);
    while (it.hasNext()) {
      const f = it.next();
      if (f.getSize() === bytes.length && md5_(f.getBlob().getBytes()) === want) {
        return true;  // same content: a retry, do not duplicate
      }
      clash = true;
    }
    if (!clash) break;
    n++;
    candidate = name.replace(/(\.[^.]*)?$/, '_dup' + n + '$1');
  }
  folder.createFile(Utilities.newBlob(bytes, mime, candidate));
  return false;
}

function md5_(bytes) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, bytes)
    .map(function (b) { return ('0' + (b & 0xFF).toString(16)).slice(-2); })
    .join('');
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
