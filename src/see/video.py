"""Video playback: ffmpeg decodes frames into a pipe, ffplay plays the sound.

Frames are decoded to raw RGB at (at most) the size they are shown, compressed with
zlib and sent as kitty images. Two image ids alternate: the new frame is placed before
the previous one is deleted, so nothing flickers. The sound runs in its own ffplay
process; its reported clock keeps the picture in step, and it is restarted at the right
position after a pause or a seek.
"""

import collections
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import zlib

from .render import Image, kitty_delete, kitty_place, kitty_upload
from .term import Key, Mouse, Terminal

VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".qt", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg", ".mpeg",
    ".m2v", ".ts", ".mts", ".m2ts", ".3gp", ".3g2", ".ogv", ".vob", ".mxf", ".asf", ".divx",
}
# image formats that ffprobe also reads as a "video stream"; they stay with the image viewer
IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".heic", ".heif", ".bmp", ".tif",
    ".tiff", ".ico", ".icns", ".svg", ".jxl", ".apng", ".pdf",
}

MAX_FPS = 30
# Decoded frame size limits. herdr passes every frame on to the outer terminal as raw
# RGBA, so 30 frames a second of ~400k pixels (about 840x470) is ~48 MB/s.
MAX_W = 960
MAX_PX = 400_000
QUEUE = 4  # decoded frames waiting to be shown
SEEK_STEP = 5.0
AUDIO_WAIT = 1.0  # seconds the picture waits for the sound to start before running alone
SYNC_TOLERANCE = 0.04  # seconds the picture may drift from the sound before it is corrected
REPORT_FRESH = 0.5  # seconds a clock report from ffplay stays usable
MESSAGE_SECONDS = 3.0
IDS = (1, 2)


def find_tool(name):
    """Path of an ffmpeg tool, also when the shell's PATH lacks Homebrew's directory."""
    return shutil.which(name) or shutil.which(name, path="/opt/homebrew/bin:/usr/local/bin")


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


def fmt_time(t, long=False):
    t = max(0, int(t))
    h, m, s = t // 3600, t // 60 % 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if long or h else f"{m}:{s:02d}"


# --- detection -------------------------------------------------------------------


class VideoInfo:
    def __init__(self, width, height, fps, duration, start, has_audio):
        self.width, self.height = width, height  # as displayed (rotation, pixel aspect)
        self.fps = fps
        self.duration = duration  # seconds; 0 when unknown
        self.start = start  # timestamp of the first frame in the file
        self.has_audio = has_audio


