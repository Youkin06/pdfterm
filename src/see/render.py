"""Document loading (PDF via PDFium, images via Pillow) and output (kitty graphics protocol)."""

import base64
import os
import subprocess
import sys
import tempfile
import threading
import zlib

import pypdfium2 as pdfium
from PIL import Image as PILImage
from PIL import ImageOps

from .convert import convert

# Images are measured in "points" like PDF pages, at 96 pixels per inch, so that a
# zoom level of 100% shows every image pixel as one screen pixel.
IMAGE_PT_PER_PX = 72 / 96


class UnsupportedFile(Exception):
    """The file is not of any kind see can show."""


def open_document(path):
    """Open a PDF or an image, deciding by the file's content rather than its name."""
    with open(path, "rb") as f:
        head = f.read(1024)
    if b"%PDF-" in head:
        return Document(path)
    from .anim import open_animated  # animated GIF, APNG and WebP

    animated = open_animated(path)
    if animated:
        return animated
    # documents, text and SVG are converted to a PDF first (see convert.py)
    converted = convert(path, head)
    if converted:
        return Document(converted) if converted.endswith(".pdf") else ImageDocument(converted)
    return ImageDocument(path)


class Document:
    """A PDF opened with PDFium. PDFium is not thread-safe, so every call takes `lock`."""

    has_text = True

    def __init__(self, path):
        self.pdf = pdfium.PdfDocument(path)
        self.lock = threading.Lock()
        self._sizes = {}
        self._text = {}  # page index -> PdfTextPage

    def __len__(self):
        return len(self.pdf)

    def page_size(self, index):
        """Page size in PDF points (width, height)."""
        if index not in self._sizes:
            with self.lock:
                self._sizes[index] = self.pdf[index].get_size()
        return self._sizes[index]

    # --- text (coordinates are PDF points, origin at the bottom-left) ---------------

    def _textpage(self, index):
        tp = self._text.get(index)
        if tp is None:
            tp = self._text[index] = self.pdf[index].get_textpage()
        return tp

    def char_at(self, index, x, y, tolerance):
        """Index of the character at (x, y), or None."""
        with self.lock:
            i = self._textpage(index).get_index(x, y, tolerance, tolerance)
        return i if i is not None and i >= 0 else None

    def char_count(self, index):
        with self.lock:
            return self._textpage(index).count_chars()

    def text(self, index, first, last):
        """Text of characters first..last (inclusive), with plain newlines."""
        with self.lock:
            s = self._textpage(index).get_text_range(first, last - first + 1)
        return s.replace("\r\n", "\n").replace("\r", "\n")

    def text_rects(self, index, first, last):
        """Rectangles (left, bottom, right, top) covering characters first..last."""
        with self.lock:
            tp = self._textpage(index)
            n = tp.count_rects(first, last - first + 1)
            return [tp.get_rect(i) for i in range(n)]

    def render_tile(self, index, zoom, x, y, w, h):
        """Render the w x h pixel region at (x, y) of page `index` scaled by `zoom`.

        Coordinates are page pixels at that zoom (pixels per PDF point).
        Returns (Image, width, height) of what was actually rendered.
        """
        pw, ph = self.page_size(index)
        W, H = pw * zoom, ph * zoom
        crop = (x / zoom, max(0.0, (H - y - h) / zoom), max(0.0, (W - x - w) / zoom), y / zoom)
        with self.lock:
            bitmap = self.pdf[index].render(
                scale=zoom, crop=crop, rev_byteorder=True, fill_color=(255, 255, 255, 255)
            )
            buf = bytes(bitmap.buffer)
            bw, bh, stride, ch = bitmap.width, bitmap.height, bitmap.stride, bitmap.n_channels
        return Image.from_rows(buf, bw, bh, stride, ch), bw, bh


