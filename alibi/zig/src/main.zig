//! alibi-check: the Zig port of `make check-alibi`. Each command prints one JSON object.
//!
//!   alibi-check parse  <rows.jsonl> <gates.toml>
//!   alibi-check score  <parse.json>
//!   alibi-check report <parse.json> <score.json>
//!   alibi-check run    <rows.jsonl> <gates.toml>
//!
//! The shapes are in alibi/zig/README.md; alibi/golden/ holds the Python tool's answers.
const std = @import("std");
const parse = @import("parse.zig");
const score = @import("score.zig");
const report = @import("report.zig");

const Allocator = std.mem.Allocator;
const Value = std.json.Value;

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();
    const io = init.io;

    const args = try init.minimal.args.toSlice(arena);
    if (args.len < 2) return usage();
    const cmd = args[1];

    const out: Value = if (std.mem.eql(u8, cmd, "parse") and args.len == 4)
        try parse.parse(arena, try readFile(io, arena, args[2]), try readFile(io, arena, args[3]))
    else if (std.mem.eql(u8, cmd, "score") and args.len == 3)
        try score.score(arena, try readJson(io, arena, args[2]))
    else if (std.mem.eql(u8, cmd, "report") and args.len == 4)
        try report.report(arena, try readJson(io, arena, args[2]), try readJson(io, arena, args[3]))
    else if (std.mem.eql(u8, cmd, "run") and args.len == 4)
        try run(arena, try readFile(io, arena, args[2]), try readFile(io, arena, args[3]))
    else
        return usage();

    const bytes = try std.json.Stringify.valueAlloc(arena, out, .{});
    try std.Io.File.stdout().writeStreamingAll(io, bytes);
    try std.Io.File.stdout().writeStreamingAll(io, "\n");
}

/// End to end: parse, then score, then report; prints score's and report's keys as one object.
fn run(arena: Allocator, rows_jsonl: []const u8, gates_toml: []const u8) !Value {
    // TODO(join-up): chain the three stages and merge the score and report objects.
    _ = arena;
    _ = rows_jsonl;
    _ = gates_toml;
    return error.NotImplemented;
}

fn readFile(io: std.Io, arena: Allocator, path: []const u8) ![]u8 {
    return std.Io.Dir.cwd().readFileAlloc(io, path, arena, .limited(64 * 1024 * 1024));
}

fn readJson(io: std.Io, arena: Allocator, path: []const u8) !Value {
    return std.json.parseFromSliceLeaky(Value, arena, try readFile(io, arena, path), .{});
}

fn usage() !void {
    std.debug.print("usage: alibi-check parse|score|report|run <files...>\n", .{});
    std.process.exit(2);
}

test {
    std.testing.refAllDecls(@This());
}
