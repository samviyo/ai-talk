//! Stage 1, parse: rows.jsonl and gates.toml in, the canonical parse object out.
const std = @import("std");

const Allocator = std.mem.Allocator;
const Value = std.json.Value;
const ObjectMap = std.json.ObjectMap;

const row_keys = [_][]const u8{ "id", "label", "input", "output", "expected" };

pub fn parse(arena: Allocator, rows_jsonl: []const u8, gates_toml: []const u8) !Value {
    var rows = std.json.Array.init(arena);
    var lines = std.mem.splitScalar(u8, rows_jsonl, '\n');
    while (lines.next()) |line| {
        const trimmed = std.mem.trim(u8, line, " \t\r");
        if (trimmed.len == 0) continue;
        const row = try std.json.parseFromSliceLeaky(Value, arena, trimmed, .{});
        var obj = ObjectMap.empty;
        for (row_keys) |k| try obj.put(arena, k, row.object.get(k) orelse .null);
        try rows.append(.{ .object = obj });
    }

    var gates = ObjectMap.empty;
    var table: ?[]const u8 = null;
    var min: ?f64 = null;
    var min_n: i64 = 1;
    var toml = std.mem.splitScalar(u8, gates_toml, '\n');
    while (toml.next()) |raw| {
        const no_comment = if (std.mem.indexOfScalar(u8, raw, '#')) |i| raw[0..i] else raw;
        const line = std.mem.trim(u8, no_comment, " \t\r");
        if (line.len == 0) continue;
        if (line[0] == '[') {
            try putGate(arena, &gates, table, min, min_n);
            table = std.mem.trim(u8, line[1 .. line.len - 1], " \t\"");
            min = null;
            min_n = 1;
        } else if (std.mem.indexOfScalar(u8, line, '=')) |eq| {
            const key = std.mem.trim(u8, line[0..eq], " \t");
            const val = std.mem.trim(u8, line[eq + 1 ..], " \t");
            if (std.mem.eql(u8, key, "min")) min = try std.fmt.parseFloat(f64, val);
            if (std.mem.eql(u8, key, "min_n")) min_n = try std.fmt.parseInt(i64, val, 10);
        }
    }
    try putGate(arena, &gates, table, min, min_n);

    var out = ObjectMap.empty;
    try out.put(arena, "gates", .{ .object = gates });
    try out.put(arena, "rows", .{ .array = rows });
    return .{ .object = out };
}

fn putGate(arena: Allocator, gates: *ObjectMap, table: ?[]const u8, min: ?f64, min_n: i64) !void {
    const name = table orelse return;
    const m = min orelse return;
    var g = ObjectMap.empty;
    try g.put(arena, "min", .{ .float = m });
    try g.put(arena, "min_n", .{ .integer = min_n });
    try gates.put(arena, name, .{ .object = g });
}
