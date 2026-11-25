"""Tests for input/element_ranking.py: pure functions, no OS access."""
import unittest

from desktop_ai_agent.input.element_ranking import rank_by_request, scores, words, words_match


def ranked(request, names):
    return rank_by_request(list(names), request, lambda n: n)


class TestWords(unittest.TestCase):
    def test_lower_case_distinct_words_without_punctuation(self):
        self.assertEqual(words("Play  'Liked Songs', play!"), {"play", "liked", "songs"})
        self.assertEqual(words("What's New_Release 2"), {"what", "s", "new", "release", "2"})
        self.assertEqual(words(""), frozenset())
        self.assertEqual(words(None), frozenset())

    def test_matching(self):
        self.assertTrue(words_match("song", "songs"))
        self.assertTrue(words_match("songs", "song"))
        self.assertTrue(words_match("play", "playlist"))
        self.assertTrue(words_match("x", "x"))
        self.assertFalse(words_match("to", "today"))  # too short for a prefix match
        self.assertFalse(words_match("play", "replay"))  # only prefixes count
        self.assertFalse(words_match("close", "clone"))
        self.assertTrue(words_match("tab", "tabs"))
        self.assertTrue(words_match("vol", "volume"))  # a 3-letter prefix may add 3 letters
        self.assertFalse(words_match("the", "thekkmasaki"))  # ...but not 8 (GitHub ranking bug)

    def test_accents_are_ignored(self):
        self.assertEqual(words("Beállítások, Súgó"), {"beallitasok", "sugo"})
        self.assertTrue(words_match("beallitasok", "beallitasok"))

    def test_one_typo_only_for_ranking_and_long_words(self):
        self.assertTrue(words_match("bealitasok", "beallitasok", typos=True))  # one missing letter
        self.assertTrue(words_match("setings", "settings", typos=True))
        self.assertFalse(words_match("bealitasok", "beallitasok"))  # window_control stays strict
        self.assertFalse(words_match("cart", "card", typos=True))  # too short to guess
        self.assertFalse(words_match("setting", "sitting2", typos=True))  # two letters differ

    def test_filler_and_command_words_do_not_rank(self):
        names = ["Chris Bumstead The Recovery Protocol", "Beállítások", "Súgó"]
        self.assertEqual(ranked("go to my profile then select bealitasok", names)[0], "Beállítások")
        self.assertEqual(ranked("then open the", names), names)  # only filler: order unchanged
        self.assertEqual(scores("click the", names), [0.0, 0.0, 0.0])


class TestRanking(unittest.TestCase):
    def test_a_rare_word_beats_a_common_one(self):
        names = [f"Play {i}" for i in range(30)] + ["Liked Songs"]
        self.assertEqual(ranked("play liked songs", names)[0], "Liked Songs")

    def test_each_word_shares_one_point(self):
        self.assertEqual(scores("play", ["Play", "Play", "Pause"]), [0.5, 0.5, 0.0])
        self.assertEqual(scores("play liked", ["Liked", "Play"]), [1.0, 1.0])

    def test_a_long_rambling_request_still_puts_the_target_first(self):
        request = ("hey so I was listening to music all day, my friend told me about a new album, "
                   "anyway play liked songs")
        names = ([f"Play {i}" for i in range(30)] + ["What's New"] + [f"Show all {i}" for i in range(10)]
                 + [f"Track {i}" for i in range(98)] + ["Liked Songs"])
        result = ranked(request, names)
        self.assertEqual(result[0], "Liked Songs")
        self.assertEqual(result[1], "What's New")

    def test_matches_come_first_and_ties_keep_screen_order(self):
        names = ["Home", "Close tab", "Search", "Close", "Settings"]
        self.assertEqual(ranked("close it", names), ["Close tab", "Close", "Home", "Search", "Settings"])

    def test_nothing_matches_keeps_the_order(self):
        names = ["Home", "Search", "Settings"]
        self.assertEqual(ranked("open the heart playlist", names), names)
        self.assertEqual(ranked("", names), names)
        self.assertEqual(ranked("!!!", names), names)

    def test_empty_list_and_a_fresh_list_is_returned(self):
        self.assertEqual(ranked("play", []), [])
        names = ["Play"]
        self.assertIsNot(rank_by_request(names, "play", lambda n: n), names)

    def test_case_and_plural_do_not_matter(self):
        self.assertEqual(ranked("SONG", ["Home", "Liked Songs"])[0], "Liked Songs")


if __name__ == "__main__":
    unittest.main()
