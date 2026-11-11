"""Turn files that are not PDFs or images into PDFs, so the PDF viewer can show them.

`convert(path, head)` is the only entry point. It returns the path of a PDF (or, as a
last resort, of a preview image) to open instead of `path`, or None when the file is not
something this module handles (PDFs, images, and anything unknown are left alone).

Routes, best first:
  * Office / iWork documents: LibreOffice (`soffice --headless --convert-to pdf`) when it
    is installed; otherwise, on macOS, the system's own readers: Cocoa's rich text
    importers (doc, docx, rtf, odt), Quick Look's previews printed by WebKit (xlsx, pptx,
    Keynote, Pages, Numbers, ...), and as a last resort a Quick Look thumbnail.
  * HTML and SVG: printed to PDF by WebKit on macOS.
  * Text, Markdown, source code, CSV, JSON, logs: laid out in a monospace font. On macOS
    Cocoa does the layout (it wraps words and finds fonts for any script, Japanese
    included); elsewhere a small PDF is written directly with PDFium.

Results are cached in the temp directory, keyed by the file's path, size and mtime.
"""

import csv
from html import escape as html_escape
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

VERSION = 1  # bump to invalidate cached conversions

OFFICE = {
    ".doc", ".docx", ".docm", ".dot", ".dotx", ".odt", ".ott", ".rtf", ".wpd", ".wps",
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".ots",
    ".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".odp", ".otp",
    ".pages", ".numbers", ".key", ".odg",
}
RICH = {".doc", ".docx", ".docm", ".dot", ".dotx", ".rtf", ".odt", ".wordml", ".webarchive"}
WEB = {".html", ".htm", ".xhtml", ".svg"}
TEXT = {
    ".txt", ".text", ".md", ".markdown", ".mdown", ".rst", ".adoc", ".org", ".tex", ".bib",
    ".log", ".out", ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".geojson", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env", ".properties", ".xml", ".plist", ".srt", ".vtt",
    ".diff", ".patch", ".sql", ".graphql", ".proto",
    ".py", ".pyi", ".ipynb", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".m", ".mm", ".cs", ".java", ".kt", ".kts",
    ".swift", ".go", ".rs", ".rb", ".php", ".pl", ".pm", ".lua", ".r", ".jl", ".dart", ".scala",
    ".hs", ".ml", ".mli", ".fs", ".ex", ".exs", ".erl", ".clj", ".el", ".vim", ".zig", ".nim",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd", ".css", ".scss", ".sass", ".less",
    ".gradle", ".cmake", ".mk", ".make", ".dockerfile", ".gitignore", ".editorconfig",
}

MAX_TEXT_CHARS = 2_000_000  # longer text files are cut, with a note at the end
SOFFICE_TIMEOUT = 180
MAC_TIMEOUT = 90
THUMBNAIL_TIMEOUT = 20  # qlmanage -t can sit there for minutes on files it can't read
A4 = (595, 842)


class ConversionError(Exception):
    pass


def convert(path, head):
    """Return a PDF or image path to show instead of `path`, or None to leave it alone."""
    kind = _classify(path, head)
    if kind is None:
        return None
    key = _cache_key(path)
    for ext in (".pdf", ".png"):
        cached = os.path.join(_cache_dir(), key + ext)
        if os.path.isfile(cached):
            return cached
    out = os.path.join(_cache_dir(), key + ".pdf")
    work = tempfile.mkdtemp(prefix="work-", dir=_cache_dir())
    _status(f"see: converting {os.path.basename(path)}…")
    try:
        result = kind(path, out, work)
        if result != out:  # a preview image: keep it under the cache name too
            out = os.path.join(_cache_dir(), key + ".png")
            os.replace(result, out)
        return out
    except KeyboardInterrupt:
        raise ConversionError("conversion interrupted") from None
    except subprocess.TimeoutExpired:
        raise ConversionError("conversion timed out") from None
    finally:
        _status("")
        shutil.rmtree(work, ignore_errors=True)


# --- deciding what a file is -------------------------------------------------------------

def _classify(path, head):
    ext = _ext(path)
    if ext in OFFICE or ext in RICH:
        return _office
    if ext in WEB:
        return _web
    if ext in TEXT:
        return _text
    if _looks_like_text(path, head):
        return _text
    return None


