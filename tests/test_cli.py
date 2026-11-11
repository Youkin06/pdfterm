import subprocess
import sys

from see import __version__


def run(*args):
    return subprocess.run([sys.executable, "-m", "see", *args], capture_output=True, text=True)


def test_version():
    assert run("--version").stdout.strip() == f"see {__version__}"


def test_missing_file():
    r = run("does-not-exist.pdf")
    assert r.returncode == 1 and "no such file" in r.stderr


def test_needs_a_terminal(png):
    r = run(png)
    assert r.returncode == 1 and "must be run in a terminal" in r.stderr


def test_web_address_is_not_a_missing_file():
    from see.convert import is_url

    assert is_url("https://example.com") and is_url("HTTP://example.com/a")
    assert not is_url("example.com") and not is_url("notes-https://x.txt")
    r = run("https://example.com")  # no terminal here, but not rejected as a missing file
    assert r.returncode == 1 and "must be run in a terminal" in r.stderr
