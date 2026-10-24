from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import adb
import bot
import engine
import hand_eval
import perception
from tools import calibrate


EXPECTED_PROMPT = (
    "You are an OCR module for the mobile poker game Pure Poker, NLH. Read this screenshot and return ONLY minified JSON — no prose, no markdown:\n"
    "{'phase':'preflop|flop|turn|river','hole':['Ah','Kd'],'board':[],'pot':150,'to_call':25,'min_raise_to':75,'max_raise_to':600,'my_position':'UTG|MP|CO|BTN|SB|BB','aggressors':1,'limpers':0,'note':''}\n"
    "Card format: rank (A K Q J T 9-2) + suit (s h d c). Chip labels like 1.5k=1500 or 2m=2000000 must be converted to integers. my_position = best read from my seat relative to the dealer button; BTN means I hold the button. aggressors = completed raises this street before my turn; limpers = calls of the blind. If it is NOT my turn (buttons dimmed / timer on another player) return phase:'waiting'. Estimate unreadable numbers to nearest sensible value and flag in 'note' (max 12 words). Blinds appear near the button."
)


def sample_state(phase="flop"):
    boards = {
        "preflop": [],
        "flop": ["7h", "Qc", "2s"],
        "turn": ["7h", "Qc", "2s", "4d"],
        "river": ["7h", "Qc", "2s", "4d", "9c"],
    }
    return {
        "phase": phase,
        "hole": ["Ah", "Kd"],
        "board": boards[phase],
        "pot": 100,
        "to_call": 30,
        "min_raise_to": 60,
        "max_raise_to": 600,
        "my_position": "BTN",
        "aggressors": 0,
        "limpers": 0,
        "note": "",
    }


def project_config():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


class HandEvaluatorTests(unittest.TestCase):
    def test_all_categories_and_wheel(self):
        cases = [
            (["As", "Ks", "Qs", "Js", "Ts", "2d", "3c"], (8, (14,))),
            (["As", "2s", "3s", "4s", "5s", "Kd", "Qh"], (8, (5,))),
            (["Ah", "2d", "3c", "4s", "5h", "Kd", "Qd"], (4, (5,))),
            (["9h", "9d", "9c", "9s", "Ah", "Kd", "2c"], (7, (9, 14))),
            (["Ah", "Ad", "Ac", "Kd", "Kc", "2s", "3h"], (6, (14, 13))),
            (["Ah", "Kh", "Qh", "9h", "4h", "2c", "3d"], (5, (14, 13, 12, 9, 4))),
            (["Ah", "Ad", "Kd", "Kc", "Qs", "2c", "3h"], (2, (14, 13, 12))),
            (["Ah", "Ad", "Kd", "Qc", "9s", "2c", "3h"], (1, (14, 13, 12, 9))),
            (["Ah", "Kd", "Qc", "9s", "7h", "2c", "3d"], (0, (14, 13, 12, 9, 7))),
        ]
        for cards, expected in cases:
            with self.subTest(cards=cards):
                self.assertEqual(hand_eval.best7(cards), expected)

    def test_two_trips_becomes_best_full_house(self):
        score = hand_eval.best7(["Ah", "Ad", "Ac", "Kh", "Kd", "Kc", "2s"])
        self.assertEqual(score, (6, (14, 13)))

    def test_straight_and_flush_tiebreaks(self):
        broadway = hand_eval.evaluate5(["As", "Kd", "Qc", "Jh", "Ts"])
        wheel = hand_eval.evaluate5(["Ah", "2d", "3c", "4h", "5s"])
        self.assertGreater(broadway, wheel)
        flush_a = hand_eval.evaluate5(["Ah", "Kh", "9h", "5h", "2h"])
        flush_b = hand_eval.evaluate5(["As", "Ks", "9s", "6s", "2s"])
        self.assertGreater(flush_b, flush_a)

    def test_description_and_validation(self):
        pair = hand_eval.best7(["Ah", "Ad", "Kd", "Qc", "9s", "2c", "3h"])
        self.assertEqual(hand_eval.describe(pair), "One Pair, Aces, King kicker")
        self.assertIn("wheel", hand_eval.describe((4, (5,))))
        for cards in (["Ah", "Ah", "Kd", "Qc", "9s", "2c", "3h"], ["Ah", "Kd"]):
            with self.assertRaises(ValueError):
                hand_eval.best7(cards)


