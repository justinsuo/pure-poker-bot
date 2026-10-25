"""Console, Tk overlay, and optional speech output for iPhoneScreenAdvisor."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from typing import Any, TextIO


def action_color(action: str) -> str:
    action = action.upper()
    if action in ("CALL", "RAISE"):
        return "#22c55e"
    if action == "CHECK":
        return "#eab308"
    if action == "FOLD":
        return "#ef4444"
    return "#f8fafc"


def headline(decision: dict[str, Any]) -> str:
    action = str(decision["action"]).upper()
    size = decision.get("size")
    return f"{action} {int(size)}" if size is not None else action


def equity_label(decision: dict[str, Any]) -> str:
    equity = decision.get("equity")
    return "n/a" if equity is None else f"{float(equity) * 100:.0f}%"


def console_line(decision: dict[str, Any], state: dict[str, Any]) -> str:
    return (
        f"{headline(decision)}   equity≈{equity_label(decision)}   "
        f"pot={int(state.get('pot', 0))}"
    )


def overlay_subline(decision: dict[str, Any]) -> str:
    reasoning = str(decision.get("reasoning", ""))
    return f"equity≈{equity_label(decision)}" + (f" · {reasoning}" if reasoning else "")


def short_phrase(decision: dict[str, Any]) -> str:
    action = str(decision["action"]).upper()
    size = decision.get("size")
    if action == "FOLD":
        return "Fold."
    if action == "CHECK":
        return "Check."
    if action == "CALL":
        return f"Call {int(size)} chips." if size is not None else "Call."
    if action == "RAISE":
        return f"Raise to {int(size)}." if size is not None else "Raise."
    raise ValueError(f"Unsupported action for speech: {action!r}")


def _ansi_enabled(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    try:
        if not stream.isatty():
            return False
    except Exception:
        return False
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
        except Exception:
            return False
    return True


def render_console(
    decision: dict[str, Any], state: dict[str, Any], stream: TextIO | None = None
) -> None:
    stream = stream or sys.stdout
    text = console_line(decision, state)
    if _ansi_enabled(stream):
        ansi = {"CALL": "32", "RAISE": "32", "CHECK": "33", "FOLD": "31"}.get(
            str(decision["action"]).upper(), "37"
        )
        text = f"\033[{ansi};1m{text}\033[0m"
    try:
        print(text, file=stream, flush=True)
    except UnicodeEncodeError:
        print(text.replace("≈", "~").replace("·", "-"), file=stream, flush=True)


class TkOverlay:
    def __init__(self) -> None:
        import tkinter as tk

        self.root = tk.Tk()
        self.root.title("iPhoneScreenAdvisor")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#0f172a")
        width, height = 640, 160
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, screen_h - height - 30)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.resizable(False, False)
        self.root.bind("<Button>", lambda _event: "break")
        self.root.bind("<Key>", lambda _event: "break")
        self.action_label = tk.Label(
            self.root,
            text="WAITING",
            font=("Arial", 52, "bold"),
            bg="#0f172a",
            fg="#f8fafc",
            anchor="center",
        )
        self.action_label.pack(fill="x", padx=12, pady=(8, 0))
        self.detail_label = tk.Label(
            self.root,
            text="Capture a frame for advice",
            font=("Arial", 13),
            bg="#0f172a",
            fg="#e2e8f0",
            wraplength=610,
            justify="center",
        )
        self.detail_label.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.root.update_idletasks()
        self.root.update()

    def update(self, decision: dict[str, Any], _state: dict[str, Any]) -> None:
        action = str(decision["action"]).upper()
        self.action_label.configure(text=headline(decision), fg=action_color(action))
        self.detail_label.configure(text=overlay_subline(decision))
        self.root.update_idletasks()
        self.root.update()

    def pump(self) -> None:
        self.root.update_idletasks()
        self.root.update()

    def close(self) -> None:
        if self.root is not None:
            self.root.destroy()
            self.root = None


class Speaker:
    def __init__(self) -> None:
        import pyttsx3

        self.engine = pyttsx3.init()

    def speak(self, decision: dict[str, Any]) -> None:
        self.engine.say(short_phrase(decision))
        self.engine.runAndWait()

    def close(self) -> None:
        self.engine.stop()


class DecisionOutput:
    def __init__(self, cfg: dict[str, Any]):
        options = cfg.get("iphone", {}).get("output", {})
        self.console = bool(options.get("console", True))
        self.overlay: TkOverlay | None = None
        self.speaker: Speaker | None = None
        if options.get("overlay", True):
            try:
                self.overlay = TkOverlay()
            except Exception as exc:
                logging.warning("Tk overlay unavailable → console-only fallback: %s", exc)
                self.console = True
        if options.get("tts", False):
            try:
                self.speaker = Speaker()
            except Exception as exc:
                logging.warning("TTS unavailable → continuing without speech: %s", exc)
                self.console = True

    def render(self, decision: dict[str, Any], state: dict[str, Any]) -> None:
        printed = False
        if self.console:
            render_console(decision, state)
            printed = True
        if self.overlay is not None:
            try:
                self.overlay.update(decision, state)
            except Exception as exc:
                logging.warning("Tk overlay failed → console-only fallback: %s", exc)
                self.overlay = None
                self.console = True
                if not printed:
                    render_console(decision, state)
        if self.speaker is not None:
            try:
                self.speaker.speak(decision)
            except Exception as exc:
                logging.warning("TTS failed → disabling speech: %s", exc)
                self.speaker = None

    def pump(self) -> None:
        if self.overlay is not None:
            try:
                self.overlay.pump()
            except Exception as exc:
                logging.warning("Tk overlay event loop failed → console-only fallback: %s", exc)
                self.overlay = None
                self.console = True

    def close(self) -> None:
        if self.overlay is not None:
            try:
                self.overlay.close()
            except Exception:
                pass
            self.overlay = None
        if self.speaker is not None:
            try:
                self.speaker.close()
            except Exception:
                pass
            self.speaker = None
