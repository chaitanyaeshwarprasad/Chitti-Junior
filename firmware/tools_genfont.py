"""Generate an 8-bit alpha bitmap font header for the firmware.

python tools_genfont.py main/font.h 22 FONT        (status text)
python tools_genfont.py main/font_small.h 16 SUB   (subtitles)
"""
import sys

from PIL import Image, ImageDraw, ImageFont

out_path, size, prefix = sys.argv[1], int(sys.argv[2]), sys.argv[3]
f = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", size)
asc, desc = f.getmetrics()
W, H = round(f.getlength("M")), asc + desc
name = prefix.lower() + "_data"
out = [f"// Generated from Consolas Bold {size}px by tools_genfont.py. Glyph alpha 0..255.",
       "#pragma once", "#include <stdint.h>",
       f"#define {prefix}_W {W}", f"#define {prefix}_H {H}", f"#define {prefix}_FIRST 32", f"#define {prefix}_COUNT 95",
       f"static const uint8_t {name}[{prefix}_COUNT][{prefix}_H][{prefix}_W] = {{"]
for c in range(32, 127):
    im = Image.new("L", (W, H), 0)
    ImageDraw.Draw(im).text((0, 0), chr(c), font=f, fill=255)
    px = im.load()
    rows = ["{" + ",".join(str(px[x, y]) for x in range(W)) + "}" for y in range(H)]
    out.append("  {" + ",".join(rows) + "},")
out.append("};")
open(out_path, "w").write("\n".join(out) + "\n")
print(out_path, "cell", W, "x", H)
