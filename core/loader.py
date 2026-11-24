"""Turn uploaded files (plain text or .zip of text files) into (name, text) pairs."""
from __future__ import annotations
import io
import os
import zipfile

MAX_ENTRY = 8 * 1024 * 1024
MAX_FILES = 500
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".exe", ".bin", ".img", ".xlsx", ".docx"}


def decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1", "replace")


def load_uploads(items):
    """items: iterable of (filename, bytes). Returns ([(name, text)], [skipped messages])."""
    out, skipped = [], []
    for name, raw in items:
        ext = os.path.splitext(name)[1].lower()
        if ext == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    for info in z.infolist()[:MAX_FILES]:
                        if info.is_dir() or info.file_size > MAX_ENTRY:
                            continue
                        if os.path.splitext(info.filename)[1].lower() in SKIP_EXT:
                            continue
                        out.append((os.path.basename(info.filename), decode(z.read(info))))
            except zipfile.BadZipFile:
                skipped.append(f"{name}: not a valid zip archive")
        elif ext in SKIP_EXT:
            skipped.append(f"{name}: unsupported file type")
        elif len(raw) > MAX_ENTRY:
            skipped.append(f"{name}: larger than 8 MB")
        else:
            out.append((os.path.basename(name), decode(raw)))
    kept = []
    for n, t in out:
        if t.strip():
            kept.append((n, t))
        else:
            skipped.append(f"{n}: file is empty")
    return kept, skipped
