# iPhoneScreenAdvisor

iPhoneScreenAdvisor watches a USB-connected or mirrored iPhone poker screen, reuses this repository's existing perception and equity engine, and displays the suggested action. **It never taps the phone, never uses ADB, and never executes an action itself. The human makes every move.**

The Android automation path remains separate in `README.md`. The iPhone path uses only `capture.py`, `advisor.py`, `output.py`, the existing `perception.py` / `engine.py` / `hand_eval.py`, and the `iphone:` section of `config.yaml`.

## 1. Requirements

- Python 3.10 or newer.
- An iPhone connected by USB or mirrored to the computer.
- The poker table visible in the phone feed, including hole cards and action amounts.
- The vision backend already configured under `vision:` in `config.yaml`. The default is local Ollama with `qwen2.5vl:7b`.
- Optional Tk display support and optional global-hotkey/speech packages. Console-only and Enter-trigger modes work without them.

Install the project and core dependencies:

```bash
python -m pip install -r requirements.txt
```

On Windows, `SETUP_WINDOWS.bat` installs everything and runs the complete test suite. Optional automatic-window search, global hotkeys, and speech are installed with:

```bash
python -m pip install -r requirements-optional.txt
```

## 2. Prepare the iPhone

1. On the iPhone, open **Settings → Privacy & Security → Developer Mode** and turn it on. Complete any restart or confirmation iOS requests.
2. Connect the phone by USB and unlock it.
3. When prompted, choose **Trust This Computer** and enter the phone passcode.
4. Keep the phone unlocked during capture.
5. Open the poker app and make the table visible.
6. Keep mirror resolution, orientation, and scale **fixed** after calibration; both CV geometry and vision input assume a stable feed.

## 3. Choose a backend

Backends are ranked by practical reliability. Switch backends by editing `iphone.backend` only; no code changes are needed.

| Rank | Backend | OS | Best use | Typical capture latency |
|---|---|---|---|---|
| 1 | `mirror_window` | Windows/macOS | A stable mirror app is already working | Usually under 50 ms for the grab |
| 2 | `mac_quicktime` | macOS | Native QuickTime USB capture | Usually low; depends on camera/frame delivery |
| 3 | `usb_screenshot` | Windows/macOS | No mirror feed, or direct USB PNGs are preferred | Usually 200–600 ms per shot |

These are latency expectations, not guarantees. Vision inference usually dominates: roughly 1–2 seconds per call on a suitable local setup, and potentially faster with a hosted model. Slower hardware, large images, and model queues increase latency.

## Backend A: `mirror_window` — recommended on Windows

Use any mirror app that displays the phone in a normal desktop window. The default title filter is `ApowerMirror`; change it to match your app.

### Discover the window

```bash
python tools/list_devices.py
```

On Windows this prints every top-level window title and `[x, y, width, height]` rectangle. Set a unique title substring:

```yaml
iphone:
  backend: mirror_window
  window_title_contains: "ApowerMirror"
  region: null
```

Automatic title search uses `pygetwindow` on Windows. On macOS, or whenever title search is unreliable, use a fixed region instead.

### Calibrate the exact phone area — most reliable option

```bash
python tools/calibrate_region.py
```

1. A fullscreen transparent overlay appears.
2. Drag exactly around the mirrored phone area.
3. Drag edges/corners to resize, or the center to move the selection.
4. Press **Enter** to save or **Esc** to cancel.
5. The tool saves `logs/iphone_region.json` and prints a YAML snippet.
6. Replace the existing `iphone.region` value with the printed setting:

```yaml
iphone:
  backend: mirror_window
  region: [100, 80, 720, 1280]
```

A configured `region` always overrides title auto-find. This is the best choice when the mirror window stays in one position. If the mirror sits on another monitor, verify the reported screen coordinates; negative `x` or `y` values can be valid.

## Backend B: `mac_quicktime` — recommended native macOS capture

1. Connect and trust the iPhone.
2. Open **QuickTime Player → File → New Movie Recording**.
3. Next to the record button, select the iPhone as the camera. You do not need to record.
4. Run:

   ```bash
   python tools/list_devices.py
   ```

5. The tool probes AVFoundation indices `0–15` and prints each available resolution. **You must verify which index actually shows the iPhone feed**; resolution alone cannot identify it.
6. Configure the verified index:

   ```yaml
   iphone:
     backend: mac_quicktime
     device_index: 1
     fourcc: ""
   ```

