"""Zip upload: lets the browser send a build folder as a .zip (useful when the app runs on the server)."""
import os
import shutil
import tempfile
import time
import zipfile

MAX_ZIP = 500 * 1024 * 1024
MAX_UNPACKED = 2 * 1024 * 1024 * 1024
PREFIX = 'coderunner-upload-'


def _cleanup():
    root = tempfile.gettempdir()
    for n in os.listdir(root):
        p = os.path.join(root, n)
        if n.startswith(PREFIX) and os.path.isdir(p) and time.time() - os.path.getmtime(p) > 86400:
            shutil.rmtree(p, ignore_errors=True)


def save_zip(stream, length):
    """Read `length` bytes of a zip from `stream`, unpack safely and return the folder to deploy."""
    if length <= 0 or length > MAX_ZIP:
        raise ValueError('Zip must be between 1 byte and %d MB' % (MAX_ZIP // 1048576))
    _cleanup()
    work = tempfile.mkdtemp(prefix=PREFIX)
    zpath = os.path.join(work, 'upload.zip')
    left = length
    with open(zpath, 'wb') as f:
        while left:
            chunk = stream.read(min(1048576, left))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    if left:
        shutil.rmtree(work, ignore_errors=True)
        raise ValueError('Upload was cut short')
    out = os.path.join(work, 'site')
    os.makedirs(out)
    try:
        with zipfile.ZipFile(zpath) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED:
                raise ValueError('Zip is too large when unpacked')
            for info in z.infolist():
                target = os.path.normpath(os.path.join(out, info.filename))
                if not target.startswith(out + os.sep) and target != out:
                    raise ValueError('Zip contains an unsafe path: ' + info.filename)
            z.extractall(out)
    except zipfile.BadZipFile:
        shutil.rmtree(work, ignore_errors=True)
        raise ValueError('That file is not a valid zip')
    except ValueError:
        shutil.rmtree(work, ignore_errors=True)
        raise
    os.remove(zpath)
    entries = [e for e in os.listdir(out) if e != '__MACOSX']
    if len(entries) == 1 and os.path.isdir(os.path.join(out, entries[0])):
        return os.path.join(out, entries[0])  # zip of a single folder: deploy the folder's content
    return out
