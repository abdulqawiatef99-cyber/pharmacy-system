"""Integer input validation without implicit truncation or boolean coercion."""


def whole_number(value) -> int:
    """Preserve int-compatible strings and whole numeric values; reject fractions.

    Raises ValueError/TypeError for callers to translate into domain messages.
    Monetary fields using this helper are already expressed in integer piastres.
    """
    if isinstance(value, bool):
        raise ValueError("Boolean is not an integer input")
    try:
        parsed = int(value)
    except OverflowError as exc:
        raise ValueError("Non-finite integer input") from exc
    if not isinstance(value, (str, bytes)) and value != parsed:
        raise ValueError("Fractional integer input")
    if not -MAX_SAFE <= parsed <= MAX_SAFE:
        # beyond this the browser cannot hold the number exactly, and past 2**63 SQLite refuses it with an
        # OverflowError (a 500); a barcode scanned twice into an amount field gets here
        raise ValueError("Integer input out of range")
    return parsed


MAX_SAFE = 2 ** 53 - 1        # Number.MAX_SAFE_INTEGER
