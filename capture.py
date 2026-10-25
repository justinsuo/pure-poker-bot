"""Pluggable iPhone screen capture; no device automation or ADB."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import cv2
import numpy as np


IPHONE_FIX = (
    "iPhone not detected → Settings > Privacy & Security > Developer Mode ON, "
    "unlock phone, trust this computer"
)


def _iphone_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("iphone", {})
    if not isinstance(section, dict):
        raise ValueError("iphone config must be a mapping → fix the iphone: section in config.yaml")
    return section


def _bgr(frame: np.ndarray) -> np.ndarray:
    if not isinstance(frame, np.ndarray) or frame.size == 0:
        raise RuntimeError("Empty frame → unlock the iPhone and make its mirrored screen visible")
    if frame.dtype != np.uint8:
        frame = frame.astype(np.uint8)
    if frame.ndim != 3 or frame.shape[2] not in (3, 4):
        raise RuntimeError("Frame is not BGR/BGRA → select the mirrored phone camera or fixed screen region")
    if frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    return np.ascontiguousarray(frame)


def _region(value: Any) -> dict[str, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Invalid region → set iphone.region to [x, y, width, height]")
    try:
        x, y, width, height = (int(item) for item in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid region → use four integer values [x, y, width, height]") from exc
    if width <= 0 or height <= 0:
        raise ValueError("Invalid region → width and height must be positive")
    return {"left": x, "top": y, "width": width, "height": height}


class ScreenSource(ABC):
    @abstractmethod
    def warmup(self) -> None:
        """Prepare the source and raise a fixable error if unavailable."""

    @abstractmethod
    def grab(self) -> np.ndarray:
        """Return one BGR uint8 frame."""

    @abstractmethod
    def frame_size(self) -> tuple[int, int]:
        """Return ``(width, height)`` for the current source."""

    @abstractmethod
    def close(self) -> None:
        """Release source resources."""


class MacQuickTimeSource(ScreenSource):
    def __init__(self, cfg: dict[str, Any]):
        section = _iphone_cfg(cfg)
        self.index = int(section.get("device_index", 0))
        self.fourcc = str(section.get("fourcc", "") or "")
        self.capture: cv2.VideoCapture | None = None
        self.size: tuple[int, int] | None = None

    def warmup(self) -> None:
        if not sys.platform == "darwin":
            raise RuntimeError(
                "mac_quicktime requires macOS → use mirror_window or usb_screenshot on this computer"
            )
        if len(self.fourcc) not in (0, 4):
            raise ValueError("Invalid fourcc → use four characters such as AVC1 or leave it empty")
        self.capture = cv2.VideoCapture(self.index, cv2.CAP_AVFOUNDATION)
        if self.fourcc:
            code = cv2.VideoWriter_fourcc(*self.fourcc)
            self.capture.set(cv2.CAP_PROP_FOURCC, code)
        if not self.capture.isOpened():
            raise RuntimeError(
                IPHONE_FIX + "; select iPhone in QuickTime and run python tools/list_devices.py"
            )
        self.grab()

    def grab(self) -> np.ndarray:
        if self.capture is None:
            self.warmup()
        assert self.capture is not None
        for attempt in range(5):
            ok, frame = self.capture.read()
            if ok and frame is not None:
                result = _bgr(frame)
                self.size = (result.shape[1], result.shape[0])
                return result
            if attempt < 4:
                time.sleep(0.05)
        raise RuntimeError(
            "QuickTime frame grab failed after 5 tries → " + IPHONE_FIX
            + "; in QuickTime choose File > New Movie Recording and select the iPhone camera"
        )

    def frame_size(self) -> tuple[int, int]:
        if self.size is None:
            self.grab()
        assert self.size is not None
        return self.size

    def close(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None


class MirrorWindowSource(ScreenSource):
    def __init__(self, cfg: dict[str, Any]):
        section = _iphone_cfg(cfg)
        self.configured_region = section.get("region")
        self.title = str(section.get("window_title_contains", "") or "")
        self.region: dict[str, int] | None = None
        self.mss_instance: Any = None

    def _find_window(self) -> dict[str, int]:
        if not self.title:
            raise RuntimeError(
                "Mirror window not configured → run python tools/calibrate_region.py and set iphone.region"
            )
        if sys.platform != "win32":
            raise RuntimeError(
                "Automatic mirror-window lookup needs Windows → on macOS set iphone.region with tools/calibrate_region.py"
            )
        try:
            import pygetwindow as gw
        except ImportError as exc:
            raise RuntimeError(
                "Mirror window search needs pygetwindow → python -m pip install pygetwindow, "
                "or set iphone.region with tools/calibrate_region.py"
            ) from exc
        try:
            windows = []
            for window in gw.getAllWindows():
                title = str(getattr(window, "title", ""))
                if self.title.lower() not in title.lower():
                    continue
                if bool(getattr(window, "isMinimized", False)):
                    continue
                if int(window.width) > 0 and int(window.height) > 0:
                    windows.append(window)
        except Exception as exc:
            raise RuntimeError(
                "Window enumeration failed → set iphone.region manually with tools/calibrate_region.py"
            ) from exc
        if not windows:
            raise RuntimeError(
                f"Mirror window containing {self.title!r} not found → open the mirror app, "
                "run python tools/list_devices.py, or set iphone.region"
            )
        window = max(windows, key=lambda item: int(item.width) * int(item.height))
        return _region([window.left, window.top, window.width, window.height])

    def warmup(self) -> None:
        if self.mss_instance is None:
            try:
                import mss
            except ImportError as exc:
                raise RuntimeError("Missing mss → python -m pip install -r requirements.txt") from exc
            self.mss_instance = mss.mss()
        self.region = (
            _region(self.configured_region)
            if self.configured_region is not None
            else self._find_window()
        )

    def grab(self) -> np.ndarray:
        if self.mss_instance is None or self.region is None:
            self.warmup()
        assert self.mss_instance is not None and self.region is not None
        try:
            shot = self.mss_instance.grab(self.region)
            result = _bgr(np.asarray(shot))
        except Exception as exc:
            raise RuntimeError(
                "Mirror capture failed → keep the mirror visible and recalibrate iphone.region "
                "with tools/calibrate_region.py"
            ) from exc
        expected = (self.region["height"], self.region["width"], 3)
        if result.shape != expected:
            raise RuntimeError(
                f"Mirror size changed → expected {expected[1]}x{expected[0]}, got "
                f"{result.shape[1]}x{result.shape[0]}; recalibrate iphone.region"
            )
        return result

    def frame_size(self) -> tuple[int, int]:
        if self.region is None:
            self.warmup()
        assert self.region is not None
        return self.region["width"], self.region["height"]

    def close(self) -> None:
        if self.mss_instance is not None:
            self.mss_instance.close()
            self.mss_instance = None


class USBScreenshotSource(ScreenSource):
    """One-shot pymobiledevice3 capture.

    iOS 17+ may need ``pymobiledevice3 remote start-tunnel`` first (admin on Windows).
    """

    def __init__(self, cfg: dict[str, Any]):
        section = _iphone_cfg(cfg)
        self.command = str(
            section.get("pmd3_command", "pymobiledevice3 developer dvt screenshot")
        )
        self.shot_path = Path(str(section.get("shot_path", "logs/_iphone_shot.png")))
        self.timeout_s = float(section.get("usb_timeout_s", 30))
        self.size: tuple[int, int] | None = None

    def _parts(self) -> list[str]:
        try:
            parts = shlex.split(self.command)
        except ValueError as exc:
            raise RuntimeError("Invalid pmd3_command → check quoting in config.yaml") from exc
        if not parts:
            raise RuntimeError("Empty pmd3_command → set it to pymobiledevice3 developer dvt screenshot")
        return parts

    def warmup(self) -> None:
        executable = self._parts()[0]
        if shutil.which(executable) is None:
            raise RuntimeError(
                f"{executable} not found → python -m pip install pymobiledevice3; "
                "also unlock the iPhone and trust this computer"
            )

    def grab(self) -> np.ndarray:
        self.warmup()
        self.shot_path.parent.mkdir(parents=True, exist_ok=True)
        parts = self._parts()
        has_placeholder = any("{shot}" in part for part in parts)
        command = [part.replace("{shot}", str(self.shot_path)) for part in parts]
        if not has_placeholder:
            command.append(str(self.shot_path))
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "USB screenshot timed out → unlock phone; on iOS 17+ run "
                "pymobiledevice3 remote start-tunnel first (admin on Windows)"
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()[:400]
            raise RuntimeError(
                "USB screenshot failed → unlock and trust; on iOS 17+ run "
                f"pymobiledevice3 remote start-tunnel first (admin on Windows). {detail}"
            )
        try:
            payload = self.shot_path.read_bytes()
        except OSError as exc:
            raise RuntimeError(
                f"Screenshot file missing at {self.shot_path} → check pmd3_command output path"
            ) from exc
        frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("USB screenshot is not a PNG → check pymobiledevice3 and tunnel status")
        result = _bgr(frame)
        self.size = (result.shape[1], result.shape[0])
        return result

    def frame_size(self) -> tuple[int, int]:
        if self.size is None:
            self.grab()
        assert self.size is not None
        return self.size

    def close(self) -> None:
        return None


def make_source(cfg: dict[str, Any]) -> ScreenSource:
    backend = str(_iphone_cfg(cfg).get("backend", "mirror_window"))
    sources = {
        "mac_quicktime": MacQuickTimeSource,
        "mirror_window": MirrorWindowSource,
        "usb_screenshot": USBScreenshotSource,
    }
    if backend not in sources:
        raise ValueError(
            f"Unknown iphone.backend {backend!r} → choose mac_quicktime, mirror_window, or usb_screenshot"
        )
    return sources[backend](cfg)