class EngineTests(unittest.TestCase):
    def test_preflop_tiers_and_integer_sizing(self):
        self.assertEqual(engine.preflop_tier(["Ah", "Ad"]), 30)
        self.assertEqual(engine.preflop_tier(["2h", "2d"]), 8)
        self.assertEqual(engine.preflop_tier(["Ah", "Kd"]), 17)
        self.assertEqual(engine.preflop_tier(["Ah", "Kh"]), 19)
        self.assertEqual(engine.preflop_raise_to(100, 30, 75, 600), 90)
        self.assertEqual(engine.preflop_raise_to(0, 0, 50, 100), 50)
        self.assertEqual(engine.preflop_raise_to(1000, 100, 75, 80), 80)

    def test_sizing_clamps_over_many_values(self):
        for pot in (0, 1, 37, 100, 10_000, 10**12):
            for to_call in (0, 1, 30, 999):
                for minimum, maximum in ((0, 0), (50, 100), (100, 80), (0, 10**9)):
                    amount = engine.preflop_raise_to(pot, to_call, minimum, maximum)
                    self.assertIsInstance(amount, int)
                    self.assertGreaterEqual(amount, 0)
                    self.assertLessEqual(amount, maximum)

    def test_position_thresholds_and_aggressors(self):
        state = sample_state("preflop")
        state.update(hole=["Ah", "Jd"], to_call=10, pot=30)
        self.assertEqual(engine.decide(state)["action"], "raise")
        state["my_position"] = "CO"
        self.assertEqual(engine.decide(state)["action"], "fold")
        state.update(hole=["Ah", "Qd"], my_position="SB")
        self.assertEqual(engine.decide(state)["action"], "raise")
        state.update(my_position="BTN", aggressors=1)
        self.assertEqual(engine.decide(state)["action"], "fold")

    def test_no_open_limp_and_illegal_raise_fallback(self):
        state = sample_state("preflop")
        state.update(hole=["Ah", "Jd"], to_call=10, pot=30, min_raise_to=0,
                     max_raise_to=0, aggressors=0, limpers=0)
        self.assertEqual(engine.decide(state)["action"], "fold")
        state.update(hole=["As", "Ad"], to_call=30, max_raise_to=20)
        decision = engine.decide(state)
        self.assertEqual(decision["action"], "call")
        self.assertNotEqual(decision["action"], "raise")

    def test_monte_carlo_determinism_and_card_canonicalization(self):
        args = (["ah", "kd"], ["7H", "qc", "2S"], 0, 30)
        first = engine.monte_carlo_equity(*args, seed=42)
        second = engine.monte_carlo_equity(*args, seed=42)
        self.assertEqual(first, second)
        self.assertTrue(0 <= first <= 1)

    def test_board_texture(self):
        self.assertTrue(engine.board_texture(["7h", "7c", "2s"])["paired"])
        self.assertFalse(engine.board_texture(["7h", "7c", "2s"])["wet"])
        self.assertTrue(engine.board_texture(["Ah", "Kh", "Qh"])["three_suited"])
        self.assertTrue(engine.board_texture(["7h", "8c", "9s"])["straightish"])
        self.assertTrue(engine.board_texture(["Ah", "2d", "3c"])["straightish"])

    def test_postflop_decision_ladder(self):
        state = sample_state()
        with patch("engine.monte_carlo_equity", return_value=0.90):
            strong = engine.decide(state)
        self.assertEqual(strong["action"], "raise")
        self.assertEqual(strong["amount"], 160)

        with patch("engine.monte_carlo_equity", return_value=0.70):
            value = engine.decide(state)
        self.assertEqual(value["action"], "raise")
        self.assertEqual(value["amount"], 127)

        with patch("engine.monte_carlo_equity", return_value=0.70):
            safety = engine.decide(dict(state, to_call=200))
        self.assertEqual(safety["action"], "fold")

        with patch("engine.monte_carlo_equity", return_value=0.10):
            free = engine.decide(dict(state, to_call=0))
        self.assertEqual(free["action"], "check")

        wet_state = dict(state, board=["7h", "8h", "9s"], to_call=0)
        with patch("engine.monte_carlo_equity", return_value=0.10), patch(
            "engine._street_rng", return_value=SimpleNamespace(random=lambda: 0.0)
        ):
            bluff = engine.decide(wet_state)
        self.assertEqual(bluff["action"], "raise")

        with patch("engine.monte_carlo_equity", return_value=0.90):
            impossible = engine.decide(dict(state, max_raise_to=20))
        self.assertEqual(impossible["action"], "call")

    def test_engine_cli_json(self):
        command = [
            sys.executable, "-m", "engine", "Ah", "Kd", "flop", "7h Qc 2s",
            "--pot=100", "--call=30", "--pos=BTN", "--aggr=0",
            "--mc-iterations=10", "--seed=7",
        ]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(set(result), {"action", "amount", "equity", "reason"})


