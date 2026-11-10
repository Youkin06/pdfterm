"""see: a mouse-friendly viewer for terminals with the kitty graphics protocol."""

import argparse
import base64
import collections
import math
import shutil
import os
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import zlib

from . import __version__
from .render import Image, UnsupportedFile, kitty_delete, kitty_place, kitty_unplace_all, kitty_upload, open_document
from .term import (
    NO_GRAPHICS,
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
SWIPE_WINDOW = 0.1  # seconds of wheel events used to measure the current rate
SWIPE_MIN_EVENTS = 3  # events within the window needed to turn a page
SWIPE_REARM = 0.35  # turning re-arms when the rate falls below this share of its peak
ZOOM_SETTLE = 0.15  # seconds without zoom input before re-rendering sharply
# Upper bound for one uploaded image. Multiplexers such as herdr re-send images to the
# outer terminal as raw RGBA and drop anything over their per-frame budget (~30 MB in
# herdr 0.9), so the image would silently disappear. 4M pixels is ~21 MB as base64 RGBA.
TILE_MAX_PX = 4_000_000
# Each new image costs a full raw transfer through herdr, so render about 1.6 screens'
# worth around the view instead of everything allowed; edge prefetch covers the rest.
TILE_VIEW_FACTOR = 1.6
RETIRE_DELAY = 0.5
# Text selection is drawn with placements of one small translucent image, so moving the
# selection never re-sends pixel data.
HIGHLIGHT_ID = 9999
HIGHLIGHT_W, HIGHLIGHT_H = 2048, 256
HIGHLIGHT_RGBA = (64, 128, 255, 110)
HIGHLIGHT_Z = 1 << 30
MESSAGE_SECONDS = 2.5  # seconds an old image stays under the new one

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


def merge_lines(rects):
    """Join character rectangles that sit on the same line into one rectangle per line."""
    lines = []
    for l, b, r, t in sorted(rects, key=lambda q: (-q[3], q[0])):
        for i, (L, B, R, T) in enumerate(lines):
            overlap = min(t, T) - max(b, B)
            if overlap > 0.5 * min(t - b, T - B):
                lines[i] = (min(l, L), min(b, B), max(r, R), max(t, T))
                break
        else:
            lines.append((l, b, r, t))
    return lines


def copy_to_clipboard(text, term):
    """Copy via pbcopy on a local Mac, otherwise ask the terminal (OSC 52)."""
    if sys.platform == "darwin" and shutil.which("pbcopy") and not os.environ.get("SSH_TTY"):
        try:
            subprocess.run(["pbcopy"], input=text.encode(), timeout=2, check=True)
            return
        except Exception:
            pass
    data = base64.standard_b64encode(text.encode()).decode()
    term.write(f"\x1b]52;c;{data}\x07")


def highlight_image():
    pixel = bytes(HIGHLIGHT_RGBA)
    data = zlib.compress(pixel * (HIGHLIGHT_W * HIGHLIGHT_H), 9)
    return Image(data, HIGHLIGHT_W, HIGHLIGHT_H, 4)


class Tile:
    """A rendered region of one page, uploaded to the terminal as an image."""

    ox = oy = 0  # where the tile starts inside its image (animation frames share one)

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
        self.cache = {}  # req -> (Image, w, h), oldest first

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
        self.doc = open_document(path)
        self.page = 0
        self.mode = "fit"  # "fit", "width" or "custom"
        self.zoom = 1.0
        self.sx = self.sy = 0.0  # viewport top-left in page pixels at self.zoom
        self.buttons = []  # (x0, x1, action) on the toolbar row
        self.message = ""
        self.message_until = 0.0
        # left drag: "select" text, or "hand" to move the page (pictures have no text)
        self.tool = "select" if self.doc.has_text else "hand"
        self.drag = None  # (px, py, sx, sy, moved) while moving the page
        self.selecting = None  # (px, py, anchor_char, moved) while selecting text
        self.sel = None  # (page, first_char, last_char)
        self.highlight = b""
        self.highlight_sent = False  # the translucent image is uploaded on first use
        self.last_click = (0.0, 0, 0)
        self.clock = time.time
        self.wheel_window = collections.deque()  # (time, button) of recent wheel events
        self.swipe_armed = True  # may the next burst of wheel events turn a page?
        self.swipe_peak = 0  # highest event rate since the last page turn
        self.swipe_valley = 0  # lowest event rate since re-arming
        self.last_zoom = 0.0
        self.tile = None  # what the terminal currently shows
        self.retired = []  # (delete_at, image_id): old images kept briefly to avoid flashes
        self.z = 0
        self.pending = None  # request being rendered
        self.next_id = 1
        self.largest_tile = 0  # pixels; reported by SEE_DEBUG
        self.renderer = Renderer(self.doc)
        self.chrome = ""  # last toolbar + status output, to skip redundant writes
        self.placed = b""
        self.dirty = True
        self.resized = True
        self.running = True
        self.anim = None  # plays animated images in place of the tile renderer
        if getattr(self.doc, "animated", False):
            from .anim import Animator

            self.anim = Animator(self)

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
        self.sel = None
        self.apply_mode()
        W, H = self.page_px()
        self.sy = H - self.vh if at_bottom else 0
        self.clamp()
        self.dirty = True

    def set_mode(self, mode):
        self.mode = mode
        self.apply_mode()
        self.dirty = True

    # --- scrolling --------------------------------------------------------

    def scroll_y(self, dy):
        """Scroll vertically (keyboard); at the page edge or when the page fits, turn it."""
        W, H = self.page_px()
        at_edge = (dy > 0 and self.sy >= H - self.vh - 1) or (dy < 0 and self.sy <= 1)
        if H <= self.vh + 1 or at_edge:
            self.go(self.page + (1 if dy > 0 else -1), at_bottom=dy < 0)
            return
        self.sy += dy
        self.clamp()
        self.dirty = True

    def trace(self, text):
        """Developer aid: with SEE_TRACE=file, log input decisions there."""
        path = os.environ.get("SEE_TRACE")
        if path:
            with open(path, "a") as f:
                f.write(f"{time.time():.3f} {text}\n")

    def on_wheel(self, m, px, py):
        """Wheel and trackpad scrolling: scroll a zoomed page, turn pages with swipes.

        A trackpad sends 100-250 events a second, and one swipe overlaps the momentum of
        the previous one, so pauses cannot separate swipes. Instead each swipe is a burst:
        the event rate rises, peaks and falls. A page turns when the rate rises sharply in
        one direction (and the page cannot scroll further that way); after that, turning is
        disarmed until the rate falls well below its peak. Momentum only ever slows down,
        so it cannot turn a second page, while a new swipe rises again and turns the next.
        The direction is the one most events in the last moment point to, so the small
        vertical drift of a sideways swipe (or the reverse) does not matter.
        """
        if m.ctrl and m.button in (WHEEL_UP, WHEEL_DOWN):
            self.zoom_at(ZOOM_STEP if m.button == WHEEL_UP else 1 / ZOOM_STEP, px, py)
            return
        button = m.button
        if m.shift and button in (WHEEL_UP, WHEEL_DOWN):  # Shift+wheel scrolls sideways
            button = WHEEL_LEFT if button == WHEEL_UP else WHEEL_RIGHT
        now = self.clock()
        window = self.wheel_window
        window.append((now, button))
        while window and window[0][0] < now - SWIPE_WINDOW:
            window.popleft()
        rate = len(window)
        counts = {}
        for _, b in window:
            counts[b] = counts.get(b, 0) + 1
        main = max(counts, key=counts.get)
        vertical = main in (WHEEL_UP, WHEEL_DOWN)
        across = sum(n for b, n in counts.items() if (b in (WHEEL_UP, WHEEL_DOWN)) != vertical)

        # re-arm once the burst has died down; track the lowest rate since then
        if not self.swipe_armed and rate <= max(1, SWIPE_REARM * self.swipe_peak):
            self.swipe_armed, self.swipe_valley = True, rate
        if self.swipe_armed:
            self.swipe_valley = min(self.swipe_valley, rate)
        else:
            self.swipe_peak = max(self.swipe_peak, rate)

        # scroll along the swipe's main axis when the page is larger than the view
        if (button in (WHEEL_UP, WHEEL_DOWN)) == vertical:
            step = 1 if button in (WHEEL_DOWN, WHEEL_RIGHT) else -1
            if vertical:
                self.sy += step * self.ch
            else:
                self.sx += step * self.cw
            self.clamp()
            self.dirty = True

        forward = main in (WHEEL_DOWN, WHEEL_RIGHT)
        W, H = self.page_px()
        if vertical:
            can_scroll = (forward and self.sy < H - self.vh - 1) or (not forward and self.sy > 1)
        else:
            can_scroll = (forward and self.sx < W - self.vw - 1) or (not forward and self.sx > 1)
        rising = rate >= SWIPE_MIN_EVENTS and rate >= 2 * self.swipe_valley + SWIPE_MIN_EVENTS
        straight = counts[main] >= 2 * across
        self.trace(
            f"b={button} rate={rate} main={main} armed={self.swipe_armed} "
            f"valley={self.swipe_valley} peak={self.swipe_peak} scroll={can_scroll}"
        )
        if self.swipe_armed and rising and straight and not can_scroll:
            self.swipe_armed, self.swipe_peak = False, rate
            target = self.page + (1 if forward else -1)
            if 0 <= target < len(self.doc):
                self.trace(f"  turn -> page {target + 1}")
                self.go(target, at_bottom=vertical and not forward)

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
        if self.anim and self.anim.on_key(k):
            return
        step_y = self.ch * 3
        c = k.char if k.name == "char" else ""
        alt = k.char if k.name == "alt" else ""
        if k.name == "esc" and self.sel:
            self.set_selection(None)
        elif k.name == "ctrl-c" or c == "q" or k.name == "esc":
            self.running = False
        elif alt == "c":  # Command+C via the Ghostty mapping
            self.copy_selection()
        elif alt == "a":  # Command+A: whole page
            n = self.doc.char_count(self.page)
            if n:
                self.set_selection((self.page, 0, n - 1))
                self.copy_selection()
        elif c == "v":
            self.toggle_tool()
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

    def on_mouse(self, m):
        px, py = m.px, m.py - self.ch  # pointer in viewport pixels
        in_view = 2 <= m.y <= self.rows - 1

        if m.button in (WHEEL_UP, WHEEL_DOWN, WHEEL_LEFT, WHEEL_RIGHT):
            if m.pressed:
                self.on_wheel(m, px, py)
            return

        if m.pressed and not m.motion and m.button in (0, 1, 2):
            if m.y == 1:
                if m.button == 0:
                    for x0, x1, action in self.buttons:
                        if x0 <= m.x <= x1:
                            action()
                            self.dirty = True
                            break
                return
            if not in_view:
                return
            # like Preview: a press on text selects it, a press on blank paper moves the page
            anchor = self.char_at(px, py) if m.button == 0 and self.tool == "select" else None
            if anchor is not None:
                self.selecting = (px, py, anchor, False)
            else:  # blank paper, hand tool, or middle/right drag in either tool
                self.drag = (m.px, m.py, self.sx, self.sy, False)
            return

        if m.motion and self.selecting:
            x0, y0, anchor, moved = self.selecting
            moved = moved or abs(px - x0) + abs(py - y0) > self.cw / 2
            here = self.char_at(px, py, loose=True)
            if anchor is None:
                anchor = here
            self.selecting = (x0, y0, anchor, moved)
            if moved and anchor is not None and here is not None:
                self.set_selection((self.page, min(anchor, here), max(anchor, here)))
            return

        if m.motion and self.drag:
            x0, y0, sx0, sy0, moved = self.drag
            dx, dy = m.px - x0, m.py - y0
            self.sx, self.sy = sx0 - dx, sy0 - dy
            self.drag = (x0, y0, sx0, sy0, moved or abs(dx) + abs(dy) > self.cw / 2)
            self.clamp()
            self.dirty = True
            return

        if not m.pressed and self.selecting:
            moved = self.selecting[3]
            self.selecting = None
            if moved:
                self.copy_selection()
                return
            if self.is_double_click(m):
                self.toggle_zoom(px, py)
            else:
                self.set_selection(None)
            return

        if not m.pressed and self.drag:
            moved = self.drag[4]
            self.drag = None
            if not moved:
                double = self.is_double_click(m)
                if self.anim:
                    self.anim.click(px, py, double)
                if double:
                    self.toggle_zoom(px, py)

    def toggle_zoom(self, px, py):
        """Double-click: zoom in 2x at the pointer, or back to the whole page."""
        if self.mode == "fit":
            self.zoom_at(2.0, px, py)
        else:
            self.set_mode("fit")

    def is_double_click(self, m):
        now = time.time()
        t0, cx, cy = self.last_click
        if now - t0 < DOUBLE_CLICK and abs(cx - m.x) <= 1 and abs(cy - m.y) <= 1:
            self.last_click = (0.0, 0, 0)
            return True
        self.last_click = (now, m.x, m.y)
        return False

    # --- text selection ---------------------------------------------------

    def to_pdf(self, px, py):
        """Viewport pixel -> PDF point (origin bottom-left)."""
        ph = self.doc.page_size(self.page)[1]
        return (self.sx + px) / self.zoom, ph - (self.sy + py) / self.zoom

    def char_at(self, px, py, loose=False):
        x, y = self.to_pdf(px, py)
        tol = (12 if loose else 3) * 72 / 96 * max(1.0, 1 / max(self.zoom, 1e-6))
        return self.doc.char_at(self.page, x, y, tol)

    def set_selection(self, sel):
        if sel != self.sel:
            self.sel = sel
            self.dirty = True

    def copy_selection(self):
        if not self.sel:
            return
        page, a, b = self.sel
        text = self.doc.text(page, a, b)
        if not text.strip():
            return
        copy_to_clipboard(text, self.term)
        self.flash(f"Copied {len(text)} characters")

    def flash(self, text):
        self.message = text
        self.message_until = time.time() + MESSAGE_SECONDS
        self.dirty = True

    def toggle_tool(self):
        self.tool = "hand" if self.tool == "select" else "select"
        self.dirty = True

    def draw_highlight(self):
        """Cover the selected text with stretched-free pieces of the translucent image."""
        out = []
        if self.sel and self.sel[0] == self.page:
            ph = self.doc.page_size(self.page)[1]
            z = self.zoom
            for l, b, r, t in merge_lines(self.doc.text_rects(*self.sel)):
                x0, y0 = l * z - self.sx, (ph - t) * z - self.sy
                x1, y1 = r * z - self.sx, (ph - b) * z - self.sy
                x0, y0 = max(0.0, x0), max(0.0, y0)
                x1, y1 = min(float(self.vw), x1), min(float(self.vh), y1)
                y = y0
                while y < y1 - 0.5:  # split pieces larger than the highlight image
                    h = min(HIGHLIGHT_H, y1 - y)
                    x = x0
                    while x < x1 - 0.5:
                        w = min(HIGHLIGHT_W, x1 - x)
                        col, row = int(x // self.cw), int(y // self.ch)
                        off = (int(x - col * self.cw), int(y - row * self.ch))
                        out.append((row + 2, col + 1, (0, 0, w, h), off))
                        x += w
                    y += h
        seq = b"".join(
            kitty_place(HIGHLIGHT_ID, r, c, src, off, None, HIGHLIGHT_Z, placement=i + 1)
            for i, (r, c, src, off) in enumerate(out)
        )
        if seq != self.highlight:
            if seq and not self.highlight_sent:
                self.term.write(kitty_upload(highlight_image(), HIGHLIGHT_ID))
                self.highlight_sent = True
            self.term.write(kitty_unplace_all(HIGHLIGHT_ID) + seq)
            self.highlight = seq

    # --- tiles ------------------------------------------------------------

    def tile_zoom(self):
        """Render zoom for tiles: the view zoom, lowered if even the viewport is too big."""
        scale = min(1.0, math.sqrt(TILE_MAX_PX * 0.9 / (self.vw * self.vh)))
        return self.zoom * scale

    def tile_budget(self, tz):
        """Most pixels one tile at zoom `tz` should have."""
        k = tz / self.zoom
        return min(TILE_MAX_PX, TILE_VIEW_FACTOR * self.vw * self.vh * k * k)

    def wanted_tile(self, tz):
        """The page region worth rendering at zoom `tz`, within the tile budget."""
        W, H = self.page_px(tz)
        budget = self.tile_budget(tz)
        if W * H <= budget:
            return 0, 0, int(W), int(H)  # small enough: render the whole page
        # otherwise the view plus as much margin as the pixel budget allows
        k = tz / self.zoom
        vx, vy, vw, vh = self.sx * k, self.sy * k, self.vw * k, self.vh * k
        b = vw + vh
        margin = max(0.0, (-b + math.sqrt(max(0.0, b * b - 4 * (vw * vh - budget)))) / 4)
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
        # trigger at half of the margin the tile was rendered with; a fresh tile centred on
        # the view must not count as "near the edge", or it would re-render forever
        pad_x = max(0.0, t.w - self.vw * k) / 4
        pad_y = max(0.0, t.h - self.vh * k) / 4
        vx0, vy0 = self.sx * k, self.sy * k
        vx1, vy1 = vx0 + self.vw * k, vy0 + self.vh * k
        return (
            (t.x > 0 and vx0 - t.x < pad_x)
            or (t.y > 0 and vy0 - t.y < pad_y)
            or (t.x + t.w < W - 1 and t.x + t.w - vx1 < pad_x)
            or (t.y + t.h < H - 1 and t.y + t.h - vy1 < pad_y)
        )

    def request_tile(self):
        if self.anim:
            return self.anim.request()
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
                if W * H <= self.tile_budget(z):
                    out.append((p, z, 0, 0, int(W), int(H)))
        return out

    def receive_tile(self):
        if self.anim:
            return self.anim.tick()
        res = self.renderer.take()
        if res is None:
            return
        req, image, w, h = res
        if req != self.pending:
            return  # superseded by a newer request
        self.pending = None
        old = self.tile
        image_id = self.next_id
        self.next_id = self.next_id % 1000 + 1
        self.z += 1
        self.tile = Tile(image_id, req[0], req[1], req[2], req[3], w, h, self.z)
        self.largest_tile = max(self.largest_tile, w * h)
        self.term.write(kitty_upload(image, image_id))
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
            t.image_id, row + 2, col + 1, (sx0 + t.ox, sy0 + t.oy, sx1 - sx0, sy1 - sy0),
            offset, cells, t.z,
        )
        if seq != self.placed:
            self.term.write(seq)
            self.placed = seq
        return True

    def toolbar(self):
        pct = round(self.zoom * 72 / 96 * 100)  # 100% = 96 dpi on screen
        page = (f" {self.page + 1}/{len(self.doc)} ", None) if len(self.doc) > 1 else None
        level = (f" {pct}% ", None)
        fit = (" Fit ", lambda: self.set_mode("fit"), "fit")
        wide = (" Width ", lambda: self.set_mode("width"), "width")
        close = " ✕ "

        name = os.path.basename(self.path)
        # the file name comes first; drop the least important parts until the rest fits
        # next to at least a recognisable piece of it
        name_min = min(width(name), 12)
        layouts = [
            [page, level, fit, wide],
            [page, level, fit],
            [page, fit],
            [page],
            [],
        ]
        layouts = [[i for i in items if i] for items in layouts]
        for items in layouts:
            rest = sum(width(i[0]) + 1 for i in items) + width(close)
            if 1 + name_min + 2 + rest <= self.cols:
                break
        room = max(0, self.cols - rest - 3)
        title = name if width(name) <= room else clip(name, max(0, room - 1)) + "…"

        self.buttons = []
        out = [f"\x1b[0;1m {title}\x1b[0m  "]
        x = 1 + 1 + width(title) + 2
        for item in items:
            label, action = item[0], item[1]
            w = width(label)
            if action is None:
                out.append(f"\x1b[0;2m{label}\x1b[0m ")
            else:
                # no backgrounds: the current mode is bold and underlined
                active = len(item) > 2 and item[2] == self.mode
                style = "\x1b[0;1;4m" if active else "\x1b[0m"
                out.append(f"{style}{label}\x1b[0m ")
                self.buttons.append((x, x + w - 1, action))
            x += w + 1
        cx = self.cols - width(close) + 1
        # place the close button by absolute position so it never wraps
        out.append(f"\x1b[1;{cx}H\x1b[0m{close}")
        self.buttons.append((cx, self.cols, self.quit))
        return "\x1b[1;1H\x1b[2K" + "".join(out)

    def status(self):
        if self.message and time.time() < self.message_until:
            text = f"\x1b[33m{clip(self.message, self.cols - 1)}\x1b[0m"
        else:
            hint = self.anim.hint() if self.anim else "Double-click: zoom   q: quit"
            text = f"\x1b[2m{clip(hint, self.cols - 1)}\x1b[0m"
        return f"\x1b[{self.rows};1H\x1b[2K" + text

    def draw(self):
        chrome = self.toolbar() + self.status()
        if chrome != self.chrome:
            self.term.write(chrome)
            self.chrome = chrome
        self.request_tile()
        self.place()
        self.draw_highlight()
        self.dirty = False

    # --- actions ----------------------------------------------------------

    def quit(self):
        self.running = False

    # --- main loop --------------------------------------------------------

    def run(self):
        signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "resized", True))
        self.renderer.start()
        while self.running:
            if self.resized:
                self.resized = False
                # Clear only the text, line by line: erasing the whole display (ED 2) can
                # also delete kitty images in Ghostty-based terminals and multiplexers.
                rows = self.term.size()[0]
                self.term.write("".join(f"\x1b[{r};1H\x1b[2K" for r in range(1, rows + 1)))
                self.measure()
                self.apply_mode()
                self.chrome, self.placed, self.highlight = "", b"", b""
                self.dirty = True
            self.receive_tile()
            self.drop_retired()
            if self.message and time.time() >= self.message_until:
                self.message = ""
                self.dirty = True
            if self.dirty:
                self.draw()
            else:
                self.request_tile()  # picks up a finished zoom gesture
            busy = (
                self.pending is not None
                or bool(self.retired)
                or time.time() - self.last_zoom < ZOOM_SETTLE * 2
            )
            timeout = 0.01 if busy else 0.1
            if self.anim:
                timeout = self.anim.timeout(timeout)
            for ev in self.term.read_events(timeout=timeout):
                self.trace(f"event {ev}")
                self.handle(ev)
                if not self.running:
                    break


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="see",
        description="View PDFs and images in the terminal with mouse controls (kitty graphics protocol).",
    )
    parser.add_argument("file", help="PDF, image or video file to open")
    parser.add_argument("-p", "--page", type=int, default=1, help="page to open (1-based)")
    parser.add_argument("--version", action="version", version=f"see {__version__}")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.file):
        parser.exit(1, f"see: no such file: {args.file}\n")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(1, "see: must be run in a terminal\n")

    # Videos have their own player (see video.py).
    from .video import is_video, play

    if is_video(args.file):
        return play(args.file)

    term = Terminal()
    try:
        viewer = Viewer(args.file, term)
    except UnsupportedFile:
        parser.exit(1, (
            f"see: {os.path.basename(args.file)} isn't a file see can show.\n"
            "     It opens PDFs, images, animated GIFs, videos, documents and text.\n"
        ))
    except Exception as e:  # unreadable or encrypted PDF, broken file
        parser.exit(1, f"see: cannot open {args.file}: {e}\n")
    viewer.page = min(max(args.page - 1, 0), len(viewer.doc) - 1)

    term.enter()
    if not term.graphics:
        term.leave()
        parser.exit(1, NO_GRAPHICS)
    try:
        viewer.run()
    except KeyboardInterrupt:
        pass
    finally:
        term.leave()
        log = os.environ.get("SEE_DEBUG")
        if log:  # developer aid: how the terminal was detected and how much was sent
            with open(log, "a") as f:
                f.write(
                    f"cell={term.cell} pixel_mouse={term.pixel_mouse} "
                    f"rows={viewer.rows} cols={viewer.cols} bytes={term.bytes_written} "
                    f"largest_tile={viewer.largest_tile}\n"
                )


if __name__ == "__main__":
    main()
