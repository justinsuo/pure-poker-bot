from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import advisor
import capture
import output
from tools import calibrate_region, list_devices


def project_cfg(backend="mirror_window"):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg["engine"]["mc_iterations"] = 10
    cfg["engine"]["seed"] = 7
    cfg["iphone"]["backend"] = backend
    cfg["iphone"]["output"] = {"console": True, "overlay": False, "tts": False}
    return cfg


def contract_state():
    return {
        "stage": "flop",
        "board": ["7h", "Qc", "2s"],
        "my_cards": ["Ah", "Kd"],
        "pot": 450,
        "to_call": 30,
        "position": "BTN",
        "my_stack": 980,
        "opponents": [{"stack": 1200, "last_action": "call"}],
        "confidence": 0.93,
    }


def legacy_state():
    return {
        "phase": "flop",
        "hole": ["Ah", "Kd"],
        "board": ["7h", "Qc", "2s"],
        "pot": 450,
        "to_call": 30,
        "min_raise_to": 60,
        "max_raise_to": 980,
        "my_position": "BTN",
        "aggressors": 0,
        "limpers": 0,
        "note": "",
    }


def fake_source(frame=None):
    source = Mock()
    source.grab.return_value = (
        np.zeros((1280, 720, 3), dtype=np.uint8) if frame is None else frame
    )
    source.frame_size.return_value = (720, 1280)
    return source


def fake_output():
    result = Mock()
    result.render = Mock()
    result.pump = Mock()
    result.close = Mock()
    return result


class ContractVision:
    def perceive(self, frame_bgr: np.ndarray) -> dict:
        assert frame_bgr.shape == (1280, 720, 3)
        assert frame_bgr.dtype == np.uint8
        return contract_state()


class LegacyVision:
    last_used_cache = False

    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0

    def perceive(self, png_bytes: bytes):
        assert isinstance(png_bytes, bytes)
        assert png_bytes.startswith(b"\x89PNG")
        item = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        if isinstance(item, Exception):
            raise item
        return item


