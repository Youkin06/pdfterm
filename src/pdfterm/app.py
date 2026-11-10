"""pdfterm: a mouse-friendly PDF viewer for terminals with the kitty graphics protocol."""

import argparse
import os
import signal
import subprocess
import sys
import time
import unicodedata

from . import __version__
from .render import Document, encode_png, kitty_delete, kitty_image
from .term import (
    WHEEL_DOWN,
    WHEEL_LEFT,
    WHEEL_RIGHT,
    WHEEL_UP,
    Key,
    Mouse,
    Terminal,
)

ZOOM_STEP = 1.1
ZOOM_MIN_FACTOR = 0.25  # relative to fit-page
ZOOM_MAX = 16.0  # pixels per point
DOUBLE_CLICK = 0.4
FLIP_COOLDOWN = 0.3
EDGE_HITS_TO_FLIP = 3

TEXT = {
    "en": {
        "fit": "Fit",
        "width": "Width",
        "quit": "Quit",
        "goto": "Go to page: ",
        "hint": "Wheel: scroll · Ctrl+wheel: zoom · Drag: move · Double-click: zoom · g: page · q: quit",
        "bad_page": "No such page",
    },
    "ja": {
        "fit": "全体",
        "width": "幅",
        "quit": "閉じる",
        "goto": "ページ番号: ",
        "hint": "ホイール: スクロール · Ctrl+ホイール: ズーム · ドラッグ: 移動 · ダブルクリック: 拡大 · g: ページ指定 · q: 終了",
        "bad_page": "そのページはありません",
    },
}


def _lang():
    """Pick the UI language: PDFTERM_LANG, then the macOS UI language, then LANG."""
    v = os.environ.get("PDFTERM_LANG", "")
    if not v and sys.platform == "darwin":
        # terminals often export LANG=en_US.UTF-8 even when the Mac itself is in Japanese
        try:
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleLanguages"],
                capture_output=True, text=True, timeout=1,
            ).stdout
            v = out.replace("(", "").strip().strip('"')
        except Exception:
            v = ""
    if not v:
        v = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG", "")
    return "ja" if v.lower().startswith("ja") else "en"


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def clip(s, w):
    out, used = "", 0
    for c in s:
        cw = width(c)
        if used + cw > w:
            break
        out += c
        used += cw
    return out


