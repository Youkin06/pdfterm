"""Shared fixtures: small sample files generated on the fly (nothing binary is committed)."""

import pypdfium2 as pdfium
import pytest
from PIL import Image as PILImage


class FakeTerm:
    """Stands in for see.term.Terminal: a fixed size and a record of what was written."""

    def __init__(self, rows=40, cols=80, cw=12, ch=26):
        self._size = (rows, cols, cw, ch)
        self.out = []

    def size(self):
        return self._size

    def write(self, data):
        self.out.append(data if isinstance(data, bytes) else data.encode())


@pytest.fixture
def fake_term():
    return FakeTerm()


@pytest.fixture
def pdf3(tmp_path):
    """A blank three-page A4 PDF."""
    doc = pdfium.PdfDocument.new()
    for _ in range(3):
        doc.new_page(595, 842)
    path = tmp_path / "three.pdf"
    doc.save(str(path))
    return str(path)


@pytest.fixture
def png(tmp_path):
    path = tmp_path / "picture.png"
    PILImage.new("RGBA", (300, 200), (200, 80, 40, 255)).save(path)
    return str(path)


@pytest.fixture
def gif(tmp_path):
    frames = [PILImage.new("RGB", (64, 48), (i * 20, 100, 200)) for i in range(6)]
    path = tmp_path / "anim.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0)
    return str(path)
