# OCRfanyi — Real-time Screen OCR Translation Overlay

## What this project does

A Windows desktop app that captures a selected screen region, OCRs text with EasyOCR (GPU, English + Japanese), translates to Chinese via DeepSeek API, and overlays the translation on screen with a transparent click-through window.

Two entry points:
- **app.py** (main) — tkinter control panel with full UI, auto-refresh, dual hotkeys, pause/resume
- **demo.py** — lightweight hotkey-driven single-shot tool (Ctrl+Shift+R select, Ctrl+Shift+T hide)

## Architecture (app.py)

```
app.py (tkinter main loop + Win32 hotkey daemon thread)
 ├── RoundedButton — tk.Canvas-based rounded button with hover effect
 ├── App class (self-contained, ~940 lines)
 │    ├── Win32 hotkey thread: message-only helper window registers 2 hotkeys via
 │    │   RegisterHotKey (single-key, MOD_NOREPEAT). threading.Event signals to
 │    │   main thread via root.after(50ms) polling.
 │    ├── OCR: EasyOCREngine (GPU) with preprocessing pipeline:
 │    │   grayscale → contrast 1.8x → sharpen → auto-upscale (target 1200px max dim)
 │    │   → readtext(text_threshold=0.4, low_text=0.2) → reading-order sort
 │    ├── Translate: aiohttp → DeepSeek Chat API, texts joined with |||
 │    │   separator, single batch per trigger (auto-refresh accumulates batches)
 │    ├── Overlay: PIL-rendered text on BGRA bitmap → OverlayWindow
 │    │   (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE)
 │    └── UI: PIL-composited background on Canvas — vertical image from beijingtu/ +
 │         semi-transparent card overlays (radius=8, alpha=80). All text is
 │         Canvas-native (transparent, shows bg through). Interactive widgets
 │         placed via create_window() with light warm bg. No Frame wrappers.
```

## Key files

| File | Purpose |
|------|---------|
| `app.py` | Main entry: tkinter control panel, hotkeys, OCR pipeline, auto-refresh, UI |
| `demo.py` | Alternative entry: single-shot region translate (no UI) |
| `config.py` | All tunable parameters (API, OCR, capture intervals, overlay settings) |
| `src/capture/screen_capture.py` | MSS wrapper, returns np.ndarray (BGRA) |
| `src/ocr/paddle_ocr.py` | EasyOCR engine with preprocessing (filename is historical, not PaddleOCR). Uses local `models/` dir, no network download needed. |
| `src/overlay/overlay_window.py` | Pure ctypes Win32 layered transparent window |
| `src/overlay/text_renderer.py` | PIL-based text rendering on BGRA bitmap |
| `src/overlay/selection_window.py` | Mouse-drag region selection overlay |
| `beijingtu/` | Background images for UI (fe1f8d99b732b01cb4af12a99f0fe576.jpg is active) |
| `models/` | EasyOCR model files (~110MB): craft_mlt_25k, english_g2, japanese_g2 |
| `app_icon.ico` | App icon (converted from `beijingtu/应用图标.png`, 256×256) |
| `bagpag/OCRfanyi/` | PyInstaller `--onedir` distributable output (~4.4GB). Contains `OCRfanyi.exe` + `_internal/` folder. |

## Removed files (do NOT recreate)

- `main.py` — replaced by app.py
- `src/pipeline/coordinator.py` — logic integrated into App class
- `src/pipeline/bounded_queue.py` — no longer needed
- `src/translator/deepseek_client.py` — inlined into app.py
- `src/translator/batch_manager.py` — inlined into app.py
- `src/capture/region_tracker.py` — grid diff not used in control panel mode
- `src/ocr/ocr_pool.py` — single EasyOCR instance is sufficient

## Important implementation details

- **GPU**: EasyOCR uses `gpu=True`, PyTorch 2.12.0+cu126, GTX 1650. Do NOT change to CPU-only.
- **API key**: Set via `DEEPSEEK_API_KEY` env var, NOT hardcoded in config.py.
- **64-bit Win32 ctypes**: Hotkey thread uses `WNDCLASSEXW` with message-only window (`HWND_MESSAGE`). WPARAM/LPARAM in `MSG` struct use `wintypes.WPARAM`/`wintypes.LPARAM`. Window class name is `"OCRfanyiHotkey"`.
- **Translation**: Texts joined with `|||`, system prompt enforces same delimiter in response, fallback to original text if response has fewer lines. SYSTEM_PROMPT is generated dynamically from the selected language's `prompt_lang` field. In demo.py, set `OCR_SOURCE_LANG=ja` env var to use Japanese OCR.
- **Hotkeys**: Two single-key hotkeys registered via `RegisterHotKey` with `MOD_NOREPEAT` (0x4000):
  - Translate key: triggers OCR + translate on the selected region
  - Hide key: hides the overlay
  - Both keys are user-customizable at runtime (captured via tkinter KeyPress event)
  - Hotkey thread is a daemon thread — killed on app exit
