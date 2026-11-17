// Receptor de fragmentos para Google Drive (Google Apps Script).
//
// Despliegue: ver README.md. Resumen:
//   1. Crea una carpeta en tu Drive y copia su ID (lo que va después de /folders/ en la URL).
//   2. script.google.com > Nuevo proyecto > pega este archivo.
//   3. Cambia TOKEN y ROOT_FOLDER_ID.
//   4. Implementar > Nueva implementación > Aplicación web:
//        Ejecutar como: Yo      Quién tiene acceso: Cualquier persona
//   5. Copia la URL que termina en /exec a "upload_url" en config.json,
//      y el mismo TOKEN a "token".
//
// Los archivos se guardan con tu cuenta, en una subcarpeta por grabación.
// Reenviar un fragmento con el mismo nombre lo reemplaza (los reintentos son seguros).

const TOKEN = 'CAMBIA_ESTE_TOKEN';
const ROOT_FOLDER_ID = 'ID_DE_TU_CARPETA_EN_DRIVE';

function doGet() {
  return json_({ ok: true, msg: 'receptor activo' });
}

function doPost(e) {
  try {
    const req = JSON.parse(e.postData.contents);
    if (req.token !== TOKEN) return json_({ ok: false, error: 'token incorrecto' });

    const root = DriveApp.getFolderById(ROOT_FOLDER_ID);
    const folderName = safe_(req.folder);
    const found = root.getFoldersByName(folderName);
    const folder = found.hasNext() ? found.next() : root.createFolder(folderName);

    if (req.action === 'chunk') {
      const name = safe_(req.name);
      const bytes = Utilities.base64Decode(req.data);
      trashByName_(folder, name);
      folder.createFile(Utilities.newBlob(bytes, 'video/x-msvideo', name));
      return json_({ ok: true, size: bytes.length, md5: md5_(bytes) });
    }

    if (req.action === 'final') {
      const name = folderName + '_FINAL.txt';
      trashByName_(folder, name);
      folder.createFile(name, JSON.stringify(req.info, null, 2), MimeType.PLAIN_TEXT);
      return json_({ ok: true });
    }

    return json_({ ok: false, error: 'accion desconocida' });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function safe_(s) {
  const out = String(s || '').replace(/[^A-Za-z0-9_.\-]/g, '_').slice(0, 120);
  if (!out) throw new Error('nombre vacio');
  return out;
}

function trashByName_(folder, name) {
  const old = folder.getFilesByName(name);
  while (old.hasNext()) old.next().setTrashed(true);
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
