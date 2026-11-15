"""Coins: the bot's one currency (since 2026-10-17, replacing "1 credit = 1
check" at 5000 so'm).

Every check costs the same 5 coins (2026-11-15, the owner's call: one price
is easy to say and to remember; before that each kind had its own price,
4-8). Coins are bought in packs, the bigger the cheaper. 1 coin is nominally
1000 so'm (settings.coin_price) - the small pack's price, used for revenue
estimates. New users get settings.welcome_coins; a referral pays
settings.referral_coins to both sides on the invited user's first purchase.
Coins never expire. They are held when grading starts and given back if the
report is not saved (bot/utils/check_guard.py), so a failed check is free.

Measured AI cost per check (7 days to 2026-11-15, $1 = 12 014 so'm): Writing
Task 1 ~2 400 so'm (worst seen ~3 700), Task 2 ~2 100, Speaking 1 400-1 600.
At 5 coins the dearest, Task 1, costs 3 750 so'm on the cheapest center coin
(750): ~36% left on average, ~0 in the worst case - which is why no pack sells
a coin below 750 so'm (2500 coins at 760, so the biggest pack stays the cheapest).
"""
from __future__ import annotations

# What one check costs, by kind - the practice module names, plus the full
# Speaking test (Part 1+2+3 in one report).
CHECK_COINS: dict[str, int] = {
    "writing_task1": 5,
    "writing_task2": 5,
    "speaking_part1": 5,
    "speaking_part2": 5,
    "speaking_part3": 5,
    "speaking_full": 15,
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
    (2500, 1_900_000),
    (5000, 3_750_000),  # 750 so'm a coin, the floor
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
