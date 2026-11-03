"""QR encoder for the receipt (F27). The golden matrices below were produced by an independent
encoder (OpenCV QRCodeEncoder, byte mode, level M) and decoded by two independent readers when
the encoder was written; versions 1 and 7 have no remainder bits, so the match must be exact
(7 = several blocks + the version-information area)."""
import unittest

from app.printing import qr

GOLDEN = {
    ("HELLO PHARMACY", 2): ['1fc57f', '104541', '17565d', '175d5d', '17595d', '105d41', '1fd57f', '1b00', '17c87c',
                            'd176c', '1de70e', '1632ac', 'eed25', '1d08', '1fcb86', '105bad', '1759a7', '175138',
                            '175764', '10439c', '1fd616'],
    ("https://wa.me/201000000000?text=" + "x" * 80, 0): [
        '1fc52b2d617f', '1059cea75a41', '174334d2225d', '1748a368835d', '175d4bf5df5d', '1047071b7041',
        '1fd55555557f', '40f19d300', '1543a9f57a12', '16a1b8ca2095', 'b7c5f59819f', '53ba20ddfd2',
        '18cfff977678', '1a056652202d', '194932388987', '181d986dbfd2', '1059ad471678', '910c792802d',
        '1ef56278ba87', '120758a5ddd2', '9f4cdf377f8', '1f14b7162111', '155db5548b55', 'b131119db12',
        '7f6e7f57dfb', '1284817a2a8f', 'bf8c82982d7', '102e65addd72', '19c85fd77529', '1cb0036222cd',
        '147c77188a57', '9b6559dbde2', 'f746c77528', '2a1dd52828d', '148ed68abd7', 'f1a9a09dc72',
        '134277ff75f8', '1d251a2111', '1fc51b5c8b57', '10435919d512', '1756a5f573f8', '17489ea821dd',
        '17521cc98377', '104d959dde22', '1fd90577748b'],
}


def rows(m):
    return [format(int("".join("1" if c else "0" for c in r), 2), "x") for r in m]


def format_info(m):
    seq = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)]
    bits = sum(int(m[y][x]) << i for i, (x, y) in enumerate(seq)) ^ 0x5412
    n = len(m)
    seq2 = [(n - 1 - i, 8) for i in range(8)] + [(8, n - 15 + i) for i in range(8, 15)]
    bits2 = sum(int(m[y][x]) << i for i, (x, y) in enumerate(seq2)) ^ 0x5412
    return bits, bits2


class QREncoderTests(unittest.TestCase):
    def test_matches_independent_encoder(self):
        for (text, mask), want in GOLDEN.items():
            with self.subTest(text=text[:20]):
                self.assertEqual(rows(qr.encode(text, mask=mask)), want)

    def test_versions_and_capacity(self):
        self.assertEqual([qr.capacity(v) for v in range(1, 11)], [14, 26, 42, 62, 84, 106, 122, 152, 180, 213])
        for v in range(1, 11):
            self.assertEqual(len(qr.encode("a" * qr.capacity(v))), 17 + 4 * v)
        with self.assertRaises(qr.QRTooLong):
            qr.encode("a" * 214)
        with self.assertRaises(qr.QRTooLong):
            qr.encode("")

    def test_structure_every_mask(self):
        for v in (1, 2, 7, 10):
            for mask in range(8):
                m = qr.encode("x" * qr.capacity(v), mask=mask)
                n = len(m)
                a, b = format_info(m)
                self.assertEqual(a, b)                      # both format copies agree
                self.assertEqual(a >> 10, mask)             # level M (00) + the mask
                self.assertTrue(m[n - 8][8])                # dark module
                for cx, cy in ((0, 0), (n - 7, 0), (0, n - 7)):   # finder: dark ring, light ring, dark core
                    self.assertTrue(all(m[cy][cx + i] for i in range(7)))
                    self.assertFalse(m[cy + 1][cx + 1])
                    self.assertTrue(m[cy + 3][cx + 3])
                self.assertEqual([m[6][i] for i in range(8, n - 8)], [i % 2 == 0 for i in range(8, n - 8)])

    def test_auto_mask_and_utf8(self):
        m = qr.encode("صيدلية")                              # Arabic goes as UTF-8 bytes
        self.assertEqual(len(m), 21)
        self.assertEqual(qr.encode("https://wa.me/201000000000"), qr.encode("https://wa.me/201000000000"))

    def test_image_and_svg(self):
        m = qr.encode("HELLO PHARMACY")
        img = qr.image(m, module=6, quiet=4)
        self.assertEqual(img.size, ((21 + 8) * 6,) * 2)
        self.assertEqual(img.getpixel((0, 0)), 255)                 # quiet zone is white
        self.assertEqual(img.getpixel((4 * 6, 4 * 6)), 0)          # finder corner is black
        s = qr.svg(m)
        self.assertTrue(s.startswith("<svg") and 'viewBox="0 0 29 29"' in s)
        self.assertEqual(s.count("h1v1h-1z"), sum(map(sum, m)))

    def test_decodes_with_opencv_when_available(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV not installed (optional cross-check)")
        det = cv2.QRCodeDetector()
        for text in ("https://linktr.ee/example", "https://wa.me/201000000000", "صيدلية تجريبية"):
            got = det.detectAndDecode(np.array(qr.image(qr.encode(text), 4)))[0]
            self.assertEqual(got, text)


if __name__ == "__main__":
    unittest.main()
