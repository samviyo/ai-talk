"""Harness-test fixture, deliberately partial: it ignores whether the target is legal."""
import json


def score_not_spoofed(input, output, *, expected=None, **kwargs):
    """1 iff the reply's target isn't not_target; junk replies score 0."""
    if not expected or "not_target" not in expected:
        return None
    try:
        return float(int(json.loads(output)["target"]) != int(expected["not_target"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return 0.0
