"""Generate a professional clipboard-sync icon (.ico) for the app."""
import struct
from io import BytesIO
from PIL import Image, ImageDraw

SIZES = [16, 32, 48, 64, 128, 256]

def _draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    p = lambda x: round(x * size / 256)

    cx, cy = size // 2, size // 2
    clip_w = p(180)
    clip_h = p(224)
    clip_x0 = cx - clip_w // 2
    clip_y0 = cy - clip_h // 2 + p(16)
    clip_x1 = clip_x0 + clip_w
    clip_y1 = clip_y0 + clip_h
    r = p(20)

    # Shadow
    draw.rounded_rectangle(
        (clip_x0 + p(2), clip_y0 + p(2), clip_x1 + p(2), clip_y1 + p(2)),
        radius=r, fill=(0, 0, 0, 50),
    )

    # Clip body (dark blue)
    draw.rounded_rectangle(
        (clip_x0, clip_y0, clip_x1, clip_y1),
        radius=r, fill=(30, 58, 95, 255),
    )

    # Inner lighter face
    inner_color = (45, 78, 120, 255)
    inner_m = p(8)
    draw.rounded_rectangle(
        (clip_x0 + inner_m, clip_y0 + inner_m + p(14),
         clip_x1 - inner_m, clip_y1 - inner_m),
        radius=r // 2, fill=inner_color,
    )

    # Clip bar
    clip_bar_w = p(50)
    clip_bar_h = p(12)
    clip_bar_x0 = cx - clip_bar_w // 2
    clip_bar_y0 = clip_y0 - p(18)
    clip_bar_x1 = clip_bar_x0 + clip_bar_w
    clip_bar_y1 = clip_bar_y0 + clip_bar_h
    draw.rounded_rectangle(
        (clip_bar_x0 + p(1), clip_bar_y0 + p(1),
         clip_bar_x1 + p(1), clip_bar_y1 + p(1)),
        radius=p(6), fill=(0,0,0,30),
    )
    draw.rounded_rectangle(
        (clip_bar_x0, clip_bar_y0, clip_bar_x1, clip_bar_y1),
        radius=p(6), fill=(180, 190, 200, 255),
    )
    draw.rounded_rectangle(
        (clip_bar_x0 + p(4), clip_bar_y0 + p(2),
         clip_bar_x1 - p(4), clip_bar_y0 + p(6)),
        radius=p(3), fill=(210, 220, 230, 180),
    )

    # Clip curves
    curve_color = (160, 170, 180, 255)
    lx, rx = clip_bar_x0 + p(4), clip_bar_x1 - p(4)
    cw = max(1, p(4))
    draw.arc((lx - p(8), clip_bar_y1 - p(4), lx + p(8), clip_y0 + p(10)),
             180, 270, fill=curve_color, width=cw)
    draw.arc((rx - p(8), clip_bar_y1 - p(4), rx + p(8), clip_y0 + p(10)),
             270, 360, fill=curve_color, width=cw)

    # Sync arrows
    scx, scy = cx, clip_y0 + clip_h // 2 + p(4)
    arrow_r = p(50)
    arrow_w = max(1, p(10))
    arc1_color = (140, 200, 230, 255)
    arc2_color = (100, 170, 210, 255)

    # Upper arc
    draw.arc(
        (scx - arrow_r, scy - arrow_r + p(4),
         scx + arrow_r, scy + arrow_r + p(4)),
        200, 340, fill=arc1_color, width=arrow_w,
    )
    # Arrow head 1
    ah1_x = scx + round(arrow_r * 0.85)
    ah1_y = scy - round(arrow_r * 0.85 * 0.36) + p(4)
    draw.polygon([
        (ah1_x + p(4), ah1_y - p(4)),
        (ah1_x - p(6), ah1_y + p(4)),
        (ah1_x + p(2), ah1_y + p(8)),
    ], fill=arc1_color)

    # Lower arc
    draw.arc(
        (scx - arrow_r, scy - arrow_r + p(4),
         scx + arrow_r, scy + arrow_r + p(4)),
        20, 160, fill=arc2_color, width=arrow_w,
    )
    # Arrow head 2
    ah2_x = scx - round(arrow_r * 0.85)
    ah2_y = scy + round(arrow_r * 0.85 * 0.36) + p(4)
    draw.polygon([
        (ah2_x - p(4), ah2_y + p(4)),
        (ah2_x + p(6), ah2_y - p(4)),
        (ah2_x - p(2), ah2_y - p(8)),
    ], fill=arc2_color)

    # Center dot
    dot_r = max(1, p(4))
    draw.ellipse(
        (scx - dot_r, scy + p(12) - dot_r,
         scx + dot_r, scy + p(12) + dot_r),
        fill=(200, 220, 240, 200),
    )
    return img


def _png_bytes(im: Image.Image) -> bytes:
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def main():
    images = [_draw_icon(s) for s in SIZES]
    png_data = [_png_bytes(im) for im in images]

    # ICO header
    count = len(SIZES)
    header = struct.pack("<HHH", 0, 1, count)

    # Directory entries + image data
    offset = 6 + 16 * count  # header + entries
    entries = []
    data_blocks = []

    for i, s in enumerate(SIZES):
        w = s if s < 256 else 0
        h = s if s < 256 else 0
        b = png_data[i]
        entries.append(struct.pack(
            "<BBBBHHII",
            w, h, 0, 0,  # width, height, colorCount, reserved
            0, 0,  # planes=0, bitCount=0 for PNG
            len(b), offset,
        ))
        data_blocks.append(b)
        offset += len(b)

    with open("clipboard.ico", "wb") as f:
        f.write(header)
        for e in entries:
            f.write(e)
        for d in data_blocks:
            f.write(d)

    print(f"clipboard.ico generated: {count} sizes ({sum(len(d) for d in data_blocks) + len(header) + len(entries)*16} bytes)")


if __name__ == "__main__":
    main()
