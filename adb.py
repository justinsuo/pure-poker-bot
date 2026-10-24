"""Small, shell-free ADB wrapper used by the poker bot."""

from __future__ import annotations

import subprocess
from typing import Any


class ADBError(RuntimeError):
    """Raised when ADB cannot execute a command successfully."""


def _timeout_s(cfg: dict[str, Any]) -> float:
    return float(cfg.get("adb_timeout_s", 20))


def _execute(
    cfg: dict[str, Any], args: list[str], *, serial: str | None = None
) -> bytes:
    adb_path = str(cfg.get("adb_path") or "adb")
    command = [adb_path]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=_timeout_s(cfg),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise ADBError(f"ADB executable not found: {adb_path}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ADBError(f"ADB command timed out: {' '.join(command)}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ADBError(
            f"ADB command failed ({completed.returncode}): {' '.join(command)}"
            f"{': ' + detail if detail else ''}"
        )
    return completed.stdout


def devices(cfg: dict[str, Any]) -> list[str]:
    """Return serial numbers for devices in the online ``device`` state."""
    output = _execute(cfg, ["devices"]).decode("utf-8", errors="replace")
    result: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of attached devices") or line.startswith("*"):
            continue
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "device":
            result.append(fields[0])
    return result


def _action_serial(cfg: dict[str, Any]) -> str | None:
    configured = str(cfg.get("serial") or "").strip()
    if configured:
        return configured
    available = devices(cfg)
    if len(available) == 1:
        return available[0]
    if not available:
        raise ADBError("No online ADB devices found; enable debugging or connect first")
    raise ADBError(
        "Multiple ADB devices found; set config.yaml serial explicitly: "
        + ", ".join(available)
    )


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _normalize_png(payload: bytes) -> bytes:
    """Preserve raw PNGs and repair only CRLF-mangled ADB output."""
    if payload.startswith(_PNG_SIGNATURE):
        return payload
    normalized = payload.replace(b"\r\n", b"\n")
    if normalized.startswith(_PNG_SIGNATURE):
        return normalized
    preview = payload[:120].decode("utf-8", errors="replace").strip()
    raise ADBError(f"Screenshot is not a PNG; ADB returned: {preview!r}")


def screencap_png(cfg: dict[str, Any]) -> bytes:
    """Capture the device screen and return a binary PNG."""
    payload = _execute(
        cfg, ["exec-out", "screencap", "-p"], serial=_action_serial(cfg)
    )
    return _normalize_png(payload)


def _coordinate(value: int | float, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer")
    try:
        result = int(round(float(value)))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if result < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return result


def tap(cfg: dict[str, Any], x: int, y: int) -> None:
    """Tap device coordinates ``x, y``."""
    px = _coordinate(x, "x")
    py = _coordinate(y, "y")
    _execute(cfg, ["input", "tap", str(px), str(py)], serial=_action_serial(cfg))


def swipe(
    cfg: dict[str, Any],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    dur_ms: int,
) -> None:
    """Drag between two device coordinates over ``dur_ms`` milliseconds."""
    px1 = _coordinate(x1, "x1")
    py1 = _coordinate(y1, "y1")
    px2 = _coordinate(x2, "x2")
    py2 = _coordinate(y2, "y2")
    duration = _coordinate(dur_ms, "dur_ms")
    _execute(
        cfg,
        [
            "input",
            "swipe",
            str(px1),
            str(py1),
            str(px2),
            str(py2),
            str(duration),
        ],
        serial=_action_serial(cfg),
    )