`fourcc` is optional. If the feed requires a specific codec, use a four-character value such as `AVC1`; otherwise leave it empty. A failed frame grab retries five times and then reports the exact device/QuickTime checks.

## Backend C: `usb_screenshot` — direct USB PNG capture

Install the external `pymobiledevice3` command:

```bash
python -m pip install pymobiledevice3
```

The default command is:

```yaml
iphone:
  backend: usb_screenshot
  pmd3_command: "pymobiledevice3 developer dvt screenshot"
  shot_path: "logs/_iphone_shot.png"
  usb_timeout_s: 30
```

The advisor appends `shot_path` to the command, waits for one PNG, and decodes it as BGR. `{shot}` inside a custom command is also replaced with the configured path instead of appending it.

### iOS 17+ tunnel

Some iOS 17+ setups need a tunnel before developer screenshots work:

```bash
pymobiledevice3 remote start-tunnel
```

Run this with administrator privileges on Windows, and keep its terminal open if required by your setup. Then run the advisor from another terminal. If the command reports pairing or developer-mode errors, repeat the Developer Mode, unlock, and trust steps.

## 4. Optional crop for letterboxing

`region` selects an area of the desktop. `crop` selects an area **inside the already captured frame**. Use crop only when the mirror adds black bars or other padding:

```yaml
iphone:
  crop: [20, 40, 680, 1200]
```

The format is `[x, y, width, height]`. Coordinates must fit inside the captured frame. Portrait frames such as 720×1280 and taller 9:19.5-style feeds are supported without special resizing code.

## 5. First decision

With the poker table visible and the vision server running:

```bash
python -m advisor --once
```

A successful cycle prints a banner such as:

```text
CALL 30   equity≈41%   pot=450
```

It also prints mandatory timing fields:

```text
capture_ms=31.2 vision_ms=1240.5 decide_ms=18.7 state={...} decision={...} warning=
```

The live first-run target is a full decision in under 6 seconds. The timing log tells you which stage is slower: capture, vision, or local decision-making.

## 6. Loop and hotkey operation

Run continuously every 2 seconds:

```bash
python -m advisor --loop
```

Use a different interval:

```bash
python -m advisor --loop 1.5
```

Run in hotkey mode:

```bash
python -m advisor --hotkey
```

The default hotkey is **F9**. Change `iphone.hotkey` in `config.yaml`. Each press captures and evaluates a fresh frame without restarting the program.

The `keyboard` library may require administrator rights on Windows. If its import or registration fails, the advisor automatically switches to **Enter fallback**: press Enter in the advisor terminal for each decision. No restart or configuration change is needed.

Stop any mode with **Ctrl+C**. The source, overlay, and optional speech engine are closed cleanly.

## 7. Console, overlay, and speech

Configure output under `iphone.output`:

```yaml
iphone:
  output:
    console: true
    overlay: true
    tts: false
```

- **Console:** one color-coded line with action, size, equity, and pot.
- **Tk overlay:** a topmost, non-interactive, approximately 640-pixel-wide banner pinned near the bottom-center of the screen. Action text is at least 48 pt and refreshes on each decision.
- **Colors:** CALL/RAISE green, CHECK yellow, FOLD red.
- **TTS:** short phrases only: “Fold.”, “Check.”, “Call 30 chips.”, or “Raise to 150.”

If Tk cannot open a display, or the overlay later fails, output falls back to console-only without crashing. TTS failures disable speech while console/overlay advice continues. Install `requirements-optional.txt` for global hotkeys and `pyttsx3` speech.

## 8. Cache and state behavior

Every cycle performs `grab → perceive → decide → render`. The last valid GameState is retained for at most 5 seconds. If vision fails, a fresh cached state can be reused with an `advisor cache age ...` warning; stale cache is never used silently.

The adapters are confined to `advisor.py`. They accept both the alternate `frame_bgr` / `stage` / `my_cards` contract and this repository's existing PNG-byte / `phase` / `hole` perception interface. Existing `perception.py`, `engine.py`, and `hand_eval.py` are unchanged.

A `waiting` perception state produces no action banner. All chip values and decision sizes remain integers. RAISE size is an absolute raise-to amount.

## 9. Full configuration reference

```yaml
iphone:
  backend: mirror_window        # mac_quicktime | mirror_window | usb_screenshot
  device_index: 0               # mac_quicktime only
  fourcc: ""                    # optional codec such as AVC1
  window_title_contains: "ApowerMirror"
  region: null                  # desktop [x,y,w,h]; overrides auto-find
  pmd3_command: "pymobiledevice3 developer dvt screenshot"
  shot_path: "logs/_iphone_shot.png"
  usb_timeout_s: 30
  poll_seconds: 2.0
  mode: hotkey                  # loop | hotkey; CLI flags override
  hotkey: F9
  crop: null                    # optional crop inside the captured frame
  output:
    console: true
    overlay: true
    tts: false
```

