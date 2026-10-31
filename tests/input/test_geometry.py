"""Tests for the pure coordinate helpers in input/geometry.py."""
import unittest

from desktop_ai_agent.input.geometry import (
    CropBox,
    box_center,
    box_grid_to_image_px,
    crop_box,
    detected_nothing,
    grid_to_image_px,
    image_px_to_screen,
    padded_grid_to_image_px,
    parse_box_2d,
    parse_point,
    parse_targets,
    pick_candidate,
    screen_to_image_px,
    slugify,
    snap_to_image,
    tile_boxes,
)


class TestParseBox2d(unittest.TestCase):
    def test_y_first_order_is_swapped_to_x_first(self):
        self.assertEqual(parse_box_2d('[{"box_2d": [100, 200, 300, 400], "label": "x"}]'), (200, 100, 400, 300))

    def test_single_object_is_accepted(self):
        self.assertEqual(parse_box_2d('{"box_2d": [100, 200, 300, 400]}'), (200, 100, 400, 300))

    def test_json_fence(self):
        text = '```json\n[{"box_2d": [10, 20, 30, 40], "label": "clock"}]\n```'
        self.assertEqual(parse_box_2d(text), (20, 10, 40, 30))

    def test_text_around_the_json(self):
        text = 'Here it is: [{"box_2d": [10, 20, 30, 40]}] hope that helps'
        self.assertEqual(parse_box_2d(text), (20, 10, 40, 30))

    def test_only_the_first_item_is_used(self):
        text = '[{"box_2d": [10, 20, 30, 40]}, {"box_2d": [500, 500, 600, 600]}]'
        self.assertEqual(parse_box_2d(text), (20, 10, 40, 30))

    def test_numbers_as_strings(self):
        self.assertEqual(parse_box_2d('{"box_2d": ["10", "20", "30.5", "40"]}'), (20, 10, 40, 30.5))

    def test_reversed_corners_are_normalized(self):
        self.assertEqual(parse_box_2d('{"box_2d": [300, 400, 100, 200]}'), (200, 100, 400, 300))

    def test_small_overshoot_is_allowed(self):
        self.assertEqual(parse_box_2d('{"box_2d": [-5, 0, 1005, 1000]}'), (0, -5, 1000, 1005))

    def test_unusable_replies_return_none(self):
        bad = [
            "",
            "   ",
            "[]",
            "I cannot find it.",
            '{"label": "no box"}',
            '{"box_2d": [1, 2, 3]}',  # wrong length
            '{"box_2d": [1, 2, 3, 4, 5]}',
            '{"box_2d": [0, 0, 5000, 100]}',  # far outside the grid
            '{"box_2d": [-200, 0, 100, 100]}',
            '{"box_2d": [0, 0, "abc", 100]}',
            '{"box_2d": [0, 0, true, 100]}',
            '{"box_2d": [0, 0, NaN, 100]}',  # json accepts NaN; we must not
            '{"box_2d": [0, 0, Infinity, 100]}',
            '{"box_2d": "10,20,30,40"}',
            "[1, 2, 3, 4]",
        ]
        for text in bad:
            with self.subTest(text=text):
                self.assertIsNone(parse_box_2d(text))

    def test_box_center(self):
        self.assertEqual(box_center((200, 100, 400, 300)), (300, 200))


class TestDetectedNothing(unittest.TestCase):
    def test_empty_list_in_any_wrapping(self):
        for text in ("[]", " [] ", "```json\n[]\n```", "Not visible: []"):
            with self.subTest(text=text):
                self.assertTrue(detected_nothing(text))

    def test_anything_else(self):
        for text in ("", "nothing", '[{"box_2d": [1, 2, 3, 4]}]', "{}"):
            with self.subTest(text=text):
                self.assertFalse(detected_nothing(text))


class TestTilesAndPadding(unittest.TestCase):
    def test_one_tile_is_the_whole_image(self):
        self.assertEqual(tile_boxes(800, 600, 1), [CropBox(0, 0, 800, 600)])

    def test_tiles_overlap_and_cover_the_image(self):
        tiles = tile_boxes(1920, 1080, 3)
        self.assertEqual(len(tiles), 9)
        self.assertEqual(tiles[0], CropBox(0, 0, 800, 450))  # 640x360 grown by 25 %
        self.assertEqual((tiles[-1].right, tiles[-1].bottom), (1920, 1080))
        self.assertLess(tiles[1].left, tiles[0].right)  # neighbours overlap
        self.assertLess(tiles[3].top, tiles[0].bottom)

    def test_padded_grid_uses_the_square_side(self):
        box = CropBox(100, 50, 800, 600)  # padded to 800x800
        self.assertEqual(padded_grid_to_image_px(500, 500, box), (500, 450))
        self.assertEqual(padded_grid_to_image_px(1000, 750, box), (900, 650))  # bottom-right of content


class TestPickCandidate(unittest.TestCase):
    def test_none_when_empty(self):
        self.assertIsNone(pick_candidate([], [], 30))

    def test_agreeing_answers_win(self):
        centers = [(100, 100), (900, 500), (905, 505), (102, 98), (898, 502)]
        self.assertEqual(pick_candidate(centers, [10] * 5, 30), 1)

    def test_tie_goes_to_the_smaller_box_then_the_earlier_tile(self):
        self.assertEqual(pick_candidate([(0, 0), (500, 500)], [400, 100], 30), 1)
        self.assertEqual(pick_candidate([(0, 0), (500, 500)], [100, 100], 30), 0)


class TestParsePoint(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_point('{"x": 1, "y": 2}'), (1, 2))
        self.assertEqual(parse_point('{"point": [3, 4]}'), (3, 4))
        self.assertEqual(parse_point("x=5, y=6"), (5, 6))
        self.assertEqual(parse_point("(7, 8)"), (7, 8))

    def test_not_found(self):
        self.assertIsNone(parse_point('{"found": false}'))
        self.assertIsNone(parse_point("The button is not visible."))
        self.assertIsNone(parse_point(""))


class TestConversions(unittest.TestCase):
    def test_grid_to_image(self):
        self.assertEqual(grid_to_image_px(500, 500, 800, 600), (400, 300))

    def test_snap(self):
        self.assertEqual(snap_to_image(800.4, 600, 800, 600), (799, 599))
        self.assertIsNone(snap_to_image(900, 10, 800, 600))

    def test_screen_round_trip_with_negative_origin(self):
        self.assertEqual(image_px_to_screen(200, 300, -1920, 0), (-1720, 300))
        self.assertEqual(screen_to_image_px(-1720, 300, -1920, 0), (200, 300))

    def test_crop_box_stays_inside(self):
        self.assertEqual(crop_box(10, 10, 800, 600, 400), CropBox(0, 0, 400, 400))
        self.assertEqual(crop_box(790, 590, 800, 600, 400), CropBox(400, 200, 400, 400))
        self.assertEqual(crop_box(100, 100, 300, 200, 400), CropBox(0, 0, 300, 200))

    def test_box_grid_to_image(self):
        self.assertEqual(box_grid_to_image_px(500, 500, CropBox(0, 100, 400, 400)), (200, 300))


class TestTextHelpers(unittest.TestCase):
    def test_parse_targets(self):
        self.assertEqual(parse_targets("# c\n\n a \nb\n"), ["a", "b"])

    def test_slugify(self):
        self.assertEqual(slugify("The Start button!"), "the_start_button")
        self.assertEqual(slugify("!!!"), "target")


if __name__ == "__main__":
    unittest.main()
