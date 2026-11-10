"""pdfterm: a mouse-friendly PDF viewer for terminals with the kitty graphics protocol."""

import argparse
import math
import os
import signal
import subprocess
import sys
import threading
import time
import unicodedata

from . import __version__
from .render import Document, kitty_delete, kitty_place, kitty_upload
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
ZOOM_SETTLE = 0.15  # seconds without zoom input before re-rendering sharply
# Upper bound for one uploaded image. Multiplexers such as herdr re-send images to the
# outer terminal as raw RGBA and drop anything over their per-frame budget (~30 MB in
# herdr 0.9), so the image would silently disappear. 4M pixels is ~21 MB as base64 RGBA.
TILE_MAX_PX = 4_000_000
RETIRE_DELAY = 0.5  # seconds an old image stays under the new one

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


class Tile:
    """A rendered region of one page, uploaded to the terminal as an image."""

    def __init__(self, image_id, page, zoom, x, y, w, h, z=0):
        self.image_id, self.page, self.zoom, self.z = image_id, page, zoom, z
        self.x, self.y, self.w, self.h = x, y, w, h  # page pixels at self.zoom


class Renderer(threading.Thread):
    """Renders tiles in the background so input handling never waits for PDFium.

    Finished tiles are kept in a small cache, and while idle the renderer prepares the
    pages next to the current one so turning a page only has to send the image.
    """

    CACHE_SIZE = 8

    def __init__(self, doc):
        super().__init__(daemon=True)
        self.doc = doc
        self.cond = threading.Condition()
        self.request = None
        self.result = None
        self.prefetch = []  # requests to render while idle
        self.cache = {}  # req -> (png, w, h), oldest first

    def submit(self, req, prefetch=()):
        with self.cond:
            self.prefetch = [r for r in prefetch if r not in self.cache]
            hit = self.cache.get(req)
            if hit is not None:
                self.request = None
                self.result = (req,) + hit
            else:
                self.request = req
            self.cond.notify()

    def take(self):
        with self.cond:
            res, self.result = self.result, None
            return res

    def _store(self, req, value):
        self.cache.pop(req, None)
        self.cache[req] = value
        while len(self.cache) > self.CACHE_SIZE:
            self.cache.pop(next(iter(self.cache)))

    def run(self):
        while True:
            with self.cond:
                while self.request is None and not self.prefetch:
                    self.cond.wait()
                if self.request is not None:
                    req, wanted = self.request, True
                    self.request = None
                else:
                    req, wanted = self.prefetch.pop(0), False
            try:
                value = self.doc.render_tile(*req)
            except Exception:
                continue
            with self.cond:
                self._store(req, value)
                if wanted:
                    self.result = (req,) + value


