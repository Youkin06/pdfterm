"""Terminal handling: raw mode, size queries, and input parsing (keys + SGR mouse)."""

import codecs
import fcntl
import os
import select
import struct
import sys
import termios
import time
import tty
from dataclasses import dataclass

# Mouse button codes in SGR reports
WHEEL_UP, WHEEL_DOWN, WHEEL_LEFT, WHEEL_RIGHT = 64, 65, 66, 67
SHIFT, ALT, CTRL, MOTION = 4, 8, 16, 32


@dataclass
class Key:
    name: str  # "char", "enter", "esc", "backspace", "up", "down", "left", "right",
    #            "pageup", "pagedown", "home", "end", "ctrl-c", "alt"
    char: str = ""


@dataclass
class Mouse:
    button: int  # base button: 0 left, 1 middle, 2 right, 3 none (motion), 64.. wheel
    x: int  # 1-based cell column
    y: int  # 1-based cell row
    pressed: bool
    px: float = 0.0  # pointer position in window pixels (cell centre when unknown)
    py: float = 0.0
    shift: bool = False
    alt: bool = False
    ctrl: bool = False
    motion: bool = False


@dataclass
class ModeReport:
    mode: int
    value: int  # 1 set, 2 reset, 0 unknown


@dataclass
class WindowPixels:
    height: int
    width: int


