# see

[English](README.md)

ターミナルの中で動く、マウスで操作できるビューアーです。PDF、画像、Office 文書、テキスト、アニメーション、音つきの動画を、[kitty 画像方式](https://sw.kovidgoyal.net/kitty/graphics-protocol/)で本物の画像として表示します。スワイプでページをめくり、ドラッグで文字を選んでコピーし、ダブルクリックで拡大できます。

![Ghostty の中で see が PDF・写真・GIF・動画を開いているところ](docs/demo.gif)

## 対応しているファイル

- PDF、画像（Pillow で読めるもの。HEIC は Mac のみ）
- テキスト、Markdown、ソースコード、JSON、CSV / TSV
- Word、Excel、PowerPoint、Keynote、Numbers、Pages、RTF、OpenDocument（LibreOffice があればそれで、なければ Mac の標準機能で表示）
- HTML、SVG（Mac のみ）
- アニメーション（GIF、APNG、WebP）
- 動画（MP4、MOV、MKV、WebM、AVI など。ffmpeg が必要）

## 必要なもの

- kitty 画像方式に対応したターミナル（Ghostty、kitty、WezTerm。その中の herdr のペインでも可）
- Python 3.10 以上
- 任意：Office 文書には LibreOffice、動画には ffmpeg

## インストール

```sh
brew install youkin06/tap/see
```

または `pipx install git+https://github.com/Youkin06/see`。

## 使い方

```sh
see ファイル.pdf
see ファイル.pdf --page 5
see 動画.mp4
```

| マウス・キー | 動き |
|---|---|
| 左右にスワイプ、← / →、Space | 次 / 前のページ |
| ホイール、↑ / ↓ | スクロール |
| ドラッグ | 文字を選択してコピー（画像は移動） |
| ダブルクリック、Ctrl + ホイール、+ / − | ズーム |
| 0 / f、w | ページ全体、幅に合わせる |
| q | 終了 |

動画：クリックか Space で再生 / 一時停止、シークバーをクリックで移動、← / → で 5 秒移動。
