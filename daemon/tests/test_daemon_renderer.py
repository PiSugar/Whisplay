import os
import sys
import unittest


DAEMON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if DAEMON_DIR not in sys.path:
    sys.path.insert(0, DAEMON_DIR)

from daemon_renderer import _changed_pixel_bounds, _draw_rounded_rectangle


class LegacyImageDraw:
    """Subset exposed by the old Pillow build on the MangoPi image."""

    def __init__(self):
        self.operations = []

    def rectangle(self, bounds, fill=None):
        self.operations.append(("rectangle", bounds, fill))

    def ellipse(self, bounds, fill=None):
        self.operations.append(("ellipse", bounds, fill))


class RoundedRectangleCompatibilityTests(unittest.TestCase):
    def test_falls_back_to_basic_primitives(self):
        draw = LegacyImageDraw()

        _draw_rounded_rectangle(
            draw,
            (10, 20, 110, 80),
            radius=10,
            fill=(16, 28, 40),
            outline=(90, 150, 200),
            width=2,
        )

        operation_names = [operation[0] for operation in draw.operations]
        self.assertEqual(operation_names.count("rectangle"), 4)
        self.assertEqual(operation_names.count("ellipse"), 8)


class ChangedPixelBoundsTests(unittest.TestCase):
    def test_finds_smallest_changed_rgb_region(self):
        previous = bytes(4 * 3 * 3)
        current = bytearray(previous)
        current[(1 * 4 + 1) * 3] = 255
        current[(2 * 4 + 2) * 3 + 2] = 255

        self.assertEqual(
            _changed_pixel_bounds(previous, bytes(current), 4, 3),
            (1, 1, 3, 3),
        )

    def test_returns_none_for_identical_frames(self):
        frame = bytes(range(24))
        self.assertIsNone(_changed_pixel_bounds(frame, frame, 4, 2))

    def test_falls_back_to_full_frame_for_mismatched_buffers(self):
        self.assertEqual(_changed_pixel_bounds(b"", b"rgb", 1, 1), (0, 0, 1, 1))


if __name__ == "__main__":
    unittest.main()
