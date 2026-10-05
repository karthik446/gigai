"""Find contact values in what the product returned, without a chance match inside an id.

A stored master, a record or a command's JSON answer holds values that are new on every run:
``record_<uuid>``, ``revision_<uuid>``, an ``updated_at`` with microseconds. A short piece of a
contact value (``555`` of a phone number) is inside one of them on some runs, so
``"555" in json.dumps(master)`` fails a correct product now and then (0.1.10.10 FK: about one run
in 80 for two uuids and a timestamp).

``contact_values_in`` reads a JSON value the way a leak would show in it:

* every string is read, keys too, except a string that is, whole, an id, a digest or a timestamp.
  A string is left out for its own shape, never for its key: an email stored under ``revision_id``
  is not a ``revision_<uuid>``, so it is found;
* a value counts where it stands on its own, never inside a longer run of letters and digits:
  ``555`` counts in ``(555) 010-0142``, not in the line mark ``fa6f555eaf0381ed``.

Not for text that holds a path (a command's plain output naming a file under pytest's temporary
folder, ``pytest-555/``): a folder name stands on its own. Give such a check the whole contact
values, or pieces no path can hold (``(555)``).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
import re

#: The whole of an id, a digest or a timestamp: what the product makes, never what a person wrote.
_MADE_BY_THE_PRODUCT = re.compile(
    r"(?:[a-z][a-z0-9]*_)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # record_<uuid>, revision_<uuid>, a uuid
    r"|sha256:[0-9a-f]{64}"
    r"|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)


def _texts(value: object) -> Iterator[str]:
    if isinstance(value, str):
        if not _MADE_BY_THE_PRODUCT.fullmatch(value):
            yield value
    elif isinstance(value, Mapping):
        for key, held in value.items():
            yield from _texts(key)
            yield from _texts(held)
    elif isinstance(value, (list, tuple)):
        for held in value:
            yield from _texts(held)


def contact_values_in(value: object, contact: Iterable[str]) -> list[str]:
    """The ``contact`` values found in ``value`` (a JSON value: a master, a record, a command's answer), in the order given."""

    texts = list(_texts(value))
    return [
        wanted for wanted in contact
        if any(re.search(rf"(?<![0-9A-Za-z]){re.escape(wanted)}(?![0-9A-Za-z])", text) for text in texts)
    ]


__all__ = ["contact_values_in"]
