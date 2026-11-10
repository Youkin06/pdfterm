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
