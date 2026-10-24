"""Local, network-free NLH decision engine."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from typing import Any, Iterable

from hand_eval import best7, parse_card


POSITIONS = ("UTG", "MP", "CO", "BTN", "SB", "BB")
OPEN_THRESHOLDS = {"BTN": 13, "CO": 15, "MP": 18, "SB": 19, "BB": 16, "UTG": 21}
RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "shdc"
FULL_DECK = tuple(rank + suit for rank in RANK_CHARS for suit in SUIT_CHARS)


def _integer(state: dict[str, Any], key: str, default: int = 0) -> int:
    value = state.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{key} must be a non-negative integer") from exc
    if result < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return result


def _validate_cards(cards: Iterable[str], expected: int, label: str) -> list[str]:
    result = list(cards)
    if len(result) != expected:
        raise ValueError(f"{label} must contain exactly {expected} cards")
    parsed = [parse_card(card) for card in result]
    if len(set(parsed)) != len(parsed):
        raise ValueError(f"{label} contains duplicate cards")
    return [RANK_CHARS[rank - 2] + suit for rank, suit in parsed]


def preflop_tier(hole: Iterable[str]) -> int:
    """Score two hole cards from 0 to 30 using ranks, suit, and gap."""
    cards = _validate_cards(hole, 2, "hole")
    ranks = sorted((parse_card(card)[0] for card in cards), reverse=True)
    high, low = ranks
    if high == low:
        # 22 = 8, TT = 24, QQ = 28, KK/AA = 30.
        return max(0, min(30, 8 + 2 * (high - 2)))

    suited = parse_card(cards[0])[1] == parse_card(cards[1])[1]
    gap = high - low - 1
    tier = high + low - 10
    if suited:
        tier += 2
    tier -= max(0, gap)
    return max(0, min(30, tier))


def _clamped_raise_to(
    desired: int, min_raise_to: int, max_raise_to: int
) -> int:
    desired = max(0, int(desired))
    minimum = max(0, int(min_raise_to))
    maximum = max(0, int(max_raise_to))
    if maximum == 0:
        return 0
    return int(min(maximum, max(minimum, desired)))


def preflop_raise_to(
    pot: int, to_call: int, min_raise_to: int, max_raise_to: int
) -> int:
    """Apply the requested preflop sizing formula using integer chip math."""
    forty_percent_pot = (int(pot) * 2) // 5
    desired = int(to_call) + max(2 * int(to_call), forty_percent_pot)
    return _clamped_raise_to(desired, min_raise_to, max_raise_to)


def _postflop_raise_to(
    pot: int,
    to_call: int,
    min_raise_to: int,
    max_raise_to: int,
    numerator: int,
    denominator: int,
) -> int:
    pot_after_call = int(pot) + int(to_call)
    raise_by = (pot_after_call * numerator) // denominator
    desired = int(to_call) + raise_by
    return _clamped_raise_to(desired, min_raise_to, max_raise_to)


def _result(action: str, amount: int, equity: float | None, reason: str) -> dict[str, Any]:
    return {
        "action": action,
        "amount": max(0, int(amount)),
        "equity": None if equity is None else round(float(equity), 6),
        "reason": reason,
    }


def _preflop_decision(state: dict[str, Any]) -> dict[str, Any]:
    hole = _validate_cards(state.get("hole", []), 2, "hole")
    _validate_cards(state.get("board", []), 0, "board")
    position = str(state.get("my_position", "MP")).upper()
    if position not in OPEN_THRESHOLDS:
        raise ValueError(f"Unknown position: {position}")

    pot = _integer(state, "pot")
    to_call = _integer(state, "to_call")
    min_raise_to = _integer(state, "min_raise_to")
    max_raise_to = _integer(state, "max_raise_to")
    aggressors = _integer(state, "aggressors")
    limpers = _integer(state, "limpers")

    tier = preflop_tier(hole)
    threshold = OPEN_THRESHOLDS[position] + 4 * aggressors
    if position in ("SB", "BB"):
        threshold -= 4

    if tier >= threshold:
        amount = preflop_raise_to(pot, to_call, min_raise_to, max_raise_to)
        if amount > to_call:
            return _result(
                "raise",
                amount,
                None,
                f"preflop tier {tier} >= adjusted threshold {threshold}; standard raise sizing",
            )
        if to_call > 0 and limpers == 0 and aggressors == 0 and tier < threshold + 6:
            return _result(
                "fold",
                0,
                None,
                f"no open-limp: tier {tier} < exceptional threshold {threshold + 6}",
            )
        return _result(
            "call" if to_call else "check",
            to_call,
            None,
            f"preflop tier {tier} qualifies, but no legal raise amount is available",
        )

    # Calling behind limpers is permitted; with zero limpers, only the
    # exceptional-tier branch above may call when raising is unavailable.
    if limpers > 0 and aggressors == 0 and tier >= threshold - 5:
        return _result(
            "call" if to_call else "check",
            to_call,
            None,
            f"preflop tier {tier}; call behind {limpers} limper(s), threshold {threshold}",
        )

    if to_call == 0:
        return _result(
            "check",
            0,
            None,
            f"preflop tier {tier} < threshold {threshold}; take the free card",
        )
    return _result(
        "fold",
        0,
        None,
        f"preflop tier {tier} < adjusted threshold {threshold}; no profitable limp",
    )


def board_texture(board: Iterable[str]) -> dict[str, bool]:
    """Return pairing, flush, straight, and aggregate wetness indicators."""
    cards = list(board)
    parsed = [parse_card(card) for card in cards]
    ranks = [rank for rank, _ in parsed]
    rank_set = set(ranks)
    paired = any(count >= 2 for count in Counter(ranks).values())
    three_suited = bool(parsed) and max(Counter(suit for _, suit in parsed).values()) >= 3

    straightish = False
    for high in range(14, 4, -1):
        needed = {14, 2, 3, 4, 5} if high == 5 else set(range(high - 4, high + 1))
        if len(rank_set.intersection(needed)) >= 3:
            straightish = True
            break

    wet = three_suited or straightish
    return {
        "paired": paired,
        "three_suited": three_suited,
        "straightish": straightish,
        "wet": wet,
        "dry": not wet,
    }


def _fisher_yates(cards: list[str], rng: random.Random) -> None:
    for index in range(len(cards) - 1, 0, -1):
        other = rng.randint(0, index)
        cards[index], cards[other] = cards[other], cards[index]


def monte_carlo_equity(
    hole: Iterable[str],
    board: Iterable[str],
    aggressors: int = 0,
    mc_iterations: int = 400,
    seed: int | None = None,
) -> float:
    """Estimate showdown equity against a rejection-sampled loose range."""
    hero = _validate_cards(hole, 2, "hole")
    community = list(board)
    if len(community) not in (3, 4, 5):
        raise ValueError("Postflop board must contain three, four, or five cards")
    known = hero + _validate_cards(community, len(community), "board")
    if len({parse_card(card) for card in known}) != len(known):
        raise ValueError("Hole cards and board contain duplicates")
    iterations = int(mc_iterations)
    if iterations <= 0:
        raise ValueError("mc_iterations must be positive")

    known_set = set(known)
    base_deck = [card for card in FULL_DECK if card not in known_set]
    cards_to_river = 5 - len(community)
    range_floor = 13 if aggressors >= 2 else 9 if aggressors == 1 else 7
    rng = random.Random(seed)
    equity_total = 0.0

    for _ in range(iterations):
        deck = list(base_deck)
        allow_junk = rng.random() < 0.15
        opponent: list[str] | None = None
        for _attempt in range(256):
            _fisher_yates(deck, rng)
            candidate = [deck[0], deck[1]]
            if allow_junk or preflop_tier(candidate) >= range_floor:
                opponent = candidate
                break
        if opponent is None:
            raise RuntimeError("Could not sample an opponent hand from the requested range")

        completion = deck[2 : 2 + cards_to_river]
        final_board = community + completion
        hero_score = best7(hero + final_board)
        opponent_score = best7(opponent + final_board)
        if hero_score > opponent_score:
            equity_total += 1.0
        elif hero_score == opponent_score:
            equity_total += 0.5

    return equity_total / iterations


def _street_rng(
    hole: list[str], board: list[str], street: str, salt: str = ""
) -> random.Random:
    # Built-in hash gives the requested deterministic-ish per-process pacing.
    return random.Random(hash("".join(hole) + "".join(board) + street + salt))


def _mixed_probability(equity: float, texture: dict[str, bool], facing_bet: bool) -> float:
    probability = (0.12 + 0.35 * equity) if facing_bet else (0.25 + 0.65 * equity)
    if texture["wet"]:
        probability -= 0.08
    if texture["paired"] and not texture["wet"]:
        probability += 0.10
    return max(0.05, min(0.85, probability))


def _postflop_decision(state: dict[str, Any], engine_cfg: dict[str, Any]) -> dict[str, Any]:
    phase = str(state.get("phase", "")).lower()
    expected_board = {"flop": 3, "turn": 4, "river": 5}[phase]
    hole = _validate_cards(state.get("hole", []), 2, "hole")
    board = _validate_cards(state.get("board", []), expected_board, "board")
    if len({parse_card(card) for card in hole + board}) != len(hole + board):
        raise ValueError("Hole cards and board contain duplicates")

    position = str(state.get("my_position", "MP")).upper()
    if position not in POSITIONS:
        raise ValueError(f"Unknown position: {position}")
    pot = _integer(state, "pot")
    to_call = _integer(state, "to_call")
    min_raise_to = _integer(state, "min_raise_to")
    max_raise_to = _integer(state, "max_raise_to")
    aggressors = _integer(state, "aggressors")
    iterations = int(engine_cfg.get("mc_iterations", 400))
    bluff_rate = max(0.0, min(1.0, float(engine_cfg.get("bluff_rate", 0.18))))
    seed = engine_cfg.get("seed")

    equity = monte_carlo_equity(hole, board, aggressors, iterations, seed)
    pot_after = pot + to_call
    pot_odds = to_call / pot_after if pot_after > 0 else 0.0
    oop_or_reraised = position in ("SB", "BB") or aggressors >= 2
    continuation_need = pot_odds + 0.02 + (0.03 if oop_or_reraised else 0.0)
    strong_need = max(pot_odds + 0.02, 0.85)
    texture = board_texture(board)

    def raise_amount(numerator: int, denominator: int) -> int:
        return _postflop_raise_to(
            pot,
            to_call,
            min_raise_to,
            max_raise_to,
            numerator,
            denominator,
        )

    def aggressive(
        numerator: int, denominator: int, reason: str
    ) -> dict[str, Any]:
        amount = raise_amount(numerator, denominator)
        if amount <= to_call:
            return _result(
                "call" if to_call else "check",
                to_call,
                equity,
                reason + "; no legal raise amount available",
            )
        return _result("raise", amount, equity, reason)

    if equity >= strong_need:
        return aggressive(
            1,
            1,
            f"equity {equity:.3f} >= strong threshold {strong_need:.3f}; pot-sized bet/raise",
        )

    if to_call == 0:
        free_card_need = max(0.50, continuation_need)
        if equity < free_card_need - 0.15:
            can_bluff = position in ("BTN", "CO") and texture["wet"]
            bluff_probability = bluff_rate * 0.6
            if can_bluff and _street_rng(hole, board, phase).random() < bluff_probability:
                return aggressive(
                    2,
                    3,
                    f"wet-board {position} bluff; equity {equity:.3f}, two-thirds pot",
                )
            return _result(
                "check",
                0,
                equity,
                f"free card; equity {equity:.3f} < weak-hand threshold {free_card_need - 0.15:.3f}",
            )

    if to_call > 0 and pot_odds > 0.5 and equity < pot_odds + 0.12:
        return _result(
            "fold",
            0,
            equity,
            f"safety gate: pot odds {pot_odds:.3f} > 0.50 and equity {equity:.3f} below buffer",
        )

    if equity >= continuation_need:
        if equity >= 0.65:
            return aggressive(
                3,
                4,
                f"equity {equity:.3f} >= 0.65 and need {continuation_need:.3f}; three-quarter pot",
            )

        facing_bet = to_call > 0
        probability = _mixed_probability(equity, texture, facing_bet)
        choose_aggression = _street_rng(hole, board, phase, ":mix").random() < probability
        passive_action = "call" if facing_bet else "check"
        passive_amount = to_call if facing_bet else 0
        texture_note = "wet" if texture["wet"] else "dry"
        if texture["paired"]:
            texture_note += ", paired"
        if choose_aggression:
            return aggressive(
                3,
                4,
                f"mixed aggression p={probability:.3f} on {texture_note} board; equity {equity:.3f}",
            )
        return _result(
            passive_action,
            passive_amount,
            equity,
            f"mixed {passive_action} p={1.0 - probability:.3f} on {texture_note} board; equity {equity:.3f}",
        )

    if to_call == 0:
        return _result(
            "check",
            0,
            equity,
            f"equity {equity:.3f} < continuation need {continuation_need:.3f}; free check",
        )
    return _result(
        "fold",
        0,
        equity,
        f"equity {equity:.3f} < continuation need {continuation_need:.3f}; fold to {to_call}",
    )


def _engine_section(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if not cfg:
        return {}
    nested = cfg.get("engine")
    return nested if isinstance(nested, dict) else cfg


def decide(state: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return ``{action, amount, equity, reason}`` for a normalized game state."""
    phase = str(state.get("phase", "")).lower()
    if phase == "waiting":
        return _result("wait", 0, None, "not my turn")
    if phase == "preflop":
        return _preflop_decision(state)
    if phase in ("flop", "turn", "river"):
        return _postflop_decision(state, _engine_section(cfg))
    raise ValueError(f"Unsupported phase: {phase!r}")


