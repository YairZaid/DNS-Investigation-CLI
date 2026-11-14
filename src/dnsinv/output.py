"""Terminal tables. Every table the tool prints goes through here, so they all look alike.

DNS data is untrusted text: a TXT record can contain "[bold]" or ":smile:". Rich would read
those as styling and emoji, so both are switched off and values are printed as they are.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.box import Box
from rich.console import Console
from rich.table import Table

# Dashed lines between columns and rows; a solid line under the header.
DASHED = Box("┌┄┬┐\n┆ ┆┆\n├─┼┤\n┆ ┆┆\n├┄┼┤\n├┄┼┤\n┆ ┆┆\n└┄┴┘\n")

MAX_LIST_ITEMS = 3
MAX_ITEM_LENGTH = 40


def console() -> Console:
    # Width comes from the terminal, or from COLUMNS when output is not a terminal.
    return Console(markup=False, emoji=False, highlight=False)


def print_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    title: str | None = None,
    keep_whole: Sequence[int] = (),
    group_by: int | None = None,
) -> None:
    """Print rows as a table with dashed separators.

    keep_whole  columns that must never break mid-value (hostnames, addresses, statuses);
                rich gives them their full width and wraps the other columns instead.
    group_by    instead of a line after every row, draw one only where this column changes.
    """
    table = Table(
        box=DASHED,
        show_lines=group_by is None,
        header_style="bold",
        title=title,
        title_justify="left",
        title_style="bold",
    )
    for index, header in enumerate(headers):
        if index in keep_whole:
            table.add_column(header, no_wrap=True)
        else:
            table.add_column(header, overflow="fold")
    previous: str | None = None
    for row in rows:
        if group_by is not None and previous is not None and row[group_by] != previous:
            table.add_section()
        table.add_row(*row)
        previous = row[group_by] if group_by is not None else None
    console().print(table)


def cell(value: Any, *, full: bool = False) -> str:
    """One table cell. Lists become one item per line.

    Unless `full`, a list shows its first few items and long text is shortened: tables are
    for reading at a glance, and --format json always has everything.
    """
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        if not value:
            return "-"
        items = value if full else value[:MAX_LIST_ITEMS]
        lines = [str(v) if full else shorten(str(v)) for v in items]
        hidden = len(value) - len(items)
        if hidden > 0:
            lines.append(f"(+{hidden} more)")
        return "\n".join(lines)
    return str(value) if full else shorten(str(value))


def shorten(text: str) -> str:
    if len(text) <= MAX_ITEM_LENGTH:
        return text
    return text[: MAX_ITEM_LENGTH - 3] + "..."
