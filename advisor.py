"""iPhoneScreenAdvisor: capture and advice only; a human acts on the phone.

The optional ``keyboard`` hotkey library needs administrator rights on Windows.
If it is unavailable or denied, terminal Enter-trigger mode is used automatically.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import inspect
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import yaml

import engine
from capture import ScreenSource, make_source
from output import DecisionOutput
from perception import Perception


class WaitingForTurn(RuntimeError):
    pass


def _chips(value: Any) -> int:
    result = int(value)
    if result < 0:
        raise ValueError("Chip values must be non-negative")
    return result


def normalize_game_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Adapt either the contract state or this repo's existing perception state."""
    if raw.get("phase") == "waiting":
        raise WaitingForTurn("waiting for my turn")
    opponents = []
    for opponent in raw.get("opponents", []):
        opponents.append({
            "stack": _chips(opponent.get("stack", 0)),
            "last_action": str(opponent.get("last_action", "")),
        })
    return {
        "stage": str(raw.get("stage", raw.get("phase", ""))).lower(),
        "board": list(raw.get("board", [])),
        "my_cards": list(raw.get("my_cards", raw.get("hole", []))),
        "pot": _chips(raw.get("pot", 0)),
        "to_call": _chips(raw.get("to_call", 0)),
        "position": str(raw.get("position", raw.get("my_position", ""))).upper(),
        "my_stack": _chips(raw.get("my_stack", raw.get("max_raise_to", 0))),
        "opponents": opponents,
        "confidence": max(0.0, min(1.0, float(raw.get("confidence", 0.0)))),
    }


def _legacy_engine_state(state: dict[str, Any]) -> dict[str, Any]:
    """Map the alternate contract onto the unchanged existing engine."""
    actions = [str(item.get("last_action", "")).lower() for item in state["opponents"]]
    return {
        "phase": state["stage"],
        "hole": state["my_cards"],
        "board": state["board"],
        "pot": state["pot"],
        "to_call": state["to_call"],
        "min_raise_to": max(1, 2 * state["to_call"]),
        "max_raise_to": state["my_stack"],
        "my_position": state["position"],
        "aggressors": sum(action in ("raise", "bet", "reraise", "all-in") for action in actions),
        "limpers": sum(action == "call" for action in actions) if state["stage"] == "preflop" else 0,
    }


