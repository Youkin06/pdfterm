"""PDF rendering (via PDFium) and image output (kitty graphics protocol)."""

import base64
import struct
import threading
import zlib

import pypdfium2 as pdfium


class Document:
    """A PDF opened with PDFium. PDFium is not thread-safe, so every call takes `lock`."""

    def __init__(self, path):
        self.pdf = pdfium.PdfDocument(path)
        self.lock = threading.Lock()
        self._sizes = {}

    def __len__(self):
        return len(self.pdf)

    def page_size(self, index):
        """Page size in PDF points (width, height)."""
        if index not in self._sizes:
            with self.lock:
                self._sizes[index] = self.pdf[index].get_size()
        return self._sizes[index]

    def render_tile(self, index, zoom, x, y, w, h):
        """Render the w x h pixel region at (x, y) of page `index` scaled by `zoom`.

        Coordinates are page pixels at that zoom (pixels per PDF point).
        Returns (png_bytes, width, height) of what was actually rendered.
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
        return encode_png(buf, bw, bh, stride, ch), bw, bh


def encode_png(buf, width, height, stride, channels):
    """Minimal PNG encoder for 8-bit RGB/RGBA rows (no external dependencies)."""
    row_len = width * channels
    raw = b"".join(
        b"\x00" + buf[y * stride : y * stride + row_len] for y in range(height)
    )

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    color_type = 6 if channels == 4 else 2
    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 1))
        + chunk(b"IEND", b"")
    )


def kitty_upload(png, image_id):
    """Transmit an image to the terminal without displaying it."""
    data = base64.standard_b64encode(png)
    out = []
    step = 4096
    for pos in range(0, len(data), step):
        part = data[pos : pos + step]
        more = 1 if pos + step < len(data) else 0
        head = f"a=t,f=100,i={image_id},q=2,m={more}" if pos == 0 else f"m={more}"
        out.append(b"\x1b_G" + head.encode() + b";" + part + b"\x1b\\")
    return b"".join(out)


def kitty_place(image_id, row, col, src, offset=(0, 0), cells=None):
    """Show the `src` rectangle (x, y, w, h in image pixels) of an uploaded image.

    The placement starts at cell (row, col), shifted by `offset` pixels inside that cell.
    With `cells` (cols, rows) the source is stretched over that many cells; otherwise it is
    shown at its natural pixel size. Re-placing replaces the previous placement in place,
    which makes panning cheap: no image data is sent again.
    """
    x, y, w, h = (max(0, int(round(v))) for v in src)
    keys = f"a=p,i={image_id},p=1,x={x},y={y},w={max(1, w)},h={max(1, h)},X={offset[0]},Y={offset[1]},C=1,q=2"
    if cells:
        keys += f",c={max(1, cells[0])},r={max(1, cells[1])}"
    return f"\x1b[{row};{col}H\x1b_G{keys}\x1b\\".encode()


def kitty_delete(image_id):
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\".encode()