class Terminal:
    def __init__(self):
        self.fd_in = sys.stdin.fileno()
        self.fd_out = sys.stdout.fileno()
        self._saved = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        self._buf = ""
        self.pixel_mouse = False
        self.cell = (8, 16)
        self.bytes_written = 0

    # --- setup / teardown -------------------------------------------------

    def enter(self):
        self._saved = termios.tcgetattr(self.fd_in)
        tty.setraw(self.fd_in)
        # alternate screen, hide cursor, mouse: buttons + drag motion, SGR encoding
        self.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1002h\x1b[?1006h\x1b[2J")
        # SGR-pixels mouse mode (1016) gives smooth dragging; ask whether it is supported
        self.write("\x1b[?1016h\x1b[?1016$p")
        deadline = time.time() + 0.3
        while time.time() < deadline:
            evs = self.read_events(timeout=deadline - time.time())
            if any(isinstance(e, ModeReport) and e.mode == 1016 for e in evs):
                self.pixel_mouse = any(e.value == 1 for e in evs if isinstance(e, ModeReport))
                break

    def leave(self):
        self.write(
            "\x1b_Ga=d,d=A,q=2\x1b\\"  # delete all kitty images
            "\x1b[?1016l\x1b[?1006l\x1b[?1002l\x1b[?1000l\x1b[?25h\x1b[?1049l"
        )
        if self._saved is not None:
            termios.tcsetattr(self.fd_in, termios.TCSADRAIN, self._saved)

    def write(self, s):
        data = s.encode() if isinstance(s, str) else s
        self.bytes_written += len(data)
        view = memoryview(data)
        while view:
            try:
                n = os.write(self.fd_out, view)
            except BlockingIOError:
                select.select([], [self.fd_out], [], 1)
                continue
            view = view[n:]

    # --- size -------------------------------------------------------------

    def size(self):
        """Return (rows, cols, cell_width_px, cell_height_px)."""
        rows, cols, xpix, ypix = struct.unpack(
            "HHHH", fcntl.ioctl(self.fd_out, termios.TIOCGWINSZ, b"\0" * 8)
        )
        if not xpix or not ypix:
            px = self._query_window_pixels()
            if px:
                ypix, xpix = px.height, px.width
        if not xpix or not ypix:
            xpix, ypix = cols * 8, rows * 16  # last resort guess
        self.cell = (max(1, xpix // max(1, cols)), max(1, ypix // max(1, rows)))
        return rows, cols, self.cell[0], self.cell[1]

    def _query_window_pixels(self):
        self.write("\x1b[14t")
        deadline = time.time() + 0.5
        while time.time() < deadline:
            for ev in self.read_events(timeout=deadline - time.time()):
                if isinstance(ev, WindowPixels):
                    return ev
        return None

    # --- input ------------------------------------------------------------

    def read_events(self, timeout):
        r, _, _ = select.select([self.fd_in], [], [], max(0, timeout))
        if not r:
            return []
        # drain everything that is already waiting so bursts (wheel, drag) are batched
        chunks = []
        while True:
            try:
                chunks.append(os.read(self.fd_in, 65536))
            except BlockingIOError:
                break
            if not select.select([self.fd_in], [], [], 0)[0]:
                break
        self._buf += self._decoder.decode(b"".join(chunks))
        events, self._buf = parse(self._buf)
        cw, ch = self.cell
        for ev in events:
            if isinstance(ev, Mouse):
                if self.pixel_mouse:  # coordinates are pixels; derive the cell
                    ev.px, ev.py = ev.x - 1, ev.y - 1
                    ev.x, ev.y = int(ev.px // cw) + 1, int(ev.py // ch) + 1
                else:
                    ev.px, ev.py = (ev.x - 0.5) * cw, (ev.y - 0.5) * ch
        return events


def _final_index(s, start):
    """Index of the final byte of a CSI sequence starting at s[start] (after "ESC[")."""
    for j in range(start, len(s)):
        if "\x40" <= s[j] <= "\x7e":
            return j
    return -1


_CSI_KEYS = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
_TILDE_KEYS = {"1": "home", "7": "home", "4": "end", "8": "end", "5": "pageup", "6": "pagedown"}


def parse(s):
    """Parse terminal input. Returns (events, unconsumed_tail)."""
    events = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\x1b":
            if i + 1 >= n:
                events.append(Key("esc"))
                i += 1
                continue
            nxt = s[i + 1]
            if nxt == "[":
                j = _final_index(s, i + 2)
                if j < 0:
                    return events, s[i:]  # incomplete; wait for more
                body, final = s[i + 2 : j], s[j]
                ev = _parse_csi(body, final)
                if ev is not None:
                    events.append(ev)
                i = j + 1
            elif nxt == "O":
                if i + 2 >= n:
                    return events, s[i:]
                name = _CSI_KEYS.get(s[i + 2])
                if name:
                    events.append(Key(name))
                i += 3
            else:
                # Alt+key (e.g. Ghostty Cmd mappings send ESC c / ESC z / ESC a)
                events.append(Key("alt", nxt))
                i += 2
            continue
        if c in "\r\n":
            events.append(Key("enter"))
        elif c in "\x7f\x08":
            events.append(Key("backspace"))
        elif c == "\x03":
            events.append(Key("ctrl-c"))
        elif c >= " ":
            events.append(Key("char", c))
        i += 1
    return events, ""


def _parse_csi(body, final):
    if body.startswith("<") and final in "Mm":
        try:
            b, x, y = (int(v) for v in body[1:].split(";"))
        except ValueError:
            return None
        return Mouse(
            button=b & ~(SHIFT | ALT | CTRL | MOTION),
            x=x,
            y=y,
            pressed=final == "M",
            shift=bool(b & SHIFT),
            alt=bool(b & ALT),
            ctrl=bool(b & CTRL),
            motion=bool(b & MOTION),
        )
    if final == "y" and body.startswith("?") and body.endswith("$"):
        try:
            mode, value = (int(v) for v in body[1:-1].split(";"))
        except ValueError:
            return None
        return ModeReport(mode, value)
    if final == "t":
        parts = body.split(";")
        if len(parts) == 3 and parts[0] == "4":
            try:
                return WindowPixels(int(parts[1]), int(parts[2]))
            except ValueError:
                return None
        return None
    if final == "~":
        name = _TILDE_KEYS.get(body.split(";")[0])
        return Key(name) if name else None
    name = _CSI_KEYS.get(final)
    return Key(name) if name else None