def normalize_decision(raw: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    action = str(raw.get("action", "")).upper()
    if action not in ("FOLD", "CHECK", "CALL", "RAISE"):
        raise ValueError(f"Unsupported engine action: {action!r}")
    size_value = raw.get("size", raw.get("amount"))
    if action in ("FOLD", "CHECK"):
        size = None
    elif action == "CALL":
        size = _chips(size_value if size_value is not None else state["to_call"])
    else:
        if size_value is None:
            raise ValueError("RAISE decision requires an absolute raise-to size")
        size = _chips(size_value)
    return {
        "action": action,
        "size": size,
        "equity": None if raw.get("equity") is None else float(raw["equity"]),
        "reasoning": str(raw.get("reasoning", raw.get("reason", ""))),
    }


def _read_existing_perception(vision: Any, frame: np.ndarray) -> tuple[dict[str, Any], str, bool]:
    parameters = inspect.signature(vision.perceive).parameters
    if "frame_bgr" in parameters:
        result = vision.perceive(frame)
    else:
        ok, encoded = cv2.imencode(".png", frame)
        if not ok:
            raise RuntimeError("Could not PNG-encode captured frame")
        result = vision.perceive(encoded.tobytes())
    if isinstance(result, tuple):
        raw, warning = result
    else:
        raw, warning = result, ""
    if raw is None:
        raise RuntimeError(warning or "Perception returned no game state")
    return raw, str(warning or ""), bool(getattr(vision, "last_used_cache", False))


def apply_crop(frame: np.ndarray, crop: Any) -> np.ndarray:
    if crop is None:
        return frame
    if not isinstance(crop, (list, tuple)) or len(crop) != 4:
        raise ValueError("iphone.crop must be [x, y, width, height]")
    x, y, width, height = (int(item) for item in crop)
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > frame.shape[1] or y + height > frame.shape[0]:
        raise ValueError(
            f"Crop {crop} is outside captured frame {frame.shape[1]}x{frame.shape[0]}"
        )
    return np.ascontiguousarray(frame[y : y + height, x : x + width])


class Advisor:
    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        source: ScreenSource | None = None,
        vision: Any = None,
        decide_fn: Callable[..., dict[str, Any]] | None = None,
        output: DecisionOutput | None = None,
    ):
        self.cfg = cfg
        self.iphone = cfg.get("iphone", {})
        self.source = source or make_source(cfg)
        self.vision = vision or Perception(cfg)
        self.decide_fn = decide_fn or engine.decide
        self._cache: tuple[dict[str, Any], dict[str, Any], float] | None = None
        self._hotkey_event = threading.Event()
        self.source.warmup()
        self.output = output or DecisionOutput(cfg)

    def _cached_or_raise(self, warning: str) -> tuple[dict[str, Any], dict[str, Any], str]:
        if self._cache is not None:
            state, raw, timestamp = self._cache
            age = time.monotonic() - timestamp
            if age < 5.0:
                return (
                    copy.deepcopy(state),
                    copy.deepcopy(raw),
                    f"{warning}; advisor cache age {age:.2f}s",
                )
        raise RuntimeError(warning or "Perception failed and no fresh cache is available")

    def _perceive(self, frame: np.ndarray) -> tuple[dict[str, Any], dict[str, Any], str]:
        try:
            raw, warning, from_vision_cache = _read_existing_perception(self.vision, frame)
            if from_vision_cache:
                return self._cached_or_raise(warning or "vision cache used")
            state = normalize_game_state(raw)
        except WaitingForTurn:
            raise
        except Exception as exc:
            return self._cached_or_raise(str(exc))
        self._cache = (copy.deepcopy(state), copy.deepcopy(raw), time.monotonic())
        return state, raw, warning

    def _decide(self, state: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
        engine_input = raw if "phase" in raw else _legacy_engine_state(state)
        return normalize_decision(self.decide_fn(engine_input, self.cfg), state)

    def cycle(self) -> dict[str, Any] | None:
        timings = {"capture_ms": 0.0, "vision_ms": 0.0, "decide_ms": 0.0}
        state: dict[str, Any] | None = None
        decision: dict[str, Any] | None = None
        warning = ""
        try:
            started = time.perf_counter()
            try:
                frame = apply_crop(self.source.grab(), self.iphone.get("crop"))
            finally:
                timings["capture_ms"] = (time.perf_counter() - started) * 1000

            started = time.perf_counter()
            try:
                state, raw, warning = self._perceive(frame)
            finally:
                timings["vision_ms"] = (time.perf_counter() - started) * 1000

            started = time.perf_counter()
            try:
                decision = self._decide(state, raw)
            finally:
                timings["decide_ms"] = (time.perf_counter() - started) * 1000
            self.output.render(decision, state)
        except WaitingForTurn as exc:
            warning = str(exc)
        except Exception as exc:
            warning = " ".join(str(exc).split())[:800]
        logging.info(
            "capture_ms=%.1f vision_ms=%.1f decide_ms=%.1f state=%s decision=%s warning=%s",
            timings["capture_ms"],
            timings["vision_ms"],
            timings["decide_ms"],
            json.dumps(state, separators=(",", ":"), ensure_ascii=False),
            json.dumps(decision, separators=(",", ":"), ensure_ascii=False),
            warning,
        )
        return decision

    def run_loop(self, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("Loop interval must be positive")
        while True:
            self.cycle()
            self.output.pump()
            time.sleep(seconds)

    def run_hotkey(self) -> None:
        hotkey = str(self.iphone.get("hotkey", "F9"))
        keyboard = None
        try:
            keyboard = importlib.import_module("keyboard")
            keyboard.on_press_key(hotkey, lambda _event: self._hotkey_event.set())
        except Exception as exc:
            logging.warning("Hotkey unavailable (%s); press Enter in this terminal instead", exc)
            while True:
                try:
                    input("Press Enter for a decision (Ctrl+C to quit): ")
                except EOFError:
                    return
                self.cycle()
                self.output.pump()
        else:
            print(f"Press {hotkey} for a decision; Ctrl+C to quit; terminal Enter fallback is automatic")
            try:
                while True:
                    if self._hotkey_event.wait(timeout=0.25):
                        self._hotkey_event.clear()
                        self.cycle()
                    self.output.pump()
            finally:
                keyboard.unhook_all()

    def close(self) -> None:
        self.output.close()
        self.source.close()


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    if not isinstance(cfg, dict):
        raise ValueError("config.yaml must contain a mapping")
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description="iPhoneScreenAdvisor (capture and advice only)")
    parser.add_argument("--config", default=str(Path(__file__).resolve().with_name("config.yaml")))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Capture and advise once")
    mode.add_argument("--loop", nargs="?", type=float, const=2.0, default=None, metavar="sec", help="Advise every sec (default 2)")
    mode.add_argument("--hotkey", action="store_true", help="Use configured hotkey, with Enter fallback")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    advisor: Advisor | None = None
    try:
        cfg = load_config(args.config)
        advisor = Advisor(cfg)
        if args.once:
            advisor.cycle()
        elif args.loop is not None:
            advisor.run_loop(args.loop)
        elif args.hotkey or str(cfg.get("iphone", {}).get("mode", "hotkey")) == "hotkey":
            advisor.run_hotkey()
        else:
            advisor.run_loop(float(cfg.get("iphone", {}).get("poll_seconds", 2.0)))
    except KeyboardInterrupt:
        logging.info("iPhoneScreenAdvisor stopped cleanly")
    except Exception as exc:
        logging.error("Advisor stopped: %s", exc)
        raise SystemExit(2) from exc
    finally:
        if advisor is not None:
            advisor.close()


if __name__ == "__main__":
    main()
