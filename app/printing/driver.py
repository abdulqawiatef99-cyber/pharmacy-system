"""#100: barcode labels through the Windows printer driver, the way E-Stock prints them.

The raw TSPL job (labels.py) carries its own size, gap and direction. On the pharmacy's Xprinter XP-235B it
never sat right: the print started early, straddled two labels and skipped every other one, whatever the shift
or calibration. E-Stock prints the same roll through the printer's Windows driver and is exact to the
millimetre. So here each label is drawn on the driver's own page, one page per label: the size, the gap and
media handling, the orientation (that driver turns the page 180°) and the darkness all come from the printer's
"Printing preferences" in Windows, set once and shared with E-Stock. Any label printer with a Windows driver
works the same way (Xprinter, TSC, Zebra, Godex...).

After the job is handed over, the queue is checked like a raw job (#98): a switched-off printer does not keep
a job that would print by itself later.

Off Windows (Linux CI) the pywin32 modules are missing and printing raises PrinterError. The Windows calls are
exercised for real by the installer build's smoke test (scripts/ci/driver_print_smoke.py).
"""
from __future__ import annotations

import time
from typing import Callable, NamedTuple

from PIL import Image

from .escpos import SPOOL_WAIT, PrinterError, await_job

# GetDeviceCaps indexes (wingdi.h); win32con is not needed for four numbers
HORZRES, VERTRES, LOGPIXELSX, LOGPIXELSY = 8, 10, 88, 90
DOC_NAME = "pharmacy-labels"
# what a label page can be; anything else is a sheet printer or an unset driver (A4 is 210 x 297)
MIN_W_MM, MIN_H_MM, MAX_MM = 15.0, 8.0, 120.0


class Page(NamedTuple):
    """The driver's printable page: size in printer dots and the dots per inch each way."""
    width: int
    height: int
    dpi_x: int
    dpi_y: int

    @property
    def w_mm(self) -> float:
        return self.width * 25.4 / self.dpi_x

    @property
    def h_mm(self) -> float:
        return self.height * 25.4 / self.dpi_y

    def size_text(self) -> str:
        """"38x25" (mm, one decimal at most), as the settings write a label size."""
        def f(v: float) -> str:
            return f"{round(v, 1):g}"
        return f"{f(self.w_mm)}x{f(self.h_mm)}"


def _modules():
    try:
        import win32gui  # type: ignore
        import win32print  # type: ignore
        from PIL import ImageWin
    except ImportError as e:
        raise PrinterError("الطباعة عن طريق تعريف ويندوز تشتغل على كمبيوتر ويندوز بس") from e
    return win32gui, win32print, ImageWin


def _open(win32gui, printer: str):
    try:
        return win32gui.CreateDC("WINSPOOL", printer, None)    # None: the printer's own "Printing preferences"
    except Exception as e:  # noqa: BLE001 — pywintypes.error
        raise PrinterError(f"طابعة ويندوز «{printer}» مش موجودة أو مش متاحة: {e}") from e


def _page(win32print, hdc) -> Page:
    try:
        pg = Page(*(int(win32print.GetDeviceCaps(hdc, i)) for i in (HORZRES, VERTRES, LOGPIXELSX, LOGPIXELSY)))
    except Exception as e:  # noqa: BLE001 — pywintypes.error
        raise PrinterError(f"تعذر قراءة مقاس الورق من تعريف الطابعة في ويندوز: {e}") from e
    if min(pg) <= 0:
        raise PrinterError("تعريف الطابعة في ويندوز مرجعش مقاس الورق")
    return pg


def check_label_page(pg: Page) -> None:
    """A label page, not a sheet: the owner sets the size in Windows, once, as for E-Stock."""
    if not (MIN_W_MM <= pg.w_mm <= MAX_MM and MIN_H_MM <= pg.h_mm <= MAX_MM):
        raise PrinterError(f"مقاس الورق في تعريف الطابعة في ويندوز {pg.size_text().replace('x', ' × ')} مم، ده مش مقاس ملصق."
                           " اضبط مقاس الملصق من «تفضيلات الطباعة» للطابعة في ويندوز (زي ما هو لإي ستوك)")


def page(printer: str) -> Page:
    """The driver's label page for this printer, without printing (the settings preview)."""
    win32gui, win32print, _ = _modules()
    hdc = _open(win32gui, printer)
    try:
        return _page(win32print, hdc)
    finally:
        win32gui.DeleteDC(hdc)


def print_pages(printer: str, make_pages: Callable[[Page], list[tuple[Image.Image, int]]], *,
                output: str | None = None, check_size: bool = True, wait: float | None = None,
                clock=time.monotonic, sleep=time.sleep) -> Page:
    """Print labels on the driver's page. make_pages(page) draws them at that size and returns
    [(image, copies)]; each copy is one page. output: a file for the job (smoke test only).
    Returns the page used. Raises PrinterError (nothing left in the queue) on any failure."""
    win32gui, win32print, ImageWin = _modules()
    hdc = _open(win32gui, printer)
    job = None
    try:
        pg = _page(win32print, hdc)
        if check_size:
            check_label_page(pg)
        pages = make_pages(pg)              # a label too small for its fields fails here, before any job
        try:
            job = win32print.StartDoc(hdc, (DOC_NAME, output, None, 0))
            try:
                for img, copies in pages:
                    dib = ImageWin.Dib(img.convert("RGB"))     # 24-bit colour: every driver takes it
                    for _ in range(copies):
                        win32print.StartPage(hdc)
                        dib.draw(int(hdc), (0, 0, pg.width, pg.height))
                        win32print.EndPage(hdc)
            except BaseException:
                try:
                    win32print.AbortDoc(hdc)        # nothing half-printed stays in the queue
                except Exception:  # noqa: BLE001
                    pass
                job = None
                raise
            win32print.EndDoc(hdc)
        except PrinterError:
            raise
        except Exception as e:  # noqa: BLE001 — pywintypes.error from the driver or the spooler
            raise PrinterError(f"تعذرت الطباعة عن طريق تعريف ويندوز: {e}") from e
    finally:
        win32gui.DeleteDC(hdc)
    if isinstance(job, int) and job > 0:
        h = win32print.OpenPrinter(printer)
        try:
            await_job(win32print, h, job, SPOOL_WAIT if wait is None else wait, clock, sleep)
        finally:
            win32print.ClosePrinter(h)
    return pg