class AdvisorTests(unittest.TestCase):
    def test_required_mock_smoke_renders_call_30(self):
        decide = Mock(return_value={
            "action": "CALL", "size": 30, "equity": 0.41, "reasoning": "pot odds",
        })
        instance = advisor.Advisor(
            project_cfg(), source=fake_source(), vision=ContractVision(), decide_fn=decide
        )
        stdout = io.StringIO()
        with self.assertLogs(level="INFO") as logs, redirect_stdout(stdout):
            decision = instance.cycle()
        self.assertEqual(decision["action"], "CALL")
        self.assertEqual(decision["size"], 30)
        self.assertIn("CALL 30   equity≈41%   pot=450", stdout.getvalue())
        self.assertIn("capture_ms=", logs.output[0])
        self.assertIn("vision_ms=", logs.output[0])
        self.assertIn("decide_ms=", logs.output[0])
        engine_state = decide.call_args.args[0]
        self.assertEqual(engine_state["phase"], "flop")
        self.assertEqual(engine_state["max_raise_to"], 980)
        instance.close()

    def test_legacy_perception_and_decision_adapter(self):
        decide = Mock(return_value={"action": "call", "amount": 30, "equity": 0.42, "reason": "legacy"})
        instance = advisor.Advisor(
            project_cfg(), source=fake_source(),
            vision=LegacyVision([(legacy_state(), "")]), decide_fn=decide, output=fake_output(),
        )
        decision = instance.cycle()
        self.assertEqual(decision, {"action": "CALL", "size": 30, "equity": 0.42, "reasoning": "legacy"})
        self.assertIs(decide.call_args.args[0], decide.call_args.args[0])
        self.assertEqual(decide.call_args.args[0]["min_raise_to"], 60)
        instance.close()

    def test_perception_cache_and_expiry(self):
        vision = LegacyVision([(legacy_state(), ""), RuntimeError("vision failed")])
        decide = Mock(return_value={"action": "CALL", "size": 30, "equity": 0.4, "reasoning": "cached"})
        instance = advisor.Advisor(
            project_cfg(), source=fake_source(), vision=vision, decide_fn=decide, output=fake_output()
        )
        self.assertEqual(instance.cycle()["action"], "CALL")
        with self.assertLogs(level="INFO") as logs:
            self.assertEqual(instance.cycle()["action"], "CALL")
        self.assertIn("advisor cache age", logs.output[-1])
        instance._cache = (instance._cache[0], instance._cache[1], instance._cache[2] - 6)
        self.assertIsNone(instance.cycle())
        instance.close()

    def test_waiting_state_does_not_render(self):
        instance = advisor.Advisor(
            project_cfg(), source=fake_source(),
            vision=LegacyVision([({"phase": "waiting", "note": ""}, "")]), output=fake_output(),
        )
        with self.assertLogs(level="INFO") as logs:
            self.assertIsNone(instance.cycle())
        instance.output.render.assert_not_called()
        self.assertIn("waiting for my turn", logs.output[-1])
        instance.close()

    def test_crop_portrait_and_validation(self):
        frame = np.zeros((1280, 720, 3), dtype=np.uint8)
        cropped = advisor.apply_crop(frame, [10, 20, 300, 900])
        self.assertEqual(cropped.shape, (900, 300, 3))
        self.assertTrue(cropped.flags.c_contiguous)
        for crop in ([0, 0, 100], [-1, 0, 100, 100], [700, 0, 100, 100]):
            with self.assertRaises(ValueError):
                advisor.apply_crop(frame, crop)

    def test_state_and_decision_normalizers(self):
        normalized = advisor.normalize_game_state(legacy_state())
        self.assertEqual(normalized["stage"], "flop")
        self.assertEqual(normalized["my_cards"], ["Ah", "Kd"])
        self.assertEqual(normalized["my_stack"], 980)
        state = contract_state()
        state["opponents"].append({"stack": 50, "last_action": "raise"})
        mapped = advisor._legacy_engine_state(state)
        self.assertEqual(mapped["aggressors"], 1)
        self.assertEqual(mapped["min_raise_to"], 60)
        self.assertEqual(
            advisor.normalize_decision({"action": "CHECK", "amount": 0, "reason": "free"}, state),
            {"action": "CHECK", "size": None, "equity": None, "reasoning": "free"},
        )

    def test_enter_fallback_triggers_decision(self):
        instance = advisor.Advisor(
            project_cfg(), source=fake_source(), vision=ContractVision(),
            decide_fn=Mock(return_value={"action": "CALL", "size": 30}), output=fake_output(),
        )
        instance.cycle = Mock()
        original_import = advisor.importlib.import_module

        def import_without_keyboard(name):
            if name == "keyboard":
                raise ImportError("no keyboard")
            return original_import(name)

        with patch("advisor.importlib.import_module", side_effect=import_without_keyboard), patch(
            "builtins.input", side_effect=["", EOFError()]
        ):
            instance.run_hotkey()
        self.assertEqual(instance.cycle.call_count, 1)
        instance.close()

    def test_hotkey_redecides_without_restart(self):
        instance = advisor.Advisor(
            project_cfg(), source=fake_source(), vision=ContractVision(),
            decide_fn=Mock(return_value={"action": "CALL", "size": 30}), output=fake_output(),
        )
        instance.cycle = Mock()
        keyboard = SimpleNamespace(callback=None, unhook_all=Mock())
        keyboard.on_press_key = Mock(side_effect=lambda _key, cb: setattr(keyboard, "callback", cb))
        pumps = [0]

        def pump():
            pumps[0] += 1
            if pumps[0] == 1:
                keyboard.callback(object())
            else:
                raise KeyboardInterrupt()

        instance.output.pump = pump
        instance._hotkey_event.set()
        with patch("advisor.importlib.import_module", return_value=keyboard):
            with self.assertRaises(KeyboardInterrupt):
                instance.run_hotkey()
        self.assertEqual(instance.cycle.call_count, 2)
        keyboard.unhook_all.assert_called_once()
        instance.close()