## 10. Acceptance tests

Run every repository test, including the no-phone iPhone smoke test:

```bash
python -m unittest discover -s tests -v
```

Run only the iPhone suite:

```bash
python -m unittest discover -s tests -p "test_iphone_advisor.py" -v
```

| Test | How to verify |
|---|---|
| T1 device discovery | On the actual computer, run `tools/list_devices.py`; visually verify the iPhone camera index or mirror title/rect. |
| T2 one live decision | Run `python -m advisor --once`; confirm a full banner and all three timing fields, targeting under 6 s. |
| T3 hotkey/fallback | Press F9 twice for two fresh decisions; then test Enter fallback by running without hotkey permissions/library. |
| T4 output | Confirm overlay refresh and action colors; enable `tts: true` and verify the short spoken phrase. |
| T5 CI mock | Included: fake 720×1280 BGR frame + contract perception + stub engine renders `CALL 30`; no phone required. |
| T6 backend switch | Included: factory selects all three classes from config alone; USB error paths and both other backends are mocked. |

Hardware-dependent T1–T4 cannot be certified by CI. The mock/unit tests validate the software path without claiming that a particular phone, mirror app, tunnel, display, or voice driver works identically.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| Black frame | Unlock the phone, make the mirror/table visible, check trust, and recalibrate the exact phone area. |
| iPhone not detected | Enable Developer Mode, unlock, trust the computer, and try another USB cable/port. |
| QuickTime opens but grabs fail | Select the iPhone in **File → New Movie Recording**, verify the index, and retry after reconnecting. |
| Mirror title not found | Run `tools/list_devices.py`, use a unique title substring, restore the minimized window, or set `region`. |
| `pygetwindow` missing | Install `requirements-optional.txt`, or bypass title search with a calibrated fixed region. |
| Mirror size changed | Restore the original mirror size/scale or recalibrate `region` and `crop`. |
| Wrong desktop area | Rerun `tools/calibrate_region.py`; check multi-monitor coordinates and keep the window fixed. |
| Crop is outside the frame | Remove `crop` for a full-frame test, then measure crop inside the captured image, not the desktop. |
| `pymobiledevice3` not found | Run `python -m pip install pymobiledevice3` and reopen the terminal if PATH changed. |
| USB tunnel error on iOS 17+ | Run `pymobiledevice3 remote start-tunnel` first, with admin rights on Windows, then retry. |
| USB screenshot times out | Unlock/trust, check the tunnel, increase `usb_timeout_s`, and verify the screenshot command manually. |
| High total latency | Read the timing log; reduce capture/crop size, use mirror capture, or switch to a faster vision model/server. |
| Hotkey does nothing | Run the terminal as administrator on Windows, change `iphone.hotkey`, or use Enter fallback. |
| Tk missing or display fails | Install Tk support for your Python distribution; the advisor otherwise continues in console-only mode. |
| No speech | Install `requirements-optional.txt`, check system audio/driver support, and leave `tts: false` if unnecessary. |
| Vision/JSON warnings | Confirm the vision server and model, make cards/amounts readable, and inspect the `warning` field in the timing log. |
| Program will not stop | Focus the advisor terminal and press Ctrl+C; close the terminal as a last resort. |

## First run cheat sheet

1. Enable iPhone Developer Mode, connect USB, unlock, and trust the computer.
2. Install the project with `SETUP_WINDOWS.bat` or `pip install -r requirements.txt`.
3. Install optional hotkey/speech/window support with `pip install -r requirements-optional.txt`.
4. Start the vision server, normally `ollama serve`.
5. Choose `mirror_window`, `mac_quicktime`, or `usb_screenshot` in `config.yaml`.
6. Run `python tools/list_devices.py` and verify the feed/window.
7. For mirror capture, run `python tools/calibrate_region.py` and paste `region`.
8. For QuickTime, select the verified camera index; for USB, install `pymobiledevice3`.
9. On iOS 17+ USB errors, start the required tunnel first.
10. Run `python -m advisor --once` and confirm the banner plus timing log.
11. Use `--loop 2`, or `--hotkey` and press F9; Enter is the fallback.
12. Keep mirror geometry fixed and stop with Ctrl+C.
