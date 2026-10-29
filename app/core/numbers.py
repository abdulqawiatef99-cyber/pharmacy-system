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
    return parsed
