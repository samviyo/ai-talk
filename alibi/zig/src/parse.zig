//! Stage 1, parse: rows.jsonl and gates.toml in, the canonical parse object out.
//! Python source: `load_check_rows` in alibi/checks.py and `load_gates` in alibi/scorers.py.
const std = @import("std");

const Allocator = std.mem.Allocator;
const Value = std.json.Value;

/// Returns {"gates": {name: {"min": f, "min_n": n}}, "rows": [{id, label, input, output, expected}]}.
pub fn parse(arena: Allocator, rows_jsonl: []const u8, gates_toml: []const u8) !Value {
    // TODO: port the loaders; check with `make parity-parse`.
    _ = arena;
    _ = rows_jsonl;
    _ = gates_toml;
    return error.NotImplemented;
}
