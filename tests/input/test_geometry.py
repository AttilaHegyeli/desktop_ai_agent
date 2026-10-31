"""Tests for the pure coordinate helpers in input/geometry.py."""
import unittest

from desktop_ai_agent.input.geometry import (
    CropBox,
    crop_box,
    image_px_to_screen,
    parse_targets,
    screen_to_image_px,
    slugify,
)


class TestConversions(unittest.TestCase):
    def test_screen_round_trip_with_negative_origin(self):
        self.assertEqual(image_px_to_screen(200, 300, -1920, 0), (-1720, 300))
        self.assertEqual(screen_to_image_px(-1720, 300, -1920, 0), (200, 300))

    def test_crop_box_stays_inside(self):
        self.assertEqual(crop_box(10, 10, 800, 600, 400), CropBox(0, 0, 400, 400))
        self.assertEqual(crop_box(790, 590, 800, 600, 400), CropBox(400, 200, 400, 400))
        self.assertEqual(crop_box(100, 100, 300, 200, 400), CropBox(0, 0, 300, 200))

    def test_crop_box_edges(self):
        box = CropBox(10, 20, 30, 40)
        self.assertEqual((box.right, box.bottom, box.as_tuple()), (40, 60, (10, 20, 40, 60)))


class TestTextHelpers(unittest.TestCase):
    def test_parse_targets(self):
        self.assertEqual(parse_targets("# c\n\n a \nb\n"), ["a", "b"])

    def test_slugify(self):
        self.assertEqual(slugify("The Start button!"), "the_start_button")
        self.assertEqual(slugify("!!!"), "target")


if __name__ == "__main__":
    unittest.main()
