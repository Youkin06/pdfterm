"""Playback of animated images (GIF, APNG, animated WebP).

Every frame is rendered once at the current zoom and uploaded once, packed into a few
large "atlas" images. Playing is then only a matter of re-placing the visible picture
with a different source rectangle (or switching to another atlas): a few bytes per frame
instead of the pixels.

Atlases rather than one image per frame because multiplexers keep only a few images that
are not on screen: herdr 0.9 holds at most 16 of them and 64 MB of decoded pixels, and
re-sends anything it dropped as raw RGBA. A handful of atlases stays within that, so after
the first loop nothing is sent again. Animations too big for that budget fall back to
rendering and uploading each frame as it is shown.
"""

import math
import threading
import time

from PIL import Image as PILImage

from . import app as appmod  # Tile and timing constants (app imports this module lazily)
from .render import IMAGE_PT_PER_PX, Image, ImageDocument, kitty_delete, kitty_unplace_all, kitty_upload

ATLAS_PX = 4_000_000  # pixels in one atlas, the same cap as for a single tile upload
ATLAS_SIDE = 8192  # longest side of an atlas
# Pixels of all frames together, for one zoom level. Kept under the 64 MB (as RGBA) that
# herdr retains for images that are not currently shown, so it never has to evict one.
FRAMES_BUDGET_PX = 14_000_000
DECODED_CACHE_BYTES = 256 * 1024 * 1024  # keep decoded frames in memory up to this size
DEFAULT_DURATION = 0.1  # seconds, for frames that ask for 0-10 ms (as browsers do)
FIRST_SHOW_DELAY = 0.1  # keep the previous picture this long under one never shown before
ID_BASE, ID_COUNT = 20000, 10000  # image ids used for frames, apart from the viewer's


def open_animated(path):
    """An AnimatedImage for an animated GIF, PNG or WebP, otherwise None."""
    try:
        src = PILImage.open(path)
        if getattr(src, "is_animated", False) and getattr(src, "n_frames", 1) > 1:
            return AnimatedImage(src)
    except Exception:
        pass
    return None


def scale_region(img, zoom, x, y, w, h):
    """The (x, y, w, h) region of `img` scaled by `zoom` (pixels per point), as a PIL image.

    The same scaling as ImageDocument.render_tile, so frames match still images.
    """
    scale = zoom * IMAGE_PT_PER_PX
    w, h = region_size(img.size, zoom, x, y, w, h)
    box = (x / scale, y / scale, (x + w) / scale, (y + h) / scale)
    resample = PILImage.NEAREST if scale >= 4 else (
        PILImage.LANCZOS if scale < 1 else PILImage.BICUBIC
    )
    return img.resize((w, h), resample=resample, box=box)


def region_size(size, zoom, x, y, w, h):
    """Size of the region actually rendered, clipped to the scaled image."""
    scale = zoom * IMAGE_PT_PER_PX
    iw, ih = size
    return max(1, min(w, int(iw * scale) - x)), max(1, min(h, int(ih * scale) - y))


class AnimatedImage(ImageDocument):
    """An animated picture. As a document it is its first frame; the rest is for Animator."""

    animated = True

    def __init__(self, src):
        self.lock = threading.Lock()
        self.src = src
        n = src.n_frames
        alpha = src.mode in ("RGBA", "LA", "PA") or "transparency" in src.info
        self.mode = "RGBA" if alpha else "RGB"
        keep = n * src.size[0] * src.size[1] * len(self.mode) <= DECODED_CACHE_BYTES
        self.durations, self._frames = [], {}
        for k in range(n):  # one pass to learn the durations, keeping frames when small
            src.seek(k)
            src.load()  # WebP only reports the duration once the frame is decoded
            d = src.info.get("duration") or 0
            self.durations.append(d / 1000 if d > 10 else DEFAULT_DURATION)
            if keep:
                self._frames[k] = src.convert(self.mode)
        self.img = self.frame(0)

    @property
    def frame_count(self):
        return len(self.durations)

    def frame(self, k):
        """Frame k, fully composed (Pillow applies the disposal and blending)."""
        f = self._frames.get(k)
        if f is None:
            with self.lock:
                self.src.seek(k)
                f = self.src.convert(self.mode)
        return f

    def render_frame(self, k, zoom, x, y, w, h):
        return scale_region(self.frame(k), zoom, x, y, w, h)


def to_image(pil):
    ch = 4 if pil.mode == "RGBA" else 3
    return Image.from_rows(pil.tobytes(), pil.width, pil.height, pil.width * ch, ch)


