"""Select a mirrored iPhone region with a fullscreen Tk overlay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _region(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Region must be [x, y, width, height]")
    x, y, width, height = (int(item) for item in value)
    if width <= 0 or height <= 0:
        raise ValueError("Region width and height must be positive")
    return [x, y, width, height]


def yaml_snippet(region: list[int]) -> str:
    x, y, width, height = _region(region)
    return f"iphone:\n  region: [{x}, {y}, {width}, {height}]"


def save_region(region: list[int], output: str | Path) -> Path:
    normalized = _region(region)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"region": normalized}, indent=2) + "\n", encoding="utf-8")
    return path


class RegionSelector:
    def __init__(self, output: Path):
        import tkinter as tk

        self.output = output
        self.result: list[int] | None = None
        self.rect: list[int] | None = None
        self.start = (0, 0)
        self.original: list[int] | None = None
        self.mode = "create"
        self.root = tk.Tk()
        self.root.title("iPhone mirror region calibration")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.40)
        self.root.configure(bg="black", cursor="crosshair")
        self.canvas = tk.Canvas(self.root, bg="black", cursor="crosshair", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            self.root.winfo_screenwidth() // 2,
            45,
            text="Drag over the mirrored iPhone. Drag edges/corners to resize; center to move. Enter saves; Esc cancels.",
            fill="white",
            font=("Arial", 18, "bold"),
            tags="instructions",
        )
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Return>", self.on_save)
        self.root.bind("<Escape>", self.on_cancel)
        self.root.protocol("WM_DELETE_WINDOW", self.on_cancel)

    def _normalized(self) -> list[int] | None:
        if self.rect is None:
            return None
        x1, y1, x2, y2 = self.rect
        return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]

    def _hit_mode(self, x: int, y: int) -> str | None:
        normalized = self._normalized()
        if normalized is None:
            return None
        x1, y1, x2, y2 = normalized
        margin = 12
        if not x1 - margin <= x <= x2 + margin or not y1 - margin <= y <= y2 + margin:
            return None
        horizontal = "w" if abs(x - x1) <= margin else "e" if abs(x - x2) <= margin else ""
        vertical = "n" if abs(y - y1) <= margin else "s" if abs(y - y2) <= margin else ""
        if horizontal or vertical:
            return vertical + horizontal
        if x1 <= x <= x2 and y1 <= y <= y2:
            return "move"
        return None

    def on_press(self, event: Any) -> None:
        self.start = (event.x, event.y)
        self.mode = self._hit_mode(event.x, event.y) or "create"
        self.original = self._normalized()
        if self.mode == "create":
            self.rect = [event.x, event.y, event.x, event.y]

    def on_drag(self, event: Any) -> None:
        if self.rect is None:
            return
        dx = event.x - self.start[0]
        dy = event.y - self.start[1]
        if self.mode == "create":
            self.rect = [self.start[0], self.start[1], event.x, event.y]
        elif self.original is not None:
            x1, y1, x2, y2 = self.original
            if self.mode == "move":
                self.rect = [x1 + dx, y1 + dy, x2 + dx, y2 + dy]
            else:
                if "n" in self.mode:
                    y1 += dy
                if "s" in self.mode:
                    y2 += dy
                if "w" in self.mode:
                    x1 += dx
                if "e" in self.mode:
                    x2 += dx
                self.rect = [x1, y1, x2, y2]
        self.draw()

    def on_release(self, _event: Any) -> None:
        normalized = self._normalized()
        if normalized is not None:
            self.rect = normalized
        self.draw()

    def draw(self) -> None:
        self.canvas.delete("region")
        normalized = self._normalized()
        if normalized is None:
            return
        x1, y1, x2, y2 = normalized
        width, height = x2 - x1, y2 - y1
        self.canvas.create_rectangle(
            x1, y1, x2, y2, fill="#22c55e", stipple="gray25",
            outline="#ffffff", width=3, tags="region",
        )
        self.canvas.create_text(
            x1 + 8, max(80, y1 - 20), anchor="w",
            text=f"{width} × {height}  |  Enter saves; drag again or resize",
            fill="white", font=("Arial", 15, "bold"), tags="region",
        )

    def on_save(self, _event: Any = None) -> None:
        normalized = self._normalized()
        if normalized is None:
            return
        x1, y1, x2, y2 = normalized
        if x2 - x1 < 5 or y2 - y1 < 5:
            return
        self.result = [
            self.root.winfo_rootx() + x1,
            self.root.winfo_rooty() + y1,
            x2 - x1,
            y2 - y1,
        ]
        self.root.destroy()

    def on_cancel(self, _event: Any = None) -> None:
        self.result = None
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Visually select the mirrored iPhone region")
    parser.add_argument("--output", default="logs/iphone_region.json")
    args = parser.parse_args()
    try:
        selector = RegionSelector(Path(args.output))
        selector.root.mainloop()
    except Exception as exc:
        raise SystemExit(
            f"Calibration overlay unavailable → set iphone.region manually in config.yaml. {exc}"
        ) from exc
    if selector.result is None:
        print("Calibration cancelled; config.yaml was not changed.")
        return
    path = save_region(selector.result, args.output)
    print(f"Saved {selector.result} to {path}")
    print("Replace the existing iphone.region value with this YAML setting:")
    print(yaml_snippet(selector.result))
    print("WARNING: keep mirror resolution, orientation, and scale FIXED after calibration; CV and vision both assume stable geometry.")


if __name__ == "__main__":
    main()
