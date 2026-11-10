# see

[Japanese](README_JP.md)

A mouse-friendly viewer for the terminal. It opens PDFs, images, Office documents, text and
code, plays animated GIF, PNG and WebP, and plays video with sound. Everything is drawn as
real images with the [kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/),
and you use it like a GUI viewer: swipe to turn pages, drag to select and copy text,
double-click to zoom.

```
 report.pdf   3/12   82%   Fit   Width                                  ✕
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│                           (the PDF page)                             │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
 Double-click: zoom   q: quit
```

## Supported files

| Kind | Status |
|---|---|
| PDF | ✅ Supported |
| Images: PNG, JPEG, WebP, AVIF, BMP, TIFF, … | ✅ Supported (anything [Pillow](https://python-pillow.org) reads) |
| iPhone photos (HEIC / HEIF) | ✅ On macOS (converted with the built-in `sips`) |
| Text, Markdown, source code, logs, JSON | ✅ As pages in a monospace font, Japanese included; text can be selected |
| CSV / TSV | ✅ As a table |
| Word, RTF, OpenDocument text (.docx, .doc, .rtf, .odt) | ✅ With LibreOffice, or on macOS with the built-in text system (simpler layout) |
| Excel, PowerPoint, Keynote, Numbers, Pages (.xlsx, .xls, .pptx, .ppt, .key, …) | ✅ With LibreOffice, or on macOS from the Quick Look preview |
| HTML, SVG | ✅ On macOS (drawn by the built-in WebKit); HTML also with LibreOffice |
| Animated GIF, APNG, animated WebP | ✅ Plays in a loop; click or Space to pause |
| Video: MP4, MOV, MKV, WebM, AVI, … | ✅ Supported, with sound (needs [ffmpeg](https://ffmpeg.org)) |

Anything else (for example 3D models) is refused with a short message that lists what see
can open.

## Requirements

- A terminal that supports the kitty graphics protocol, such as
  [Ghostty](https://ghostty.org), [kitty](https://sw.kovidgoyal.net/kitty/) or
  [WezTerm](https://wezterm.org). It also works inside [herdr](https://herdr.dev) panes.
  see checks this when it starts; in other terminals (such as macOS Terminal.app) it says so
  and exits instead of drawing garbage. `SEE_FORCE_GRAPHICS=1` skips the check.
- Python 3.10 or newer
- Optional: [LibreOffice](https://www.libreoffice.org) for the most faithful Office documents
  (and to open them at all outside macOS). see finds `soffice` on the `PATH` or in
  `/Applications/LibreOffice.app`.
- For video: [ffmpeg](https://ffmpeg.org) (`ffmpeg` and `ffprobe`; `ffplay` for the sound),
  for example `brew install ffmpeg`. PDFs and images do not need it.

## Install

With [Homebrew](https://brew.sh):

```sh
brew install youkin06/tap/see
brew install ffmpeg            # only needed for videos
```

Update with `brew upgrade see`, uninstall with `brew uninstall see`. Homebrew installs see
from source, so it needs up-to-date Command Line Tools (if it complains, update them from
System Settings → Software Update).

Or with pipx / uv (the Python package is called `see-viewer`, because `see` is taken on PyPI):

```sh
pipx install git+https://github.com/Youkin06/see
# or
uv tool install git+https://github.com/Youkin06/see
```

Uninstall with `pipx uninstall see-viewer` (or `uv tool uninstall see-viewer`).

## Usage

```sh
see file.pdf
see file.pdf --page 5    # open at page 5
see photo.png
see report.docx          # documents and text files open as pages
see notes.md
see movie.mp4            # play a video
```

Documents and text files are converted to PDF first. That takes a moment the first time
(`see: converting …` is shown meanwhile); the result is cached, so opening the file again is
instant until it changes.

### Screen

- **Top bar:** file name, current page / page count (PDFs with more than one page), zoom level, **Fit** (whole page),
  **Width** (fit the page width) and **✕** (quit). The active mode is underlined. In narrow
  windows the less important parts are hidden.
- **Bottom line:** `Double-click: zoom   q: quit`, and short messages such as
  `Copied 34 characters`.

### Mouse and trackpad

| Action | Effect |
|---|---|
| Swipe right → left / left → right | Next / previous page |
| Swipe or wheel up / down | Scroll the page; turns one page at the top or bottom, or when the whole page is visible |
| Drag | PDFs: select text; it is copied to the clipboard when you release. Images: move the picture |
| Double-click | Zoom in 2× at that spot / back to the whole page |
| Click the picture | Animations: play / pause |
| Ctrl + wheel | Zoom in or out around the pointer |
| Right or middle drag | Move a zoomed page around |
| Shift + wheel | Same as a sideways swipe: scrolls a page wider than the window, otherwise turns the page |
| Click Fit / Width / ✕ | Whole page / fit width / quit |

One swipe turns exactly one page, also when the trackpad's momentum keeps scrolling.

### Keyboard

| Key | Effect |
|---|---|
| → / ←, l / h, PageDown / PageUp, Space | Next / previous page |
| ↓ / ↑, j / k | Scroll |
| Home / End | First / last page |
| + / − | Zoom in / out |
| 0 or f | Whole page (Fit) |
| w | Fit width |
| v | Make the left drag move the page instead of selecting text (press again to go back) |
| Space | Animations: play / pause (instead of next page) |
| → / ← while paused | Animations: next / previous frame |
| Esc | Clear the selection (quits when nothing is selected) |
| q | Quit |

Copying uses `pbcopy` on a local Mac and the OSC 52 escape sequence elsewhere (for example
over SSH). If your terminal sends Alt+C / Alt+A for Command+C / Command+A, see treats
them as copy and as "select the whole page".

### Video

```
 movie.mp4                                                            ✕
┌──────────────────────────────────────────────────────────────────────┐
│                          (the video)                                 │
└──────────────────────────────────────────────────────────────────────┘
 ▶ 0:42 / 3:10  ━━━━━━━━━━━━●──────────────────────────────────────────
```

A video starts playing at once, with its sound. The bottom line shows a play/pause button that
works like YouTube's (⏸ while playing, ⏵ when paused), the current time / the length, and a
seek bar.

| Action | Effect |
|---|---|
| Click on the video, or Space | Play / pause (at the end, Space plays again from the start) |
| Click or drag on the seek bar | Jump to that point |
| → / ← | Forward / back 5 seconds |
| q, Esc, or click ✕ | Quit |

### Limitations

- **Scanned PDFs** contain pictures of text, not text, so nothing can be selected on them.
- **Images** are shown at 100% when one image pixel is one screen pixel; enlarging a small
  image a lot keeps hard pixel edges (good for pixel art and icons).
- **Animations** loop forever (the file's loop count is ignored). Each frame is shown for
  its own duration; frames asking for 10 ms or less get 100 ms, as in web browsers. Long or
  large animations that do not fit the frame budget (see below) are rendered frame by
  frame, which is slower, especially inside herdr. When the zoom enlarges an animation
  beyond what fits, its frames are rendered a little smaller and stretched by the terminal.
- **Office documents without LibreOffice** (macOS): Word, RTF and OpenDocument text go
  through the macOS text system, which keeps text, fonts and tables but simplifies the
  layout (headers, footers, columns and text boxes may be lost). Excel, PowerPoint and iWork
  files show Quick Look's preview: slides and sheets are close to the original, though some
  formatting and effects can be missing. If even
  that fails, a Quick Look thumbnail of the first page is shown, with a note. Install
  LibreOffice for full fidelity.
- **Text files** are laid out in Menlo, 9 pt, on A4 pages and wrapped at the page width;
  only the first 2 million characters are shown. Long single-line JSON is pretty-printed.
  Outside macOS a simpler built-in layout is used, which needs a font such as IPAGothic or
  DejaVu Sans Mono for non-Latin text.
- **SVG** is drawn by WebKit as a picture on a page the size of the drawing; scripts,
  animations and external files referenced by the SVG are not loaded. HTML pages are
  printed as WebKit would print them.
- **Pinch to zoom** is not possible: terminals do not pass pinch gestures to programs. Use a
  double-click, Ctrl + wheel or the + / − keys.
- Inside a multiplexer such as herdr, every new page image is passed on to the outer
  terminal, which costs a moment. Scrolling and dragging only move the image already sent,
  and neighbouring pages are prepared in advance, so this mainly shows when zooming.
- **Video** is shown at up to about 400,000 pixels per frame (for example 840 × 470) and
  30 frames per second, stretched to fill the window, so it is softer than in a video
  player. Frames that would arrive late are skipped. After a seek the picture and sound
  start once the first frame is decoded, which can take a moment in files with few
  keyframes. There is no volume control, subtitle display or zoom for video. If `ffplay`
  is missing the video plays without sound.

## How it works

- PDF pages are rendered with [PDFium](https://pdfium.googlesource.com/pdfium/) (through
  [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)) at the current zoom level, so
  zoomed-in text stays sharp. Images are read with [Pillow](https://python-pillow.org) and
  only the visible part is scaled. The file type is detected from the content, not the name.
- Other files are turned into a PDF and then shown like one (`src/see/convert.py`):
  LibreOffice (`soffice --headless --convert-to pdf`) when installed; otherwise on macOS
  Cocoa's document readers and text layout, and WebKit printing of HTML, SVG and the HTML
  that Quick Look (`qlmanage -p`) makes for Office and iWork files (Keynote previews are
  PDFs already and are joined directly). Text outside macOS is written to a PDF with PDFium.
  Results are cached in the temp directory, keyed by path, size and modification time.
  Every conversion runs with a timeout.
- Each rendered region is uploaded to the terminal once. Scrolling and dragging only change
  which part of it is shown, so they send a few bytes instead of a new image. Zooming
  stretches the current image at once and renders a sharp one when the wheel stops.
- Images are kept under 4 million pixels (about 1.6 screens around the view). Multiplexers
  such as herdr re-send images uncompressed and drop ones over their limit.
- Rendering runs in a background thread, and the pages before and after the current one are
  rendered while idle.
- Animations are rendered frame by frame at the current zoom and uploaded once, packed
  into a few images of up to 4 million pixels (an "atlas"). Playing then only re-places the
  picture with another part of an atlas, a few bytes per frame. All frames together are
  kept under 14 million pixels, because herdr keeps at most 16 images that are not on
  screen (64 MB of pixels) and sends anything it dropped again. After a zoom the frames are
  rendered again in the background while the old ones keep playing.
- Trackpad swipes are recognised from the rate of scroll events: a page turns when the rate
  rises sharply, and turning re-arms when it has fallen well below its peak.
- Videos are recognised by their extension (or, for other names, by `ffprobe`). `ffmpeg`
  decodes the picture into raw RGB frames already scaled to the shown size, and each frame
  is sent as a new image; two image ids take turns, and the old frame is deleted only
  after the new one is in place, so nothing flickers. The sound is played by a separate
  `ffplay` process, whose clock the picture follows; after a pause or a seek it is
  restarted at the exact position. Both processes are stopped when see exits.

## Development

```sh
git clone https://github.com/Youkin06/see
cd see
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/see some.pdf
.venv/bin/pytest -q            # tests (also run on GitHub for every pull request)
```

Two environment variables help when tuning input and rendering:

- `SEE_TRACE=/path/to/log` logs every input event and the swipe decisions.
- `SEE_DEBUG=/path/to/log` writes the detected cell size, mouse mode, bytes sent and the
  largest image on exit.

To release a new version, note the changes under `## Unreleased` in CHANGELOG.md and run
`scripts/release.sh X.Y.Z`. It bumps the version, merges it through a pull request once CI
passes, tags it, and updates the Homebrew formula in
[Youkin06/homebrew-tap](https://github.com/Youkin06/homebrew-tap).

See [CHANGELOG.md](CHANGELOG.md) for the history.

## License

MIT
