"""The phonetic blocking key: Metaphone of the first two normalized name tokens.

The build spec names double metaphone for this block. This uses jellyfish's Metaphone: the
key only has to *recall* candidates - the score decides - and jellyfish is maintained (Rust
wheels, released 2025) where the double-metaphone packages on PyPI last released in 2016.
Recorded as decision D-007 in the program's DECISIONS.md.
"""

from __future__ import annotations

import jellyfish


def phonetic_key(normalized: str | None) -> str | None:
    """``"BOYS AND GIRLS CLUB"`` -> Metaphone of ``BOYS`` + ``AND``, or None when nothing encodes."""
    if not normalized:
        return None
    codes = [jellyfish.metaphone(token) for token in normalized.split(" ", 2)[:2] if token]
    key = "".join(code for code in codes if code)
    return key or None