def _ext(path):
    name = os.path.basename(path).lower()
    if name in ("dockerfile", "makefile"):
        return "." + name
    return os.path.splitext(name)[1]


def _looks_like_text(path, head):
    """Unknown extension: text if it has no NUL bytes, decodes, and Pillow can't open it."""
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):  # UTF-16 with a byte order mark
        return True
    if not head or b"\0" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        if e.start < len(head) - 4:  # not just a character cut off at the end
            try:
                head.decode("cp932")
            except UnicodeDecodeError:
                return False
    controls = sum(1 for b in head if b < 32 and b not in (9, 10, 12, 13, 27))
    if controls > len(head) // 100:
        return False
    try:  # XPM, XBM and ASCII PPM are pictures made of text
        from PIL import Image
        with Image.open(path):
            return False
    except Exception:
        return True


# --- cache and progress ------------------------------------------------------------------

def _cache_dir():
    d = os.path.join(tempfile.gettempdir(), f"see-cache-{os.getuid()}")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _cache_key(path):
    st = os.stat(path)
    raw = f"{VERSION}\0{os.path.abspath(path)}\0{st.st_size}\0{st.st_mtime_ns}"
    return hashlib.sha1(raw.encode("utf-8", "surrogateescape")).hexdigest()


def _status(message):
    """A one-line note on stderr while converting (the viewer hasn't started yet)."""
    if sys.stderr.isatty():
        sys.stderr.write("\r\x1b[K" + message)  # erase the line; never clear the screen
        sys.stderr.flush()


def _run(cmd, timeout, **kw):
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, **kw)


def _ok_pdf(path):
    try:
        with open(path, "rb") as f:
            return b"%PDF-" in f.read(1024)
    except OSError:
        return False


# --- Office and rich documents -----------------------------------------------------------

def soffice():
    """Path of LibreOffice's command-line program, or None."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for p in ("/Applications/LibreOffice.app/Contents/MacOS/soffice",
              os.path.expanduser("~/Applications/LibreOffice.app/Contents/MacOS/soffice")):
        if os.access(p, os.X_OK):
            return p
    return None


def _office(path, out, work):
    ext = _ext(path)
    errors = []
    office = soffice()
    if office:
        try:
            return _soffice_pdf(office, path, out, work)
        except Exception as e:
            errors.append(f"LibreOffice: {e}")
    if sys.platform == "darwin":
        if ext in RICH:
            try:
                _jxa("rich", path, out)
                return out
            except Exception as e:
                errors.append(str(e))
        try:
            return _quicklook_pdf(path, out, work)
        except Exception as e:
            errors.append(str(e))
        try:
            img = _quicklook_thumbnail(path, work)
            _status("")
            sys.stderr.write(
                f"see: only a Quick Look preview of {os.path.basename(path)} is available"
                " (install LibreOffice to see the whole document)\n"
            )
            return img
        except Exception as e:
            errors.append(str(e))
    elif not office:
        raise ConversionError("install LibreOffice (soffice) to open this kind of file")
    raise ConversionError(errors[0] if errors else "cannot convert this file")


def _soffice_pdf(office, path, out, work):
    profile = os.path.join(_cache_dir(), "libreoffice-profile")
    _run([office, "-env:UserInstallation=file://" + profile, "--headless", "--norestore",
          "--convert-to", "pdf", "--outdir", work, os.path.abspath(path)], SOFFICE_TIMEOUT)
    made = os.path.join(work, os.path.splitext(os.path.basename(path))[0] + ".pdf")
    if not _ok_pdf(made):
        raise ConversionError("conversion failed")
    os.replace(made, out)
    return out


def _quicklook_pdf(path, out, work):
    """Print Quick Look's preview of a document (HTML, or a stack of PDF pages) to a PDF."""
    _run(["qlmanage", "-p", "-o", work, path], MAC_TIMEOUT)
    previews = [d for d in os.listdir(work) if d.endswith(".qlpreview")]
    if not previews:
        raise ConversionError("Quick Look has no preview for this file")
    d = os.path.join(work, previews[0])
    for name in ("Preview.pdf",):
        if _ok_pdf(os.path.join(d, name)):
            os.replace(os.path.join(d, name), out)
            return out
    page = os.path.join(d, "Preview.html")
    if not os.path.isfile(page):
        raise ConversionError("Quick Look has no preview for this file")
    with open(page, encoding="utf-8", errors="replace") as f:
        html = f.read()

    # Keynote and Pages: nothing but one ready-made PDF per page
    pdfs = re.findall(r'<img[^>]*src="([^"]+\.pdf)"', html)
    rest = re.sub(r"(?is)<head.*?</head>|<style.*?</style>|<[^>]+>|&nbsp;|\s+", "", html)
    if pdfs and not rest:
        _merge([os.path.join(d, p) for p in pdfs], out)
        return out

    # spreadsheets: the sheet tabs link to one HTML page per sheet
    sheets = []
    if "<iframe" in html:
        for s in re.findall(r'href="(Attachment\d+\.html?)"', html):
            if s not in sheets:
                sheets.append(s)
    if sheets:
        parts = [os.path.join(work, f"sheet{i}.pdf") for i in range(len(sheets))]
        _jxa("web", [os.path.join(d, s) for s in sheets], parts, paper=(A4[1], A4[0]), margin=18)
        _merge(parts, out)
        return out

    # presentations: absolutely positioned slides; print one slide per page
    m = re.search(r"div\.slide[^{]*\{\s*width:\s*(\d+)[^;]*;\s*height:\s*(\d+)", html)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        css = ("<style>@media print { body { margin: 0 !important; background: white; }"
               " div.slide { margin: 0 !important; -webkit-box-shadow: none !important;"
               " page-break-after: always; break-after: page; } }</style>")
        printable = os.path.join(d, "see-print.html")
        with open(printable, "w", encoding="utf-8") as f:
            f.write(html.replace("</head>", css + "</head>", 1))
        _jxa("web", printable, out, paper=(w, h), margin=0)
        return out

    _jxa("web", page, out, margin=18)
    return out


