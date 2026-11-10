import shutil
import subprocess

import pytest

from see.video import is_video


def test_video_extensions(tmp_path):
    for name in ("clip.mp4", "clip.MOV", "clip.mkv", "clip.webm"):
        path = tmp_path / name
        path.write_bytes(b"\0" * 16)
        assert is_video(str(path))


def test_images_and_text_are_not_videos(png, pdf3, tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("hello")
    assert not is_video(png) and not is_video(pdf3) and not is_video(str(text))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_real_video_without_extension(tmp_path):
    path = tmp_path / "clip"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
         "-t", "1", "-f", "mp4", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    assert is_video(str(path))
