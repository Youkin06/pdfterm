# see

[Japanese](README_JP.md)

A mouse-friendly viewer for the terminal. It shows PDFs, images, Office documents, text,
animations and video (with sound) as real images through the
[kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/): swipe to turn
pages, drag to select and copy text, double-click to zoom.

![see opening a PDF, a photo, a GIF and a video in Ghostty](docs/demo.gif)

## Supported files

- PDF, images (anything [Pillow](https://python-pillow.org) reads; HEIC on macOS)
- Text, Markdown, source code, JSON, CSV / TSV
- Word, Excel, PowerPoint, Keynote, Numbers, Pages, RTF, OpenDocument (with LibreOffice, or on macOS without it)
- HTML, SVG (macOS)
- Animated GIF, APNG, WebP
- Video: MP4, MOV, MKV, WebM, AVI, … (needs [ffmpeg](https://ffmpeg.org))

## Requirements

- A terminal with kitty graphics: [Ghostty](https://ghostty.org), [kitty](https://sw.kovidgoyal.net/kitty/), [WezTerm](https://wezterm.org), or a [herdr](https://herdr.dev) pane inside one
  (herdr itself is not enough: in Terminal.app, iTerm2 or VS Code the page stays blank, and see
  says so on the bottom line)
- Python 3.10+
- Optional: [LibreOffice](https://www.libreoffice.org) for Office documents, ffmpeg for video

## Install

```sh
brew install youkin06/tap/see
```

or `pipx install git+https://github.com/Youkin06/see`.

## Usage

```sh
see file.pdf
see file.pdf --page 5
see movie.mp4
```

| Mouse / key | Effect |
|---|---|
| Swipe left / right, ← / →, Space | Next / previous page |
| Wheel, ↑ / ↓ | Scroll |
| Drag | On text: select and copy it. Elsewhere (and on images): move the page |
| Double-click, Ctrl + wheel, + / − | Zoom |
| 0 / f, w | Fit page, fit width |
| q | Quit |

For video: click or Space to play / pause, click the seek bar to jump, ← / → for 5 seconds,
click the speaker icon at the bottom right (or press m) to turn the sound off and on (dimmed
when the video has no sound). The speaker icon follows [Lucide](https://lucide.dev)'s volume icons.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest -q
```

Release with `scripts/release.sh X.Y.Z "Release name"` (tag, GitHub release and Homebrew formula). See [CHANGELOG.md](CHANGELOG.md) for the history.

## License

MIT
