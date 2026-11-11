# Changelog

## 0.5.0 — 2026-11-11

### Added
- Video: a sound on/off button at the bottom right of the player (`m` toggles it too): a white
  line speaker icon after Lucide's volume icons, always shown and dimmed for videos without
  sound.
- A demo GIF at the top of the README (`docs/demo.gif`): a blue gradient background,
  sample files in one matching palette, ending on a `❯see` prompt.

- Inside herdr, when the terminal around it doesn't look like Ghostty, kitty or WezTerm, the
  bottom line says so for 10 seconds at start. herdr answers the graphics query itself, so
  see can't tell that the images will be dropped, and the page just stayed blank.

### Changed
- PDF and documents: a left drag that starts on blank paper moves the page (handy when
  zoomed in); a drag that starts on text still selects and copies it, as in Preview.

## 0.4.0 — 2026-11-10

### Added
- `scripts/release.sh X.Y.Z` releases a version in one go: version bump, PR, CI, merge, tag
  and the Homebrew formula update.
- Automated tests (input parsing, file-type detection, swipes, zoom, tile budget, video
  detection, the command line) and GitHub Actions running them on macOS and Linux with
  Python 3.10 and 3.13.
- Install with Homebrew: `brew install youkin06/tap/see` (tap: https://github.com/Youkin06/homebrew-tap).

### Changed
- Python 3.10 or newer is required (3.9 has reached its end of life and is no longer
  available on current CI runners).
- Files of an unsupported kind get a friendlier message that says what see can open:
  `see: model.stl isn't a file see can show. It opens PDFs, images, animated GIFs, videos,
  documents and text.`

### Fixed
- In terminals without the kitty graphics protocol (e.g. macOS Terminal.app) see now says so
  and exits, instead of showing a blank or garbled screen. `SEE_FORCE_GRAPHICS=1` skips the check.

## 0.3.0 — 2026-11-10

### Added
- Images: PNG, JPEG, WebP, AVIF, BMP, TIFF and anything else Pillow reads, plus HEIC/HEIF on
  macOS (converted with `sips`). EXIF rotation is applied.
- Animated GIF, APNG and animated WebP play in a loop, each frame for its own duration.
  Click the picture or press Space to pause; while paused, ← / → step through the frames.
  Zoom and dragging work as for still images, and the bottom line shows `Paused` and the
  frame number.
- Animation frames are rendered once per zoom level and uploaded once, packed into a few
  large images; playing only re-places them, so herdr does not re-send pixels on every
  frame. Animations over the frame budget (14 million pixels) are rendered frame by frame.
- The file type is detected from the content. Unsupported files get a clear error message.
- For images the left drag moves the picture (there is no text to select), and the page
  counter is hidden for single-page documents.
- Documents, text and SVG open too, converted to PDF so pages, zoom and text selection work:
  - Office and iWork files (.docx, .doc, .rtf, .odt, .xlsx, .xls, .pptx, .ppt, .key, .numbers,
    .pages, …) through LibreOffice when it is installed, otherwise on macOS through the
    built-in text system and Quick Look previews, with a Quick Look thumbnail as last resort.
  - Text, Markdown, source code, logs and JSON as pages in a monospace font (Japanese
    included, Shift_JIS files too); CSV/TSV as a table.
  - HTML and SVG, drawn by WebKit on macOS.
  - Conversions are cached, have timeouts, show a short `see: converting …` note, and fail
    with a one-line error.
- Video playback: `see movie.mp4` (MP4, MOV, MKV, WebM, AVI, …) plays the video with its
  sound. ffmpeg decodes the frames at the shown size (up to ~400k pixels, 30 fps) and late
  frames are skipped; ffplay plays the sound and the picture follows its clock. Click the
  video or press Space to pause, click or drag the seek bar on the bottom line, ← / → to
  seek 5 s, q / Esc / ✕ to quit. Needs ffmpeg; plays silently without ffplay.

### Changed
- Renamed from **pdfterm** to **see**: the command is `see`, the package `see-viewer`, the
  repository `Youkin06/see`, and the tuning variables `SEE_TRACE` / `SEE_DEBUG`.

## 0.2.0 — 2026-11-10

### Added
- Select text by dragging; it is copied when you release (pbcopy on a local Mac, OSC 52 elsewhere).
- Turn pages with trackpad swipes: right → left for the next page, left → right for the previous one.
- Double-click zooms in 2× at the pointer, and back to the whole page.
- `v` switches the left drag between selecting text and moving the page; right or middle drag always moves it.
- Pages next to the current one are rendered in advance.
- `PDFTERM_TRACE` and `PDFTERM_DEBUG` environment variables for tuning.

### Changed
- Much smoother scrolling and dragging: the image is uploaded once and only the visible part changes.
- Zooming stretches the current image immediately and re-renders a sharp one when the wheel stops.
- Dragging follows the pointer pixel by pixel where the terminal supports it.
- One swipe turns exactly one page, even with trackpad momentum. Swipes are detected from the rate of scroll events.
- Simpler, English-only screen: the toolbar shows the file name, page, zoom level, Fit, Width and ✕, with no button backgrounds. The bottom line reads `Double-click: zoom   q: quit`.

### Removed
- The ◀ ▶ and − ＋ toolbar buttons, the select/move toolbar button, the go-to-page prompt (`g`), and the Japanese UI.

### Fixed
- Pages no longer disappear when zoomed in or in large windows. Images stay within herdr's graphics budget.
- No more blank flashes when a new image replaces the old one.
- The selection highlight is visible. The screen is no longer erased with ED 2, which also deleted images.

## 0.1.0 — 2026-11-10

- First version: PDF pages as kitty graphics images, wheel scrolling, Ctrl + wheel zoom, dragging, a clickable toolbar and keyboard shortcuts.
