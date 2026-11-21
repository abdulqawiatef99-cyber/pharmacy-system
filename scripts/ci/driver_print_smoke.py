"""#100: the label path through the Windows printer driver, printed for real on a Windows machine.

The unit tests fake pywin32; this prints two sample labels through a real Windows driver into a file
("Microsoft Print to PDF", the XPS writer, or any printer on a file port), so the pywin32 and Pillow calls
the pharmacy PC will make are checked: CreateDC, GetDeviceCaps, StartDoc (job id), StartPage/EndPage,
ImageWin.Dib.draw, EndDoc, DeleteDC and the queue check. Exit 1 on any failure; without a file printer it
says so and exits 0.

Usage (Windows): python scripts/ci/driver_print_smoke.py [printer name]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.printing import driver, escpos, labels as lb  # noqa: E402

FILE_PRINTERS = ("Microsoft Print to PDF", "Microsoft XPS Document Writer")
SAMPLE = {"name": "Panadol Extra 24 Tab", "value": "6221025012340", "code": "8130", "price": "40.00 ج",
          "expiry": "10/27", "pharmacy": "صيدلية تجربة", "phone": "01000000000"}


def main(argv: list[str]) -> int:
    printers = escpos.windows_printers()
    print("Windows printers:", printers)
    name = argv[1] if len(argv) > 1 else next((p for p in FILE_PRINTERS if p in printers), None)
    if not name:
        print("::warning::no file printer here: the driver label path was not printed for real")
        return 0
    out = os.path.join(tempfile.gettempdir(), "driver-labels" + (".pdf" if "PDF" in name else ".xps"))
    if os.path.exists(out):
        os.remove(out)
    seen = {}

    def make(pg):
        seen["page"] = pg
        img, dropped = lb.render_label(SAMPLE, 38, 25, pg.dpi_x, {**lb.DEFAULT_FIELDS, "pharmacy": True, "phone": True,
                                                                  "expiry": True})
        return [(img, 1), (img.rotate(180), 1)]

    pg = driver.print_pages(name, make, output=out, check_size=False, wait=30)   # a sheet printer: no size check
    print(f"page {pg} = {pg.size_text()} mm on «{name}»")
    end = time.monotonic() + 30
    while time.monotonic() < end and not (os.path.exists(out) and os.path.getsize(out) > 0):
        time.sleep(0.5)
    size = os.path.getsize(out) if os.path.exists(out) else 0
    print(f"output {out}: {size} bytes")
    if size <= 0:
        print("::error::the driver job produced no output")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
