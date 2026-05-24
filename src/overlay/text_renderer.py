import numpy as np
from PIL import Image, ImageDraw, ImageFont
from config import FONT_PATH, FONT_SIZE_RATIO, BG_ALPHA


class TextRenderer:
    def __init__(self, screen_width, screen_height):
        self.width = screen_width
        self.height = screen_height
        self._font_cache = {}

    def _get_font(self, size):
        if size not in self._font_cache:
            try:
                self._font_cache[size] = ImageFont.truetype(FONT_PATH, size)
            except OSError:
                self._font_cache[size] = ImageFont.load_default()
        return self._font_cache[size]

    def render(self, entries: list) -> np.ndarray:
        """
        entries: list of (bbox_xywh, translated_text)
        Returns BGRA uint8 numpy array (H, W, 4), transparent where no text.
        """
        img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        for (x, y, w, h), text in entries:
            font_size = max(10, int(h * FONT_SIZE_RATIO))
            font = self._get_font(font_size)

            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]

            pad = 4
            bg_x1 = x
            bg_y1 = y - th - pad * 2
            bg_x2 = max(x + tw + pad * 2, x + w)
            bg_y2 = y

            bg_y1 = max(0, bg_y1)
            bg_x2 = min(self.width, bg_x2)

            draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2],
                           fill=(30, 30, 30, BG_ALPHA))
            draw.text((bg_x1 + pad, bg_y1 + pad), text,
                      font=font, fill=(255, 255, 255, 255))

        return np.array(img)
