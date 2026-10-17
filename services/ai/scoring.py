"""Band arithmetic shared by the Speaking and Writing reports (reliability
rules, Speaking report prompt section 1.9 and Part 4, 2026-10-13/16).

  * every criterion is a whole band (0-9), as in the real IELTS descriptors;
  * the overall band is the mean of the four, rounded the IELTS way
    (.25 up to .5, .75 up to the next whole band);
  * a report's criterion bands are the median of SCORE_RUNS independent
    scoring calls - gpt-5 accepts no temperature, so this is what keeps one
    answer's band from moving between runs (the third call is only made
    when the first two disagree - see median_of_runs). An identical
    resubmission is never scored again at all: its saved report is reused
    (see the handlers).

The ESL adjustment softens the wording of feedback only; it is not a
reason to give a higher number (LENIENCY_NOTE).
"""
from __future__ import annotations

import asyncio
import logging
import math
import statistics
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

SCORE_RUNS = 3

LENIENCY_NOTE = """\
SCORING DISCIPLINE: give each criterion the WHOLE band (an integer 0-9) whose descriptor best \
matches the evidence, reading the descriptors as a trained IELTS examiner would. The students are \
ESL learners and the written feedback is supportive and encouraging, but that is a matter of \
wording only: never raise a number to encourage, and never round up when an answer sits between \
two bands - choose the band whose description fits the WHOLE answer. Be especially careful at \
Band 7 and above: those bands need the features their descriptors name, not just an absence of \
basic errors."""


def whole_band(value: float) -> float:
    """A criterion band: a whole number from 0 to 9 (halves round up)."""
    return float(max(0, min(9, math.floor(float(value) + 0.5))))


def overall_band(criteria: list[float]) -> float:
    """Mean of the four criteria, rounded the IELTS way: .25 up to .5, .75 up
    to the next whole band."""
    mean = sum(criteria) / len(criteria)
    return math.floor(mean * 2 + 0.5) / 2


AIM_RANGE = (6.0, 8.0)


def aim_band(overall: float) -> float:
    """The band a Speaking model answer is aimed at: one above the student's,
    kept between AIM_RANGE (candidate-specific samples, 2026-10-14)."""
    return min(AIM_RANGE[1], max(AIM_RANGE[0], overall + 1.0))


def writing_aim_band(overall: float) -> float:
    """The band a Writing model answer is aimed at (the user's rule,
    2026-10-17): always better than the student's own - Band 9 from 8.0 up,
    Band 8 below that."""
    return 9.0 if overall >= 8.0 else 8.0


def target_band(overall: float) -> float:
    return min(9.0, overall + 0.5)


def median_bands(runs: list[dict[str, float]], keys: tuple[str, ...]) -> dict[str, float]:
    """Per-criterion median of whole bands (median_low, so it is always one of
    the bands actually given)."""
    return {k: whole_band(statistics.median_low(whole_band(r[k]) for r in runs)) for k in keys}


async def median_of_runs(
    score_once: Callable[[], Awaitable[dict[str, float]]],
    keys: tuple[str, ...],
    runs: int = SCORE_RUNS,
    label: str = "",
) -> tuple[dict[str, float], list[dict[str, float]]]:
    """Returns (the per-criterion median of `runs` scoring calls, every run
    that succeeded). Two calls run first, in parallel; when they give the same
    whole band on every criterion, the median is that band whatever the other
    runs would say, so those are skipped - the same result for one call less
    (2026-10-17). Otherwise the rest run too. One failed run is tolerated;
    with fewer than two good runs the scoring fails."""
    first = min(2, runs)
    results = list(await asyncio.gather(*(score_once() for _ in range(first)), return_exceptions=True))
    good = [r for r in results if not isinstance(r, BaseException)]
    agreed = len(good) == 2 and all(whole_band(good[0][k]) == whole_band(good[1][k]) for k in keys)
    if not agreed and runs > first:
        results += await asyncio.gather(*(score_once() for _ in range(runs - first)), return_exceptions=True)
        good = [r for r in results if not isinstance(r, BaseException)]
    for r in results:
        if isinstance(r, BaseException):
            logger.warning("A %s scoring run failed: %r", label or "scoring", r)
    if len(good) < min(2, runs):
        raise RuntimeError(f"{label or 'Scoring'}: only {len(good)} of {len(results)} runs succeeded")
    medians = median_bands(good, keys)
    logger.info(
        "Scores %s: runs %s -> median %s%s",
        label,
        [{k: whole_band(r[k]) for k in keys} for r in good],
        medians,
        " (the first two agreed)" if agreed and runs > first else "",
    )
    return medians, good
