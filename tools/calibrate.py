"""Capture a screen and overlay a numbered grid for button calibration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import adb  # noqa: E402


def _outlined_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    scale: float,
    color: tuple[int, int, int],
) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(image, text, origin, font, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, text, origin, font, scale, color, 1, cv2.LINE_AA)


def calibrate(cfg: dict, output: str | Path) -> Path:
    png = adb.screencap_png(cfg)
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("OpenCV could not decode the ADB screenshot")
    height, width = image.shape[:2]
    if height < 10 or width < 10:
        raise RuntimeError(f"Screenshot is too small to calibrate: {width}x{height}")

    x_lines = [min(width - 1, round(index * width / 10)) for index in range(11)]
    y_lines = [min(height - 1, round(index * height / 10)) for index in range(11)]
    for x in x_lines:
        cv2.line(image, (x, 0), (x, height - 1), (180, 180, 40), 1, cv2.LINE_AA)
    for y in y_lines:
        cv2.line(image, (0, y), (width - 1, y), (180, 180, 40), 1, cv2.LINE_AA)

    cell_scale = max(0.30, min(0.60, width / 1800.0))
    for row in range(10):
        for column in range(10):
            number = f"{row * 10 + column:02d}"
            x0, x1 = x_lines[column], x_lines[column + 1]
            y0, y1 = y_lines[row], y_lines[row + 1]
            (text_width, text_height), _ = cv2.getTextSize(
                number, cv2.FONT_HERSHEY_SIMPLEX, cell_scale, 1
            )
            origin = (
                x0 + max(2, (x1 - x0 - text_width) // 2),
                y0 + max(text_height + 2, (y1 - y0 + text_height) // 2),
            )
            _outlined_text(image, number, origin, cell_scale, (80, 255, 255))

    pixel_scale = max(0.28, min(0.45, width / 2400.0))
    for x in range(0, width, 50):
        origin = (min(x + 2, max(0, width - 45)), 18)
        _outlined_text(image, str(x), origin, pixel_scale, (0, 255, 0))
    for y in range(0, height, 50):
        origin = (2, min(y + 40, height - 4))
        _outlined_text(image, str(y), origin, pixel_scale, (0, 255, 0))

    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), image):
        raise RuntimeError(f"Could not write calibration image: {destination}")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a 10x10 Pure Poker calibration grid")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.yaml"))
    parser.add_argument("--output", default="screen_grid.png")
    args = parser.parse_args()
    with Path(args.config).open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    if not isinstance(cfg, dict):
        raise ValueError("config file must contain a YAML mapping")
    destination = calibrate(cfg, args.output)
    print(destination)


if __name__ == "__main__":
    main()
