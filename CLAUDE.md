# OCRfanyi — Real-time Screen OCR Translation Overlay

## What this project does

A Windows desktop app that captures the screen, OCRs English text with EasyOCR (GPU), translates to Chinese via DeepSeek API, and overlays the translation on screen with a transparent click-through window. Toggle with Ctrl+Shift+T.

## Architecture

```
main.py (Win32 message pump + global hotkey)
 └── Coordinator (4 threads + async event loop)
      ├── Capture thread: mss screenshot → RegionTracker (200×200 grid frame diff via MD5)
      │                    → changes pushed to ocr_queue (BoundedQueue, 50 cap)
      ├── OCR thread:      ocr_queue → OCRPool (single EasyOCR instance, GPU) → translate_queue (200 cap)
      │                    Translation cache check happens before enqueueing to translate
      ├── Translate thread: asyncio event loop, DeepSeekClient + BatchManager (debounce 200ms, max 800ms, batch 20)
      │                    → result_queue (1 cap, only latest)
      └── Render thread:   30 FPS, drains result_queue, expires entries after 5s, PIL/ImageDraw rendering
                            → OverlayWindow (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE)
```

## Key files

| File | Purpose |
|------|---------|
| `main.py` | Entry point, Win32 hotkey + message loop, Coordinator lifecycle |
| `config.py` | All tunable parameters (API, OCR, capture intervals, cache sizes) |
| `src/capture/screen_capture.py` | MSS wrapper, returns np.ndarray (BGRA) |
| `src/capture/region_tracker.py` | 200×200 grid frame diff via subsampled MD5 |
| `src/ocr/paddle_ocr.py` | EasyOCR wrapper (filename is historical, actually EasyOCR not PaddleOCR) |
| `src/ocr/ocr_pool.py` | Single-instance OCR pool (memory-safe via Queue put/get) |
| `src/translator/deepseek_client.py` | Async DeepSeek Chat API client, batch translate with \|\|\| separator |
| `src/translator/batch_manager.py` | Accumulates texts, flushes on debounce/max-wait/max-size triggers |
| `src/overlay/overlay_window.py` | Pure ctypes Win32 layered transparent window |
| `src/overlay/text_renderer.py` | PIL-based text rendering on BGRA bitmap |
| `src/pipeline/coordinator.py` | Central orchestrator — all threading, queues, cache, lifecycle |
| `src/pipeline/bounded_queue.py` | Thread-safe queue that drops oldest when full |

## Important implementation details

- **GPU**: EasyOCR uses `gpu=True`, PyTorch 2.12.0+cu126, GTX 1650. Do NOT change to CPU-only.
- **API key**: Set via `DEEPSEEK_API_KEY` env var, NOT hardcoded in config.py. The user's key is `sk-5459ea259d6146aa851317f4e48b6220`.
- **64-bit Win32 ctypes**: WNDPROC uses `c_ulonglong` for WPARAM/LPARAM (not c_longlong/wintypes.WPARAM). `DefWindowProcW.argtypes` must be set explicitly. Window class name is `"OCRTranslatorOverlay"`.
- **Translation**: Texts joined with `|||`, system prompt enforces same delimiter in response, fallback to original text if response has fewer lines.
- **Caches**: Two LRU caches (OrderedDict) — translation cache (1000 entries) and OCR region cache (hash-based in RegionTracker). Translation cache checked in both OCR thread and translate thread.
- **Queue behavior**: ocr_queue and translate_queue drop oldest on overflow. result_queue has capacity=1 (only latest result kept).
- **Hotkey**: `MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT` + `VK_T`, registered via `ctypes.windll.user32.RegisterHotKey`. Must use `MOD_NOREPEAT` (0x4000) to avoid repeat triggers.
- **Dependencies**: easyocr, torch (CUDA), numpy, mss, Pillow, aiohttp, pywin32, pynput (see requirements.txt)

## Common pitfalls

- PaddleOCR was abandoned (PIR model bug on Windows + complex install). Do NOT suggest switching back.
- PyPI torch is CPU-only. CUDA torch must come from `download.pytorch.org/whl/cu126`.
- Overlay window must use `WS_EX_NOACTIVATE` to avoid stealing focus.
- The `paddle_ocr.py` filename is misleading — it contains EasyOCR, not PaddleOCR.
- BatchManager splits large batches at BATCH_MAX_SIZE, leaving overflow for next flush.
- `_trans_cache_lock` is re-entered in `_translate_async` — held across OrderedDict operations only, never across await.