class Viewer:
    def __init__(self, path, term):
        self.path = path
        self.term = term
        self.doc = Document(path)
        self.t = TEXT[_lang()]
        self.page = 0
        self.mode = "fit"  # "fit", "width" or "custom"
        self.zoom = 1.0
        self.sx = self.sy = 0.0
        self.buttons = []  # (x0, x1, action) on the toolbar row
        self.prompt = None  # text typed for "go to page", or None
        self.message = ""
        self.drag = None  # (x, y, sx, sy, moved)
        self.last_click = (0.0, 0, 0)
        self.last_flip = 0.0
        self.edge_hits = 0
        self.image_id = 1
        self.dirty = True
        self.resized = True
        self.running = True

    # --- geometry ---------------------------------------------------------

    def measure(self):
        self.rows, self.cols, self.cw, self.ch = self.term.size()
        self.vr = max(1, self.rows - 2)  # viewport rows: between toolbar and status
        self.vc = max(1, self.cols)
        self.vw, self.vh = self.vc * self.cw, self.vr * self.ch

    def fit_zoom(self):
        pw, ph = self.doc.page_size(self.page)
        return min(self.vw / pw, self.vh / ph)

    def page_px(self):
        pw, ph = self.doc.page_size(self.page)
        return pw * self.zoom, ph * self.zoom

    def apply_mode(self):
        if self.mode == "fit":
            self.zoom = self.fit_zoom()
        elif self.mode == "width":
            self.zoom = self.vw / self.doc.page_size(self.page)[0]
        self.clamp()

    def clamp(self):
        W, H = self.page_px()
        self.sx = (W - self.vw) / 2 if W <= self.vw else min(max(self.sx, 0), W - self.vw)
        self.sy = (H - self.vh) / 2 if H <= self.vh else min(max(self.sy, 0), H - self.vh)

    def zoom_at(self, factor, px=None, py=None):
        """Zoom by `factor`, keeping viewport pixel (px, py) fixed on the page."""
        if px is None:
            px, py = self.vw / 2, self.vh / 2
        fit = self.fit_zoom()
        new = min(max(self.zoom * factor, fit * ZOOM_MIN_FACTOR), ZOOM_MAX)
        # page point under the cursor stays under the cursor
        ux, uy = (self.sx + px) / self.zoom, (self.sy + py) / self.zoom
        self.zoom = new
        self.sx, self.sy = ux * new - px, uy * new - py
        self.mode = "fit" if abs(new - fit) < 1e-6 else "custom"
        self.clamp()
        self.dirty = True

    def go(self, page, at_bottom=False):
        page = min(max(page, 0), len(self.doc) - 1)
        if page == self.page:
            return
        self.page = page
        self.apply_mode()
        W, H = self.page_px()
        self.sy = H - self.vh if at_bottom else 0
        self.clamp()
        self.edge_hits = 0
        self.dirty = True

    # --- scrolling --------------------------------------------------------

    def scroll_y(self, dy):
        W, H = self.page_px()
        now = time.time()
        if H <= self.vh + 1:  # whole page visible: wheel flips pages
            if now - self.last_flip > FLIP_COOLDOWN:
                self.last_flip = now
                self.go(self.page + (1 if dy > 0 else -1), at_bottom=dy < 0)
            return
        at_edge = (dy > 0 and self.sy >= H - self.vh - 1) or (dy < 0 and self.sy <= 1)
        if at_edge:
            self.edge_hits += 1
            if self.edge_hits >= EDGE_HITS_TO_FLIP and now - self.last_flip > FLIP_COOLDOWN:
                self.last_flip = now
                self.go(self.page + (1 if dy > 0 else -1), at_bottom=dy < 0)
            return
        self.edge_hits = 0
        self.sy += dy
        self.clamp()
        self.dirty = True

    def scroll_x(self, dx):
        self.sx += dx
        self.clamp()
        self.dirty = True

    # --- input ------------------------------------------------------------

    def handle(self, ev):
        if isinstance(ev, Mouse):
            self.on_mouse(ev)
        elif isinstance(ev, Key):
            self.on_key(ev)

    def on_key(self, k):
        if self.prompt is not None:
            if k.name == "char" and k.char.isdigit():
                self.prompt += k.char
            elif k.name == "backspace":
                self.prompt = self.prompt[:-1]
            elif k.name == "enter":
                text, self.prompt = self.prompt, None
                if text.isdigit() and 1 <= int(text) <= len(self.doc):
                    self.go(int(text) - 1)
                elif text:
                    self.message = self.t["bad_page"]
            elif k.name in ("esc", "ctrl-c"):
                self.prompt = None
            self.dirty = True
            return

        self.message = ""
        step_y = self.ch * 3
        c = k.char if k.name == "char" else ""
        if k.name == "ctrl-c" or c == "q" or k.name == "esc":
            self.running = False
        elif k.name in ("right", "pagedown") or c in ("l", "n", " "):
            self.go(self.page + 1)
        elif k.name in ("left", "pageup") or c in ("h", "p"):
            self.go(self.page - 1)
        elif k.name == "down" or c == "j":
            self.scroll_y(step_y)
        elif k.name == "up" or c == "k":
            self.scroll_y(-step_y)
        elif k.name == "home":
            self.go(0)
        elif k.name == "end":
            self.go(len(self.doc) - 1)
        elif c in ("+", "="):
            self.zoom_at(ZOOM_STEP ** 2)
        elif c in ("-", "_"):
            self.zoom_at(1 / ZOOM_STEP ** 2)
        elif c in ("0", "f"):
            self.set_mode("fit")
        elif c == "w":
            self.set_mode("width")
        elif c == "g":
            self.prompt = ""
            self.dirty = True

    def set_mode(self, mode):
        self.mode = mode
        self.apply_mode()
        self.dirty = True

    def on_mouse(self, m):
        px = (m.x - 1) * self.cw + self.cw / 2
        py = (m.y - 2) * self.ch + self.ch / 2
        in_view = 2 <= m.y <= self.rows - 1

        if m.button in (WHEEL_UP, WHEEL_DOWN, WHEEL_LEFT, WHEEL_RIGHT):
            if not m.pressed:
                return
            up = m.button == WHEEL_UP
            if m.ctrl and m.button in (WHEEL_UP, WHEEL_DOWN):
                self.zoom_at(ZOOM_STEP if up else 1 / ZOOM_STEP, px, py)
            elif m.shift or m.button in (WHEEL_LEFT, WHEEL_RIGHT):
                left = up or m.button == WHEEL_LEFT
                self.scroll_x(-self.cw * 4 if left else self.cw * 4)
            else:
                self.scroll_y(-self.ch * 2 if up else self.ch * 2)
            return

        if m.button == 0 and m.pressed and not m.motion:
            if m.y == 1:
                for x0, x1, action in self.buttons:
                    if x0 <= m.x <= x1:
                        action()
                        self.dirty = True
                        break
                return
            if in_view:
                self.drag = (m.x, m.y, self.sx, self.sy, False)
            return

        if m.motion and self.drag:
            x0, y0, sx0, sy0, _ = self.drag
            self.sx = sx0 - (m.x - x0) * self.cw
            self.sy = sy0 - (m.y - y0) * self.ch
            self.drag = (x0, y0, sx0, sy0, True)
            self.clamp()
            self.dirty = True
            return

        if not m.pressed and self.drag:
            x0, y0, _, _, moved = self.drag
            self.drag = None
            if not moved:
                now = time.time()
                t0, cx, cy = self.last_click
                if now - t0 < DOUBLE_CLICK and (cx, cy) == (m.x, m.y):
                    self.last_click = (0.0, 0, 0)
                    if self.mode == "fit":
                        self.zoom_at(2.0, px, py)
                    else:
                        self.set_mode("fit")
                else:
                    self.last_click = (now, m.x, m.y)

    # --- drawing ----------------------------------------------------------

    def toolbar(self):
        t = self.t
        pct = round(self.zoom * 72 / 96 * 100)  # 100% = 96 dpi on screen
        page = f" {self.page + 1}/{len(self.doc)} "
        prev = (" ◀ ", lambda: self.go(self.page - 1))
        nxt = (" ▶ ", lambda: self.go(self.page + 1))
        out_ = (" − ", lambda: self.zoom_at(1 / ZOOM_STEP ** 2))
        in_ = (" ＋ ", lambda: self.zoom_at(ZOOM_STEP ** 2))
        level = (f" {pct}% ", None)
        fit = (f" {t['fit']} ", lambda: self.set_mode("fit"), "fit")
        wide = (f" {t['width']} ", lambda: self.set_mode("width"), "width")
        quit_long, quit_short = (f" ✕ {t['quit']} ", self.quit), (" ✕ ", self.quit)

        # drop the least important parts until the bar fits the pane width
        layouts = [
            ([prev, nxt, (page, self.start_prompt), out_, level, in_, fit, wide], quit_long),
            ([prev, nxt, (page, self.start_prompt), out_, level, in_, fit, wide], quit_short),
            ([prev, nxt, (page, self.start_prompt), out_, level, in_, fit], quit_short),
            ([prev, nxt, (page, self.start_prompt), out_, in_, fit], quit_short),
            ([prev, nxt, (page, self.start_prompt), out_, in_], quit_short),
        ]
        for items, right in layouts:
            need = sum(width(i[0]) + 1 for i in items) + width(right[0])
            if need <= self.cols:
                break

        self.buttons = []
        out, x = [], 1
        for item in items:
            label, action = item[0], item[1]
            w = width(label)
            if action is None:
                out.append(f"\x1b[0;2m{label}\x1b[0m ")
            else:
                active = len(item) > 2 and item[2] == self.mode
                style = "\x1b[0;30;46m" if active else "\x1b[0;97;100m"
                out.append(f"{style}{label}\x1b[0m ")
                self.buttons.append((x, x + w - 1, action))
            x += w + 1
        rw = width(right[0])
        rx = self.cols - rw + 1
        room = rx - x - 1
        if room > 6:
            out.append(f"\x1b[2m{clip(os.path.basename(self.path), room)}\x1b[0m")
        # place the close button by absolute position so it never wraps
        out.append(f"\x1b[1;{rx}H\x1b[0;97;41m{right[0]}\x1b[0m")
        self.buttons.append((rx, self.cols, right[1]))
        return "\x1b[1;1H\x1b[2K" + "".join(out)

    def status(self):
        if self.prompt is not None:
            text = f"\x1b[1m{self.t['goto']}{self.prompt}\x1b[0m▏"
        elif self.message:
            text = f"\x1b[33m{self.message}\x1b[0m"
        else:
            text = f"\x1b[2m{clip(self.t['hint'], self.cols - 1)}\x1b[0m"
        return f"\x1b[{self.rows};1H\x1b[2K" + text

    def draw(self):
        rgb = self.doc.render_view(
            self.page, self.zoom, int(round(self.sx)), int(round(self.sy)), self.vw, self.vh
        )
        png = encode_png(rgb, self.vw, self.vh)
        old, self.image_id = self.image_id, 3 - self.image_id
        self.term.write(
            self.toolbar().encode()
            + kitty_image(png, self.image_id, 2, 1, self.vc, self.vr)
            + kitty_delete(old)
            + self.status().encode()
        )
        self.dirty = False

    # --- actions ----------------------------------------------------------

    def start_prompt(self):
        self.prompt = ""

    def quit(self):
        self.running = False

    # --- main loop --------------------------------------------------------

    def run(self):
        signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "resized", True))
        while self.running:
            if self.resized:
                self.resized = False
                self.term.write("\x1b[2J")
                self.measure()
                self.apply_mode()
                self.dirty = True
            if self.dirty:
                self.draw()
            for ev in self.term.read_events(timeout=0.1):
                self.handle(ev)
                if not self.running:
                    break


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="pdfterm",
        description="View PDFs in the terminal with mouse controls (kitty graphics protocol).",
    )
    parser.add_argument("file", help="PDF file to open")
    parser.add_argument("-p", "--page", type=int, default=1, help="page to open (1-based)")
    parser.add_argument("--version", action="version", version=f"pdfterm {__version__}")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.file):
        parser.exit(1, f"pdfterm: no such file: {args.file}\n")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(1, "pdfterm: must be run in a terminal\n")

    term = Terminal()
    try:
        viewer = Viewer(args.file, term)
    except Exception as e:  # unreadable or encrypted PDF
        parser.exit(1, f"pdfterm: cannot open {args.file}: {e}\n")
    viewer.page = min(max(args.page - 1, 0), len(viewer.doc) - 1)

    term.enter()
    try:
        viewer.run()
    except KeyboardInterrupt:
        pass
    finally:
        term.leave()


if __name__ == "__main__":
    main()