def _nonnegative(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Test the local NLH decision engine")
    parser.add_argument("hole1")
    parser.add_argument("hole2")
    parser.add_argument("phase", choices=("preflop", "flop", "turn", "river"))
    parser.add_argument("board", nargs="?", default="", help="Space-separated board cards")
    parser.add_argument("--pot", type=_nonnegative, default=100)
    parser.add_argument("--call", dest="to_call", type=_nonnegative, default=0)
    parser.add_argument("--pos", dest="position", choices=POSITIONS, default="BTN")
    parser.add_argument("--aggr", dest="aggressors", type=_nonnegative, default=0)
    parser.add_argument("--limpers", type=_nonnegative, default=0)
    parser.add_argument("--min-raise-to", type=_nonnegative, default=60)
    parser.add_argument("--max-raise-to", type=_nonnegative, default=1000)
    parser.add_argument("--mc-iterations", type=_nonnegative, default=400)
    parser.add_argument("--bluff-rate", type=float, default=0.18)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    state = {
        "phase": args.phase,
        "hole": [args.hole1, args.hole2],
        "board": args.board.split() if args.board.strip() else [],
        "pot": args.pot,
        "to_call": args.to_call,
        "min_raise_to": args.min_raise_to,
        "max_raise_to": args.max_raise_to,
        "my_position": args.position,
        "aggressors": args.aggressors,
        "limpers": args.limpers,
    }
    cfg = {
        "mc_iterations": args.mc_iterations,
        "bluff_rate": args.bluff_rate,
        "seed": args.seed,
    }
    print(json.dumps(decide(state, cfg), separators=(",", ":")))


if __name__ == "__main__":
    main()
