import sys

import pytest

from see.render import Document, ImageDocument, UnsupportedFile, open_document


def test_pdf_is_detected_by_content(pdf3, tmp_path):
    renamed = tmp_path / "no-extension"
    renamed.write_bytes(open(pdf3, "rb").read())
    doc = open_document(str(renamed))
    assert isinstance(doc, Document) and len(doc) == 3


def test_image_tile_has_the_requested_size(png):
    doc = open_document(png)
    assert isinstance(doc, ImageDocument) and len(doc) == 1
    img, w, h = doc.render_tile(0, 1.0, 10, 10, 120, 80)
    assert (w, h) == (120, 80) and img.channels == 4


def test_animated_gif_is_animated(gif):
    doc = open_document(gif)
    assert type(doc).__name__ == "AnimatedImage"


def test_text_file_becomes_pages(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello see\n" * 200)
    doc = open_document(str(path))
    assert isinstance(doc, Document) and len(doc) >= 1


def test_unknown_binary_is_refused(tmp_path):
    path = tmp_path / "model.stl"
    path.write_bytes(bytes(range(256)) * 8)
    with pytest.raises(UnsupportedFile):
        open_document(str(path))


@pytest.mark.skipif(sys.platform != "darwin", reason="text layout uses macOS tools")
def test_japanese_text_is_selectable(tmp_path):
    path = tmp_path / "memo.md"
    path.write_text("# メモ\nこんにちは、see。\n")
    doc = open_document(str(path))
    assert "こんにちは" in doc.text(0, 0, doc.char_count(0) - 1)
