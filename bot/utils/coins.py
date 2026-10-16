"""Coins: the bot's one currency (since 2026-10-17, replacing "1 credit = 1
check" at 5000 so'm).

Every check costs its own number of coins, set by what it costs us in
OpenAI usage (audio is the expensive part of Speaking, so a long Part 3
costs more than a Writing Task 1); coins are bought in packs, the bigger
the cheaper. 1 coin is nominally 1000 so'm (settings.coin_price) - the
small pack's price, used for revenue estimates. New users get
settings.welcome_coins; a referral pays settings.referral_coins to both
sides on the invited user's first purchase. Coins never expire, and a
check that fails costs nothing (they are spent when the report is saved).

The prices below were chosen with the owner on 2026-10-17 from measured
per-check costs (+15% for free coins, failed checks, fees), keeping at
least ~45% margin on the biggest student pack and ~33% on the biggest
center pack in the worst case.
"""
from __future__ import annotations

# What one check costs, by kind - the practice module names, plus the full
# Speaking test (Part 1+2+3 in one report).
CHECK_COINS: dict[str, int] = {
    "writing_task1": 4,
    "writing_task2": 5,
    "speaking_part1": 6,
    "speaking_part2": 5,
    "speaking_part3": 8,
    "speaking_full": 16,
}

# (coins, so'm) - what anyone can buy, and the bigger packs only teachers
# and learning centers see (they pay for their students' checks).
COIN_PACKS: tuple[tuple[int, int], ...] = (
    (10, 10_000),
    (25, 23_000),
    (50, 45_000),
    (100, 88_000),
    (200, 170_000),
)
CENTER_PACKS: tuple[tuple[int, int], ...] = (
    (500, 400_000),
    (1000, 780_000),
    (2500, 1_875_000),
    (5000, 3_600_000),
)
ALL_PACKS = dict((*COIN_PACKS, *CENTER_PACKS))

# Rough "how many checks" a pack is, shown on the buttons.
TYPICAL_CHECK = CHECK_COINS["writing_task2"]


def writing_kind(task_type: str) -> str:
    """"task1" -> "writing_task1"."""
    return f"writing_{task_type}"


def speaking_kind(part: str) -> str:
    """"part1" / "full" -> "speaking_part1" / "speaking_full"."""
    return f"speaking_{part}"


def check_coins(kind: str) -> int:
    return CHECK_COINS[kind]


def pack_price(coins: int) -> int | None:
    """The so'm price of a pack, or None if there is no such pack."""
    return ALL_PACKS.get(coins)


def discount_percent(coins: int, price: int, coin_price: int) -> int:
    full = coins * coin_price
    return round(100 * (full - price) / full) if full else 0
