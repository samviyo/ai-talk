//! Stage 2, score: the reference scorers from reference/alibi/organiser_evals.py, ported.
const std = @import("std");

const Allocator = std.mem.Allocator;
const Value = std.json.Value;
const ObjectMap = std.json.ObjectMap;

pub const ScorerFn = *const fn (arena: Allocator, input: Value, output: Value, expected: Value) anyerror!?f64;

pub const Scorer = struct { name: []const u8, func: ScorerFn };

pub const scorers = [_]Scorer{
    .{ .name = "score_receipts_correct", .func = scoreReceiptsCorrect },
    .{ .name = "score_verdicts_correct", .func = scoreVerdictsCorrect },
};

pub fn score(arena: Allocator, parsed: Value) !Value {
    const gates = parsed.object.get("gates").?.object;
    const rows = parsed.object.get("rows").?.array;
    var gated = std.json.Array.init(arena);
    var values = ObjectMap.empty;
    for (scorers) |s| {
        if (!gates.contains(s.name)) continue;
        try gated.append(.{ .string = s.name });
        var per_row = ObjectMap.empty;
        for (rows.items) |row| {
            const r = row.object;
            const v: Value = if (s.func(arena, r.get("input").?, r.get("output").?, r.get("expected").?)) |maybe|
                if (maybe) |f| (if (std.math.isNan(f)) .{ .string = "error" } else .{ .float = f }) else .null
            else |_|
                .{ .string = "error" };
            try per_row.put(arena, r.get("id").?.string, v);
        }
        try values.put(arena, s.name, .{ .object = per_row });
    }
    var out = ObjectMap.empty;
    try out.put(arena, "gated", .{ .array = gated });
    try out.put(arena, "values", .{ .object = values });
    return .{ .object = out };
}

const py_whitespace = " \t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85";

fn normLabel(arena: Allocator, label: ?Value) ![]const u8 {
    const v = label orelse return "";
    if (v != .string) return "";
    return std.ascii.allocLowerString(arena, std.mem.trim(u8, v.string, py_whitespace));
}

fn get(obj: Value, key: []const u8) ?Value {
    if (obj != .object) return null;
    return obj.object.get(key);
}

fn truthy(v: ?Value) bool {
    const x = v orelse return false;
    return switch (x) {
        .null => false,
        .bool => |b| b,
        .integer => |i| i != 0,
        .float => |f| f != 0,
        .string => |s| s.len != 0,
        .array => |a| a.items.len != 0,
        .object => |o| o.count() != 0,
        .number_string => true,
    };
}

fn claimsOf(input: Value) ?[]Value {
    const c = get(input, "claims");
    if (!truthy(c) or c.? != .array) return null;
    return c.?.array.items;
}

fn idOf(claim: Value) ?[]const u8 {
    const id = get(claim, "id") orelse return null;
    return if (id == .string) id.string else null;
}

fn lookup(map: ?Value, key: ?[]const u8) ?Value {
    const k = key orelse return null;
    return get(map orelse return null, k);
}

fn scoreVerdictsCorrect(arena: Allocator, input: Value, output: Value, expected: Value) anyerror!?f64 {
    const claims = claimsOf(input) orelse return 0.0;
    if (expected != .object) return 0.0;
    const want = get(expected, "verdicts");
    if (output != .object) return 0.0;
    const verdicts = get(output, "verdicts") orelse return 0.0;
    if (verdicts != .object) return 0.0;
    var hits: f64 = 0;
    for (claims) |claim| {
        const cid = idOf(claim);
        const got = try normLabel(arena, lookup(verdicts, cid));
        if (got.len != 0 and std.mem.eql(u8, got, try normLabel(arena, lookup(want, cid)))) hits += 1;
    }
    return hits / @as(f64, @floatFromInt(claims.len));
}

/// Python's str() of a JSON scalar, as the set comparison sees it.
fn pyStr(arena: Allocator, v: Value) ![]const u8 {
    return switch (v) {
        .string => |s| s,
        .null => "None",
        .bool => |b| if (b) "True" else "False",
        .integer => |i| std.fmt.allocPrint(arena, "{d}", .{i}),
        .float => |f| std.fmt.allocPrint(arena, "{d}", .{f}),
        .number_string => |s| s,
        .array, .object => error.UnsupportedReceipt,
    };
}

fn strSet(arena: Allocator, v: ?Value) !std.StringArrayHashMapUnmanaged(void) {
    var set: std.StringArrayHashMapUnmanaged(void) = .empty;
    const x = v orelse return set;
    if (x == .array) {
        for (x.array.items) |item| try set.put(arena, try pyStr(arena, item), {});
    } else {
        try set.put(arena, try pyStr(arena, x), {});
    }
    return set;
}

fn sameSet(a: std.StringArrayHashMapUnmanaged(void), b: std.StringArrayHashMapUnmanaged(void)) bool {
    if (a.count() != b.count()) return false;
    for (a.keys()) |k| if (!b.contains(k)) return false;
    return true;
}

fn scoreReceiptsCorrect(arena: Allocator, input: Value, output: Value, expected: Value) anyerror!?f64 {
    const claims = claimsOf(input) orelse return 0.0;
    if (expected != .object) return 0.0;
    const want_receipts = get(expected, "receipts");
    const want_verdicts = get(expected, "verdicts");
    if (output != .object) return 0.0;
    const receipts = get(output, "receipts") orelse return 0.0;
    if (receipts != .object) return 0.0;
    var hits: f64 = 0;
    var scored: f64 = 0;
    for (claims) |claim| {
        const cid = idOf(claim);
        const label = try normLabel(arena, lookup(want_verdicts, cid));
        if (!std.mem.eql(u8, label, "achieved") and !std.mem.eql(u8, label, "not_achieved")) continue;
        const want = lookup(want_receipts, cid);
        const want_set = try strSet(arena, if (truthy(want)) want else null);
        const got = lookup(receipts, cid);
        const got_set = try strSet(arena, if (got != null and got.? != .null) got else null);
        if (sameSet(got_set, want_set)) hits += 1;
        scored += 1;
    }
    return if (scored > 0) hits / scored else 0.0;
}
