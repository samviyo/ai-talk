//! Stage 2, score: run every gated scorer on every row.
//! Python source: the scorers in alibi/zig/scorers/scorers.py, run through `wrapped` in alibi/scorers.py.
const std = @import("std");

const Allocator = std.mem.Allocator;
const Value = std.json.Value;

/// One ported `score_*`: null is Python's None, and returning any error means the scorer raised.
pub const ScorerFn = *const fn (arena: Allocator, input: Value, output: Value, expected: Value) anyerror!?f64;

pub const Scorer = struct { name: []const u8, func: ScorerFn };

/// The scorers, in the order Python loads them: by module file name, then by function name.
pub const scorers = [_]Scorer{
    // TODO: port each score_* from alibi/zig/scorers/scorers.py, e.g. .{ .name = "score_x", .func = scoreX },
};

/// Returns {"gated": [name], "values": {scorer: {row_id: number | null | "error"}}}.
pub fn score(arena: Allocator, parsed: Value) !Value {
    // TODO: port the scoring loop from `evaluate` in alibi/checks.py; check with `make parity-score`.
    _ = arena;
    _ = parsed;
    return error.NotImplemented;
}
