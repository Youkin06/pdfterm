"""PDF rendering (via PDFium) and image output (kitty graphics protocol)."""

import base64
import struct
import zlib

import pypdfium2 as pdfium

BACKGROUND = (38, 38, 42)


class Document:
    def __init__(self, path):
        self.pdf = pdfium.PdfDocument(path)
        self._sizes = {}

    def __len__(self):
        return len(self.pdf)

    def page_size(self, index):
        """Page size in PDF points (width, height)."""
        if index not in self._sizes:
            self._sizes[index] = self.pdf[index].get_size()
        return self._sizes[index]

    def render_view(self, index, zoom, sx, sy, vw, vh):
        """Render the part of page `index` seen through a vw x vh pixel viewport.

        `zoom` is pixels per PDF point; (sx, sy) is the viewport's top-left corner in
        page pixels (negative when the page is smaller than the viewport and centered).
        Returns raw RGB bytes of exactly vw x vh pixels.
        """
        pw, ph = self.page_size(index)
        W, H = pw * zoom, ph * zoom
        x0, y0 = max(sx, 0), max(sy, 0)
        x1, y1 = min(sx + vw, W), min(sy + vh, H)
        canvas = bytearray(bytes(BACKGROUND) * (vw * vh))
        if x1 <= x0 or y1 <= y0:
            return bytes(canvas)

        crop = (x0 / zoom, (H - y1) / zoom, (W - x1) / zoom, y0 / zoom)  # left, bottom, right, top
        bitmap = self.pdf[index].render(
            scale=zoom, crop=crop, rev_byteorder=True, fill_color=(255, 255, 255, 255)
        )
        src = bytes(bitmap.buffer)
        stride, ch = bitmap.stride, bitmap.n_channels
        ox, oy = int(round(x0 - sx)), int(round(y0 - sy))
        w = min(bitmap.width, vw - ox)
        h = min(bitmap.height, vh - oy)
        for row in range(h):
            start = row * stride
            line = src[start : start + w * ch]
            if ch != 3:  # drop the 4th channel if PDFium gave us RGBx
                line = bytes(b for k, b in enumerate(line) if k % ch < 3)
            dst = ((oy + row) * vw + ox) * 3
            canvas[dst : dst + w * 3] = line
        return bytes(canvas)


def encode_png(rgb, width, height):
    """Minimal PNG encoder for 8-bit RGB data (no external dependencies)."""
    row_len = width * 3
    raw = b"".join(b"\x00" + rgb[y * row_len : (y + 1) * row_len] for y in range(height))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 1))
        + chunk(b"IEND", b"")
    )


def kitty_image(png, image_id, row, col, cols, rows):
    """Escape sequence that draws `png` at (row, col), stretched over cols x rows cells."""
    data = base64.standard_b64encode(png)
    out = [f"\x1b[{row};{col}H".encode()]
    step = 4096
    for pos in range(0, len(data), step):
        part = data[pos : pos + step]
        more = 1 if pos + step < len(data) else 0
        if pos == 0:
            head = f"a=T,f=100,i={image_id},p=1,c={cols},r={rows},C=1,q=2,m={more}"
        else:
            head = f"m={more}"
        out.append(b"\x1b_G" + head.encode() + b";" + part + b"\x1b\\")
    return b"".join(out)


def kitty_delete(image_id):
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\".encode()
