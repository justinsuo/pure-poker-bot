"""Main capture -> perceive -> decide -> act loop for Pure Poker."""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import yaml

import adb
import engine
from perception import Perception


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    if not isinstance(cfg, dict):
        raise ValueError("config file must contain a YAML mapping")
    return cfg


def _point(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"game.buttons.{name} must be [x, y]")
    try:
        x, y = int(value[0]), int(value[1])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"game.buttons.{name} must contain integers") from exc
    if x < 0 or y < 0:
        raise ValueError(f"game.buttons.{name} coordinates must be non-negative")
    return x, y


def _one_line(value: Any) -> str:
    return " ".join(str(value).split())[:800]


def execute_action(
    cfg: dict[str, Any],
    state: dict[str, Any],
    decision: dict[str, Any],
    *,
    dry_run: bool,
) -> str:
    """Map an engine action to calibrated controls; return an execution summary."""
    action = str(decision.get("action", "")).lower()
    if action in ("", "wait"):
        return "no action"
    game = cfg.get("game", {})
    if not isinstance(game, dict):
        raise ValueError("game must be a mapping")
    buttons = game.get("buttons", {})
    if not isinstance(buttons, dict):
        raise ValueError("game.buttons must be a mapping")
    if not dry_run and not bool(game.get("calibrated", False)):
        raise ValueError(
            "live actions are locked: calibrate controls and set game.calibrated: true"
        )

    if action in ("fold", "call", "check"):
        button_name = "fold" if action == "fold" else "call_check"
        point = _point(buttons.get(button_name), button_name)
        if dry_run:
            return f"dry-run: tap {button_name} at {list(point)}"
        adb.tap(cfg, *point)
        return f"tapped {button_name} at {list(point)}"

    if action not in ("raise", "bet"):
        raise ValueError(f"unsupported action: {action!r}")

    try:
        max_raise_to = int(state.get("max_raise_to", 0))
        requested = int(decision.get("amount", 0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("raise amount and max_raise_to must be integers") from exc
    if max_raise_to <= 0:
        raise ValueError("max_raise_to must be positive for a raise")
    amount = min(max(0, requested), max_raise_to)
    if amount <= 0:
        raise ValueError("raise amount must be positive")

    raise_point = _point(buttons.get("raise_open"), "raise_open")
    confirm_value = buttons.get("raise_confirm")
    confirm_point = _point(confirm_value, "raise_confirm") if confirm_value else None
    slider = buttons.get("slider")
    slider_plan: tuple[int, int, int, int] | None = None
    if slider is not None:
        if not isinstance(slider, dict):
            raise ValueError("game.buttons.slider must be a mapping or null")
        try:
            y = int(slider["y"])
            x_min = int(slider["x_min"])
            x_max = int(slider["x_max"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("slider requires integer y, x_min, and x_max") from exc
        if min(y, x_min, x_max) < 0 or x_max < x_min:
            raise ValueError("slider coordinates are invalid")
        fraction = min(1.0, max(0.0, amount / max_raise_to))
        target_x = int(round(x_min + (x_max - x_min) * fraction))
        slider_plan = (x_min, y, target_x, y)

    if dry_run:
        parts = ["dry-run: raise_open"]
        if slider_plan is not None:
            parts.append(f"slider x={slider_plan[2]}")
        else:
            parts.append("no slider configured")
        parts.append(f"amount={amount}/{max_raise_to}")
        if confirm_point is not None:
            parts.append(f"confirm at {list(confirm_point)}")
        return " then ".join(parts)

    adb.tap(cfg, *raise_point)
    time.sleep(max(0.0, float(game.get("raise_open_delay_s", 0.35))))
    if slider_plan is not None:
        duration = int(game.get("swipe_duration_ms", 450))
        adb.swipe(cfg, *slider_plan, duration)
    if confirm_point is not None:
        time.sleep(max(0.0, float(game.get("raise_confirm_delay_s", 0.20))))
        adb.tap(cfg, *confirm_point)

    summary = f"raise {amount}/{max_raise_to}"
    if slider_plan is not None:
        summary += f"; slider target x={slider_plan[2]}"
    else:
        summary += "; no slider configured"
    if confirm_point is not None:
        summary += "; confirmation tapped"
    return summary


def _iteration_log(
    state: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    execution: str,
    warning: str,
) -> None:
    record = {
        "state": state,
        "decision": decision,
        "execution": execution,
        "warning": warning,
    }
    logging.info(json.dumps(record, separators=(",", ":"), ensure_ascii=False))


def _state_signature(state: dict[str, Any]) -> str:
    stable = {key: value for key, value in state.items() if key != "note"}
    return json.dumps(stable, sort_keys=True, separators=(",", ":"))


def run(cfg: dict[str, Any], *, dry_run: bool = True, once: bool = False) -> None:
    game = cfg.get("game", {})
    if not dry_run and (
        not isinstance(game, dict) or not bool(game.get("calibrated", False))
    ):
        raise ValueError(
            "live actions are locked: calibrate controls and set game.calibrated: true"
        )
    perception = Perception(cfg)
    consecutive_bad = 0
    last_action_signature: str | None = None
    loop_delay = max(0.0, float(cfg.get("loop_delay_s", 1.5)))
    pause_delay = max(0.0, float(cfg.get("bad_state_pause_s", 30)))

    while True:
        state: dict[str, Any] | None = None
        decision: dict[str, Any] | None = None
        warning = ""
        execution = "not attempted"
        bad_this_iteration = False
        try:
            png = adb.screencap_png(cfg)
            state, warning = perception.perceive(png)
            if state is None or warning:
                consecutive_bad += 1
                bad_this_iteration = True
            else:
                consecutive_bad = 0

            if state is not None:
                decision = engine.decide(state, cfg)
                if state.get("phase") == "waiting":
                    last_action_signature = None
                    execution = execute_action(cfg, state, decision, dry_run=dry_run)
                elif perception.last_used_cache:
                    execution = "skipped: cached state, no taps"
                elif dry_run:
                    execution = execute_action(cfg, state, decision, dry_run=True)
                else:
                    signature = _state_signature(state)
                    if signature == last_action_signature:
                        execution = "skipped: unchanged state already acted on"
                    else:
                        execution = execute_action(cfg, state, decision, dry_run=False)
                        last_action_signature = signature
        except Exception as exc:
            if not bad_this_iteration:
                consecutive_bad += 1
            message = _one_line(exc)
            warning = f"{warning}; {message}" if warning else message
            execution = "error"

        _iteration_log(state, decision, execution, warning)
        if consecutive_bad >= 3:
            logging.critical(
                "!!! THREE CONSECUTIVE BAD STATES — PAUSING FOR %.1f SECONDS; "
                "CHECK THE EMULATOR AND PRESS CTRL+C TO STOP !!!",
                pause_delay,
            )
            if not once:
                time.sleep(pause_delay)
            consecutive_bad = 0

        if once:
            break
        time.sleep(loop_delay)


def main() -> None:
    default_config = Path(__file__).resolve().with_name("config.yaml")
    parser = argparse.ArgumentParser(
        description="Local Pure Poker vision/automation bot (dry-run by default)"
    )
    parser.add_argument("--config", default=str(default_config))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="Read and decide without taps (default)"
    )
    mode.add_argument("--live", action="store_true", help="Enable calibrated taps/swipes")
    parser.add_argument("--once", action="store_true", help="Run one iteration and exit")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        run(load_config(args.config), dry_run=not args.live, once=args.once)
    except KeyboardInterrupt:
        logging.critical("KILL SWITCH: Ctrl+C received; bot stopped without further taps")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        logging.error("Startup failed: %s", _one_line(exc))
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