class PerceptionTests(unittest.TestCase):
    def test_exact_prompt_and_default_model(self):
        self.assertEqual(perception.EXTRACTION_PROMPT, EXPECTED_PROMPT)
        self.assertEqual(project_config()["vision"]["model"], "qwen2.5vl:7b")

    def test_fenced_json_and_integer_chip_labels(self):
        raw = sample_state()
        raw.update(pot="1.5k", max_raise_to="2m", to_call="25", note="estimated pot")
        text = "Result follows:\n```json\n" + json.dumps(raw) + "\n```"
        state = perception.parse_extraction(text)
        self.assertEqual(state["pot"], 1500)
        self.assertEqual(state["max_raise_to"], 2_000_000)
        self.assertEqual(state["to_call"], 25)
        self.assertEqual(perception.chip_int("1,250"), 1250)
        self.assertEqual(perception.chip_int("1.2345k"), 1235)
        with self.assertRaises(ValueError):
            perception.chip_int(-1)

    def test_literal_waiting_and_invalid_states(self):
        self.assertEqual(perception.parse_extraction(str(sample_state("preflop")))["phase"], "preflop")
        self.assertEqual(perception.parse_extraction("{'phase':'waiting'}"), {"phase": "waiting", "note": ""})
        invalid = sample_state("preflop")
        invalid["board"] = ["2c"]
        with self.assertRaises(ValueError):
            perception.parse_extraction(json.dumps(invalid))
        duplicate = sample_state()
        duplicate["board"][0] = duplicate["hole"][0]
        with self.assertRaises(ValueError):
            perception.parse_extraction(json.dumps(duplicate))

    def test_cache_fresh_reuse_expiry_and_flags(self):
        client = perception.Perception({"vision": {}})
        state = sample_state()
        client._request = Mock(side_effect=[state, RuntimeError("bad frame"), RuntimeError("bad again")])
        first, first_warning = client.perceive(b"png")
        self.assertEqual(first, state)
        self.assertEqual(first_warning, "")
        self.assertFalse(client.last_used_cache)
        cached, warning = client.perceive(b"png")
        self.assertEqual(cached, state)
        self.assertIn("using cached state", warning)
        self.assertTrue(client.last_used_cache)
        client._last_valid_at = time.monotonic() - 6
        expired, _ = client.perceive(b"png")
        self.assertIsNone(expired)
        self.assertFalse(client.last_used_cache)

    def test_bad_frame_never_raises(self):
        client = perception.Perception({"vision": {}})
        with patch.object(client, "_request", side_effect=ValueError("bad JSON")):
            state, warning = client.perceive(b"png")
        self.assertIsNone(state)
        self.assertIn("bad JSON", warning)

    def test_request_contract_without_network(self):
        response = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"choices": [{"message": {"content": json.dumps(sample_state())}}]},
        )
        client = perception.Perception({"vision": {
            "api_base": "http://localhost:11434/v1", "model": "qwen2.5vl:7b",
            "key_env": "", "timeout_s": 10,
        }})
        with patch("perception.requests.post", return_value=response) as post:
            state = client._request(b"\x89PNG\r\n\x1a\n")
        self.assertEqual(state["phase"], "flop")
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["json"]["messages"][0]["content"], EXPECTED_PROMPT)
        self.assertEqual(kwargs["json"]["max_tokens"], 700)
        image_url = kwargs["json"]["messages"][1]["content"][0]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
        self.assertTrue(post.call_args.args[0].endswith("/chat/completions"))


