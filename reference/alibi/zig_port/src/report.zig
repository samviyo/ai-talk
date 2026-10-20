//! Stage 3, report: the check rule from `evaluate` in alibi/checks.py.
const std = @import("std");

const Allocator = std.mem.Allocator;
const Value = std.json.Value;
const ObjectMap = std.json.ObjectMap;

fn num(v: Value) ?f64 {
    return switch (v) {
        .float => |f| f,
        .integer => |i| @floatFromInt(i),
        else => null,
    };
}

fn failure(arena: Allocator, kind: []const u8, row: ?[]const u8, scorer: ?[]const u8) !Value {
    var f = ObjectMap.empty;
    try f.put(arena, "kind", .{ .string = kind });
    try f.put(arena, "row", if (row) |r| .{ .string = r } else .null);
    try f.put(arena, "scorer", if (scorer) |s| .{ .string = s } else .null);
    return .{ .object = f };
}

fn isLabel(label: Value, want: []const u8) bool {
    return label == .string and std.mem.eql(u8, label.string, want);
}

pub fn report(arena: Allocator, parsed: Value, scores: Value) !Value {
    const gates = parsed.object.get("gates").?.object;
    const rows = parsed.object.get("rows").?.array.items;
    const gated = scores.object.get("gated").?.array.items;
    const values = scores.object.get("values").?.object;

    var out_rows = std.json.Array.init(arena);
    var failures = std.json.Array.init(arena);
    var out = ObjectMap.empty;

    if (gated.len == 0) {
        try failures.append(try failure(arena, "no_gates", null, null));
        try out.put(arena, "green", .{ .bool = false });
        try out.put(arena, "rows", .{ .array = out_rows });
        try out.put(arena, "failures", .{ .array = failures });
        return .{ .object = out };
    }

    for (rows) |row| {
        const rid = row.object.get("id").?.string;
        const label = row.object.get("label").?;
        const wrong = isLabel(label, "wrong");
        var ok = true;
        var caught = false;
        for (gated) |s| {
            const name = s.string;
            const v = values.get(name).?.object.get(rid).?;
            const gate = num(gates.get(name).?.object.get("min").?).?;
            if (v == .null) continue;
            if (v == .string) {
                ok = false;
                try failures.append(try failure(arena, "raised", rid, name));
                continue;
            }
            const x = num(v).?;
            if (wrong) {
                if (x < gate) caught = true;
            } else if (x < gate and (isLabel(label, "correct") or isLabel(label, "equivalent"))) {
                ok = false;
                try failures.append(try failure(arena, "must_pass", rid, name));
            } else if (x < gate) {
                ok = false;
                try failures.append(try failure(arena, "must_pass", rid, name));
            }
        }
        if (wrong) {
            if (!caught) {
                ok = false;
                try failures.append(try failure(arena, "slips_through", rid, null));
            }
        } else if (!isLabel(label, "correct") and !isLabel(label, "equivalent")) {
            ok = false;
            try failures.append(try failure(arena, "unknown_label", rid, null));
        }
        var r = ObjectMap.empty;
        try r.put(arena, "id", .{ .string = rid });
        try r.put(arena, "label", label);
        try r.put(arena, "ok", .{ .bool = ok });
        try out_rows.append(.{ .object = r });
    }

    for (gated) |s| {
        const per_row = values.get(s.string).?.object.values();
        if (per_row.len == 0) continue;
        const first = num(per_row[0]) orelse continue;
        var constant = true;
        for (per_row) |v| {
            const x = num(v) orelse {
                constant = false;
                break;
            };
            if (x != first) constant = false;
        }
        if (constant) try failures.append(try failure(arena, "constant", null, s.string));
    }

    try out.put(arena, "green", .{ .bool = failures.items.len == 0 });
    try out.put(arena, "rows", .{ .array = out_rows });
    try out.put(arena, "failures", .{ .array = failures });
    return .{ .object = out };
}
