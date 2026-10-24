"""Vision-LLM perception with robust JSON parsing and short-term caching."""

from __future__ import annotations

import ast
import base64
import copy
import json
import math
import os
import re
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import requests

from hand_eval import RANKS, SUITS


EXTRACTION_PROMPT = (
    "You are an OCR module for the mobile poker game Pure Poker, NLH. Read this screenshot and return ONLY minified JSON — no prose, no markdown:\n"
    "{'phase':'preflop|flop|turn|river','hole':['Ah','Kd'],'board':[],'pot':150,'to_call':25,'min_raise_to':75,'max_raise_to':600,'my_position':'UTG|MP|CO|BTN|SB|BB','aggressors':1,'limpers':0,'note':''}\n"
    "Card format: rank (A K Q J T 9-2) + suit (s h d c). Chip labels like 1.5k=1500 or 2m=2000000 must be converted to integers. my_position = best read from my seat relative to the dealer button; BTN means I hold the button. aggressors = completed raises this street before my turn; limpers = calls of the blind. If it is NOT my turn (buttons dimmed / timer on another player) return phase:'waiting'. Estimate unreadable numbers to nearest sensible value and flag in 'note' (max 12 words). Blinds appear near the button."
)

_POSITIONS = {"UTG", "MP", "CO", "BTN", "SB", "BB"}
_EXPECTED_BOARD = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
_CHIP_PATTERN = re.compile(r"^([+]?(?:\d+(?:\.\d*)?|\.\d+))([kmb]?)$")


def _one_line(value: Any, limit: int = 600) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def chip_int(value: Any, field: str = "chip value") -> int:
    """Normalize integers and labels such as ``1.5k`` without float chip math."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        if isinstance(value, int):
            result = value
        else:
            if isinstance(value, float):
                if not math.isfinite(value):
                    raise ValueError(f"{field} must be finite")
                decimal_value = Decimal(str(value))
            elif isinstance(value, Decimal):
                decimal_value = value
            else:
                compact = str(value).strip().lower().replace(",", "").replace("$", "")
                compact = compact.replace(" ", "")
                match = _CHIP_PATTERN.fullmatch(compact)
                if not match:
                    raise ValueError(f"invalid {field}: {value!r}")
                decimal_value = Decimal(match.group(1))
                multiplier = {
                    "": Decimal(1),
                    "k": Decimal(1_000),
                    "m": Decimal(1_000_000),
                    "b": Decimal(1_000_000_000),
                }[match.group(2)]
                decimal_value *= multiplier
            result = int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if result < 0:
        raise ValueError(f"{field} must be non-negative")
    return result


def _card(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid card: {value!r}")
    compact = value.strip()
    if len(compact) != 2:
        raise ValueError(f"invalid card: {value!r}")
    rank = compact[0].upper()
    suit = compact[1].lower()
    if rank not in RANKS or suit not in SUITS:
        raise ValueError(f"invalid card: {value!r}")
    return rank + suit


def normalize_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a decoded vision response."""
    if not isinstance(raw, dict):
        raise ValueError("vision response is not a JSON object")
    phase = str(raw.get("phase", "")).strip().lower()
    note_value = raw.get("note", "")
    note = "" if note_value is None else str(note_value).strip()
    if phase == "waiting":
        return {"phase": "waiting", "note": note}
    if phase not in _EXPECTED_BOARD:
        raise ValueError(f"invalid phase: {phase!r}")

    hole_raw = raw.get("hole")
    board_raw = raw.get("board")
    if not isinstance(hole_raw, list) or not isinstance(board_raw, list):
        raise ValueError("hole and board must be arrays")
    hole = [_card(card) for card in hole_raw]
    board = [_card(card) for card in board_raw]
    if len(hole) != 2:
        raise ValueError("active states require exactly two hole cards")
    expected_board = _EXPECTED_BOARD[phase]
    if len(board) != expected_board:
        raise ValueError(f"{phase} requires {expected_board} board cards")
    if len(set(hole + board)) != len(hole + board):
        raise ValueError("duplicate cards in hole/board")

    position = str(raw.get("my_position", "")).strip().upper()
    if position not in _POSITIONS:
        raise ValueError(f"invalid position: {position!r}")
    state = {
        "phase": phase,
        "hole": hole,
        "board": board,
        "pot": chip_int(raw.get("pot"), "pot"),
        "to_call": chip_int(raw.get("to_call"), "to_call"),
        "min_raise_to": chip_int(raw.get("min_raise_to"), "min_raise_to"),
        "max_raise_to": chip_int(raw.get("max_raise_to"), "max_raise_to"),
        "my_position": position,
        "aggressors": chip_int(raw.get("aggressors", 0), "aggressors"),
        "limpers": chip_int(raw.get("limpers", 0), "limpers"),
        "note": note,
    }
    if state["max_raise_to"] <= 0:
        raise ValueError("max_raise_to must be positive in an active state")
    return state


