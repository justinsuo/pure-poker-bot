"""Diagnose local setup without taking screenshots or controlling the game."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import adb  # noqa: E402
import engine  # noqa: E402
import hand_eval  # noqa: E402


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def passed(self, name: str, detail: str = "") -> None:
        print(f"[PASS] {name}" + (f": {detail}" if detail else ""))

    def warn(self, name: str, detail: str) -> None:
        self.warnings += 1
        print(f"[WARN] {name}: {detail}")

    def fail(self, name: str, detail: str) -> None:
        self.failures += 1
        print(f"[FAIL] {name}: {detail}")


def _dependencies(report: Report) -> bool:
    okay = True
    for module in ("cv2", "numpy", "yaml", "requests", "mss", "PIL"):
        try:
            imported = importlib.import_module(module)
            version = getattr(imported, "__version__", "installed")
            report.passed(f"Python package {module}", str(version))
        except ImportError as exc:
            report.fail(f"Python package {module}", str(exc))
            okay = False
    return okay


def _load_config(path: Path, report: Report) -> dict[str, Any] | None:
    try:
        yaml = importlib.import_module("yaml")
        with path.open("r", encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle)
        if not isinstance(cfg, dict):
            raise ValueError("top level must be a YAML mapping")
        report.passed("Configuration file", str(path))
        return cfg
    except Exception as exc:
        report.fail("Configuration file", str(exc))
        return None


def _point(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    return all(isinstance(item, (int, float)) and not isinstance(item, bool) and item >= 0 for item in value)


def _validate_config(cfg: dict[str, Any], report: Report, live: bool) -> None:
    try:
        game = cfg["game"]
        buttons = game["buttons"]
        for name in ("fold", "call_check", "raise_open"):
            if not _point(buttons.get(name)):
                raise ValueError(f"game.buttons.{name} must be [x, y] with non-negative numbers")
        slider = buttons.get("slider")
        if slider is not None:
            if not isinstance(slider, dict):
                raise ValueError("game.buttons.slider must be a mapping or null")
            values = [slider.get(key) for key in ("y", "x_min", "x_max")]
            if not all(isinstance(item, (int, float)) and not isinstance(item, bool) and item >= 0 for item in values):
                raise ValueError("slider y, x_min, and x_max must be non-negative numbers")
            if slider["x_max"] < slider["x_min"]:
                raise ValueError("slider x_max must be >= x_min")
        if buttons.get("raise_confirm") is not None and not _point(buttons["raise_confirm"]):
            raise ValueError("raise_confirm must be null or [x, y]")
        if int(cfg["engine"]["mc_iterations"]) <= 0:
            raise ValueError("engine.mc_iterations must be positive")
        if not 0 <= float(cfg["engine"]["bluff_rate"]) <= 1:
            raise ValueError("engine.bluff_rate must be between 0 and 1")
        report.passed("Configuration values")
    except Exception as exc:
        report.fail("Configuration values", str(exc))

    calibrated = bool(cfg.get("game", {}).get("calibrated", False))
    if calibrated:
        report.passed("Calibration gate", "game.calibrated is true")
    elif live:
        report.fail("Calibration gate", "set game.calibrated: true only after calibration")
    else:
        report.warn("Calibration gate", "false is correct until you finish calibration")


def _check_engine(report: Report) -> None:
    try:
        royal = hand_eval.best7(["As", "Ks", "Qs", "Js", "Ts", "2d", "3c"])
        assert royal == (8, (14,))
        state = {
            "phase": "preflop",
            "hole": ["Ah", "Ad"],
            "board": [],
            "pot": 150,
            "to_call": 50,
            "min_raise_to": 100,
            "max_raise_to": 500,
            "my_position": "BTN",
            "aggressors": 0,
            "limpers": 0,
        }
        decision = engine.decide(state)
        assert decision["action"] == "raise"
        assert 0 < decision["amount"] <= 500
        report.passed("Local hand evaluator and engine", "no device or network used")
    except Exception as exc:
        report.fail("Local hand evaluator and engine", str(exc))


def _check_adb(cfg: dict[str, Any], report: Report) -> None:
    try:
        available = adb.devices(cfg)
        configured = str(cfg.get("serial") or "").strip()
        if configured and configured not in available:
            raise RuntimeError(f"configured serial {configured!r} is not online; found {available}")
        if not available:
            raise RuntimeError("no online devices; enable emulator ADB and run adb devices")
        report.passed("ADB connection", ", ".join(available))
    except Exception as exc:
        report.fail("ADB connection", str(exc))


def _check_vision(cfg: dict[str, Any], report: Report) -> None:
    try:
        requests = importlib.import_module("requests")
        vision = cfg["vision"]
        base = str(vision["api_base"]).strip().rstrip("/")
        model = str(vision["model"]).strip()
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        key_env = str(vision.get("key_env", "")).strip()
        key = os.environ.get(key_env, "") if key_env else ""
        host = urlparse(base).hostname or ""
        if host not in ("localhost", "127.0.0.1", "::1") and key_env and not key:
            raise RuntimeError(f"environment variable {key_env} is not set")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        response = requests.get(base + "/models", headers=headers, timeout=15)
        if response.status_code >= 400:
            raise RuntimeError(f"GET /models returned HTTP {response.status_code}: {response.text[:200]}")
        body = response.json()
        entries = body.get("data", []) if isinstance(body, dict) else []
        names = [str(item.get("id") or item.get("name") or "") for item in entries if isinstance(item, dict)]
        if names and model not in names:
            raise RuntimeError(f"model {model!r} is not installed; available: {', '.join(names[:12])}")
        if names:
            report.passed("Vision server and model", model)
        else:
            report.warn("Vision server", "reachable, but /models returned no model names; run a dry-run frame")
    except Exception as exc:
        report.fail("Vision server and model", str(exc))


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Pure Poker bot setup without tapping")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.yaml"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Skip ADB and vision-server checks")
    mode.add_argument("--live", action="store_true", help="Require every live-readiness check")
    args = parser.parse_args()

    report = Report()
    if sys.version_info >= (3, 10):
        report.passed("Python version", sys.version.split()[0])
    else:
        report.fail("Python version", "3.10 or newer is required")
    dependencies_ok = _dependencies(report)
    cfg = _load_config(Path(args.config), report) if dependencies_ok else None
    if cfg is not None:
        _validate_config(cfg, report, args.live)
    _check_engine(report)
    if args.offline:
        print("[SKIP] ADB connection: offline mode")
        print("[SKIP] Vision server and model: offline mode")
    elif cfg is not None:
        _check_adb(cfg, report)
        _check_vision(cfg, report)

    print(f"SUMMARY: {report.failures} failure(s), {report.warnings} warning(s)")
    if report.failures:
        print("Fix every [FAIL] line above, then run this command again.")
        raise SystemExit(1)
    if args.live:
        print("Live-readiness checks passed. Start with python bot.py --live --once.")
    elif args.offline:
        print("Offline checks passed. Connect ADB and Ollama, then run python tools/doctor.py.")
    else:
        print("Setup checks passed. Complete calibration and the 10-hand dry-run checklist.")


if __name__ == "__main__":
    main()
