"""North American phone number normalisation (the clinic is in Alberta)."""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\d")

# Spoken digits that STT sometimes returns as words.
_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


_DIGIT_WORD = r"(?:zero|oh|o|one|two|three|four|five|six|seven|eight|nine|\d)"
_REPEAT = re.compile(rf"\b(double|triple)[\s-]+({_DIGIT_WORD})\b", re.IGNORECASE)


def digits_in(text: str) -> str:
    """All digits in order, counting spoken forms: "seven zero, double two" -> "7022"."""
    text = _REPEAT.sub(lambda m: " ".join([m.group(2)] * (2 if m.group(1).lower() == "double" else 3)), text.lower())
    for word, digit in _WORDS.items():
        text = re.sub(rf"\b{word}\b", digit, text)
    return "".join(_DIGITS.findall(text))


def normalize(raw: str | None) -> str | None:
    """Return +1NXXNXXXXXX, or None if this isn't a valid NANP number."""
    if not raw:
        return None
    digits = digits_in(raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    # NANP: area code and exchange can't start with 0 or 1.
    if digits[0] in "01" or digits[3] in "01":
        return None
    return f"+1{digits}"


def spoken(e164: str) -> str:
    """'+14035550142' -> '4 0 3, 5 5 5, 0 1 4 2' for a clear read-back."""
    d = e164.removeprefix("+1")
    if len(d) != 10:
        return e164
    return f"{' '.join(d[:3])}, {' '.join(d[3:6])}, {' '.join(d[6:])}"


def display(e164: str | None) -> str:
    """'+14035550142' -> '(403) 555-0142'."""
    if not e164:
        return "unknown number"
    d = e164.removeprefix("+1")
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else e164
