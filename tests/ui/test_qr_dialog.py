import unittest

from desktop_ai_agent.ui.qr_dialog import QUIET_ZONE, qr_matrix

URL = "https://channels-poems-operator-labels.trycloudflare.com"


class QrMatrixTests(unittest.TestCase):
    def test_square_and_has_finder_pattern(self):
        m = qr_matrix(URL)
        self.assertEqual(len(m), len(m[0]))
        self.assertTrue(all(m[0][:7]))  # top-left finder pattern edge

    def test_decodes_back_to_the_url(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("opencv not installed")
        m = qr_matrix(URL)
        scale, pad = 8, QUIET_ZONE
        n = len(m) + 2 * pad
        img = np.full((n * scale, n * scale), 255, dtype=np.uint8)
        for y, row in enumerate(m):
            for x, dark in enumerate(row):
                if dark:
                    img[(y + pad) * scale:(y + pad + 1) * scale,
                        (x + pad) * scale:(x + pad + 1) * scale] = 0
        text, _points, _ = cv2.QRCodeDetector().detectAndDecode(img)
        self.assertEqual(text, URL)


class QrWindowTests(unittest.TestCase):
    def test_window_draws_one_rect_per_dark_module(self):
        import tkinter as tk

        from desktop_ai_agent.ui.qr_dialog import show_qr

        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        root.withdraw()
        try:
            win = show_qr(root, URL)
            canvas = next(w for w in win.winfo_children() if isinstance(w, tk.Canvas))
            dark = sum(cell for row in qr_matrix(URL) for cell in row)
            self.assertEqual(len(canvas.find_all()), dark)
            win.destroy()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
