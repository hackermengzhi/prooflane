"""In-memory pagination with opaque offset cursors over a stable, sorted collection."""

import base64
import binascii
from dataclasses import dataclass
from typing import Iterable

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class Page:
    items: list[int]
    next_cursor: str | None


def _encode_cursor(offset: int) -> str:
    return base64.b64encode(str(offset).encode("ascii")).decode("ascii")


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not isinstance(cursor, str):
        raise ValueError("cursor must be a string")
    try:
        decoded = base64.b64decode(cursor, validate=True).decode("ascii")
    except (ValueError, UnicodeError, binascii.Error):
        raise ValueError("invalid cursor") from None
    if not decoded.isascii() or not decoded.isdecimal():
        raise ValueError("invalid cursor")
    return int(decoded)


def paginate(
    items: Iterable[int],
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    descending: bool = False,
) -> Page:
    """Return a page and a cursor only when more records remain.

    Items are sorted numerically without modifying the caller's collection.
    Reuse a cursor with the same collection, page size and sorting direction;
    offsets do not track inserts or removals between requests.
    """
    if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError("page_size must be an integer between 1 and 100")
    if type(descending) is not bool:
        raise ValueError("descending must be a boolean")

    offset = _decode_cursor(cursor)
    ordered = sorted(items, reverse=descending)
    page_items = ordered[offset : offset + page_size]
    end = offset + len(page_items)
    has_more = end < len(ordered)
    return Page(
        items=page_items,
        next_cursor=_encode_cursor(end) if has_more else None,
    )
