"""CKR CAPTCHA solver (spec §7) — "Surprise! Find the jumping card!".

Mechanic (confirmed): 6 cards in a 2x3 grid; 4 share a pose and 2 differ (the odd
pair — jump or slide). Tap the 2 odd cards. This is solved from a SINGLE frame:
compare all 6 cards pairwise; the 2 odd cards match the majority poorly, so they
have the lowest mean similarity to the others.

Pure functions (frame in, results out) — unit-tested against the captcha fixtures.
The card grid coordinates are CKR-specific, so this lives in the game layer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import cv2
import numpy as np

from ckrbot.capture.screen import Frame
from ckrbot.vision.vision import find_template

# 6 card content regions (x1, y1, x2, y2), row-major: c0 c1 c2 / c3 c4 c5.
# Inner regions (borders excluded) on the 1280x720 captcha screen.
_COLS_X = [(360, 520), (556, 716), (752, 912)]
_ROWS_Y = [(195, 405), (450, 660)]
CARD_REGIONS: list[tuple[int, int, int, int]] = [
    (x1, y1, x2, y2) for (y1, y2) in _ROWS_Y for (x1, x2) in _COLS_X
]

_NORM_SIZE = (120, 160)  # cards resized to this before comparison
NUM_ODD = 2  # exactly two cards differ

# Padded search area for the first digit of "Tries left X/3" (X = 3,2,1).
TRIES_DIGIT_REGION = (552, 120, 628, 168)


def card_center(region: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = region
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def _similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = cv2.resize(a, _NORM_SIZE)
    b = cv2.resize(b, _NORM_SIZE)
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0])


def find_odd_cards(frame: Frame, num_odd: int = NUM_ODD) -> list[int]:
    """Return the indices (0..5) of the ``num_odd`` cards that differ from the rest.

    Each card's score is its mean similarity to the other five; the odd cards
    (minority pose) score lowest.
    """
    cards = [frame[y1:y2, x1:x2] for (x1, y1, x2, y2) in CARD_REGIONS]
    n = len(cards)
    sim = np.ones((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            s = _similarity(cards[i], cards[j])
            sim[i, j] = sim[j, i] = s
    mean_sim = [(sim[i].sum() - 1.0) / (n - 1) for i in range(n)]
    return sorted(sorted(range(n), key=lambda i: mean_sim[i])[:num_odd])


def solve_captcha(frame: Frame, num_odd: int = NUM_ODD) -> list[tuple[int, int]]:
    """Return the (x, y) tap points for the odd cards (to tap in order)."""
    return [card_center(CARD_REGIONS[i]) for i in find_odd_cards(frame, num_odd)]


def find_odd_cards_voted(frames: Sequence[Frame], num_odd: int = NUM_ODD) -> list[int]:
    """Odd-card indices decided by majority vote across several frames.

    The cards animate, so a single frame can misjudge the minority at a bad
    animation phase. Sampling multiple frames and taking the cards flagged odd
    most often is robust to that.
    """
    votes: Counter[int] = Counter()
    for frame in frames:
        votes.update(find_odd_cards(frame, num_odd))
    # Highest-voted first; ties broken by card index for determinism.
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    return sorted(idx for idx, _ in ranked[:num_odd])


def solve_captcha_multiframe(
    frames: Sequence[Frame], num_odd: int = NUM_ODD
) -> list[tuple[int, int]]:
    """Voted (x, y) tap points for the odd cards across ``frames``."""
    return [card_center(CARD_REGIONS[i]) for i in find_odd_cards_voted(frames, num_odd)]


def read_tries(
    frame: Frame,
    digit_templates: dict[int, np.ndarray],
    region: tuple[int, int, int, int] = TRIES_DIGIT_REGION,
    min_conf: float = 0.6,
) -> int | None:
    """Read "Tries left X/3" — how many correct rounds remain (3 → 2 → 1 → gone).

    Picks the best-matching digit template (argmax) so the near-identical digits
    separate reliably. Returns None when no digit matches (captcha cleared / not a
    captcha screen). ``digit_templates`` maps 3/2/1 to the digit crop image.
    """
    best_n: int | None = None
    best_c = min_conf
    for n, tpl in digit_templates.items():
        c = find_template(frame, tpl, region).confidence
        if c >= best_c:
            best_c, best_n = c, n
    return best_n
