# pdfterm

A mouse-friendly PDF viewer for the terminal. Pages are drawn as real images with the
[kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/), and you use it
like a GUI viewer: scroll with the wheel, zoom with Ctrl+wheel, drag to move around, and
click the toolbar buttons.

ターミナルの中で動く、マウスで操作できる PDF ビューアーです。（日本語の説明は下にあります）

```
 ◀   ▶   1/3   −   78%   ＋   Fit   Width   report.pdf        ✕ Quit
┌──────────────────────────────────────────────────────────────────┐
│                         (the PDF page)                           │
└──────────────────────────────────────────────────────────────────┘
 Wheel: scroll · Ctrl+wheel: zoom · Drag: move · Double-click: zoom
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
| Wheel | Scroll the page; flips pages at the top/bottom (or when the whole page is visible) |
| Ctrl + wheel | Zoom in/out around the mouse pointer |
| Shift + wheel, horizontal wheel | Scroll left/right |
| Drag | Move the page around (like a hand tool) |
| Double-click | Zoom in 2× at that spot / back to whole page |
| Toolbar | ◀ ▶ previous/next page · page number: go to page · − ＋ zoom · Fit · Width · ✕ quit |

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
| q, Esc | Quit |

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
| ホイール | スクロール（ページの端やページ全体が見えているときはページ送り） |
| Ctrl + ホイール | マウスの位置を中心にズーム |
| Shift + ホイール | 左右にスクロール |
| ドラッグ | ページを動かす（手のひらツール） |
| ダブルクリック | その場所を2倍に拡大 / ページ全体に戻す |
| 上のボタン | ◀ ▶ ページ送り · ページ番号を押すとページ指定 · − ＋ ズーム · 全体 · 幅 · ✕ 閉じる |
| g → 数字 → Enter | ページを指定して移動 |
| q | 終了 |

kitty 画像方式に対応したターミナル（Ghostty、kitty、WezTerm など）で動きます。herdr のペインの中でも使えます。
