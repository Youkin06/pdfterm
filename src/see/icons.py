"""Small line icons drawn as images, for the video player's controls.

The speaker shapes follow Lucide's "volume-2" and "volume-x" icons (https://lucide.dev,
ISC license): a 24x24 grid, 2-unit strokes with round caps, single colour.
"""

import math

from PIL import Image as PILImage
from PIL import ImageDraw

from .render import Image

SUPERSAMPLE = 4


def sound_icon(state, width, height):
    """A white speaker icon filling width x height pixels (one or two terminal cells).

    state: "on" (speaker with sound waves), "off" (speaker with a cross), or "none"
    (the "off" icon dimmed, for videos without sound).
    """
    s = SUPERSAMPLE
    W, H = width * s, height * s
    im = PILImage.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    alpha = 110 if state == "none" else 255
    colour = (255, 255, 255, alpha)
    size = min(W, H) * 0.86  # the 24-unit grid, centred
    ox, oy = (W - size) / 2, (H - size) / 2
    unit = size / 24
    stroke = max(1, round(2 * unit))

    def p(x, y):
        return ox + x * unit, oy + y * unit

    def dot(x, y):  # round cap / join
        cx, cy = p(x, y)
        r = stroke / 2
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)

    def line(points):
        d.line([p(x, y) for x, y in points], fill=colour, width=stroke, joint="curve")
        for x, y in points:
            dot(x, y)

    def arc(cx, cy, r, a0, a1):
        # Pillow draws the stroke inward from the box, so grow the box by half a stroke
        # to keep the stroke centred on the radius (and on the round caps below)
        x0, y0 = p(cx - r, cy - r)
        x1, y1 = p(cx + r, cy + r)
        h = stroke / 2
        d.arc([x0 - h, y0 - h, x1 + h, y1 + h], a0, a1, fill=colour, width=stroke)
        for a in (a0, a1):
            dot(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))

    # speaker body: the box on the left and the cone opening to the right
    line([(3, 9), (6, 9), (11, 4.8), (11, 19.2), (6, 15), (3, 15), (3, 9)])
    if state == "on":
        arc(12, 12, 5, -37, 37)  # "M16 9a5 5 0 0 1 0 6"
        arc(13, 12, 9, -45, 45)  # "M19.364 18.364a9 9 0 0 0 0-12.728"
    else:
        line([(22, 9), (16, 15)])
        line([(16, 9), (22, 15)])
    im = im.resize((width, height), PILImage.LANCZOS)
    return Image.from_rows(im.tobytes(), width, height, width * 4, 4)
