"""List macOS camera indices or Windows mirror windows for iPhone setup."""

from __future__ import annotations

import sys

import cv2


IPHONE_FIX = (
    "iPhone not detected → Settings > Privacy & Security > Developer Mode ON, "
    "unlock phone, trust this computer"
)


def list_mac_cameras() -> int:
    found = 0
    for index in range(16):
        capture = None
        try:
            capture = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
            if not capture.isOpened():
                continue
            ok, frame = capture.read()
            if ok and frame is not None:
                found += 1
                print(
                    f"index {index} → {frame.shape[1]}x{frame.shape[0]} | "
                    "USER VERIFY: choose this index if it shows the live iPhone feed"
                )
            else:
                print(f"index {index} → opened, but no frame; unlock the iPhone and retry")
        except Exception as exc:
            print(f"index {index} → probe failed: {exc}")
        finally:
            if capture is not None:
                capture.release()
    if not found:
        print(IPHONE_FIX)
        print("In QuickTime choose File > New Movie Recording and select the iPhone camera.")
    else:
        print("Set iphone.device_index to the verified live iPhone index in config.yaml.")
    return 0 if found else 1


def list_windows() -> int:
    try:
        import pygetwindow as gw
    except ImportError:
        print("pygetwindow is missing → python -m pip install pygetwindow")
        print("Alternative: run python tools/calibrate_region.py and set iphone.region directly.")
        return 1
    found = 0
    try:
        for window in gw.getAllWindows():
            title = str(getattr(window, "title", "")).strip()
            if not title:
                continue
            found += 1
            minimized = bool(getattr(window, "isMinimized", False))
            print(
                f"{title} → rect=[{window.left}, {window.top}, {window.width}, {window.height}]"
                + (" | minimized" if minimized else "")
            )
    except Exception as exc:
        print(f"Window enumeration failed → {exc}; use tools/calibrate_region.py instead")
        return 1
    if not found:
        print("No titled windows found → open the mirror app and make its window visible")
        return 1
    print("Set iphone.window_title_contains to unique title text, or use a listed rect as iphone.region.")
    return 0


def main() -> int:
    if sys.platform == "darwin":
        return list_mac_cameras()
    if sys.platform == "win32":
        return list_windows()
    print("Automatic device listing supports macOS and Windows.")
    print("For usb_screenshot, test pymobiledevice3 directly; see README_IPHONE.md.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
