// Receptor de fragmentos para Google Drive (Google Apps Script).
//
// Despliegue: ver README.md. Resumen:
//   1. Crea una carpeta en tu Drive y copia su ID (lo que va después de /folders/ en la URL).
//   2. script.google.com > Nuevo proyecto > pega este archivo.
//   3. Cambia TOKEN y ROOT_FOLDER_ID (TOKEN debe ser igual al de exam_settings.py).
//   4. Implementar > Nueva implementación > Aplicación web:
//        Ejecutar como: Yo      Quién tiene acceso: Cualquier persona
//   5. Copia la URL que termina en /exec a exam_settings.py (UPLOAD_URL).
//
// Solo AÑADE archivos: nunca borra ni reemplaza nada.
//  - Reenviar un fragmento idéntico (reintento tras perder la respuesta) no duplica.
//  - Si llega un fragmento con el mismo nombre pero contenido distinto, se guarda
//    aparte como *_dup2, *_dup3... y el original queda intacto.
// Solo acepta carpetas y nombres con la forma exacta que genera el grabador.

const TOKEN = 'CAMBIA_ESTE_TOKEN';
const ROOT_FOLDER_ID = 'ID_DE_TU_CARPETA_EN_DRIVE';
const MAX_BYTES = 45 * 1024 * 1024;  // tope de cordura por fragmento

// matricula_AAAA-MM-DD_HH-MM-SS_IPHEX
const FOLDER_RE = /^[A-Za-z0-9]{1,64}_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[0-9A-F]{8}$/;

function doGet() {
  return json_({ ok: true, msg: 'receptor activo' });
}

function doPost(e) {
  try {
    const req = JSON.parse(e.postData.contents);
    if (req.token !== TOKEN) return json_({ ok: false, error: 'token incorrecto' });

    const folderName = String(req.folder || '');
    if (!FOLDER_RE.test(folderName)) return json_({ ok: false, error: 'carpeta invalida' });

    if (req.action === 'chunk') {
      const name = String(req.name || '');
      if (!new RegExp('^' + folderName + '_parte\\d{3}\\.avi$').test(name)) {
        return json_({ ok: false, error: 'nombre invalido' });
      }
      const bytes = Utilities.base64Decode(req.data);
      if (bytes.length === 0 || bytes.length > MAX_BYTES) {
        return json_({ ok: false, error: 'tamano invalido' });
      }
      saveAppendOnly_(getFolder_(folderName), name, bytes, 'video/x-msvideo');
      return json_({ ok: true, size: bytes.length, md5: md5_(bytes) });
    }

    if (req.action === 'final') {
      const bytes = Utilities.newBlob(JSON.stringify(req.info, null, 2)).getBytes();
      saveAppendOnly_(getFolder_(folderName), folderName + '_FINAL.txt', bytes, 'text/plain');
      return json_({ ok: true });
    }

    return json_({ ok: false, error: 'accion desconocida' });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function getFolder_(folderName) {
  const root = DriveApp.getFolderById(ROOT_FOLDER_ID);
  const found = root.getFoldersByName(folderName);
  return found.hasNext() ? found.next() : root.createFolder(folderName);
}

// Guarda sin tocar lo existente. Devuelve true si ya estaba idéntico.
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
        return true;  // mismo contenido: reintento, no duplicar
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
