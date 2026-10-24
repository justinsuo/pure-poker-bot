"""Seven-card Texas Hold'em hand evaluation using every five-card subset."""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Iterable


RANKS = {str(rank): rank for rank in range(2, 10)}
RANKS.update({"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14})
SUITS = {"s", "h", "d", "c"}

Score = tuple[int, tuple[int, ...]]

_CATEGORY_NAMES = {
    0: "High Card",
    1: "One Pair",
    2: "Two Pair",
    3: "Three of a Kind",
    4: "Straight",
    5: "Flush",
    6: "Full House",
    7: "Four of a Kind",
    8: "Straight Flush",
}
_RANK_NAMES = {
    2: ("Two", "Twos"),
    3: ("Three", "Threes"),
    4: ("Four", "Fours"),
    5: ("Five", "Fives"),
    6: ("Six", "Sixes"),
    7: ("Seven", "Sevens"),
    8: ("Eight", "Eights"),
    9: ("Nine", "Nines"),
    10: ("Ten", "Tens"),
    11: ("Jack", "Jacks"),
    12: ("Queen", "Queens"),
    13: ("King", "Kings"),
    14: ("Ace", "Aces"),
}


def parse_card(card: str) -> tuple[int, str]:
    """Convert a card such as ``Ah`` into ``(14, 'h')``."""
    if not isinstance(card, str):
        raise ValueError(f"Invalid card: {card!r}")
    compact = card.strip()
    if len(compact) != 2:
        raise ValueError(f"Invalid card: {card!r}")
    rank = RANKS.get(compact[0].upper())
    suit = compact[1].lower()
    if rank is None or suit not in SUITS:
        raise ValueError(f"Invalid card: {card!r}")
    return rank, suit


def _straight_high(rank_set: set[int]) -> int | None:
    for high in range(14, 4, -1):
        needed = {14, 2, 3, 4, 5} if high == 5 else set(range(high - 4, high + 1))
        if needed.issubset(rank_set):
            return high
    return None


def evaluate5(card_strings: Iterable[str]) -> Score:
    """Evaluate exactly five cards and return a directly comparable score."""
    cards = [parse_card(card) for card in card_strings]
    if len(cards) != 5:
        raise ValueError("evaluate5 requires exactly five cards")
    if len(set(cards)) != len(cards):
        raise ValueError("Duplicate cards are not allowed")

    ranks = [rank for rank, _ in cards]
    rank_set = set(ranks)
    counts = Counter(ranks)
    count_groups = sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    is_flush = len({suit for _, suit in cards}) == 1
    straight_high = _straight_high(rank_set)

    if is_flush and straight_high is not None:
        return 8, (straight_high,)
    if count_groups[0][1] == 4:
        quad = count_groups[0][0]
        kicker = max(rank for rank in ranks if rank != quad)
        return 7, (quad, kicker)
    if count_groups[0][1] == 3 and count_groups[1][1] == 2:
        return 6, (count_groups[0][0], count_groups[1][0])
    if is_flush:
        return 5, tuple(sorted(ranks, reverse=True))
    if straight_high is not None:
        return 4, (straight_high,)
    if count_groups[0][1] == 3:
        trips = count_groups[0][0]
        kickers = sorted((rank for rank in ranks if rank != trips), reverse=True)
        return 3, (trips, *kickers)
    if count_groups[0][1] == 2 and count_groups[1][1] == 2:
        pairs = sorted((count_groups[0][0], count_groups[1][0]), reverse=True)
        kicker = max(rank for rank in ranks if rank not in pairs)
        return 2, (pairs[0], pairs[1], kicker)
    if count_groups[0][1] == 2:
        pair = count_groups[0][0]
        kickers = sorted((rank for rank in ranks if rank != pair), reverse=True)
        return 1, (pair, *kickers)
    return 0, tuple(sorted(ranks, reverse=True))


def best7(card_strings: Iterable[str]) -> Score:
    """Return the best score among all C(7, 5) = 21 five-card combinations."""
    cards = list(card_strings)
    if len(cards) != 7:
        raise ValueError("best7 requires exactly seven cards")
    parsed = [parse_card(card) for card in cards]
    if len(set(parsed)) != 7:
        raise ValueError("Duplicate cards are not allowed")
    return max(evaluate5(combo) for combo in combinations(cards, 5))


def _rank(rank: int, *, plural: bool = False) -> str:
    try:
        return _RANK_NAMES[rank][1 if plural else 0]
    except KeyError as exc:
        raise ValueError(f"Invalid rank in score: {rank}") from exc


def describe(score: Score) -> str:
    """Describe a score, for example ``'One Pair, Kings, Ace kicker'``."""
    try:
        category, tiebreak = score
        tiebreak = tuple(tiebreak)
        if category not in _CATEGORY_NAMES or not tiebreak:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid hand score: {score!r}") from exc

    if category in (4, 8):
        label = "Straight Flush" if category == 8 else "Straight"
        suffix = " (wheel)" if tiebreak[0] == 5 else ""
        return f"{label}, {_rank(tiebreak[0])} high{suffix}"
    if category == 7:
        result = f"Four of a Kind, {_rank(tiebreak[0], plural=True)}"
        if len(tiebreak) > 1:
            result += f", {_rank(tiebreak[1])} kicker"
        return result
    if category == 6 and len(tiebreak) >= 2:
        return (
            f"Full House, {_rank(tiebreak[0], plural=True)} full of "
            f"{_rank(tiebreak[1], plural=True)}"
        )
    if category == 5:
        return f"Flush, {_rank(tiebreak[0])} high"
    if category == 3:
        return f"Three of a Kind, {_rank(tiebreak[0], plural=True)}"
    if category == 2 and len(tiebreak) >= 2:
        result = (
            f"Two Pair, {_rank(tiebreak[0], plural=True)} and "
            f"{_rank(tiebreak[1], plural=True)}"
        )
        if len(tiebreak) > 2:
            result += f", {_rank(tiebreak[2])} kicker"
        return result
    if category == 1:
        result = f"One Pair, {_rank(tiebreak[0], plural=True)}"
        if len(tiebreak) > 1:
            result += f", {_rank(tiebreak[1])} kicker"
        return result
    if category == 0:
        return f"High Card, {_rank(tiebreak[0])}"
    return _CATEGORY_NAMES[category]
