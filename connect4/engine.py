"""Connect Four engine: 7 columns x 6 rows, pure rules, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

COLS = 7
ROWS = 6
EMPTY = 0
P1 = 1
P2 = -1

SYMBOLS = {EMPTY: ".", P1: "X", P2: "O"}


@dataclass(frozen=True)
class Board:
    # grid[c] is a bottom-up tuple of column c; immutable so boards are hashable.
    grid: tuple[tuple[int, ...], ...]

    def legal_moves(self) -> list[int]:
        return [c for c in range(COLS) if len(self.grid[c]) < ROWS]

    def apply(self, col: int, player: int) -> "Board":
        if col not in range(COLS) or len(self.grid[col]) >= ROWS:
            raise ValueError(f"illegal move: column {col}")
        cols = [list(c) for c in self.grid]
        cols[col].append(player)
        return Board(tuple(tuple(c) for c in cols))

    def cell(self, col: int, row: int) -> int:
        # Out-of-bounds reads as EMPTY so winner() needs no edge checks.
        if not (0 <= col < COLS and 0 <= row < ROWS):
            return EMPTY
        column = self.grid[col]
        return column[row] if row < len(column) else EMPTY

    def winner(self) -> int:
        for c in range(COLS):
            for r in range(len(self.grid[c])):
                p = self.grid[c][r]
                for dc, dr in ((1, 0), (0, 1), (1, 1), (1, -1)):
                    if all(self.cell(c + i * dc, r + i * dr) == p for i in range(4)):
                        return p
        return EMPTY

    def is_draw(self) -> bool:
        return not self.legal_moves() and self.winner() == EMPTY

    def is_terminal(self) -> bool:
        return self.winner() != EMPTY or not self.legal_moves()

    def render(self) -> str:
        lines = []
        for r in range(ROWS - 1, -1, -1):
            lines.append("|" + " ".join(SYMBOLS[self.cell(c, r)] for c in range(COLS)) + "|")
        lines.append(" " + " ".join(str(c) for c in range(COLS)))
        return "\n".join(lines)


def new_board() -> Board:
    return Board(tuple(() for _ in range(COLS)))
