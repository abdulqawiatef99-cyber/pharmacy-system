"""Entry point of the compiled-build evaluation (docs/PROTECTION_PLAN.md, layer 3). Never shipped.

The evaluation compiles THIS file instead of manage.py so that the same binary can also answer
`--eval-probe`: one JSON line saying whether each shipped library really works inside the compiled
program (a missing DLL or data file shows only when the library is used, not when the build ends).
Every other command line goes to manage.py unchanged (selftest, init, verify, serve, ...).
"""
from __future__ import annotations

import json
import sys


def _tiny_pdf(text: str) -> bytes:
    """A one-page PDF written by hand (reportlab is a test-only package, not in the program)."""
    stream = f"BT /F1 18 Tf 40 100 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += str(n).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n"
            + str(xref).encode() + b"\n%%EOF\n")
    return out


def probe() -> dict:
    """Use every shipped library once. A value is "OK...", "WARN: ..." or "FAIL: ..."; never raises."""
    import io
    import os
    result: dict[str, str] = {}

    def check(name, fn):
        try:
            result[name] = str(fn() or "OK")
        except Exception as e:  # noqa: BLE001 - the report must list every failure
            result[name] = f"FAIL: {type(e).__name__}: {e}"

    def runtime():
        return ("OK frozen=%s compiled=%s python=%s" % (
            getattr(sys, "frozen", False), "__compiled__" in globals(), sys.version.split()[0]))
    check("runtime", runtime)

    def sqlite():
        import sqlite3
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE TABLE t(x)")
        finally:
            con.close()
        return "OK " + sqlite3.sqlite_version
    check("sqlite", sqlite)

    def web_files():
        from app.api.app import WEB_DIR
        for name in ("index.html", "app.js", "app.css"):
            if not (WEB_DIR / name).is_file():
                raise FileNotFoundError(name)
    check("web_files", web_files)

    def migrations():
        from app.db import MIGRATIONS_DIR
        n = len(list(MIGRATIONS_DIR.glob("*.sql")))
        if not n:
            raise FileNotFoundError("no .sql migration next to the program")
        return f"OK {n} files"
    check("migrations", migrations)

    def excel():
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active["A1"] = "دواء"
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        if openpyxl.load_workbook(buf).active["A1"].value != "دواء":
            raise ValueError("cell changed")
    check("excel(openpyxl)", excel)

    def pdf():
        import pdfplumber
        with pdfplumber.open(io.BytesIO(_tiny_pdf("PROBE 12345"))) as doc:
            text = doc.pages[0].extract_text() or ""
        if "PROBE 12345" not in text:
            raise ValueError(f"text not read: {text!r}")
    check("pdf(pdfplumber)", pdf)

    def pdfium():
        import pypdfium2
        doc = pypdfium2.PdfDocument(_tiny_pdf("PROBE"))
        return f"OK {len(doc)} page"
    check("pdf(pypdfium2)", pdfium)

    def receipt():
        from PIL import features
        from app.printing import escpos
        if not (features.check("raqm") and features.check("fribidi")):
            return "WARN: raqm/fribidi missing (Arabic receipts fall back to the browser)"
        size = escpos.render_receipt([("title", "صيدلية", ""), ("row", "بانادول", "40.00")]).size
        return f"OK {size[0]}x{size[1]}"
    check("arabic_receipt(pillow)", receipt)

    def signature():
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        key.public_key().verify(key.sign(b"probe"), b"probe")
    check("signature(cryptography)", signature)

    def tls():
        import ssl
        import certifi
        import requests  # noqa: F401
        ssl.create_default_context(cafile=certifi.where())
    check("tls(certifi)", tls)

    def server():
        import python_multipart  # noqa: F401
        import starlette  # noqa: F401
        import uvicorn
        from uvicorn.config import Config
        Config(app=lambda scope, receive, send: None).load()      # resolves the "auto" loop/protocol classes
        return "OK uvicorn " + uvicorn.__version__
    check("server(uvicorn)", server)

    def printers():
        if os.name != "nt":
            return "OK (not Windows: skipped)"
        import win32print
        return f"OK {len(win32print.EnumPrinters(2))} printers"
    check("windows_printing(pywin32)", printers)

    return result


def main() -> int:
    if sys.argv[1:2] == ["--eval-probe"]:
        result = probe()
        print(json.dumps(result, ensure_ascii=True))
        return 1 if any(v.startswith("FAIL") for v in result.values()) else 0
    import manage
    manage.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