class ImageDocument:
    """A picture shown as a one-page document, with the same interface as Document."""

    has_text = False

    def __init__(self, path):
        self.lock = threading.Lock()
        img = _load_image(path)
        img = ImageOps.exif_transpose(img)  # phone photos store their rotation separately
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "transparency" in img.info or img.mode in ("LA", "PA") else "RGB")
        self.img = img

    def __len__(self):
        return 1

    def page_size(self, index):
        w, h = self.img.size
        return w * IMAGE_PT_PER_PX, h * IMAGE_PT_PER_PX

    def render_tile(self, index, zoom, x, y, w, h):
        scale = zoom * IMAGE_PT_PER_PX  # screen pixels per image pixel
        iw, ih = self.img.size
        w = max(1, min(w, int(iw * scale) - x))
        h = max(1, min(h, int(ih * scale) - y))
        box = (x / scale, y / scale, (x + w) / scale, (y + h) / scale)
        # enlarging small images (pixel art, icons) a lot stays crisp with nearest-neighbour
        resample = PILImage.NEAREST if scale >= 4 else (
            PILImage.LANCZOS if scale < 1 else PILImage.BICUBIC
        )
        with self.lock:
            tile = self.img.resize((w, h), resample=resample, box=box)
        ch = 4 if tile.mode == "RGBA" else 3
        return Image.from_rows(tile.tobytes(), w, h, w * ch, ch), w, h

    # no text layer
    def char_count(self, index):
        return 0

    def char_at(self, index, x, y, tolerance):
        return None

    def text(self, index, first, last):
        return ""

    def text_rects(self, index, first, last):
        return []


def _load_image(path):
    try:
        img = PILImage.open(path)
        img.load()
        return img
    except Exception:
        # HEIC/HEIF (iPhone photos) are not readable by Pillow; macOS can convert them
        if sys.platform == "darwin":
            fd, tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            try:
                subprocess.run(
                    ["sips", "-s", "format", "png", path, "--out", tmp],
                    capture_output=True, check=True, timeout=30,
                )
                img = PILImage.open(tmp)
                img.load()
                return img
            except Exception:
                pass
            finally:
                os.unlink(tmp)
        raise UnsupportedFile(path)


class Image:
    """Pixels ready to send: zlib-compressed raw RGB(A).

    Raw pixels rather than PNG because multiplexers like herdr decode images and send
    them on uncompressed; plain RGB keeps that at 3 bytes per pixel instead of 4.
    """

    def __init__(self, data, width, height, channels):
        self.data, self.width, self.height, self.channels = data, width, height, channels

    @classmethod
    def from_rows(cls, buf, width, height, stride, channels):
        row = width * channels
        if stride == row:
            packed = buf[: row * height]
        else:
            packed = b"".join(buf[y * stride : y * stride + row] for y in range(height))
        return cls(zlib.compress(packed, 1), width, height, channels)


def kitty_upload(image, image_id):
    """Transmit an Image to the terminal without displaying it."""
    data = base64.standard_b64encode(image.data)
    fmt = 32 if image.channels == 4 else 24
    first = f"a=t,f={fmt},s={image.width},v={image.height},o=z,i={image_id},q=2"
    out = []
    step = 4096
    for pos in range(0, len(data), step):
        part = data[pos : pos + step]
        more = 1 if pos + step < len(data) else 0
        head = f"{first},m={more}" if pos == 0 else f"m={more}"
        out.append(b"\x1b_G" + head.encode() + b";" + part + b"\x1b\\")
    return b"".join(out)


def kitty_place(image_id, row, col, src, offset=(0, 0), cells=None, z=0, placement=1):
    """Show the `src` rectangle (x, y, w, h in image pixels) of an uploaded image.

    The placement starts at cell (row, col), shifted by `offset` pixels inside that cell.
    With `cells` (cols, rows) the source is stretched over that many cells; otherwise it is
    shown at its natural pixel size. Re-placing replaces the previous placement in place,
    which makes panning cheap: no image data is sent again. Higher `z` draws on top.
    """
    x, y, w, h = (max(0, int(round(v))) for v in src)
    keys = f"a=p,i={image_id},p={placement},x={x},y={y},w={max(1, w)},h={max(1, h)},X={offset[0]},Y={offset[1]},z={z},C=1,q=2"
    if cells:
        keys += f",c={max(1, cells[0])},r={max(1, cells[1])}"
    return f"\x1b[{row};{col}H\x1b_G{keys}\x1b\\".encode()


def kitty_unplace_all(image_id):
    """Remove every placement of an image but keep its data."""
    return f"\x1b_Ga=d,d=i,i={image_id},q=2\x1b\\".encode()


def kitty_delete(image_id):
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\".encode()