class Viewer:
    def __init__(self, path, term):
        self.path = path
        self.term = term
        self.doc = Document(path)
        self.t = TEXT[_lang()]
        self.page = 0
        self.mode = "fit"  # "fit", "width" or "custom"
        self.zoom = 1.0
        self.sx = self.sy = 0.0  # viewport top-left in page pixels at self.zoom
        self.buttons = []  # (x0, x1, action) on the toolbar row
        self.prompt = None  # text typed for "go to page", or None
        self.message = ""
        self.drag = None  # (px, py, sx, sy, moved)
        self.last_click = (0.0, 0, 0)
        self.last_flip = 0.0
        self.last_zoom = 0.0
        self.edge_hits = 0
        self.tile = None  # what the terminal currently shows
        self.retired = []  # (delete_at, image_id): old images kept briefly to avoid flashes
        self.z = 0
        self.pending = None  # request being rendered
        self.next_id = 1
        self.largest_tile = 0  # pixels; reported by PDFTERM_DEBUG
        self.renderer = Renderer(self.doc)
        self.chrome = ""  # last toolbar + status output, to skip redundant writes
        self.placed = b""
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

    def page_px(self, zoom=None):
        pw, ph = self.doc.page_size(self.page)
        z = self.zoom if zoom is None else zoom
        return pw * z, ph * z

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
        ux, uy = (self.sx + px) / self.zoom, (self.sy + py) / self.zoom
        self.zoom = new
        self.sx, self.sy = ux * new - px, uy * new - py
        self.mode = "fit" if abs(new - fit) < 1e-6 else "custom"
        self.last_zoom = time.time()
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

    def set_mode(self, mode):
        self.mode = mode
        self.apply_mode()
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

    def on_mouse(self, m):
        px, py = m.px, m.py - self.ch  # pointer in viewport pixels
        in_view = 2 <= m.y <= self.rows - 1

        if m.button in (WHEEL_UP, WHEEL_DOWN, WHEEL_LEFT, WHEEL_RIGHT):
            if not m.pressed:
                return
            up = m.button == WHEEL_UP
            if m.ctrl and m.button in (WHEEL_UP, WHEEL_DOWN):
                self.zoom_at(ZOOM_STEP if up else 1 / ZOOM_STEP, px, py)
            elif m.shift or m.button in (WHEEL_LEFT, WHEEL_RIGHT):
                left = up or m.button == WHEEL_LEFT
                self.scroll_x(-self.cw * 3 if left else self.cw * 3)
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
                self.drag = (m.px, m.py, self.sx, self.sy, False)
            return

        if m.motion and self.drag:
            x0, y0, sx0, sy0, moved = self.drag
            dx, dy = m.px - x0, m.py - y0
            self.sx, self.sy = sx0 - dx, sy0 - dy
            self.drag = (x0, y0, sx0, sy0, moved or abs(dx) + abs(dy) > self.cw / 2)
            self.clamp()
            self.dirty = True
            return

        if not m.pressed and self.drag:
            moved = self.drag[4]
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

    # --- tiles ------------------------------------------------------------

    def tile_zoom(self):
        """Render zoom for tiles: the view zoom, lowered if even the viewport is too big."""
        scale = min(1.0, math.sqrt(TILE_MAX_PX * 0.9 / (self.vw * self.vh)))
        return self.zoom * scale

    def wanted_tile(self, tz):
        """The page region worth rendering at zoom `tz`, within TILE_MAX_PX pixels."""
        W, H = self.page_px(tz)
        if W * H <= TILE_MAX_PX:
            return 0, 0, int(W), int(H)  # small enough: render the whole page
        # otherwise the view plus as much margin as the pixel budget allows
        k = tz / self.zoom
        vx, vy, vw, vh = self.sx * k, self.sy * k, self.vw * k, self.vh * k
        b = vw + vh
        margin = max(0.0, (-b + math.sqrt(b * b - 4 * (vw * vh - TILE_MAX_PX))) / 4)
        x0, y0 = max(0, int(vx - margin)), max(0, int(vy - margin))
        x1, y1 = min(int(W), int(vx + vw + margin)), min(int(H), int(vy + vh + margin))
        return x0, y0, x1 - x0, y1 - y0

    def tile_ok(self):
        t = self.tile
        tz = self.tile_zoom()
        if t is None or t.page != self.page or abs(t.zoom - tz) > 1e-6 * tz:
            return False
        k = tz / self.zoom
        W, H = self.page_px()
        vx0, vy0 = max(self.sx, 0) * k, max(self.sy, 0) * k
        vx1, vy1 = min(self.sx + self.vw, W) * k, min(self.sy + self.vh, H) * k
        return t.x <= vx0 + 1 and t.y <= vy0 + 1 and vx1 - 1 <= t.x + t.w and vy1 - 1 <= t.y + t.h

    def near_tile_edge(self):
        """True when the view is close to an edge of a partial tile, so the next tile
        should be rendered before the empty area comes into view."""
        t = self.tile
        k = t.zoom / self.zoom
        W, H = self.page_px(t.zoom)
        pad_x, pad_y = self.vw * k / 4, self.vh * k / 4
        vx0, vy0 = self.sx * k, self.sy * k
        vx1, vy1 = vx0 + self.vw * k, vy0 + self.vh * k
        return (
            (t.x > 0 and vx0 - t.x < pad_x)
            or (t.y > 0 and vy0 - t.y < pad_y)
            or (t.x + t.w < W - 1 and t.x + t.w - vx1 < pad_x)
            or (t.y + t.h < H - 1 and t.y + t.h - vy1 < pad_y)
        )

    def request_tile(self):
        if self.tile_ok() and not self.near_tile_edge():
            return
        settling = time.time() - self.last_zoom < ZOOM_SETTLE
        same_page = self.tile is not None and self.tile.page == self.page
        if settling and same_page:
            return  # keep stretching the old tile while the wheel is still turning
        if self.pending is not None and self.tile_ok():
            return  # an edge prefetch is already on its way
        tz = self.tile_zoom()
        req = (self.page, tz) + self.wanted_tile(tz)
        if req != self.pending:
            self.pending = req
            self.renderer.submit(req, self.neighbours(tz))

    def neighbours(self, tz):
        """Whole-page requests for the pages around the current one, for prerendering."""
        out = []
        for p in (self.page + 1, self.page - 1, self.page + 2):
            if 0 <= p < len(self.doc):
                pw, ph = self.doc.page_size(p)
                if self.mode == "fit":
                    z = min(self.vw / pw, self.vh / ph)
                elif self.mode == "width":
                    z = self.vw / pw
                else:
                    z = self.zoom
                z *= tz / self.zoom
                W, H = pw * z, ph * z
                if W * H <= TILE_MAX_PX:
                    out.append((p, z, 0, 0, int(W), int(H)))
        return out

    def receive_tile(self):
        res = self.renderer.take()
        if res is None:
            return
        req, png, w, h = res
        if req != self.pending:
            return  # superseded by a newer request
        self.pending = None
        old = self.tile
        image_id = self.next_id
        self.next_id = self.next_id % 1000 + 1
        self.z += 1
        self.tile = Tile(image_id, req[0], req[1], req[2], req[3], w, h, self.z)
        self.largest_tile = max(self.largest_tile, w * h)
        self.term.write(kitty_upload(png, image_id))
        self.placed = b""
        if self.place():
            if old is not None:
                # The multiplexer may show the new image a frame later; deleting the old one
                # right away would flash an empty screen. The new one is stacked on top.
                self.retired.append((time.time() + RETIRE_DELAY, old.image_id))
        else:
            # the view moved off the new tile while it was rendering: keep the old one
            self.term.write(kitty_delete(image_id))
            self.tile = old
            self.placed = b""
            self.place()

    def drop_retired(self):
        now = time.time()
        keep = []
        for at, image_id in self.retired:
            if at <= now:
                self.term.write(kitty_delete(image_id))
            else:
                keep.append((at, image_id))
        self.retired = keep

    def place(self):
        """Show the current view from the uploaded tile (stretched if zoom differs).

        Returns False when the tile has nothing to show for the current view.
        """
        t = self.tile
        if t is None or t.page != self.page:
            return False
        W, H = self.page_px()
        # visible part of the page, in viewport pixels
        dx0, dy0 = max(0.0, -self.sx), max(0.0, -self.sy)
        dx1, dy1 = min(float(self.vw), W - self.sx), min(float(self.vh), H - self.sy)
        if dx1 <= dx0 or dy1 <= dy0:
            return False
        r = t.zoom / self.zoom  # tile pixels per view pixel
        sx0 = (self.sx + dx0) * r - t.x
        sy0 = (self.sy + dy0) * r - t.y
        sx1 = (self.sx + dx1) * r - t.x
        sy1 = (self.sy + dy1) * r - t.y
        # clip the source to what the tile holds, moving the destination along with it
        if sx0 < 0:
            dx0 -= sx0 / r
            sx0 = 0
        if sy0 < 0:
            dy0 -= sy0 / r
            sy0 = 0
        if sx1 > t.w:
            dx1 -= (sx1 - t.w) / r
            sx1 = t.w
        if sy1 > t.h:
            dy1 -= (sy1 - t.h) / r
            sy1 = t.h
        if dx1 <= dx0 or dy1 <= dy0:
            return False
        col, row = int(dx0 // self.cw), int(dy0 // self.ch)
        offset = (int(dx0 - col * self.cw), int(dy0 - row * self.ch))
        cells = None
        if abs(r - 1) > 1e-6:  # different zoom: stretch over the destination cells
            cells = (
                max(1, round((dx1 - col * self.cw) / self.cw)),
                max(1, round((dy1 - row * self.ch) / self.ch)),
            )
        seq = kitty_place(
            t.image_id, row + 2, col + 1, (sx0, sy0, sx1 - sx0, sy1 - sy0), offset, cells, t.z
        )
        if seq != self.placed:
            self.term.write(seq)
            self.placed = seq
        return True

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
        chrome = self.toolbar() + self.status()
        if chrome != self.chrome:
            self.term.write(chrome)
            self.chrome = chrome
        self.request_tile()
        self.place()
        self.dirty = False

    # --- actions ----------------------------------------------------------

    def start_prompt(self):
        self.prompt = ""

    def quit(self):
        self.running = False

    # --- main loop --------------------------------------------------------

    def run(self):
        signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "resized", True))
        self.renderer.start()
        while self.running:
            if self.resized:
                self.resized = False
                self.term.write("\x1b[2J")
                self.measure()
                self.apply_mode()
                self.chrome, self.placed = "", b""
                self.dirty = True
            self.receive_tile()
            self.drop_retired()
            if self.dirty:
                self.draw()
            else:
                self.request_tile()  # picks up a finished zoom gesture
            busy = (
                self.pending is not None
                or bool(self.retired)
                or time.time() - self.last_zoom < ZOOM_SETTLE * 2
            )
            for ev in self.term.read_events(timeout=0.01 if busy else 0.1):
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
        log = os.environ.get("PDFTERM_DEBUG")
        if log:  # developer aid: how the terminal was detected and how much was sent
            with open(log, "a") as f:
                f.write(
                    f"cell={term.cell} pixel_mouse={term.pixel_mouse} "
                    f"rows={viewer.rows} cols={viewer.cols} bytes={term.bytes_written} "
                    f"largest_tile={viewer.largest_tile}\n"
                )


if __name__ == "__main__":
    main()