class AdbTests(unittest.TestCase):
    def test_devices(self):
        listing = b"List of attached devices\nemulator-5554\tdevice\noffline-1\toffline\n"
        with patch("adb._execute", return_value=listing):
            self.assertEqual(adb.devices({}), ["emulator-5554"])

    def test_raw_png_is_byte_for_byte_unchanged(self):
        raw = b"\x89PNG\r\n\x1a\nBINARY\r\nDATA\x00\r\nMORE"
        self.assertEqual(adb._normalize_png(raw), raw)

    def test_crlf_mangled_png_is_repaired(self):
        raw = b"\x89PNG\r\n\x1a\nBINARY\nDATA\x00\r\nMORE"
        mangled = raw.replace(b"\n", b"\r\n")
        self.assertNotEqual(mangled, raw)
        self.assertEqual(adb._normalize_png(mangled), raw)

    def test_invalid_png_rejected(self):
        with self.assertRaises(adb.ADBError):
            adb._normalize_png(b"not a png")


class FakePerception:
    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0
        self.last_used_cache = False

    def perceive(self, _png):
        state, warning, cached = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        self.last_used_cache = cached
        return copy.deepcopy(state), warning


class BotTests(unittest.TestCase):
    def setUp(self):
        self.cfg = project_config()

    def test_dry_run_never_taps_and_clamps(self):
        state = sample_state()
        with patch("bot.adb.tap") as tap, patch("bot.adb.swipe") as swipe:
            bot.execute_action(self.cfg, state, {"action": "fold", "amount": 0}, dry_run=True)
            summary = bot.execute_action(self.cfg, state, {"action": "raise", "amount": 99999}, dry_run=True)
        tap.assert_not_called()
        swipe.assert_not_called()
        self.assertIn("amount=600/600", summary)

    def test_live_requires_calibration_gate(self):
        with self.assertRaisesRegex(ValueError, "live actions are locked"):
            bot.execute_action(self.cfg, sample_state(), {"action": "fold", "amount": 0}, dry_run=False)
        with self.assertRaisesRegex(ValueError, "live actions are locked"):
            bot.run(self.cfg, dry_run=False, once=True)

    def test_live_slider_and_confirmation_math(self):
        self.cfg["game"]["calibrated"] = True
        self.cfg["game"]["buttons"]["raise_confirm"] = [1000, 1800]
        with patch("bot.adb.tap") as tap, patch("bot.adb.swipe") as swipe, patch("bot.time.sleep"):
            summary = bot.execute_action(self.cfg, sample_state(), {"action": "raise", "amount": 300}, dry_run=False)
        self.assertEqual(tap.call_args_list, [call(self.cfg, 960, 1570), call(self.cfg, 1000, 1800)])
        swipe.assert_called_once_with(self.cfg, 250, 1450, 590, 1450, 450)
        self.assertIn("confirmation tapped", summary)

    def test_cached_state_never_acts(self):
        state = sample_state("preflop")
        self.cfg["game"]["calibrated"] = True
        fake = FakePerception([(state, "bad; using cached state", True)])
        with patch("bot.Perception", return_value=fake), patch("bot.adb.screencap_png", return_value=b"png"), patch(
            "bot.execute_action"
        ) as execute, patch("bot._iteration_log") as log:
            bot.run(self.cfg, dry_run=False, once=True)
        execute.assert_not_called()
        self.assertIn("cached state, no taps", log.call_args.args[2])

    def test_unchanged_live_state_acts_only_once(self):
        state = sample_state("preflop")
        self.cfg["game"]["calibrated"] = True
        fake = FakePerception([(state, "", False), (state, "", False)])
        with patch("bot.Perception", return_value=fake), patch("bot.adb.screencap_png", return_value=b"png"), patch(
            "bot.execute_action", return_value="acted"
        ) as execute, patch("bot.time.sleep", side_effect=[None, KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                bot.run(self.cfg, dry_run=False)
        self.assertEqual(execute.call_count, 1)

    def test_waiting_resets_duplicate_guard(self):
        state = sample_state("preflop")
        waiting = {"phase": "waiting", "note": ""}
        self.cfg["game"]["calibrated"] = True
        fake = FakePerception([(state, "", False), (waiting, "", False), (state, "", False)])
        with patch("bot.Perception", return_value=fake), patch("bot.adb.screencap_png", return_value=b"png"), patch(
            "bot.execute_action", return_value="acted"
        ) as execute, patch("bot.time.sleep", side_effect=[None, None, KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                bot.run(self.cfg, dry_run=False)
        active_calls = [
            item for item in execute.call_args_list if item.args[1].get("phase") != "waiting"
        ]
        self.assertEqual(len(active_calls), 2)

    def test_three_bad_frames_pause_once(self):
        cfg = dict(self.cfg, loop_delay_s=1.5, bad_state_pause_s=30)
        with patch("bot.adb.screencap_png", side_effect=RuntimeError("capture failed")), patch(
            "bot.time.sleep", side_effect=[None, None, KeyboardInterrupt()]
        ) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                bot.run(cfg, dry_run=True)
        self.assertEqual([item.args[0] for item in sleep.call_args_list], [1.5, 1.5, 30])

    def test_mocked_capture_vision_decision_action_pipeline(self):
        state = sample_state("preflop")
        state["hole"] = ["As", "Ad"]
        response = SimpleNamespace(
            status_code=200, text="",
            json=lambda: {"choices": [{"message": {"content": json.dumps(state)}}]},
        )
        with patch("bot.adb.screencap_png", return_value=b"png") as capture, patch(
            "perception.requests.post", return_value=response
        ) as post, patch("bot.adb.tap") as tap, patch("bot._iteration_log") as log:
            bot.run(self.cfg, dry_run=True, once=True)
        capture.assert_called_once()
        post.assert_called_once()
        tap.assert_not_called()
        logged_state, decision, execution, warning = log.call_args.args
        self.assertEqual(logged_state["hole"], ["As", "Ad"])
        self.assertEqual(decision["action"], "raise")
        self.assertIn("dry-run", execution)
        self.assertEqual(warning, "")


class CalibrationAndDoctorTests(unittest.TestCase):
    def test_grid_image_is_created(self):
        source = np.full((200, 100, 3), 80, dtype=np.uint8)
        png = cv2.imencode(".png", source)[1].tobytes()
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            output = Path(directory) / "grid.png"
            with patch("tools.calibrate.adb.screencap_png", return_value=png):
                result = calibrate.calibrate({}, output)
            rendered = cv2.imread(str(result))
            self.assertEqual(rendered.shape, source.shape)
            self.assertGreater(int(np.count_nonzero(rendered != source)), 100)

    def test_offline_doctor(self):
        completed = subprocess.run(
            [sys.executable, "tools/doctor.py", "--offline"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("Offline checks passed", completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
