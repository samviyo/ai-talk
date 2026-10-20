//! Stage 3, report: apply the check rule to the scores.
//! Python source: `evaluate` in alibi/checks.py (everything after the scores are computed).
const std = @import("std");

const Allocator = std.mem.Allocator;
const Value = std.json.Value;

/// Returns {"green": bool, "rows": [{id, label, ok}], "failures": [{kind, row, scorer}]}.
pub fn report(arena: Allocator, parsed: Value, scores: Value) !Value {
    // TODO: port the rule; check with `make parity-report`.
    _ = arena;
    _ = parsed;
    _ = scores;
    return error.NotImplemented;
}
