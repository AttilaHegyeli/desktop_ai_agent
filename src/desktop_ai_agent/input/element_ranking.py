"""Put the elements whose names match the user's request first (pure, no OS access).

Why: element lists are cut to a fixed length to save tokens, in screen order. In a
busy window the element the user means ("Liked Songs") can sit past the cut, and the
model never sees it. Ranking before the cut keeps it in.

How: every distinct word of the request hands out 1 point in total, shared equally
among the elements whose name contains it. A rare word ("liked", in 1 element) gives
a lot to few elements; a common one ("play", in 30) gives crumbs to many; a word no
element contains gives nothing.

Filler and command words ("the", "then", "to", "my", "go", "click", "open") are left
out. Rarity weighting alone was not enough: on YouTube "then" matched the one video
titled "The ...", which the rarity weight then put at the very top.

Words match when equal (accents ignored: "beallitasok" = "Beállítások"), or when one
starts with the other and both have at least `MIN_PREFIX` letters (song/songs,
play/playing). For ranking, words of `TYPO_MIN` or more letters that differ by one
letter match too ("bealitasok" / "beallitasok"). A prefix of exactly `MIN_PREFIX` letters may only add
`SHORT_PREFIX_EXTRA` letters (tab/tabs, vol/volume), so a short word cannot match a
long rare name ("the" -> "thekkmasaki" on GitHub). Ties keep the original screen order.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable, Iterable, TypeVar

MIN_PREFIX = 3
SHORT_PREFIX_EXTRA = 3
TYPO_MIN = 6

_WORD = re.compile(r"[^\W_]+")  # letters and digits; underscores and punctuation split words

# Request words that never decide which element is meant.
IGNORED_WORDS = frozenset("""
a an the and or but then than so to of in on at for from with into onto under over by as
my me i you your it its this that these those there here is are be am was were
please can could would will should shall do does did just now also again
go goes going open click press tap select choose pick find show see look type enter put
switch use want need get take make let button
""".split())

T = TypeVar("T")


def fold(text: str) -> str:
    """Lower case without accents ('Beállítások' -> 'beallitasok')."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def words(text: str) -> frozenset[str]:
    """Distinct lower-case, accent-free words of `text`."""
    return frozenset(_WORD.findall(fold(text)))


def meaningful_words(request: str) -> frozenset[str]:
    """The request's words without filler and command words."""
    return words(request) - IGNORED_WORDS


def _one_edit(a: str, b: str) -> bool:
    """Do `a` and `b` differ by exactly one inserted, deleted or changed letter?"""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i + (len(a) == len(b)):] == b[i + 1:]


def words_match(asked: str, named: str, typos: bool = False) -> bool:
    """Does a request word refer to a name word? `typos` also accepts one wrong letter
    in long words (list ranking only: window_control acts on a match, so it stays strict)."""
    if asked == named:
        return True
    shorter, longer = sorted((asked, named), key=len)
    if typos and len(shorter) >= TYPO_MIN and _one_edit(shorter, longer):
        return True
    if len(shorter) < MIN_PREFIX or not longer.startswith(shorter):
        return False
    return len(shorter) > MIN_PREFIX or len(longer) - len(shorter) <= SHORT_PREFIX_EXTRA


def scores(request: str, names: Iterable[str]) -> list[float]:
    """One score per name: the sum of 1 / (names containing it) over the request words it contains."""
    name_words = [words(n) for n in names]
    totals = [0.0] * len(name_words)
    for asked in meaningful_words(request):
        hits = [i for i, ws in enumerate(name_words) if any(words_match(asked, w, typos=True) for w in ws)]
        for i in hits:
            totals[i] += 1.0 / len(hits)
    return totals


def rank_by_request(items: list[T], request: str, name_of: Callable[[T], str]) -> list[T]:
    """`items` reordered best match first; unchanged order when nothing matches."""
    if not items or not meaningful_words(request):
        return list(items)
    totals = scores(request, (name_of(item) for item in items))
    order = sorted(range(len(items)), key=lambda i: -totals[i])  # sorted() is stable: ties keep screen order
    return [items[i] for i in order]
