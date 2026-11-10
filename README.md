# pdfterm

A mouse-friendly PDF viewer for the terminal. Pages are drawn as real images with the
[kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/), and you use it
like a GUI viewer: drag to select and copy text, scroll with the wheel, zoom with
Ctrl+wheel, and click the toolbar buttons.

ターミナルの中で動く、マウスで操作できる PDF ビューアーです。（日本語の説明は下にあります）

```
 ◀   ▶   1/3   Ｉ Select   −   78%   ＋   Fit   Width   report.pdf  ✕ Quit
┌──────────────────────────────────────────────────────────────────┐
│                         (the PDF page)                           │
└──────────────────────────────────────────────────────────────────┘
 Drag: select text (copied) · Wheel: scroll · Ctrl+wheel: zoom · v: select/move
```

## Requirements

- A terminal that supports the kitty graphics protocol:
  [Ghostty](https://ghostty.org), [kitty](https://sw.kovidgoyal.net/kitty/),
  [WezTerm](https://wezterm.org) and others. It also works inside
  [herdr](https://herdr.dev) panes.
- Python 3.9+

## Install

```sh
pipx install git+https://github.com/Youkin06/pdfterm
# or
uv tool install git+https://github.com/Youkin06/pdfterm
```

## Usage

```sh
pdfterm file.pdf
pdfterm file.pdf --page 5
```

### Mouse

| Action | Effect |
|---|---|
| Drag | Select text; it is copied to the clipboard when you release |
| Double-click | Select (and copy) a word |
| Wheel | Scroll the page; flips pages at the top/bottom (or when the whole page is visible) |
| Ctrl + wheel | Zoom in/out around the mouse pointer |
| Shift + wheel, horizontal wheel | Scroll left/right |
| Right or middle drag | Move the page around |
| Toolbar | ◀ ▶ previous/next page · page number: go to page · Ｉ/✋ switch between selecting text and moving the page · − ＋ zoom · Fit · Width · ✕ quit |

With the move tool (✋, or press `v`), a left drag moves the page and a double-click zooms
in 2× / back to the whole page.

### Keyboard

| Key | Effect |
|---|---|
| → / ← , l / h, PageDown / PageUp, Space | Next / previous page |
| ↓ / ↑ , j / k | Scroll |
| Home / End | First / last page |
| + / − | Zoom in / out |
| 0 or f | Fit whole page |
| w | Fit width |
| g, then a number, Enter | Go to page |
| v | Switch between selecting text and moving the page |
| Esc | Clear the selection (quits when nothing is selected) |
| q | Quit |

Copying uses `pbcopy` on a local Mac and the OSC 52 escape sequence elsewhere (e.g. over
SSH). If you map Command+C / Command+A in your terminal to send Alt+C / Alt+A, pdfterm
treats them as copy / select the whole page.

The UI language follows your system (English or Japanese). Set `PDFTERM_LANG=en` or
`PDFTERM_LANG=ja` to choose.

## How it works

Pages are rendered with [PDFium](https://pdfium.googlesource.com/pdfium/) (through
[pypdfium2](https://github.com/pypdfium2-team/pypdfium2)). Only the visible part of the page is
rendered, at the current zoom level, so zoomed-in text stays sharp.

## License

MIT

---

## 日本語

ターミナル（Ghostty など）の中で、PDF を画像として表示するビューアーです。GUI のアプリのように、マウスで操作できます。

### インストール

```sh
pipx install git+https://github.com/Youkin06/pdfterm
```

### 使い方

```sh
pdfterm ファイル.pdf
```

| 操作 | 動き |
|---|---|
| ドラッグ | 文字を選択（離すと自動でコピー） |
| ダブルクリック | 単語を選択してコピー |
| ホイール | スクロール（ページの端やページ全体が見えているときはページ送り） |
| Ctrl + ホイール | マウスの位置を中心にズーム |
| Shift + ホイール | 左右にスクロール |
| 右ドラッグ | ページを動かす |
| 上のボタン | ◀ ▶ ページ送り · ページ番号を押すとページ指定 · Ｉ/✋ 文字選択と移動の切り替え · − ＋ ズーム · 全体 · 幅 · ✕ 閉じる |
| v | 文字選択 / 移動 の切り替え（移動のときは、左ドラッグでページを動かし、ダブルクリックで拡大） |
| g → 数字 → Enter | ページを指定して移動 |
| q | 終了 |

kitty 画像方式に対応したターミナル（Ghostty、kitty、WezTerm など）で動きます。herdr のペインの中でも使えます。