class CaptureTests(unittest.TestCase):
    def test_factory_switches_all_backends_by_config_only(self):
        expected = {
            "mac_quicktime": capture.MacQuickTimeSource,
            "mirror_window": capture.MirrorWindowSource,
            "usb_screenshot": capture.USBScreenshotSource,
        }
        for backend, source_type in expected.items():
            with self.subTest(backend=backend):
                source = capture.make_source(project_cfg(backend))
                self.assertIsInstance(source, source_type)
                source.close()
        with self.assertRaises(ValueError):
            capture.make_source(project_cfg("unknown"))

    def test_fixed_region_mirror_capture(self):
        cfg = project_cfg()
        cfg["iphone"]["region"] = [10, 20, 720, 1280]
        bgra = np.zeros((1280, 720, 4), dtype=np.uint8)
        bgra[..., :3] = [10, 20, 30]
        bgra[..., 3] = 255
        mss_instance = SimpleNamespace(grab=Mock(return_value=bgra), close=Mock())
        with patch.dict(sys.modules, {"mss": SimpleNamespace(mss=lambda: mss_instance)}):
            source = capture.MirrorWindowSource(cfg)
            source.warmup()
            frame = source.grab()
        self.assertEqual(frame.shape, (1280, 720, 3))
        self.assertEqual(frame[0, 0].tolist(), [10, 20, 30])
        self.assertEqual(source.frame_size(), (720, 1280))
        mss_instance.grab.assert_called_once_with({"left": 10, "top": 20, "width": 720, "height": 1280})
        source.close()

    def test_windows_auto_find_mirror(self):
        cfg = project_cfg()
        cfg["iphone"]["region"] = None
        window = SimpleNamespace(
            title="ApowerMirror - iPhone", left=100, top=200, width=720, height=1280,
            isMinimized=False,
        )
        fake_gw = SimpleNamespace(getAllWindows=lambda: [window])
        with patch("capture.sys.platform", "win32"), patch.dict(sys.modules, {"pygetwindow": fake_gw}), patch.dict(
            sys.modules, {"mss": SimpleNamespace(mss=lambda: SimpleNamespace(close=lambda: None))}
        ):
            source = capture.MirrorWindowSource(cfg)
            source.warmup()
        self.assertEqual(source.frame_size(), (720, 1280))
        self.assertEqual(source.region["left"], 100)

    def test_mac_quicktime_capture_and_five_try_error(self):
        cfg = project_cfg("mac_quicktime")
        frame = np.zeros((1280, 720, 3), dtype=np.uint8)
        camera = Mock()
        camera.isOpened.return_value = True
        camera.read.return_value = (True, frame)
        with patch("capture.sys.platform", "darwin"), patch("capture.cv2.VideoCapture", return_value=camera) as video:
            source = capture.MacQuickTimeSource(cfg)
            source.warmup()
            self.assertEqual(source.frame_size(), (720, 1280))
            source.close()
        video.assert_called_once_with(0, cv2.CAP_AVFOUNDATION)
        camera.release.assert_called_once()

        failing = Mock()
        failing.read.return_value = (False, None)
        source = capture.MacQuickTimeSource(cfg)
        source.capture = failing
        with self.assertRaisesRegex(RuntimeError, "after 5 tries"):
            source.grab()
        self.assertEqual(failing.read.call_count, 5)

    def test_usb_screenshot_and_tunnel_error(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            cfg = project_cfg("usb_screenshot")
            shot = Path(directory) / "shot.png"
            cfg["iphone"]["shot_path"] = str(shot)

            def run(command, **_kwargs):
                cv2.imwrite(command[-1], np.zeros((1280, 720, 3), dtype=np.uint8))
                return SimpleNamespace(returncode=0, stderr=b"")

            with patch("capture.shutil.which", return_value="/usr/bin/pymobiledevice3"), patch(
                "capture.subprocess.run", side_effect=run
            ) as process:
                source = capture.USBScreenshotSource(cfg)
                frame = source.grab()
            self.assertEqual(frame.shape, (1280, 720, 3))
            self.assertEqual(process.call_args.args[0][-1], str(shot))

            with patch("capture.shutil.which", return_value="/usr/bin/pymobiledevice3"), patch(
                "capture.subprocess.run", return_value=SimpleNamespace(returncode=1, stderr=b"tunnel required")
            ):
                with self.assertRaisesRegex(RuntimeError, "start-tunnel"):
                    source.grab()

    def test_usb_missing_executable_has_fix(self):
        with patch("capture.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "pip install pymobiledevice3"):
                capture.USBScreenshotSource(project_cfg("usb_screenshot")).warmup()


class OutputAndToolsTests(unittest.TestCase):
    def test_console_colors_and_speech_phrases(self):
        state = contract_state()
        decision = {"action": "CALL", "size": 30, "equity": 0.41, "reasoning": "test"}
        self.assertEqual(output.console_line(decision, state), "CALL 30   equity≈41%   pot=450")
        self.assertEqual(output.short_phrase(decision), "Call 30 chips.")
        self.assertEqual(output.short_phrase({"action": "RAISE", "size": 150}), "Raise to 150.")
        self.assertEqual(output.short_phrase({"action": "FOLD"}), "Fold.")
        self.assertEqual(output.short_phrase({"action": "CHECK"}), "Check.")
        self.assertEqual(output.action_color("CALL"), "#22c55e")
        self.assertEqual(output.action_color("RAISE"), "#22c55e")
        self.assertEqual(output.action_color("CHECK"), "#eab308")
        self.assertEqual(output.action_color("FOLD"), "#ef4444")

    def test_overlay_and_tts_failures_fall_back_to_console(self):
        cfg = project_cfg()
        cfg["iphone"]["output"] = {"console": False, "overlay": True, "tts": True}
        with patch("output.TkOverlay", side_effect=RuntimeError("no display")), patch(
            "output.Speaker", side_effect=RuntimeError("no voice")
        ):
            renderer = output.DecisionOutput(cfg)
        self.assertTrue(renderer.console)
        self.assertIsNone(renderer.overlay)
        self.assertIsNone(renderer.speaker)
        stream = io.StringIO()
        with patch("output.sys.stdout", stream):
            renderer.render({"action": "CALL", "size": 30, "equity": 0.41}, contract_state())
        self.assertIn("CALL 30", stream.getvalue())
        renderer.close()

    def test_region_save_and_yaml(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "region.json"
            result = calibrate_region.save_region([100, 200, 720, 1280], path)
            self.assertEqual(json.loads(result.read_text())["region"], [100, 200, 720, 1280])
        self.assertEqual(
            calibrate_region.yaml_snippet([100, 200, 720, 1280]),
            "iphone:\n  region: [100, 200, 720, 1280]",
        )
        with self.assertRaises(ValueError):
            calibrate_region.save_region([0, 0, 0, 10], "unused")

    def test_mac_device_listing_marks_user_verification(self):
        opened = Mock()
        opened.isOpened.return_value = True
        opened.read.return_value = (True, np.zeros((1280, 720, 3), dtype=np.uint8))
        closed = Mock()
        closed.isOpened.return_value = False
        with patch("tools.list_devices.sys.platform", "darwin"), patch(
            "tools.list_devices.cv2.VideoCapture", side_effect=[opened] + [closed] * 15
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(list_devices.main(), 0)
        self.assertIn("index 0 → 720x1280", stdout.getvalue())
        self.assertIn("USER VERIFY", stdout.getvalue())

    def test_windows_device_listing_prints_title_and_rect(self):
        window = SimpleNamespace(title="ApowerMirror", left=1, top=2, width=720, height=1280, isMinimized=False)
        with patch("tools.list_devices.sys.platform", "win32"), patch.dict(
            sys.modules, {"pygetwindow": SimpleNamespace(getAllWindows=lambda: [window])}
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(list_devices.main(), 0)
        self.assertIn("ApowerMirror → rect=[1, 2, 720, 1280]", stdout.getvalue())

    def test_iphone_config_schema(self):
        cfg = project_cfg()
        required = {
            "backend", "device_index", "fourcc", "window_title_contains", "region",
            "pmd3_command", "shot_path", "poll_seconds", "mode", "hotkey", "crop", "output",
        }
        self.assertTrue(required.issubset(cfg["iphone"]))
        self.assertIn("game", cfg)
        self.assertIn("vision", cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