def _rate(s):
    try:
        num, _, den = s.partition("/")
        v = float(num) / float(den or 1)
        return v if v > 0 and math.isfinite(v) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path, ffprobe=None):
    """VideoInfo for a file with a moving picture, or None."""
    ffprobe = ffprobe or find_tool("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-of", "json", "-show_entries",
             "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,sample_aspect_ratio,"
             "duration:stream_disposition=attached_pic:stream_side_data=rotation:"
             "format=duration,start_time",
             path],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
        ).stdout
        data = json.loads(out or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    streams = data.get("streams") or []
    video = next(
        (s for s in streams
         if s.get("codec_type") == "video" and s.get("width") and s.get("height")
         and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    if video is None:
        return None
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0)
    except ValueError:
        duration = 0.0
    try:
        start = float(fmt.get("start_time") or 0)
    except ValueError:
        start = 0.0
    w, h = int(video["width"]), int(video["height"])
    sar = _rate((video.get("sample_aspect_ratio") or "1:1").replace(":", "/")) or 1.0
    w = max(1, round(w * sar))
    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(side["rotation"])
    if abs(rotation) % 180 == 90:
        w, h = h, w
    fps = _rate(video.get("avg_frame_rate", "")) or _rate(video.get("r_frame_rate", "")) or 25.0
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return VideoInfo(w, h, fps, max(0.0, duration), start, has_audio)


def is_video(path):
    """Should `path` be played as a video? Known video extensions are; known image or PDF
    files are not; anything else is a video when it is neither a PDF nor a picture
    Pillow reads, and ffprobe finds a moving picture in it."""
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS:
        return True
    if ext in IMAGE_EXTS:
        return False
    try:
        with open(path, "rb") as f:
            if b"%PDF-" in f.read(1024):
                return False
        from PIL import Image as PILImage

        with PILImage.open(path):
            return False  # a picture
    except Exception:
        pass
    info = probe(path)
    return info is not None and info.duration > 0


# --- processes --------------------------------------------------------------------


def _stop(proc, kill=False):
    """End a child process and reap it."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if kill:
            proc.kill()
        else:
            proc.terminate()
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    except OSError:
        pass


class Decoder:
    """ffmpeg turning the video into raw RGB frames of w x h pixels, from `start` on.

    A thread reads the pipe and keeps up to QUEUE frames as (time, bytes)."""

    def __init__(self, ffmpeg, path, start, fps, w, h):
        self.start, self.fps, self.w, self.h = start, fps, w, h
        self.frames = collections.deque()
        self.cond = threading.Condition()
        self.eof = False
        self.count = 0
        self.error = ""
        self.stopped = False
        self.log = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(
            [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", f"{start:.3f}", "-i", path,
             "-map", "0:v:0", "-an", "-sn", "-dn",
             "-vf", f"fps={fps:.6g},scale={w}:{h}:flags=bilinear",
             "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=self.log,
        )
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        size = self.w * self.h * 3
        out = self.proc.stdout
        try:
            while not self.stopped:
                data = out.read(size)
                if len(data) < size:
                    break
                with self.cond:
                    while len(self.frames) >= QUEUE and not self.stopped:
                        self.cond.wait(0.1)
                    if self.stopped:
                        break
                    self.frames.append((self.start + self.count / self.fps, data))
                    self.count += 1
        except (OSError, ValueError):
            pass
        if not self.stopped and self.count == 0:
            self.proc.wait()
            try:
                self.log.seek(0)
                lines = self.log.read().decode(errors="replace").strip().splitlines()
                self.error = lines[-1] if lines else ""
            except (OSError, ValueError):
                pass
        with self.cond:
            self.eof = True

    def peek(self):
        with self.cond:
            return self.frames[0] if self.frames else None

    def pop(self):
        with self.cond:
            frame = self.frames.popleft() if self.frames else None
            self.cond.notify_all()
            return frame

    def done(self):
        with self.cond:
            return self.eof and not self.frames

    def stop(self):
        with self.cond:
            self.stopped = True
            self.cond.notify_all()
        # killed outright: ffmpeg blocked on a full pipe would not act on SIGTERM
        _stop(self.proc, kill=True)
        self.thread.join(timeout=2)
        for f in (self.proc.stdout, self.log):
            try:
                f.close()
            except (OSError, ValueError):
                pass


class Audio:
    """ffplay playing the sound from `start` without a window.

    ffplay's -ss lands on the keyframe before the position, so atrim cuts the sound to
    start exactly there. With -stats ffplay prints its clock (file time) about every
    30 ms; `report` holds the latest as (position, monotonic time it was read)."""

    def __init__(self, ffplay, path, start, offset):
        self.offset = offset
        self.report = None
        self.proc = subprocess.Popen(
            [ffplay, "-nodisp", "-vn", "-sn", "-autoexit", "-stats", "-loglevel", "quiet",
             "-ss", f"{start:.3f}", "-af", f"atrim=start={start + offset:.3f}", path],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        buf = b""
        fd = self.proc.stderr.fileno()
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.replace(b"\n", b"\r").split(b"\r")
            for line in reversed(lines):
                parts = line.split()
                try:
                    t = float(parts[0]) if parts else math.nan
                except ValueError:
                    continue
                if math.isfinite(t) and t >= 0:
                    self.report = (t - self.offset, time.monotonic())
                    break

    def running(self):
        return self.proc.poll() is None

    def stop(self):
        _stop(self.proc)
        self.thread.join(timeout=2)
        try:
            self.proc.stderr.close()
        except (OSError, ValueError):
            pass


# --- player -----------------------------------------------------------------------


class Player:
    def __init__(self, path, info, term, ffmpeg, ffplay=None, clock=time.monotonic):
        self.path, self.info, self.term = path, info, term
        self.ffmpeg, self.ffplay = ffmpeg, ffplay
        self.clock = clock
        self.fps = min(info.fps, MAX_FPS)
        self.duration = info.duration
        self.pos0 = 0.0  # position at clock time t0
        self.t0 = clock()
        self.playing = False
        self.ended = False
        self.decoder = None
        self.audio = None
        self.audio_since = None  # waiting for the sound to start (clock time) or None
        self.wait_frame = False  # the decoder was (re)started and has no frame yet
        self.last_report = None
        self.ids = list(IDS)
        self.shown = None  # image id on screen
        self.shown_time = None
        self.frames_shown = 0
        self.dropped = 0
        self.scrub = None  # position under the pointer while dragging on the bar
        self.bar = None  # (x0, cells) of the seek bar on the bottom row
        self.icon = None  # (x0, x1) of the play/pause icon
        self.close_x = 0
        self.chrome = ""
        self.message = ""
        self.message_until = 0.0
        self.running = True
        self.resized = True
        self.rows = self.cols = 0
        if info.has_audio and not ffplay:
            self.flash("No sound: ffplay was not found")

    # --- clock -------------------------------------------------------------

    def position(self):
        if not self.playing:
            return self.pos0
        now = self.clock()
        if self.wait_frame:  # hold the clock until the first frame after a seek is decoded
            self.t0 = now
            return self.pos0
        if self.audio_since is not None:
            report = self.audio.report if self.audio else None
            if report is None and now - self.audio_since < AUDIO_WAIT:
                return self.pos0  # hold the picture until the sound starts
            self.audio_since = None
            self.pos0, self.t0 = (report[0] + now - report[1], now) if report else (self.pos0, now)
            self.last_report = report
        p = self.pos0 + now - self.t0
        return min(p, self.duration) if self.duration else p

    def sync(self):
        """Follow the sound's clock when it has drifted from ours."""
        if not (self.playing and self.audio and self.audio_since is None):
            return
        report = self.audio.report
        now = self.clock()
        if report is None or report is self.last_report or now - report[1] > REPORT_FRESH:
            return
        self.last_report = report
        heard = report[0] + now - report[1]
        if abs(heard - self.position()) > SYNC_TOLERANCE:
            self.pos0, self.t0 = heard, now

    # --- control -----------------------------------------------------------

    def start_audio(self):
        self.stop_audio()
        if self.info.has_audio and self.ffplay and self.playing:
            if not self.duration or self.pos0 < self.duration - 0.05:
                self.audio = Audio(self.ffplay, self.path, self.pos0, self.info.start)
                self.audio_since = self.clock()

    def stop_audio(self):
        if self.audio:
            self.audio.stop()
            self.audio = None
        self.audio_since = None

    def start_decoder(self, pos):
        if self.decoder:
            self.decoder.stop()
        self.decoder = Decoder(self.ffmpeg, self.path, pos, self.fps, self.dec_w, self.dec_h)
        self.wait_frame = True

    def play(self):
        if self.playing:
            return
        if self.ended or (self.duration and self.pos0 >= self.duration - 0.05):
            self.ended = False
            self.seek(0.0)
        self.playing = True
        self.t0 = self.clock()
        if not self.wait_frame:
            self.start_audio()

    def pause(self):
        if not self.playing:
            return
        self.pos0 = self.position()
        self.playing = False
        self.stop_audio()

    def toggle(self):
        self.pause() if self.playing else self.play()

    def seek(self, pos):
        if self.duration:
            pos = min(pos, max(0.0, self.duration - 0.05))
        pos = max(0.0, pos)
        self.ended = False
        self.pos0, self.t0 = pos, self.clock()
        self.stop_audio()
        self.start_decoder(pos)  # the sound restarts once the first frame is ready

    def quit(self):
        self.running = False

    def close(self):
        """Stop every child process. Safe to call more than once."""
        self.stop_audio()
        if self.decoder:
            self.decoder.stop()
            self.decoder = None

    def flash(self, text):
        self.message = text
        self.message_until = time.time() + MESSAGE_SECONDS

    # --- geometry ----------------------------------------------------------

    def layout(self):
        self.rows, self.cols, cw, ch = self.term.size()
        self.cw, self.ch = cw, ch
        area_rows = max(1, self.rows - 2)
        vw, vh = self.info.width, self.info.height
        s = min(self.cols * cw / vw, area_rows * ch / vh)
        dw, dh = vw * s, vh * s
        self.cells = (
            max(1, min(self.cols, round(dw / cw))),
            max(1, min(area_rows, round(dh / ch))),
        )
        self.at = (2 + (area_rows - self.cells[1]) // 2, (self.cols - self.cells[0]) // 2 + 1)
        # decode at the shown size, but never above the limits or the source size
        k = min(1.0, MAX_W / dw, math.sqrt(MAX_PX / (dw * dh)), vw / dw)
        self.dec_w = max(2, int(dw * k) // 2 * 2)
        self.dec_h = max(2, int(dh * k) // 2 * 2)

    # --- frames ------------------------------------------------------------

    def next_frame(self, p):
        """The newest decoded frame that is due at position p; older due ones are dropped."""
        frame = None
        half = 0.5 / self.fps
        while True:
            head = self.decoder.peek()
            if head is None or head[0] > p + half:
                break
            if frame is not None:
                self.dropped += 1
            frame = self.decoder.pop()
        return frame

    def show(self, frame):
        t, data = frame
        image = Image(zlib.compress(data, 1), self.dec_w, self.dec_h, 3)
        iid = self.ids[0]
        self.ids.reverse()
        out = kitty_upload(image, iid) + kitty_place(
            iid, self.at[0], self.at[1], (0, 0, self.dec_w, self.dec_h), cells=self.cells
        )
        if self.shown is not None:
            out += kitty_delete(self.shown)  # after the new frame is in place: no flicker
        self.term.write(out)
        self.shown, self.shown_time = iid, t
        self.frames_shown += 1

    def tick(self):
        if self.wait_frame and (self.decoder.peek() is not None or self.decoder.eof):
            self.wait_frame = False
            self.t0 = self.clock()
            if self.playing and self.audio is None:
                self.start_audio()
        self.sync()
        p = self.position()
        frame = self.next_frame(p)
        if frame:
            self.show(frame)
        if self.playing and self.decoder.done():
            if self.decoder.count == 0 and self.decoder.error:
                self.flash(f"Cannot decode: {self.decoder.error}")
                self.pause()
            elif not self.duration or p >= self.duration - 0.5 / self.fps:
                self.pos0 = self.duration or p
                self.playing = False
                self.ended = True
                self.stop_audio()

    def wait_time(self):
        """How long to wait for input before the next frame is due."""
        head = self.decoder.peek() if self.decoder else None
        if head is None:
            return 0.01 if self.playing or not self.decoder.done() else 0.1
        if not self.playing:
            return 0.1
        return max(0.002, min(0.05, head[0] - 0.5 / self.fps - self.position()))

    # --- screen ------------------------------------------------------------

    def toolbar(self):
        close = " ✕ "
        room = max(0, self.cols - width(close) - 3)
        name = os.path.basename(self.path)
        title = name if width(name) <= room else clip(name, max(0, room - 1)) + "…"
        self.close_x = self.cols - width(close) + 1
        return (
            f"\x1b[1;1H\x1b[2K\x1b[0;1m {title}\x1b[0m"
            f"\x1b[1;{self.close_x}H\x1b[0m{close}"
        )

    def status(self):
        row = f"\x1b[{self.rows};1H\x1b[2K"
        if self.message and time.time() < self.message_until:
            self.bar = self.icon = None
            return row + f"\x1b[33m {clip(self.message, self.cols - 2)}\x1b[0m"
        p = self.scrub if self.scrub is not None else self.position()
        # ⏵ and ⏸ are a matched pair of media-control symbols (U+23F5 / U+23F8), so they are
        # drawn at the same size; each is padded to two columns so the time and the seek bar
        # never shift when playback is toggled. (Both default to text, not emoji, style.)
        icon = "⏵ " if self.playing else "⏸ "
        long = self.duration >= 3600
        total = fmt_time(self.duration, long) if self.duration else ""
        now = fmt_time(p, long).rjust(len(total))  # fixed width as the time grows
        times = now + (f" / {total}" if total else "")
        left = f" {icon} {times}  "
        self.icon = (2, 1 + width(icon))
        cells = self.cols - width(left) - 1
        if not self.duration or cells < 5:
            self.bar = None
            return row + clip(left, self.cols)
        frac = min(1.0, max(0.0, p / self.duration))
        k = round(frac * (cells - 1))
        self.bar = (width(left) + 1, cells)
        return (
            row + left
            + "━" * k + "●" + "\x1b[2m" + "─" * (cells - 1 - k) + "\x1b[0m"
        )

    def draw(self):
        chrome = self.toolbar() + self.status()
        if chrome != self.chrome:
            self.term.write(chrome)
            self.chrome = chrome

    # --- input -------------------------------------------------------------

    def bar_position(self, m):
        x0, cells = self.bar
        frac = (m.px - (x0 - 1) * self.cw) / (cells * self.cw)
        return min(1.0, max(0.0, frac)) * self.duration

    def on_key(self, k):
        c = k.char if k.name == "char" else ""
        if k.name in ("ctrl-c", "esc") or c == "q":
            self.quit()
        elif c == " ":
            self.toggle()
        elif k.name == "right":
            self.seek(self.position() + SEEK_STEP)
        elif k.name == "left":
            self.seek(self.position() - SEEK_STEP)

    def on_mouse(self, m):
        if m.button not in (0, 3) and not m.motion:
            return
        if self.scrub is not None:
            if m.motion:
                self.scrub = self.bar_position(m) if self.bar else self.scrub
            elif not m.pressed:
                pos, self.scrub = self.scrub, None
                self.seek(pos)
            return
        if not m.pressed or m.motion or m.button != 0:
            return
        if m.y == 1:
            if m.x >= self.close_x:
                self.quit()
        elif m.y == self.rows:
            if self.icon and self.icon[0] - 1 <= m.x <= self.icon[1] + 1:
                self.toggle()
            elif self.bar and m.x >= self.bar[0] - 1:
                self.scrub = self.bar_position(m)
        else:
            self.toggle()

    def handle(self, ev):
        if isinstance(ev, Key):
            self.on_key(ev)
        elif isinstance(ev, Mouse):
            self.on_mouse(ev)

    # --- main loop ---------------------------------------------------------

    def on_resize(self):
        self.resized = False
        # clear the text line by line: ED 2 would also delete kitty images
        rows = self.term.size()[0]
        self.term.write("".join(f"\x1b[{r};1H\x1b[2K" for r in range(1, rows + 1)))
        self.chrome = ""
        p = self.position()
        self.layout()
        self.pos0, self.t0 = p, self.clock()
        self.start_decoder(p)  # the frame size may have changed

    def start(self):
        self.playing = True
        self.t0 = self.clock()
        self.on_resize()  # starts the decoder; the sound follows with the first frame

    def step(self, timeout=None):
        if self.resized:
            self.on_resize()
        self.tick()
        self.draw()
        wait = self.wait_time() if timeout is None else timeout
        for ev in self.term.read_events(timeout=wait):
            self.handle(ev)
            if not self.running:
                break

    def run(self):
        signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "resized", True))
        self.start()
        while self.running:
            self.step()


def _exit_on_signal(signum, frame):
    raise SystemExit(128 + signum)


def play(path):
    """Play a video file full-window until the user quits. Returns an exit status."""
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        sys.exit("see: playing videos needs ffmpeg (ffmpeg and ffprobe on the PATH)")
    info = probe(path, ffprobe)
    if info is None:
        sys.exit(f"see: cannot open {path}: no video found")
    term = Terminal()
    player = Player(path, info, term, ffmpeg, find_tool("ffplay"))
    # a closed terminal or `kill` must still stop ffmpeg and ffplay
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _exit_on_signal)
    entered = False
    try:
        term.enter()
        entered = True
        player.run()
    except KeyboardInterrupt:
        pass
    finally:
        player.close()
        if entered:
            term.leave()
        log = os.environ.get("SEE_DEBUG")
        if log:
            with open(log, "a") as f:
                f.write(
                    f"video {info.width}x{info.height} decode={getattr(player, 'dec_w', 0)}x"
                    f"{getattr(player, 'dec_h', 0)} fps={player.fps:.3g} "
                    f"shown={player.frames_shown} dropped={player.dropped} "
                    f"bytes={term.bytes_written} cell={term.cell} pixel_mouse={term.pixel_mouse}\n"
                )
    return 0
