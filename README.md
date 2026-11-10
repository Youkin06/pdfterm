# pdfterm

A mouse-friendly PDF viewer for the terminal. Pages are drawn as real images with the
[kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/), and you use it
like a GUI viewer: swipe to turn pages, drag to select and copy text, double-click to zoom.

ターミナルの中で動く、マウスで操作できる PDF ビューアーです。（[日本語の説明](#日本語)）

```
 report.pdf   3/12   82%   Fit   Width                                  ✕
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│                           (the PDF page)                             │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
 Double-click: zoom   q: quit
```

## Requirements

- A terminal that supports the kitty graphics protocol, such as
  [Ghostty](https://ghostty.org), [kitty](https://sw.kovidgoyal.net/kitty/) or
  [WezTerm](https://wezterm.org). It also works inside [herdr](https://herdr.dev) panes.
- Python 3.9 or newer

## Install

```sh
pipx install git+https://github.com/Youkin06/pdfterm
# or
uv tool install git+https://github.com/Youkin06/pdfterm
```

Uninstall with `pipx uninstall pdfterm` (or `uv tool uninstall pdfterm`).

## Usage

```sh
pdfterm file.pdf
pdfterm file.pdf --page 5    # open at page 5
```

### Screen

- **Top bar:** file name, current page / page count, zoom level, **Fit** (whole page),
  **Width** (fit the page width) and **✕** (quit). The active mode is underlined. In narrow
  windows the less important parts are hidden.
- **Bottom line:** `Double-click: zoom   q: quit`, and short messages such as
  `Copied 34 characters`.

### Mouse and trackpad

| Action | Effect |
|---|---|
| Swipe right → left / left → right | Next / previous page |
| Swipe or wheel up / down | Scroll the page; turns one page at the top or bottom, or when the whole page is visible |
| Drag | Select text; it is copied to the clipboard when you release |
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
over SSH). If your terminal sends Alt+C / Alt+A for Command+C / Command+A, pdfterm treats
them as copy and as "select the whole page".

### Limitations

- **Scanned PDFs** contain pictures of text, not text, so nothing can be selected on them.
- **Pinch to zoom** is not possible: terminals do not pass pinch gestures to programs. Use a
  double-click, Ctrl + wheel or the + / − keys.
- Inside a multiplexer such as herdr, every new page image is passed on to the outer
  terminal, which costs a moment. Scrolling and dragging only move the image already sent,
  and neighbouring pages are prepared in advance, so this mainly shows when zooming.

## How it works

- Pages are rendered with [PDFium](https://pdfium.googlesource.com/pdfium/) (through
  [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)) at the current zoom level, so
  zoomed-in text stays sharp.
- Each rendered region is uploaded to the terminal once. Scrolling and dragging only change
  which part of it is shown, so they send a few bytes instead of a new image. Zooming
  stretches the current image at once and renders a sharp one when the wheel stops.
- Images are kept under 4 million pixels (about 1.6 screens around the view). Multiplexers
  such as herdr re-send images uncompressed and drop ones over their limit.
- Rendering runs in a background thread, and the pages before and after the current one are
  rendered while idle.
- Trackpad swipes are recognised from the rate of scroll events: a page turns when the rate
  rises sharply, and turning re-arms when it has fallen well below its peak.

## Development

```sh
git clone https://github.com/Youkin06/pdfterm
cd pdfterm
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pdfterm some.pdf
```

Two environment variables help when tuning input and rendering:

- `PDFTERM_TRACE=/path/to/log` logs every input event and the swipe decisions.
- `PDFTERM_DEBUG=/path/to/log` writes the detected cell size, mouse mode, bytes sent and the
  largest image on exit.

See [CHANGELOG.md](CHANGELOG.md) for the history.

## License

MIT

---

## 日本語

ターミナル（Ghostty など）の中で、PDF をきれいな画像で表示するビューアーです。GUI のアプリのように、マウスやトラックパッドで操作できます。

### 必要なもの

- kitty 画像方式に対応したターミナル（Ghostty、kitty、WezTerm など）。herdr のペインの中でも使えます。
- Python 3.9 以上

### インストール

```sh
pipx install git+https://github.com/Youkin06/pdfterm
```

アンインストールは `pipx uninstall pdfterm` です。

### 使い方

```sh
pdfterm ファイル.pdf
pdfterm ファイル.pdf --page 5    # 5ページ目から開く
```

**画面**

- 上の行：ファイル名、ページ（今のページ / 全ページ数）、倍率、**Fit**（ページ全体）、**Width**（幅に合わせる）、**✕**（終了）。今のモードには下線が付きます。ウィンドウが狭いときは、重要度の低いものから省きます。
- 下の行：`Double-click: zoom   q: quit` と、「Copied 34 characters」のような短いお知らせ。

**マウス・トラックパッド**

| 操作 | 動き |
|---|---|
| 右→左 / 左→右にスワイプ | 次 / 前のページ |
| 上下にスワイプ、ホイール | スクロール（ページの端や、ページ全体が見えているときは1ページめくる） |
| ドラッグ | 文字を選択（離すと自動でコピー） |
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

**できないこと**

- **スキャンした PDF**：中身が文字ではなく写真なので、文字を選べません。
- **ピンチでのズーム**：ターミナルはピンチ操作をアプリに渡さないため使えません。ダブルクリック、Ctrl + ホイール、+ / − キーを使ってください。
- herdr などの中では、新しいページの画像を外側のターミナルに渡すのに少し時間がかかります。スクロールやドラッグは送ってある画像を動かすだけで、前後のページも先に用意しているので、待ち時間を感じるのは主にズームのときです。
