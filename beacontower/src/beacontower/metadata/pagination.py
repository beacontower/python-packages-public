"""Pagination helpers shared across Beacon Tower clients."""

from typing import TypeVar

T = TypeVar("T")


class PaginatedList(list[T]):
    """A list that also reports whether pagination stopped early.

    Behaves exactly like a list; check `.truncated` only if you care: True means a
    `max_count` cut it off while more was available, False means everything was
    downloaded. Kept as a list subclass (rather than a `(list, bool)` return or a
    pydantic wrapper) so callers who don't care just use it as a list.

    Note: `.truncated` is a plain instance attribute -- it is not serialized and does
    not survive slicing/copying (`result[:5]` / `list(result)` give a plain list).
    Read it off the returned object directly.
    """

    truncated: bool = False
