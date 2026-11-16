"""Put the elements whose names match the user's request first (pure, no OS access).

Why: element lists are cut to a fixed length to save tokens, in screen order. In a
busy window the element the user means ("Liked Songs") can sit past the cut, and the
model never sees it. Ranking before the cut keeps it in.

How: every distinct word of the request hands out 1 point in total, shared equally
among the elements whose name contains it. A rare word ("liked", in 1 element) gives
a lot to few elements; a common one ("play", in 30) gives crumbs to many; a word no
element contains (filler like "anyway") gives nothing. So a long, rambling request
cannot push the target out, and no stop-word list is needed.

Words match when equal, or when one starts with the other and both have at least
`MIN_PREFIX` letters (song/songs, play/playing). Ties keep the original screen order.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, TypeVar

MIN_PREFIX = 3

_WORD = re.compile(r"[^\W_]+")  # letters and digits; underscores and punctuation split words

T = TypeVar("T")


def words(text: str) -> frozenset[str]:
    """Distinct lower-case words of `text`."""
    return frozenset(_WORD.findall((text or "").casefold()))


def words_match(asked: str, named: str) -> bool:
    """Does a request word refer to a name word?"""
    if asked == named:
        return True
    if min(len(asked), len(named)) < MIN_PREFIX:
        return False
    return named.startswith(asked) or asked.startswith(named)


def scores(request: str, names: Iterable[str]) -> list[float]:
    """One score per name: the sum of 1 / (names containing it) over the request words it contains."""
    name_words = [words(n) for n in names]
    totals = [0.0] * len(name_words)
    for asked in words(request):
        hits = [i for i, ws in enumerate(name_words) if any(words_match(asked, w) for w in ws)]
        for i in hits:
            totals[i] += 1.0 / len(hits)
    return totals


def rank_by_request(items: list[T], request: str, name_of: Callable[[T], str]) -> list[T]:
    """`items` reordered best match first; unchanged order when nothing matches."""
    if not items or not words(request):
        return list(items)
    totals = scores(request, (name_of(item) for item in items))
    order = sorted(range(len(items)), key=lambda i: -totals[i])  # sorted() is stable: ties keep screen order
    return [items[i] for i in order]