class FrameSet:
    """All frames rendered at one zoom and region, packed into atlases (or live)."""

    tokens = 0

    def __init__(self, want, zoom, x, y, w, h, n, live, first_alone=False):
        FrameSet.tokens += 1
        self.token = FrameSet.tokens
        self.want = want  # (zoom, x, y, w, h) the viewer asked for
        self.zoom, self.x, self.y, self.w, self.h = zoom, x, y, w, h
        self.live = live
        self.chunks = []  # (first frame, count, columns) per atlas
        self.ids = []  # uploaded image id per atlas, None until uploaded
        self.images = {}  # atlas index -> Image waiting to be uploaded
        if live:
            return
        cmax, rmax = max(1, ATLAS_SIDE // w), max(1, ATLAS_SIDE // h)
        per = max(1, min(ATLAS_PX // (w * h), cmax * rmax))
        first = 0
        if first_alone:  # nothing on screen yet: let the first frame arrive on its own
            self.chunks.append((0, 1, 1))
            first = 1
        while first < n:
            c = min(per, n - first)
            cols = max(1, min(c, cmax, math.ceil(math.sqrt(c * h / w))))
            if math.ceil(c / cols) > rmax:
                cols = math.ceil(c / rmax)
            self.chunks.append((first, c, cols))
            first += c
        self.ids = [None] * len(self.chunks)

    def area(self):
        """Pixels of all atlases together."""
        return sum(cols * math.ceil(c / cols) for _, c, cols in self.chunks) * self.w * self.h

    def locate(self, k):
        """(atlas index, x, y) of frame k."""
        for i, (first, c, cols) in enumerate(self.chunks):
            if first <= k < first + c:
                j = k - first
                return i, (j % cols) * self.w, (j // cols) * self.h
        raise IndexError(k)

    def complete(self):
        return all(i is not None for i in self.ids)

    def render_atlas(self, doc, i):
        first, c, cols = self.chunks[i]
        rows = math.ceil(c / cols)
        atlas = PILImage.new(doc.mode, (cols * self.w, rows * self.h))
        for j in range(c):
            f = doc.render_frame(first + j, self.zoom, self.x, self.y, self.w, self.h)
            atlas.paste(f, ((j % cols) * self.w, (j // cols) * self.h))
        return to_image(atlas)


class Worker(threading.Thread):
    """Renders frames in the background: one live frame first, then atlases in order."""

    def __init__(self):
        super().__init__(daemon=True)
        self.cond = threading.Condition()
        self.live = None  # (key, fn): the frame to show next in live mode
        self.jobs = []  # [(key, fn)] atlases to render
        self.results = []  # [(key, value)]
        self.busy = None  # key being rendered

    def submit_live(self, key, fn):
        with self.cond:
            if key != self.busy and all(key != r[0] for r in self.results):
                self.live = (key, fn)
                self.cond.notify()

    def submit_jobs(self, tasks):
        with self.cond:
            self.jobs = list(tasks)
            self.cond.notify()

    def take(self):
        with self.cond:
            out, self.results = self.results, []
            return out

    def idle(self):
        with self.cond:
            return self.busy is None and self.live is None and not self.jobs

    def run(self):
        while True:
            with self.cond:
                while self.live is None and not self.jobs:
                    self.cond.wait()
                if self.live is not None:
                    (key, fn), self.live = self.live, None
                else:
                    key, fn = self.jobs.pop(0)
                self.busy = key
            try:
                value = fn()
            except Exception:
                value = None
            with self.cond:
                self.busy = None
                if value is not None:
                    self.results.append((key, value))


class Animator:
    """Plays an AnimatedImage in a Viewer by managing the viewer's current tile."""

    def __init__(self, viewer):
        self.v = viewer
        self.doc = viewer.doc
        self.n = self.doc.frame_count
        self.playing = True
        self.frame = 0  # frame on screen (or to be put on screen)
        self.shown = None  # (set token, frame) actually on screen
        self.due = 0.0  # when the next frame should replace the current one
        self.set = None  # FrameSet being played
        self.job = None  # FrameSet being prepared for a new zoom or region
        self.ready = {}  # (token, frame) -> Image, live mode
        self.seen = set()  # image ids that have been on screen
        self.unplace = []  # (time, image_id)
        self.toggle_at = None  # pending single click
        self.next_id = 0
        self.worker = Worker()
        self.worker.start()

    # --- controls ---------------------------------------------------------

    def toggle(self):
        self.playing = not self.playing
        if self.playing:
            self.due = time.time() + self.doc.durations[self.frame]
        self.v.dirty = True

    def step(self, d):
        self.playing = False
        self.frame = (self.frame + d) % self.n
        self.v.dirty = True

    def on_key(self, k):
        """Space plays and pauses; while paused the arrow keys step through frames."""
        c = k.char if k.name == "char" else ""
        if c == " ":
            self.toggle()
        elif not self.playing and (k.name == "right" or c in ("l", ".")):
            self.step(1)
        elif not self.playing and (k.name == "left" or c in ("h", ",")):
            self.step(-1)
        else:
            return False
        return True

    def click(self, px, py, double):
        """A click (not a drag) on the picture toggles play once it is not a double-click."""
        if double:
            self.toggle_at = None
            return
        W, H = self.v.page_px()
        if -self.v.sx <= px < W - self.v.sx and -self.v.sy <= py < H - self.v.sy:
            self.toggle_at = time.time() + appmod.DOUBLE_CLICK

    def hint(self):
        if self.playing:
            return "Space: pause   Double-click: zoom   q: quit"
        return f"Paused {self.frame + 1}/{self.n}   Space: play   ←/→: frame   q: quit"

    def timeout(self, t):
        """How long the main loop may wait for input."""
        now = time.time()
        waits = [t]
        if self.playing and self.set is not None:
            waits.append(self.due - now)
        if self.toggle_at is not None:
            waits.append(self.toggle_at - now)
        if self.unplace:
            waits.append(min(at for at, _ in self.unplace) - now)
        if self.job is not None or self.shown != self._current() or not self.worker.idle():
            waits.append(0.01)
        return max(0.005, min(waits))

    def _current(self):
        return (self.set.token, self.frame) if self.set is not None else None

    # --- choosing what to render --------------------------------------------

    def _fits(self, s):
        """Does frame set `s` (as requested) still cover the view well enough?"""
        v = self.v
        tz, x, y, w, h = s.want
        saved = v.tile
        v.tile = appmod.Tile(0, v.page, tz, x, y, w, h)  # reuse the viewer's own checks
        try:
            return v.tile_ok() and not v.near_tile_edge()
        finally:
            v.tile = saved

    def plan(self, want):
        """A FrameSet for the requested zoom and region, within the pixel budget.

        At the requested zoom when all frames fit; otherwise at a lower zoom, as long as
        that still has at least one rendered pixel per image pixel (the terminal stretches
        it); otherwise live, rendering each frame when it is shown.
        """
        tz, x, y, w, h = want
        size = self.doc.img.size
        first_alone = self.set is None
        w0, h0 = region_size(size, tz, x, y, w, h)
        s = FrameSet(want, tz, x, y, w0, h0, self.n, False, first_alone)
        if s.area() <= FRAMES_BUDGET_PX:
            return s
        native = 1 / IMAGE_PT_PER_PX  # zoom at which one image pixel is one screen pixel
        f = math.sqrt(FRAMES_BUDGET_PX / s.area())
        for _ in range(4):
            f *= 0.97
            z = tz * f
            if z < native * 0.999:
                break
            x1, y1 = int(x * f), int(y * f)
            w1, h1 = region_size(size, z, x1, y1, max(1, int(w * f)), max(1, int(h * f)))
            s = FrameSet(want, z, x1, y1, w1, h1, self.n, False, first_alone)
            if s.area() <= FRAMES_BUDGET_PX:
                return s
        return FrameSet(want, tz, x, y, w0, h0, self.n, True)

    def request(self):
        """Called by the viewer instead of requesting a tile: keep frames for the view."""
        v = self.v
        if self.set is not None and self._fits(self.set):
            if self.job is not None:
                self._drop(self.job)
                self.job = None
            return
        if self.set is not None and time.time() - v.last_zoom < appmod.ZOOM_SETTLE:
            return  # keep stretching the current frames while the zoom is changing
        if self.job is not None and self._fits(self.job):
            return
        tz = v.tile_zoom()
        s = self.plan((tz,) + v.wanted_tile(tz))
        if self.job is not None:
            self._drop(self.job)
            self.job = None
        if s.live or self.set is None:
            if self.set is not None:
                self._retire(self.set)
            self.set = s
        else:
            self.job = s
        self._schedule()

    def _schedule(self):
        """Hand the atlases still to be rendered to the worker, the playing set first."""
        tasks = []
        for s in (self.set, self.job):
            if s is not None and not s.live:
                for i, image_id in enumerate(s.ids):
                    if image_id is None and i not in s.images:
                        tasks.append(((s.token, i), lambda s=s, i=i: s.render_atlas(self.doc, i)))
        self.worker.submit_jobs(tasks)

    # --- images -------------------------------------------------------------

    def _new_id(self):
        self.next_id = (self.next_id + 1) % ID_COUNT
        return ID_BASE + self.next_id

    def _drop(self, s):
        """Forget a frame set that never got on screen."""
        for image_id in s.ids:
            if image_id is not None:
                self.v.term.write(kitty_delete(image_id))

    def _retire(self, s):
        """Delete a frame set's images shortly, once its successor is on screen."""
        at = time.time() + appmod.RETIRE_DELAY
        ids = list(s.ids)
        t = self.v.tile
        if s.live and t is not None:
            ids.append(t.image_id)
        for image_id in ids:
            if image_id is not None:
                self.v.retired.append((at, image_id))
                self.seen.discard(image_id)

    def _collect(self):
        """Take finished renders; upload at most one atlas per call."""
        for (token, i), value in self.worker.take():
            for s in (self.set, self.job):
                if s is not None and s.token == token:
                    if s.live:
                        self.ready[(token, i)] = value
                    else:
                        s.images[i] = value
        for s in (self.set, self.job):
            if s is not None and s.images:
                i = min(s.images)
                image_id = self._new_id()
                self.v.term.write(kitty_upload(s.images.pop(i), image_id))
                s.ids[i] = image_id
                return

    # --- playback -------------------------------------------------------------

    def tick(self):
        """Called by the viewer instead of receiving a tile, on every loop."""
        now = time.time()
        if self.toggle_at is not None and now >= self.toggle_at:
            self.toggle_at = None
            self.toggle()
        self._unplace_due(now)
        self._collect()
        if self.job is not None and self.job.complete():
            self._retire(self.set)
            self.set, self.job = self.job, None
        s = self.set
        if s is None:
            return
        advance = False
        if self.shown != (s.token, self.frame):
            k = self.frame  # a new frame set, a step, or the very first frame
        elif self.playing and now >= self.due:
            k, advance = (self.frame + 1) % self.n, True
        else:
            k = None
        if k is not None:
            if self._available(s, k):
                first = self.shown is None
                self._show(s, k)
                d = self.doc.durations[k]
                if advance:
                    self.due = self.due + d if self.due + d > now else now + d
                elif first:
                    self.due = now + d
            elif s.live:
                self._render_live(s, k)
        if s.live:
            if self.playing:
                self._render_live(s, (self.frame + 1) % self.n)  # prefetch the next one
            keep = (self.frame, (self.frame + 1) % self.n)
            self.ready = {
                key: im for key, im in self.ready.items() if key[0] == s.token and key[1] in keep
            }

    def _available(self, s, k):
        if s.live:
            return (s.token, k) in self.ready
        return s.ids[s.locate(k)[0]] is not None

    def _render_live(self, s, k):
        if (s.token, k) not in self.ready:
            self.worker.submit_live(
                (s.token, k),
                lambda: to_image(self.doc.render_frame(k, s.zoom, s.x, s.y, s.w, s.h)),
            )

    def _show(self, s, k):
        v = self.v
        old = v.tile
        if s.live:
            image_id, ox, oy = self._new_id(), 0, 0
            v.term.write(kitty_upload(self.ready.pop((s.token, k)), image_id))
        else:
            i, ox, oy = s.locate(k)
            image_id = s.ids[i]
        switch = old is None or old.image_id != image_id
        if switch:
            v.z += 1
        t = appmod.Tile(image_id, v.page, s.zoom, s.x, s.y, s.w, s.h, v.z if switch else old.z)
        t.ox, t.oy = ox, oy
        v.tile = t
        v.place()
        self.frame, self.shown = k, (s.token, k)
        if not switch or old is None:
            self.seen.add(image_id)
            return
        # Take the previous picture away. Under a picture never shown before keep it a
        # moment, since a multiplexer may only draw the new one a frame later.
        same_set = old.image_id in s.ids
        later = image_id not in self.seen
        self.seen.add(image_id)
        if s.live and old.image_id not in {image_id for _, image_id in v.retired}:
            # live frames are used once: drop the previous one soon
            if self.doc.mode == "RGBA":
                v.term.write(kitty_unplace_all(old.image_id))
            v.retired.append((time.time() + FIRST_SHOW_DELAY, old.image_id))
            self.seen.discard(old.image_id)
        elif same_set:
            if later:
                self.unplace.append((time.time() + FIRST_SHOW_DELAY, old.image_id))
            else:
                v.term.write(kitty_unplace_all(old.image_id))

    def _unplace_due(self, now):
        keep = []
        for at, image_id in self.unplace:
            if at > now:
                keep.append((at, image_id))
            elif self.v.tile is None or self.v.tile.image_id != image_id:
                self.v.term.write(kitty_unplace_all(image_id))
        self.unplace = keep
