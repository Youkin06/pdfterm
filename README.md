# see

A mouse-friendly viewer for the terminal. It opens PDF files, images and videos; animated GIFs
are coming next. Everything is drawn as real images with the
[kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/), and you use it
like a GUI viewer: swipe to turn pages, drag to select and copy text, double-click to zoom.

ターミナルの中で動く、マウスで操作できるビューアーです（PDF、画像、動画に対応。GIF アニメも対応予定）。（[日本語の説明](#日本語)）

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
| Animated GIF | Shows the first frame; animation is planned |
| Video: MP4, MOV, MKV, WebM, AVI, … | ✅ Supported, with sound (needs [ffmpeg](https://ffmpeg.org)) |

## Requirements

- A terminal that supports the kitty graphics protocol, such as
  [Ghostty](https://ghostty.org), [kitty](https://sw.kovidgoyal.net/kitty/) or
  [WezTerm](https://wezterm.org). It also works inside [herdr](https://herdr.dev) panes.
- Python 3.9 or newer
- For video: [ffmpeg](https://ffmpeg.org) (`ffmpeg` and `ffprobe`; `ffplay` for the sound),
  for example `brew install ffmpeg`. PDFs and images do not need it.

## Install

The command is `see`; the Python package is called `see-viewer` (the name `see` is taken on
PyPI).

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
see movie.mp4            # play a video
```

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

A video starts playing at once, with its sound. The bottom line shows ▶ while playing (❚❚
when paused), the current time / the length, and a seek bar.

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
- Each rendered region is uploaded to the terminal once. Scrolling and dragging only change
  which part of it is shown, so they send a few bytes instead of a new image. Zooming
  stretches the current image at once and renders a sharp one when the wheel stops.
- Images are kept under 4 million pixels (about 1.6 screens around the view). Multiplexers
  such as herdr re-send images uncompressed and drop ones over their limit.
- Rendering runs in a background thread, and the pages before and after the current one are
  rendered while idle.
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
.venv/bin/pip install -e .
.venv/bin/see some.pdf
```

Two environment variables help when tuning input and rendering:

- `SEE_TRACE=/path/to/log` logs every input event and the swipe decisions.
- `SEE_DEBUG=/path/to/log` writes the detected cell size, mouse mode, bytes sent and the
  largest image on exit.

See [CHANGELOG.md](CHANGELOG.md) for the history.

## License

MIT

---

## 日本語

ターミナル（Ghostty など）の中で、PDF や画像をきれいに表示するビューアーです。GUI のアプリのように、マウスやトラックパッドで操作できます。

### 対応しているファイル

| 種類 | 状態 |
|---|---|
| PDF | ✅ 対応 |
| 画像（PNG、JPEG、WebP、AVIF、BMP、TIFF など） | ✅ 対応（Pillow で読めるもの） |
| iPhone の写真（HEIC / HEIF） | ✅ Mac で対応（Mac 標準の `sips` で変換） |
| GIF アニメ | 最初のコマだけ表示。アニメーションは対応予定 |
| 動画（MP4、MOV、MKV、WebM、AVI など） | ✅ 対応・音声つき（ffmpeg が必要） |

### 必要なもの

- kitty 画像方式に対応したターミナル（Ghostty、kitty、WezTerm など）。herdr のペインの中でも使えます。
- Python 3.9 以上
- 動画を見るときは ffmpeg（`ffmpeg` と `ffprobe`、音を出すには `ffplay`）。`brew install ffmpeg` などで入れてください。PDF と画像には不要です。

### インストール

```sh
pipx install git+https://github.com/Youkin06/see
```

アンインストールは `pipx uninstall see-viewer` です。

### 使い方

```sh
see ファイル.pdf
see ファイル.pdf --page 5    # 5ページ目から開く
see 写真.png
see 動画.mp4             # 動画を再生
```

**画面**

- 上の行：ファイル名、ページ（今のページ / 全ページ数。2ページ以上の PDF のみ）、倍率、**Fit**（ページ全体）、**Width**（幅に合わせる）、**✕**（終了）。今のモードには下線が付きます。ウィンドウが狭いときは、重要度の低いものから省きます。
- 下の行：`Double-click: zoom   q: quit` と、「Copied 34 characters」のような短いお知らせ。

**マウス・トラックパッド**

| 操作 | 動き |
|---|---|
| 右→左 / 左→右にスワイプ | 次 / 前のページ |
| 上下にスワイプ、ホイール | スクロール（ページの端や、ページ全体が見えているときは1ページめくる） |
| ドラッグ | PDF：文字を選択（離すと自動でコピー）。画像：画像を動かす |
| ダブルクリック | その場所を2倍に拡大 / ページ全体に戻す |
| Ctrl + ホイール | マウスの位置を中心にズーム |
| 右ドラッグ | 拡大したページを動かす |
| Shift + ホイール | 左右スワイプと同じ（ページが横にはみ出していれば左右にスクロール、そうでなければページをめくる） |
| Fit / Width / ✕ をクリック | ページ全体 / 幅に合わせる / 終了 |

1回のスワイプでめくれるのは1ページだけです。慣性でスクロールが続いても、余分にめくれません。

**キーボード**

| キー | 動き |
|---|---|
| → / ←、l / h、PageDown / PageUp、Space | 次 / 前のページ |
| ↓ / ↑、j / k | スクロール |
| Home / End | 最初 / 最後のページ |
| + / − | ズームイン / アウト |
| 0 または f | ページ全体（Fit） |
| w | 幅に合わせる（Width） |
| v | 左ドラッグを「文字の選択」と「ページの移動」で切り替え |
| Esc | 選択を解除（何も選択していなければ終了） |
| q | 終了 |

コピーは、Mac では `pbcopy`、ssh 先などでは OSC 52 を使います。Command+C / Command+A を押したときに Alt+C / Alt+A を送るようにターミナルを設定していれば、それぞれ「コピー」「ページ全体を選択」として使えます。

**動画**

開くとすぐに音声つきで再生が始まります。下の行には、再生中は ▶（一時停止中は ❚❚）、今の時間 / 全体の長さ、シークバーが出ます。

| 操作 | 動き |
|---|---|
| 動画をクリック、Space | 再生 / 一時停止（最後まで行ったあとの Space は最初から再生） |
| シークバーをクリック・ドラッグ | その位置へ移動 |
| → / ← | 5秒進む / 戻る |
| q、Esc、✕ をクリック | 終了 |

**できないこと**

- **スキャンした PDF**：中身が文字ではなく写真なので、文字を選べません。
- **画像の倍率**：画像の1画素が画面の1画素になる大きさが 100% です。小さい画像を大きく拡大したときは、ドット絵やアイコンがぼやけないよう、画素の角を残して拡大します。
- **ピンチでのズーム**：ターミナルはピンチ操作をアプリに渡さないため使えません。ダブルクリック、Ctrl + ホイール、+ / − キーを使ってください。
- herdr などの中では、新しいページの画像を外側のターミナルに渡すのに少し時間がかかります。スクロールやドラッグは送ってある画像を動かすだけで、前後のページも先に用意しているので、待ち時間を感じるのは主にズームのときです。
- **動画の画質**：1コマあたり約40万画素（例：840 × 470）、毎秒30コマまでに縮めてから画面いっぱいに引き伸ばすので、動画プレーヤーよりは少しぼやけます。間に合わないコマは飛ばします。シークのあとは最初のコマができてから映像と音が始まるので、キーフレームの少ないファイルでは少し待つことがあります。音量の調整、字幕、動画の拡大はできません。`ffplay` がないときは音なしで再生します。
