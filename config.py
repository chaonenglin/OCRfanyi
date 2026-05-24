import os
import multiprocessing

# --- API ---
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = "deepseek-chat"

# --- OCR ---
OCR_LANG_LIST = ["en"]  # EasyOCR: English only, lightweight
OCR_CONFIDENCE_THRESHOLD = 0.5
OCR_WORKERS = 1  # EasyOCR uses GPU internally; single instance is fine

# --- Capture ---
CAPTURE_INTERVAL_MS = 150  # screenshot interval (faster updates for smooth following)
GRID_CELL_SIZE = 200       # region diff grid cell size
SCENE_CHANGE_THRESHOLD = 0.3  # ratio of changed cells to trigger full rescan

# --- Translation ---
BATCH_DEBOUNCE_MS = 200   # wait for more texts before sending batch
BATCH_MAX_WAIT_MS = 800   # hard cap since first text arrived
BATCH_MAX_SIZE = 20       # max texts per API call

# --- Overlay ---
OVERLAY_FPS = 30
FONT_PATH = "C:/Windows/Fonts/msyh.ttc"  # Microsoft YaHei
FONT_SIZE_RATIO = 0.55  # font size = bbox height * this ratio
BG_ALPHA = 180  # background rectangle opacity (0-255)
ENTRY_TTL_SECONDS = 5.0  # remove overlay if not refreshed within this time

# --- Hotkey ---
HOTKEY_MODIFIERS = 0x0002 | 0x0004  # MOD_CONTROL | MOD_SHIFT
HOTKEY_VK = 0x54  # 'T' key

# --- Performance ---
OCR_CACHE_SIZE = 500
TRANSLATION_CACHE_SIZE = 1000
