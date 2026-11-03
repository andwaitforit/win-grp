"""Pick the order parser for a political-file PDF's text.

Each parser fills the same `Contract` model, so storage and the dashboard
views don't care which traffic-system print form a station uses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import wideorbit, wideorbit_order
from .wideorbit import Contract


@dataclass(frozen=True)
class Parser:
    format: str
    version: str
    detect: Callable[[str], bool]
    parse: Callable[[str], Contract]


PARSERS = [
    Parser(wideorbit.FORMAT, wideorbit.PARSER_VERSION, wideorbit.is_wideorbit_contract, wideorbit.parse_contract),
    Parser(wideorbit_order.FORMAT, wideorbit_order.PARSER_VERSION, wideorbit_order.is_wideorbit_order, wideorbit_order.parse_order),
]

# Recorded on every extraction attempt; adding or bumping a parser changes it,
# so files no parser could read before are tried again.
EXTRACTOR = "orders"
EXTRACTOR_VERSION = ";".join(f"{p.format}:{p.version}" for p in PARSERS)


def detect(text: str) -> Parser | None:
    return next((p for p in PARSERS if p.detect(text)), None)