def parse_extraction(text: str) -> dict[str, Any]:
    """Strip fences/prose, decode JSON (or safe literal syntax), and validate."""
    if not isinstance(text, str):
        raise ValueError("vision message content is not text")
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found in vision response")
    candidate = stripped[start : end + 1]
    try:
        decoded = json.loads(candidate)
    except json.JSONDecodeError:
        try:
            decoded = ast.literal_eval(candidate)
        except (SyntaxError, ValueError) as exc:
            raise ValueError("could not parse vision JSON") from exc
    return normalize_state(decoded)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
        if pieces:
            return "".join(pieces)
    raise ValueError("vision response has no textual message content")


class Perception:
    """Stateful vision client; failed frames may reuse a sub-five-second state."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self._last_state: dict[str, Any] | None = None
        self._last_valid_at = float("-inf")
        self.last_used_cache = False

    def _request(self, png_bytes: bytes) -> dict[str, Any]:
        if not isinstance(png_bytes, (bytes, bytearray)) or not png_bytes:
            raise ValueError("screenshot PNG bytes are empty")
        vision = self.cfg.get("vision", {})
        if not isinstance(vision, dict):
            raise ValueError("vision config must be a mapping")
        backend = str(vision.get("backend", "vision_llm"))
        if backend != "vision_llm":
            raise ValueError(f"unsupported vision backend: {backend}")
        api_base = str(vision.get("api_base", "")).strip().rstrip("/")
        model = str(vision.get("model", "")).strip()
        if not api_base or not model:
            raise ValueError("vision.api_base and vision.model are required")
        endpoint = (
            api_base
            if api_base.endswith("/chat/completions")
            else api_base + "/chat/completions"
        )
        headers = {"Content-Type": "application/json"}
        key_env = str(vision.get("key_env", "")).strip()
        api_key = os.environ.get(key_env, "") if key_env else ""
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        encoded = base64.b64encode(bytes(png_bytes)).decode("ascii")
        payload = {
            "model": model,
            "temperature": 0,
            "stream": False,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": EXTRACTION_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + encoded},
                        }
                    ],
                },
            ],
        }
        timeout = float(vision.get("timeout_s", 60))
        response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
        if response.status_code >= 400:
            raise RuntimeError(
                f"vision HTTP {response.status_code}: {_one_line(response.text, 240)}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ValueError("vision endpoint returned non-JSON HTTP content") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("vision response is missing choices[0].message.content") from exc
        return parse_extraction(_message_text(content))

    def perceive(self, png_bytes: bytes) -> tuple[dict[str, Any] | None, str]:
        """Return ``(state_or_None, warning)`` and never raise for a bad frame."""
        self.last_used_cache = False
        try:
            state = self._request(png_bytes)
        except Exception as exc:
            warning = _one_line(exc)
            age = time.monotonic() - self._last_valid_at
            if self._last_state is not None and age < 5.0:
                self.last_used_cache = True
                return (
                    copy.deepcopy(self._last_state),
                    f"{warning}; using cached state age {age:.2f}s",
                )
            return None, warning

        self._last_state = copy.deepcopy(state)
        self._last_valid_at = time.monotonic()
        return copy.deepcopy(state), ""
