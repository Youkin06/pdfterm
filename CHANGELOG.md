# Changelog

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