- **Anti-spam**: `_translating` boolean flag blocks concurrent translate requests. `_switching_lang` flag blocks OCR/translate during language transitions. Queued hotkey presses during translation are discarded via `_hk_trigger_trans.clear()` in finally.
- **Pause/resume**: Calls `_stop_hotkey_thread()` to unregister hotkeys (returning keys to system) and `_start_hotkey_thread()` to recapture. Label: "释放按键原功能".
- **OCR preprocessing** (in `EasyOCREngine._preprocess()`):
  1. BGRA → RGB → grayscale (PIL `convert("L")`)
  2. Contrast enhancement 1.8x
  3. Sharpen filter
  4. Auto-upscale via `mag_ratio` for small regions (target ~1200px max dimension)
  5. `readtext(text_threshold=0.4, low_text=0.2)` — lower thresholds improve Japanese kanji/kana detection
  6. Reading-order sort: top-to-bottom rows (row tolerance = 0.5× avg height), left-to-right within row
- **UI compositing**: PIL creates composite image: background photo + semi-transparent card overlays (`(255, 248, 240, 80)` — ~31% opacity) with rounded corners (radius=8). This composite is set as the Canvas background via `create_image`. All static text is Canvas-native (`create_text()`) — inherently transparent, shows the background image through. Dynamic text (status, hotkey names, auto count) uses `StringVar.trace_add("write")` to auto-sync to Canvas text items. Interactive widgets placed on Canvas via `create_window()` with light warm bg (`#faf5f0`). No tkinter Frame wrappers anywhere. All fonts are black bold. Window size: 460×584. Root bg: `#2a1f1a`.
- **"翻译中..." overlay**: Rendered as a large PIL text bitmap on the overlay window while API call is in flight, centered on the selected region.
- **Auto-refresh mode**: Continuously captures at configurable interval, diffs via MD5 to skip unchanged frames, batches translations.
- **Language switching**: Combobox in UI switches between English ("英语 → 中文") and Japanese ("日语 → 中文") OCR. Switch runs in background thread via `ThreadPoolExecutor` (model loading ~10s). Atomic engine swap: create new engine first, assign to `self.ocr_engine`, then `del old` + `torch.cuda.empty_cache()`. `_switching_lang` boolean flag blocks OCR/translate operations during transition (checked in hotkey, auto-refresh, and select paths). Combobox disabled during switch to prevent re-entry. Status bar shows progress.
- **Local models**: EasyOCR models bundled in `models/` (~110MB). `EasyOCREngine` sets `download_enabled=False` and points to local dir. No network needed for OCR.

## PyInstaller packaging

```bash
python -m PyInstaller --onedir --noconsole --name OCRfanyi --icon app_icon.ico \
  --add-data "beijingtu;beijingtu" \
  --add-data "models;models" \
  --hidden-import aiohttp --hidden-import pynput \
  --hidden-import pywintypes --hidden-import win32con --hidden-import win32api --hidden-import win32gui \
  --collect-all easyocr \
  --exclude-module pandas --exclude-module lxml --exclude-module shapely \
  --exclude-module pythonwin --exclude-module IPython \
  --distpath bagpag app.py
```

Produces `bagpag/OCRfanyi/` directory with `OCRfanyi.exe` + `_internal/` (~4.4GB). Uses `--onedir` for instant startup (no temp extraction like `--onefile`). CUDA DLLs (3.9GB of the total) are deeply interdependent — do NOT attempt to remove individual DLLs (cufft, cusolver, cublas, cudnn, etc.) as this causes OSError 126 at runtime.

## Dependencies

`easyocr`, `torch` (CUDA from `download.pytorch.org/whl/cu126`), `numpy`, `mss`, `Pillow`, `aiohttp`, `pywin32`, `pynput`, `pyinstaller`

## Common pitfalls

- PaddleOCR was abandoned (PIR model bug on Windows + complex install). Do NOT suggest switching back.
- PyPI torch is CPU-only. CUDA torch must come from `download.pytorch.org/whl/cu126`.
- Overlay window must use `WS_EX_NOACTIVATE` to avoid stealing focus.
- The `paddle_ocr.py` filename is misleading — it contains EasyOCR, not PaddleOCR.
- `easyocr.Reader()` does NOT accept `text_threshold` or `low_text` — those go in `reader.readtext()`.
- tkinter `-transparentcolor` shows through to the DESKTOP, not to other widgets in the same window. Use PIL compositing + Canvas text for layered backgrounds instead.
- Hotkey thread must use a message-only window (`HWND_MESSAGE`) to avoid creating a visible window.
- RoundedButton's `set_enabled()` must be used to enable/disable; `configure(state=...)` is a Canvas option and does NOT control the custom button's `_enabled` flag.
- Do NOT wrap widgets in tkinter Frames — the solid bg blocks the composited background. Use Canvas `create_window()` instead.
- Don't add comments to code unless the WHY is non-obvious.
