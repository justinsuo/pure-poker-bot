# Pure Poker Local Vision Bot

[![Offline tests](https://github.com/justinsuo/pure-poker-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/justinsuo/pure-poker-bot/actions/workflows/tests.yml)

**Public repository:** <https://github.com/justinsuo/pure-poker-bot>

Use only where permitted; you accept all ToS, account, and financial risk.

A local Python 3.10+ no-limit Texas Hold'em bot for Pure Poker on an Android emulator. It captures the screen with ADB, reads the table with an OpenAI-compatible vision model, decides with a local heuristic/Monte Carlo engine, and can tap calibrated emulator controls.

**Using an iPhone instead?** See [README_IPHONE.md](README_IPHONE.md) for **iPhoneScreenAdvisor**, a separate capture-and-advice-only mode. It has no ADB and no taps: the human executes every action on the phone.

**No AI assistant is needed to install or operate this repository.** The vision model is a required component of the bot itself. Every installation, calibration, testing, and troubleshooting step is included below.

## What is verified and what needs your hardware

The repository includes 52 automated tests for the hand evaluator, decision engine, chip math, JSON recovery, ADB PNG handling, action limits, stale/duplicate-action prevention, iPhone capture backends, hotkey fallback, overlay/speech fallback, and mocked end-to-end pipelines. GitHub Actions runs these tests on Python 3.10 and 3.12. The release evaluator was also checked against all 2,598,960 possible five-card hands.

Your friend must still calibrate and dry-run on the actual emulator because button positions, screen resolution, Pure Poker's layout, and vision-model OCR accuracy vary. Offline tests cannot confirm what a real table looks like or where its buttons are.

## Quick start: Windows

### 1. Download and extract

On the GitHub page, choose **Code → Download ZIP**. Extract the entire ZIP into a normal folder, then open that folder. Do not run the bot from inside the ZIP preview.

If you already use Git, clone it instead:

```bash
git clone https://github.com/justinsuo/pure-poker-bot.git
cd pure-poker-bot
```

### 2. Install the three external programs

1. **Python 3.10 or newer** from <https://www.python.org/downloads/>. On Windows, enable **Add Python to PATH** during installation.
2. **Android SDK Platform Tools** from <https://developer.android.com/tools/releases/platform-tools>. Extract them and either add the folder to PATH or set `adb_path` in `config.yaml` to the full `adb.exe` path.
3. **Ollama** from <https://ollama.com/download>. After installation, open a new terminal and run:

   ```bash
   ollama pull qwen2.5vl:7b
   ollama serve
   ```

   Keep `ollama serve` running while using the bot. The model name is `qwen2.5vl`, with no hyphen between `5` and `vl`. If your computer cannot run the 7B model, choose another installed vision model and put its exact `ollama list` name in `config.yaml`.

### 3. Run automatic setup

Double-click **`SETUP_WINDOWS.bat`**. It will:

1. Find Python and create an isolated `.venv` environment.
2. Install the packages in `requirements.txt`.
3. Run every offline test.
4. Run offline diagnostics.

Do not continue until the script finishes without a failure. If Windows cannot find Python, close and reopen the terminal or installer after enabling PATH, then try again.

The equivalent manual commands are:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe tools\doctor.py --offline
```

## Connect the emulator

For BlueStacks, enable **Settings → ADB debugging**. For Nox or LDPlayer, enable its ADB/Android debugging option. Start Pure Poker, then open a terminal in the project folder:

```bash
adb devices
```

If no emulator appears, try the common local endpoint:

```bash
adb connect 127.0.0.1:5555
adb devices
```

A usable line ends in `device`, not `unauthorized` or `offline`. With one online device, leave `serial: ""`. With several, copy the intended serial into `config.yaml`:

```yaml
serial: "127.0.0.1:5555"
```

If `adb` is not on PATH, use an absolute path such as:

```yaml
adb_path: "C:\\platform-tools\\adb.exe"
```

## Check ADB and the vision model

With the emulator and `ollama serve` running, execute:

```powershell
.venv\Scripts\python.exe tools\doctor.py
```

Or, after activating the virtual environment:

```bash
python tools/doctor.py
```

The doctor never taps the emulator. It checks:

- Python and required packages.
- Configuration structure and engine settings.
- Local evaluator/engine behavior.
- Online ADB serials.
- The vision server's `/models` endpoint and configured model name.
- The live calibration gate.

Fix every `[FAIL]` line and rerun it. A `[WARN]` about `game.calibrated: false` is expected before calibration.

## Calibrate Pure Poker controls

The supplied coordinates are examples only. Live mode is locked until you explicitly change `game.calibrated` to `true`.

1. Keep the emulator at a fixed resolution and orientation.
2. Open Pure Poker to a table with the action controls visible.
3. Run:

   ```bash
   python tools/calibrate.py --config config.yaml
   ```

4. Open the generated `screen_grid.png`. It has cells numbered `00`–`99` and green pixel labels every 50 pixels.
5. Use the pixel labels to edit `game.buttons` in `config.yaml`:

   | Field | What to measure |
   |---|---|
   | `fold` | Center of Fold |
   | `call_check` | Center of the shared Call/Check button |
   | `raise_open` | Control that opens the bet/raise panel |
   | `raise_confirm` | Final Bet/Raise confirmation button, or `null` if no separate confirmation exists |
   | `slider.y` | Vertical center of the raise slider |
   | `slider.x_min` | Slider position for the minimum raise |
   | `slider.x_max` | Slider position for maximum/all-in |

6. If there is no slider, set `slider: null`. In that mode the bot can press raise controls but cannot enter an exact amount through a slider.
7. Repeat calibration after changing resolution, orientation, emulator, game layout, or table theme.

The engine's raise `amount` is a total **raise-to** amount. Both engine and action layer clamp it to `max_raise_to`. Slider position is linear: `amount / max_raise_to` between `x_min` and `x_max`.

## Mandatory ten-hand dry-run

Dry-run is the default; it reads and logs decisions but never taps or swipes.

Test one frame first:

```bash
python bot.py --dry-run --once
```

Then observe **10 complete hands**:

```bash
python bot.py --dry-run
```

On Windows, double-clicking `START_BOT.bat` does the same thing.

Check every decision opportunity in all 10 hands:

- [ ] `waiting` appears when it is not your turn.
- [ ] Hole cards and board cards match exactly.
- [ ] Pot, call, minimum raise, and maximum raise match the screen, including `k`/`m` labels.
- [ ] Position, aggressors, and limpers match the action.
- [ ] The action and amount are plausible.
- [ ] Any vision `note` is investigated rather than ignored.
- [ ] The planned Fold or Call/Check coordinate is correct.
- [ ] The planned slider direction, endpoint, and sizing are correct.
- [ ] If the interface needs a final confirmation, `raise_confirm` is configured.
- [ ] Bad frames warn instead of crashing; three consecutive bad states pause the loop.

If a material field is repeatedly wrong, press Ctrl+C, correct the model/configuration/calibration, and restart the count from zero.

## Start live mode

Only after the 10-hand checklist:

1. Open `config.yaml` and set:

   ```yaml
   game:
     calibrated: true
   ```

2. Verify everything, including the calibration gate:

   ```bash
   python tools/doctor.py --live
   ```

3. Test exactly one live iteration:

   ```bash
   python bot.py --live --once
   ```

4. If that iteration reads and acts correctly, run continuously:

   ```bash
   python bot.py --live
   ```

   On Windows, this is also available as:

   ```bat
   START_BOT.bat --live
   ```

**Kill switch: press Ctrl+C in the bot terminal to stop the loop and prevent further taps.**

## Built-in action safeguards

- Running `bot.py` without `--live` always means dry-run.
- Live mode refuses to run while `game.calibrated` is false.
- A cached perception result is logged for diagnosis but **never causes a tap**.
- After a successful live action, an unchanged table state is not acted on again.
- A valid `waiting` state resets the unchanged-state guard for the next opportunity.
- Three consecutive bad states produce a loud warning and pause for `bad_state_pause_s`.
- Raise amounts are integers and cannot exceed `max_raise_to`.
- If a computed raise is not above the call amount, the engine emits a legal call/check instead.

## Understand the log

Each iteration emits one JSON line with four fields:

```json
{"state":{"phase":"waiting","note":""},"decision":{"action":"wait","amount":0,"equity":null,"reason":"not my turn"},"execution":"no action","warning":""}
```

- `state`: normalized cards, chips, position, and action counts.
- `decision.action`: `fold`, `call`, `check`, `raise`, or `wait`.
- `decision.amount`: total raise-to amount for a raise; call amount for a call.
- `decision.equity`: postflop Monte Carlo equity; preflop is `null`.
- `decision.reason`: the rule that caused the decision.
- `execution`: what was or was not sent to ADB.
- `warning`: capture, vision, parsing, or execution problem; empty on a clean frame.

Messages such as `skipped: cached state, no taps` and `skipped: unchanged state already acted on` are intentional safeguards.

## Test the engine without ADB or a model

The local engine makes zero network calls:

```bash
python -m engine Ah Kd flop "7h Qc 2s" --pot=100 --call=30 --pos=BTN --aggr=0
```

It prints `{action, amount, equity, reason}` as JSON. Add `--seed=7` for a repeatable Monte Carlo result.

Preflop example:

```bash
python -m engine As Ks preflop "" --pot=150 --call=50 --pos=CO --aggr=1 --min-raise-to=150 --max-raise-to=2000
```

Useful options: `--limpers`, `--min-raise-to`, `--max-raise-to`, `--mc-iterations`, `--bluff-rate`, and `--seed`.

Run all repository tests yourself:

```bash
python -m unittest discover -s tests -v
```

Run offline diagnostics yourself:

```bash
python tools/doctor.py --offline
```

## Configuration reference

| Path | Meaning | Default |
|---|---|---|
| `adb_path` | ADB executable or PATH name | `adb` |
| `serial` | Device serial; empty auto-selects one online device | `""` |
| `adb_timeout_s` | Timeout for each ADB command | `20` |
| `vision.api_base` | OpenAI-compatible base URL or full chat endpoint | `http://localhost:11434/v1` |
| `vision.model` | Exact installed vision model name | `qwen2.5vl:7b` |
| `vision.key_env` | Environment variable containing an optional API key | `VISION_API_KEY` |
| `vision.timeout_s` | Vision request timeout | `60` |
| `game.calibrated` | Explicit live-action unlock | `false` |
| `game.buttons` | Calibrated screen controls | Example values |
| `game.swipe_duration_ms` | Slider drag duration | `450` |
| `game.raise_open_delay_s` | Delay after opening raise panel | `0.35` |
| `game.raise_confirm_delay_s` | Delay before optional confirmation | `0.20` |
| `loop_delay_s` | Delay between iterations | `1.5` |
| `bad_state_pause_s` | Pause after three bad states | `30` |
| `engine.mc_iterations` | Postflop simulations per decision | `400` |
| `engine.bluff_rate` | Base bluff rate from 0 to 1 | `0.18` |

### Use another OpenAI-compatible provider

Change `vision.api_base` and `vision.model`. Put the API key in the environment, never in the public config:

```powershell
# Windows PowerShell
$env:VISION_API_KEY="your-secret-key"
```

```bash
# macOS/Linux
export VISION_API_KEY="your-secret-key"
```

The request is `POST <api_base>/chat/completions` unless `api_base` already ends in `/chat/completions`. It contains the exact extraction system prompt, a base64 PNG `image_url`, `temperature: 0`, and `max_tokens: 700`.

Ollama's supported OpenAI vision request format is documented at <https://docs.ollama.com/api/openai-compatibility>. Available Qwen2.5-VL model tags are listed at <https://ollama.com/library/qwen2.5vl>.

## How the local engine works

- `hand_eval.py` evaluates all 21 five-card subsets of a seven-card hand. Categories and kickers are directly comparable tuples; wheel straights are included.
- Preflop uses pair/high-low ranks, suitedness, and gap penalty to produce tier 0–30. Position thresholds are BTN 13, CO 15, MP 18, SB 19, BB 16, UTG 21; SB/BB subtract 4 and each prior aggressor adds 4.
- Postflop samples random river completions with Fisher–Yates shuffling. Opponent hands use rejection sampling with aggression-dependent range floors and 15% junk hands.
- Equity is wins plus half of ties. The decision ladder uses pot odds, position/aggression margins, board texture, deterministic-ish mixed actions, and a high-pot-odds safety gate.
- The engine is heuristic and deliberately simple enough to inspect in `engine.py`; it is not a game-theory solver.

## macOS/Linux setup

Use an emulator supported on your operating system, then run from the project directory:

```bash
chmod +x setup.sh
./setup.sh
```

After setup, the commands are:

```bash
.venv/bin/python tools/doctor.py
.venv/bin/python tools/calibrate.py --config config.yaml
.venv/bin/python bot.py --dry-run --once
.venv/bin/python bot.py --dry-run
.venv/bin/python bot.py --live --once
```

Set `PYTHON=python3.12` before `./setup.sh` if `python3` points to an older version.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `python`/`py` is not recognized | Install Python 3.10+, enable PATH, then open a new terminal. |
| Setup cannot create `.venv` | Install the Python venv component; on Debian/Ubuntu use your package manager's `python3-venv` package. |
| ADB executable not found | Install Platform Tools and set `adb_path` to the full executable path. |
| No online ADB devices | Enable emulator ADB, try `adb connect 127.0.0.1:5555`, and rerun `adb devices`. |
| Device is unauthorized/offline | Accept the emulator's debugging prompt or restart its ADB connection until status is `device`. |
| Multiple devices found | Set the intended `serial` in `config.yaml`. |
| Vision connection refused | Start `ollama serve` and confirm the API base is `http://localhost:11434/v1`. |
| Model not installed | Run `ollama pull qwen2.5vl:7b`; make the config exactly match `ollama list`. |
| HTTP 401/403 from a provider | Set the environment variable named by `vision.key_env` in the same terminal before starting the bot. |
| Repeated JSON warnings | Use a stronger vision model and verify that buttons/cards are large and unobstructed; the parser still requires the requested fields. |
| Wrong button or slider position | Recalibrate at the current resolution and inspect `screen_grid.png`. |
| Slider moves but no bet is confirmed | Set the optional `raise_confirm` coordinate if Pure Poker presents a separate confirmation button. |
| Live mode is locked | Complete calibration and deliberately set `game.calibrated: true`; use `--live` as well. |
| Same state is skipped | This prevents duplicate taps. A valid waiting/new state resets the guard; investigate if the screen never changes. |
| Bot does not stop | Focus its terminal and press Ctrl+C; close the terminal as a last resort. |

## Project layout

```text
adb.py                  ADB transport and binary-safe screenshots
hand_eval.py            Exhaustive 7-card evaluator
engine.py               Local strategy and CLI
perception.py           Vision request, JSON recovery, validation, cache
bot.py                  Android main loop and action safeguards
capture.py              iPhone QuickTime, mirror-region, and USB capture
advisor.py              iPhone capture → perception → engine → advice pipeline
output.py               iPhone console, Tk overlay, and optional speech
README_IPHONE.md        Complete iPhone setup and operation guide
config.yaml             Local device/model/control configuration
requirements.txt        Core Python dependencies
requirements-optional.txt Optional window, hotkey, and voice dependencies
tools/calibrate.py      Android numbered screenshot grid
tools/list_devices.py   iPhone camera/window discovery
tools/calibrate_region.py iPhone visual mirror-region selector
tools/doctor.py         Offline/online/live-readiness diagnostics
tests/test_suite.py     Android unit and mocked integration tests
tests/test_iphone_advisor.py iPhone unit and mocked integration tests
SETUP_WINDOWS.bat       Automatic Windows setup
START_BOT.bat           Windows launcher; dry-run by default
setup.sh                Unix setup
.github/workflows/      GitHub Actions test workflow
```