def _quicklook_thumbnail(path, work):
    try:
        _run(["qlmanage", "-t", "-s", "2000", "-o", work, path], THUMBNAIL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ConversionError("Quick Look cannot preview this file") from None
    for name in os.listdir(work):
        if name.endswith(".png"):
            return os.path.join(work, name)
    raise ConversionError("Quick Look cannot preview this file")


def _merge(paths, out):
    import pypdfium2 as pdfium
    merged = pdfium.PdfDocument.new()
    for p in paths:
        src = pdfium.PdfDocument(p)
        merged.import_pages(src)
        src.close()
    if len(merged) == 0:
        raise ConversionError("empty preview")
    merged.save(out)
    merged.close()


# --- web pages ---------------------------------------------------------------------------

WEB_WIDTH = 1200  # the browser window width web pages are laid out at, in points
WEB_MAX_HEIGHT = 60000  # endless pages are cut off here


def is_url(arg):
    return re.match(r"(?i)https?://", arg) is not None


def fetch_web(url):
    """Load a web page with WebKit and return it as a one-page PDF (macOS only)."""
    if sys.platform != "darwin":
        raise ConversionError("web pages can be shown on macOS only")
    key = hashlib.sha1(f"{VERSION}\0{url}".encode()).hexdigest()
    out = os.path.join(_cache_dir(), f"web-{key}.pdf")
    _status(f"see: loading {re.sub(r'(?i)^https?://', '', url)}…")
    try:
        _jxa("url", url, out, paper=(WEB_WIDTH, WEB_MAX_HEIGHT), margin=0)
        return out
    except KeyboardInterrupt:
        raise ConversionError("loading interrupted") from None
    except subprocess.TimeoutExpired:
        raise ConversionError("loading timed out") from None
    finally:
        _status("")


# --- HTML and SVG ------------------------------------------------------------------------

def _web(path, out, work):
    if sys.platform != "darwin":
        office = soffice()
        if office and _ext(path) != ".svg":
            return _soffice_pdf(office, path, out, work)
        raise ConversionError("HTML and SVG files can be shown on macOS only")
    if _ext(path) == ".svg":
        size = _svg_size(path)
        if not size:
            raise ConversionError("not an SVG image")
        # a page exactly the drawing's size, with the drawing scaled to fill it
        page = os.path.join(work, "svg.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write(
                "<!doctype html><meta charset='utf-8'><style>html, body { margin: 0; padding: 0; }"
                f" img {{ display: block; width: {size[0]}pt; height: {size[1] - 0.5}pt; }}</style>"
                "<img src='image.svg'>"
            )
        shutil.copyfile(path, os.path.join(work, "image.svg"))
        _jxa("web", page, out, paper=size, margin=0)
        return out
    _jxa("web", path, out, margin=36)
    return out


def _svg_size(path):
    """(width, height) in points from the root <svg> element, or None."""
    with open(path, "rb") as f:
        head = f.read(8192).decode("utf-8", "replace")
    m = re.search(r"<svg\b[^>]*>", head, re.S)
    if not m:
        return None
    tag = m.group(0)

    def attr(name):
        a = re.search(r'\s%s\s*=\s*["\']([^"\']*)["\']' % name, tag)
        return a.group(1).strip() if a else None

    units = {"px": 0.75, "pt": 1, "in": 72, "cm": 72 / 2.54, "mm": 72 / 25.4, "pc": 12, "": 0.75}

    def length(s):
        n = re.fullmatch(r"([0-9.]+)\s*(px|pt|in|cm|mm|pc|)", s or "")
        return float(n.group(1)) * units[n.group(2)] if n else None

    w, h = length(attr("width")), length(attr("height"))
    if not (w or h or attr("viewBox")):
        w, h = 300 * 0.75, 150 * 0.75  # what browsers assume for an unsized SVG
    box = (attr("viewBox") or "").replace(",", " ").split()
    if len(box) == 4:
        try:
            bw, bh = float(box[2]) * 0.75, float(box[3]) * 0.75
        except ValueError:
            bw = bh = 0
        if bw > 0 and bh > 0:
            if w and not h:
                h = w * bh / bw
            elif h and not w:
                w = h * bw / bh
            elif not w and not h:
                w, h = bw, bh
    if not w or not h:
        return None
    # tiny icons get a readable page; huge drawings stay within PDF limits
    scale = max(1.0, 200 / max(w, h))
    scale = min(scale, 14000 / max(w, h))
    return round(w * scale, 2), round(h * scale, 2)


# --- text --------------------------------------------------------------------------------

def _text(path, out, work):
    text = _read_text(path)
    ext = _ext(path)
    if ext in (".csv", ".tsv"):
        rows = _rows(text, "\t" if ext == ".tsv" else ",")
        if rows and sys.platform == "darwin":
            try:  # a real table, with Japanese lined up too
                return _table_pdf(rows, out, work)
            except Exception:
                pass
        text = _table(rows) or text
    elif ext in (".json", ".geojson", ".ipynb"):
        text = _pretty_json(text)
    text = "\n".join(line.expandtabs(4) for line in text.splitlines()) or " "
    if sys.platform == "darwin":
        src = os.path.join(work, "text.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            _jxa("text", src, out)
            return out
        except Exception:
            pass  # e.g. no window server (ssh): write the PDF ourselves
    _text_pdf(text, out)
    return out


def _read_text(path):
    with open(path, "rb") as f:
        data = f.read(MAX_TEXT_CHARS * 2)
        cut = bool(f.read(1))
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", "replace")
    else:
        for enc in ("utf-8-sig", "cp932", "euc-jp"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError as e:
                if enc == "utf-8-sig" and cut and e.start >= len(data) - 4:
                    text = data[: e.start].decode(enc)
                    break
        else:
            text = data.decode("utf-8", "replace")
    if len(text) > MAX_TEXT_CHARS:
        text, cut = text[:MAX_TEXT_CHARS], True
    if cut:
        text = text.rsplit("\n", 1)[0] + "\n\n[see: the file is long; only the beginning is shown]\n"
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")


def _width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _rows(text, delimiter):
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error:
        return None
    if not rows or len(rows) > 20000 or max(len(r) for r in rows) < 2:
        return None
    return rows


def _table_pdf(rows, out, work):
    """CSV as an HTML table, printed by WebKit."""
    cols = max(len(r) for r in rows)
    wide = sum(min(max(_width(r[i]) for r in rows if i < len(r)), 40) for i in range(cols)) > 100
    cell = lambda tag, c: f"<{tag}>{html_escape(c)}</{tag}>"
    body = [f"<tr>{''.join(cell('th', c) for c in rows[0])}</tr>"]
    body += [f"<tr>{''.join(cell('td', c) for c in r)}</tr>" for r in rows[1:]]
    page = os.path.join(work, "table.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(
            "<!doctype html><meta charset='utf-8'><style>"
            "body { margin: 0; font: 9px Menlo, monospace; }"
            "table { border-collapse: collapse; }"
            "th, td { border: 1px solid #bbb; padding: 1px 4px; text-align: left;"
            " vertical-align: top; white-space: pre-wrap; max-width: 30em; }"
            "th { background: #eee; } thead { display: table-header-group; }"
            "tr { page-break-inside: avoid; }"
            f"</style><table><thead>{body[0]}</thead><tbody>{''.join(body[1:])}</tbody></table>"
        )
    _jxa("web", page, out, paper=(A4[1], A4[0]) if wide else A4, margin=28)
    return out


def _table(rows):
    """Line CSV columns up as text, if the table is narrow enough to read that way."""
    if not rows:
        return None
    cols = max(len(r) for r in rows)
    widths = [0] * cols
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], min(_width(cell), 40))
    if sum(widths) + 2 * cols > 160:
        return None
    lines = []
    for r in rows:
        cells = [c.replace("\n", " ") for c in r]
        lines.append("  ".join(c + " " * max(0, widths[i] - _width(c)) for i, c in enumerate(cells)).rstrip())
    return "\n".join(lines)


def _pretty_json(text):
    if max((len(line) for line in text.splitlines()), default=0) <= 200:
        return text  # already readable; keep it as written
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except ValueError:
        return text


# portable fallback: write the PDF directly with PDFium

FONTS = [  # monospace with Japanese first, then monospace, then anything with Japanese
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/takao-gothic/TakaoGothic.ttf",
    "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def _text_pdf(text, out, size=9.0, margin=40):
    import ctypes

    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    pdf = pdfium.PdfDocument.new()
    font = None
    wide = any(ord(c) > 0xFF for c in text)
    for p in FONTS:
        if not os.path.isfile(p) or (not wide and "Unicode" in p):
            continue
        with open(p, "rb") as f:
            data = f.read()
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        font = raw.FPDFText_LoadFont(pdf.raw, buf, len(data), raw.FPDF_FONT_TRUETYPE, 1)
        if font:
            pdf._see_font_data = buf  # keep the bytes alive while the document lives
            break
    if not font:
        font = raw.FPDFText_LoadStandardFont(pdf.raw, b"Courier")

    pw, ph = A4
    cols = int((pw - 2 * margin) / (size * 0.6))
    leading = size * 1.3
    per_page = int((ph - 2 * margin) / leading)
    lines = []
    for line in text.split("\n"):
        while _width(line) > cols:
            n, w = 0, 0
            while n < len(line) and w + _width(line[n]) <= cols:
                w += _width(line[n])
                n += 1
            lines.append(line[:n])
            line = line[n:]
        lines.append(line)
    for start in range(0, len(lines), per_page):
        page = pdf.new_page(pw, ph)
        for i, line in enumerate(lines[start : start + per_page]):
            if not line.strip():
                continue
            obj = raw.FPDFPageObj_CreateTextObj(pdf.raw, font, size)
            s = ctypes.create_string_buffer(line.encode("utf-16-le") + b"\0\0")
            raw.FPDFText_SetText(obj, ctypes.cast(s, ctypes.POINTER(raw.FPDF_WCHAR)))
            raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, margin, ph - margin - (i + 1) * leading + size * 0.3)
            raw.FPDFPage_InsertObject(page.raw, obj)
        raw.FPDFPage_GenerateContent(page.raw)
        page.close()
    tmp = out + ".part"
    pdf.save(tmp)
    pdf.close()
    os.replace(tmp, out)


# --- macOS: Cocoa and WebKit through JavaScript for Automation ---------------------------

def _jxa(mode, src, out, paper=A4, margin=None):
    """Lay out `src` with Cocoa ("rich", "text") or WebKit ("web") and print it to `out`.

    `src` and `out` may also be lists of the same length, converted in one go.
    """
    srcs, outs = ([src], [out]) if isinstance(src, str) else (src, out)
    digest = hashlib.sha1(JXA_PRINT.encode()).hexdigest()[:12]
    script = os.path.join(_cache_dir(), f"print-{digest}.js")
    if not os.path.isfile(script):
        tmp = script + f".{os.getpid()}"
        with open(tmp, "w") as f:
            f.write(JXA_PRINT)
        os.replace(tmp, script)
    if margin is None:
        margin = 54 if mode == "rich" else 40
    cmd = ["osascript", "-l", "JavaScript", script, mode, str(paper[0]), str(paper[1]), str(margin)]
    for s, o in zip(srcs, outs):
        cmd += [s if mode == "url" else os.path.abspath(s), o + ".part.pdf"]
    r = _run(cmd, MAC_TIMEOUT * len(srcs))
    for o in outs:
        if not _ok_pdf(o + ".part.pdf"):
            msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
            msg = msg[-1].split("execution error: ")[-1] if msg else "conversion failed"
            raise ConversionError(re.sub(r"^(Error: )+|\s*\(-?\d+\)$", "", msg))
        os.replace(o + ".part.pdf", o)


JXA_PRINT = r"""
ObjC.import('AppKit');
ObjC.import('WebKit');

function spin(seconds) {
  $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(seconds));
}

function printInfo(out, w, h, margin, fit) {
  var info = $.NSPrintInfo.alloc.initWithDictionary($({}));
  info.paperSize = $.NSMakeSize(w, h);
  info.topMargin = margin; info.bottomMargin = margin;
  info.leftMargin = margin; info.rightMargin = margin;
  info.horizontalPagination = fit ? $.NSPrintingPaginationModeFit : $.NSPrintingPaginationModeAutomatic;
  info.verticalPagination = $.NSPrintingPaginationModeAutomatic;
  info.horizontallyCentered = false; info.verticallyCentered = false;
  info.jobDisposition = $.NSPrintSaveJob;
  info.dictionary.setObjectForKey($.NSURL.fileURLWithPath(out), $.NSPrintJobSavingURL);
  return info;
}

function printText(fill, out, w, h, margin) {
  var width = w - 2 * margin;
  var view = $.NSTextView.alloc.initWithFrame($.NSMakeRect(0, 0, width, 100));
  view.verticallyResizable = true;
  view.horizontallyResizable = false;
  view.textContainer.containerSize = $.NSMakeSize(width, 1e7);
  view.textContainer.widthTracksTextView = true;
  fill(view);
  view.layoutManager.ensureLayoutForTextContainer(view.textContainer);
  view.sizeToFit;
  var op = $.NSPrintOperation.printOperationWithViewPrintInfo(view, printInfo(out, w, h, margin, true));
  op.showsPrintPanel = false; op.showsProgressPanel = false;
  if (!op.runOperation) throw new Error('printing failed');
}

function evaluate(web, code) {
  var result = null, done = false;
  web.evaluateJavaScriptCompletionHandler(code, function (value, error) { result = value; done = true; });
  for (var t = 0; t < 200 && !done; t++) spin(0.02);
  return result && !result.isNil() ? result.js : null;
}

// url: load a web page as a browser window `width` wide, then make the window as tall as
// the page (so lazy images load too) and save all of it as one PDF page: the screen look
// rather than the print style, with selectable text and the links kept.
function snapshot(address, out, width, maxHeight) {
  $.NSApplication.sharedApplication;
  var frame = $.NSMakeRect(0, 0, width, 900);
  var web = $.WKWebView.alloc.initWithFrameConfiguration(frame, $.WKWebViewConfiguration.alloc.init);
  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(frame, 0, $.NSBackingStoreBuffered, false);
  win.contentView = web;
  var url = $.NSURL.URLWithString(address);
  if (!url || url.isNil()) throw new Error('not a web address');
  web.loadRequest($.NSURLRequest.requestWithURL(url));
  for (var t = 0; t < 600 && (web.isLoading || t < 4); t++) spin(0.05);
  spin(0.5);
  var heightJs = 'Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)';
  var height = evaluate(web, heightJs);
  if (!height || !evaluate(web, 'document.body ? document.body.childElementCount : 0'))
    throw new Error('the page did not load');
  for (var i = 0; i < 3; i++) {  // growing the window can load more content
    height = Math.min(height, maxHeight);
    web.frame = $.NSMakeRect(0, 0, width, height);
    win.setContentSize($.NSMakeSize(width, height));
    spin(0.4);
    for (var t = 0; t < 200 && web.isLoading; t++) spin(0.05);
    var now = evaluate(web, heightJs) || height;
    if (Math.min(now, maxHeight) === height) break;
    height = now;
  }
  var config = $.WKPDFConfiguration.alloc.init;
  config.rect = $.NSMakeRect(0, 0, width, height);
  var data = null, done = false;
  web.createPDFWithConfigurationCompletionHandler(config, function (d, e) { data = d; done = true; });
  for (var t = 0; t < 1200 && !done; t++) spin(0.05);
  if (!data || data.isNil()) throw new Error('could not capture the page');
  data.writeToFileAtomically(out, true);
}

function run(argv) {
  var mode = argv[0];
  var w = parseFloat(argv[1]), h = parseFloat(argv[2]), margin = parseFloat(argv[3]);
  for (var i = 4; i + 1 < argv.length; i += 2) convert(mode, argv[i], argv[i + 1], w, h, margin);
}

function convert(mode, src, out, w, h, margin) {
  var url = $.NSURL.fileURLWithPath(src);

  if (mode === 'rich') {
    var text = $.NSAttributedString.alloc.initWithURLOptionsDocumentAttributesError(url, $({}), null, null);
    if (!text || text.isNil()) throw new Error('cannot read this document');
    printText(function (view) { view.textStorage.setAttributedString(text); }, out, w, h, margin);
    return;
  }

  if (mode === 'text') {
    var s = $.NSString.stringWithContentsOfFileEncodingError(src, $.NSUTF8StringEncoding, null);
    if (!s || s.isNil()) throw new Error('cannot read the text');
    var font = $.NSFont.fontWithNameSize('Menlo', 9);
    if (!font || font.isNil()) font = $.NSFont.userFixedPitchFontOfSize(9);
    printText(function (view) {
      view.string = s;
      view.textStorage.font = font;
      view.textStorage.foregroundColor = $.NSColor.blackColor;
      var style = $.NSMutableParagraphStyle.alloc.init;  // even lines, whatever the script
      style.minimumLineHeight = 12; style.maximumLineHeight = 12;
      view.textStorage.addAttributeValueRange($.NSParagraphStyleAttributeName, style, $.NSMakeRange(0, view.textStorage.length));
    }, out, w, h, margin);
    return;
  }

  if (mode === 'url') {
    snapshot(src, out, w, h);
    return;
  }

  // web: load the page in WebKit, then print it
  $.NSApplication.sharedApplication;
  var frame = $.NSMakeRect(0, 0, Math.max(400, w - 2 * margin), Math.max(300, h - 2 * margin));
  var web = $.WKWebView.alloc.initWithFrameConfiguration(frame, $.WKWebViewConfiguration.alloc.init);
  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(frame, 0, $.NSBackingStoreBuffered, false);
  win.contentView = web;
  web.loadFileURLAllowingReadAccessToURL(url, url.URLByDeletingLastPathComponent);
  for (var t = 0; t < 600 && (web.isLoading || t < 4); t++) spin(0.05);
  spin(0.3);  // let scripts and fonts settle
  var op = web.printOperationWithPrintInfo(printInfo(out, w, h, margin, true));
  op.showsPrintPanel = false; op.showsProgressPanel = false;
  op.view.frame = $.NSMakeRect(0, 0, w - 2 * margin, h - 2 * margin);
  op.runOperationModalForWindowDelegateDidRunSelectorContextInfo(win, $(), $(), $());
  // wait until the PDF is complete (it ends with %%EOF) and has stopped growing
  var fm = $.NSFileManager.defaultManager, last = -1;
  for (var i = 0; i < 1200; i++) {
    spin(0.05);
    var data = fm.contentsAtPath(out);
    if (data && !data.isNil() && data.length > 0) {
      var n = data.length;
      if (n === last) {
        var tail = $.NSString.alloc.initWithDataEncoding(data.subdataWithRange($.NSMakeRange(Math.max(0, n - 32), Math.min(32, n))), $.NSISOLatin1StringEncoding).js;
        if (tail.indexOf('%%EOF') >= 0) return;
      }
      last = n;
    }
  }
  throw new Error('printing did not finish');
}
"""
