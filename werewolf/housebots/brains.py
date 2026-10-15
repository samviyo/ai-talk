"""The four house-bot brains, a skill ladder: random < quiet < accuser < baseline.

None of them calls an LLM; all decisions are deterministic given the seed.
"""

from __future__ import annotations

from .core import Action, BaseBrain, View, most_accused, seer_named_wolf


class RandomBrain(BaseBrain):
    """Random legal targets, never posts."""

    def _vote(self, view: View) -> Action | None:
        candidates = self._vote_candidates(view)
        if not candidates:
            return None
        return {"tool": "vote", "args": {"seat": self.rng.choice(candidates)}}


class QuietBrain(BaseBrain):
    """Never posts; follows a seer claim, else votes the most-accused candidate."""

    def _vote(self, view: View) -> Action | None:
        candidates = self._vote_candidates(view)
        if not candidates:
            return None
        if view.get("role") != "werewolf":
            named = seer_named_wolf(view, candidates)
            if named is not None:
                return {"tool": "vote", "args": {"seat": named}}
        target = most_accused(view, candidates, self.rng) or self.rng.choice(candidates)
        return {"tool": "vote", "args": {"seat": target}}


class AccuserBrain(BaseBrain):
    """Accuses one random alive non-partner each day and votes for them."""

    def __init__(self, seed: int):
        super().__init__(seed)
        self._day_accusations: dict[int, int] = {}

    def _pick_target(self, view: View) -> int | None:
        me = view["seat"]
        excluded = {me} | set(view.get("partners") or [])
        turn = view.get("turn") or {}
        legal = [s for s in turn.get("legal_targets") or [] if s not in excluded]
        if legal:
            return self.rng.choice(sorted(legal))
        pool = [s for s in self._alive(view) if s not in excluded]
        return self.rng.choice(pool) if pool else None

    def _target(self, view: View) -> int | None:
        # Stable within a day so the vote matches the accusation.
        day = (view.get("turn") or {}).get("day", 0)
        if day not in self._day_accusations:
            target = self._pick_target(view)
            if target is None:
                return None
            self._day_accusations[day] = target
        return self._day_accusations[day]

    def _post(self, view: View) -> Action | None:
        target = self._target(view)
        if target is None:
            return None
        return {
            "tool": "post",
            "args": {
                "chat": f"I think seat {target} is acting suspicious.",
                "accuse": target,
            },
        }

    def _vote(self, view: View) -> Action | None:
        candidates = self._vote_candidates(view)
        if not candidates:
            return None
        target = self._target(view)
        if target not in candidates:
            target = self.rng.choice(candidates)
        return {"tool": "vote", "args": {"seat": target}}


class BaselineBrain(BaseBrain):
    """Claims tracking: believes the seer claim, votes its revealed wolves."""

    COUNTERCLAIM_CHANCE = 0.15

    def __init__(self, seed: int):
        super().__init__(seed)
        self._claims: dict[int, str] = {}  # seat -> claimed_role, insertion-ordered
        self._findings: dict[int, dict[int, str]] = {}  # claimant -> {seat: role}
        self._posts: dict[tuple[int, int], Action | None] = {}  # (day, round) -> post
        self._counterclaim_done = False

    # -- interpretation -----------------------------------------------------

    def _parse_claim(self, chat: str) -> dict[int, str]:
        # Only the "I am the seer: <seat> <role>, ..." format a baseline seer posts.
        findings: dict[int, str] = {}
        lower = chat.lower()
        i = lower.find("seer:")
        if i < 0:
            return findings
        for part in lower[i + 5 :].replace(".", ",").split(","):
            tokens = part.strip().split()
            if len(tokens) < 2:
                continue
            try:
                seat = int(tokens[0])
            except ValueError:
                continue
            if any(t.rstrip(".,") == "werewolf" for t in tokens[1:]):
                findings[seat] = "werewolf"
            elif any(t.rstrip(".,") == "villager" for t in tokens[1:]):
                findings[seat] = "villager"
        return findings

    def _observe(self, view: View) -> None:
        super()._observe(view)
        dead = view.get("dead") or {}
        for post in view.get("board") or []:
            seat = post.get("seat")
            if not isinstance(seat, int):
                continue
            claimed = post.get("claimed_role")
            # First claim sticks unless contradicted by a later death reveal.
            if claimed and seat not in self._claims:
                self._claims[seat] = claimed
        # Discard claims from seats exposed as liars on death.
        for seat, role in dead.items():
            if seat in self._claims and self._claims[seat] != role:
                del self._claims[seat]
                self._findings.pop(seat, None)
        for post in view.get("board") or []:
            seat = post.get("seat")
            if not isinstance(seat, int) or seat in dead:
                continue
            if self._claims.get(seat) == "seer":
                parsed = self._parse_claim(post.get("chat") or "")
                if parsed:
                    self._findings.setdefault(seat, {}).update(parsed)

    def _trusted_seer_claim(self, view: View) -> int | None:
        """The first living seer claimant, unless a later claim contradicts its findings."""
        me = view["seat"]
        dead = view.get("dead") or {}
        claimants = [
            s for s in self._claims
            if self._claims[s] == "seer" and s != me and s not in dead
        ]
        if not claimants:
            return None
        first = claimants[0]
        # Believe the first claim unless a later claimant directly contradicted its findings.
        for later in claimants[1:]:
            later_findings = self._findings.get(later, {})
            for seat, role in self._findings.get(first, {}).items():
                if seat in later_findings and later_findings[seat] != role:
                    return later
        return first

    def _known_wolves(self, view: View) -> set[int]:
        wolves = set()
        dead = set((view.get("dead") or {}).keys())
        if view.get("role") == "seer":
            # The real seer trusts its own checks above any claimant.
            own = {
                s for s, r in (view.get("my_checks") or {}).items()
                if r == "werewolf" and s not in dead
            }
            if own:
                return own
        claimant = self._trusted_seer_claim(view)
        if claimant is not None:
            wolves.update(
                s for s, r in self._findings.get(claimant, {}).items() if r == "werewolf"
            )
        wolves.difference_update(dead)
        return wolves

    def _cleared_villagers(self, view: View) -> set[int]:
        cleared = set()
        if view.get("role") == "seer":
            cleared.update(
                s for s, r in (view.get("my_checks") or {}).items() if r != "werewolf"
            )
        claimant = self._trusted_seer_claim(view)
        if claimant is not None:
            cleared.update(
                s for s, r in self._findings.get(claimant, {}).items() if r != "werewolf"
            )
        return cleared

    # -- day ----------------------------------------------------------------

    def _post(self, view: View) -> Action | None:
        turn = view.get("turn") or {}
        key = (turn.get("day", 0), turn.get("round", 0))
        if key not in self._posts:
            self._posts[key] = self._build_post(view)
        return self._posts[key]

    def _build_post(self, view: View) -> Action | None:
        role = view.get("role")
        me = view["seat"]
        turn = view.get("turn") or {}
        if role == "seer":
            checks = view.get("my_checks") or {}
            found = any(r == "werewolf" for r in checks.values())
            # Claim on a wolf, otherwise by day 3 the checks are worth sharing.
            if checks and (found or turn.get("day", 0) >= 3):
                summary = ", ".join(f"{s} {r}" for s, r in sorted(checks.items()))
                return {
                    "tool": "post",
                    "args": {"chat": f"I am the seer: {summary}.", "claimed_role": "seer"},
                }
            return None
        if role == "werewolf":
            if (
                not self._counterclaim_done
                and "seer" not in self._claims.values()
                and self.rng.random() < self.COUNTERCLAIM_CHANCE
            ):
                self._counterclaim_done = True
                partners = set(view.get("partners") or [])
                targets = [
                    s for s in self._alive(view) if s != me and s not in partners
                ]
                if targets:
                    target = self.rng.choice(targets)
                    return {
                        "tool": "post",
                        "args": {
                            "chat": f"I am the seer: {target} werewolf.",
                            "claimed_role": "seer",
                        },
                    }
            return None
        # Villager or doctor: relay the trusted seer's wolf, else join the pile.
        wolves = self._known_wolves(view)
        if wolves:
            target = min(wolves)
            return {
                "tool": "post",
                "args": {"chat": f"The seer says seat {target} is a werewolf.", "accuse": target},
            }
        # Doctors stay quiet; villagers accuse only a pile a trusted signal started.
        if view.get("role") != "doctor":
            candidates = self._vote_candidates(view)
            target = self._weighted_most_accused(view, candidates)
            if target is not None and target != me:
                return {
                    "tool": "post",
                    "args": {"chat": f"Seat {target} has the most suspicion.", "accuse": target},
                }
        return None

    def _weighted_most_accused(self, view: View, candidates: list[int]) -> int | None:
        """Trusted-seer wolf finding first, else the most-accused seat weighted by claims."""
        claimant = self._trusted_seer_claim(view)
        if claimant is not None:
            named = [
                s for s, r in self._findings.get(claimant, {}).items()
                if r == "werewolf" and s in candidates
            ]
            if named:
                return min(named)
        counts: dict[int, int] = {}
        for post in view.get("board") or []:
            target = post.get("accuse")
            if isinstance(target, int) and target in candidates:
                # Only the trusted seer's structured accusation weighs double.
                weight = 2 if post.get("seat") == claimant else 1
                counts[target] = counts.get(target, 0) + weight
        if not counts:
            return None
        top = max(counts.values())
        return self.rng.choice(sorted(s for s, c in counts.items() if c == top))

    def _vote(self, view: View) -> Action | None:
        candidates = self._vote_candidates(view)
        if not candidates:
            return None
        wolves = self._known_wolves(view)
        vote_wolves = [s for s in candidates if s in wolves]
        if vote_wolves:
            return {"tool": "vote", "args": {"seat": min(vote_wolves)}}
        cleared = self._cleared_villagers(view)
        pool = [s for s in candidates if s not in cleared]
        if view.get("role") == "werewolf" and len(pool) > 1:
            # Wolves steer votes away from cleared villagers when they can.
            candidates = pool
        else:
            pool = pool or candidates
        if view.get("role") == "werewolf":
            target = most_accused(view, pool, self.rng) or self.rng.choice(pool)
            return {"tool": "vote", "args": {"seat": target}}
        target = self._weighted_most_accused(view, pool)
        if target is None:
            # No signal: a coordinated fallback beats scattering onto a random pile.
            target = self._fallback_target(view, pool)
        return {"tool": "vote", "args": {"seat": target}}

    def _fallback_target(self, view: View, pool: list[int]) -> int:
        """No seer signal: follow the most-accused pile so the village stays coordinated."""
        target = most_accused(view, pool, self.rng)
        return target if target is not None else min(pool)

    # -- night --------------------------------------------------------------

    def _night(self, view: View, owe: str) -> Action | None:
        legal = self._legal(view)
        if not legal:
            return None
        role = view.get("role")
        me = view["seat"]
        if owe == "doctor_save" and role == "doctor":
            claimant = self._trusted_seer_claim(view)
            if claimant in legal:
                return {"tool": "night_action", "args": {"target": claimant}}
            if me in legal:
                return {"tool": "night_action", "args": {"target": me}}
            return {"tool": "night_action", "args": {"target": self.rng.choice(legal)}}
        if owe == "seer_check" and role == "seer":
            checked = set((view.get("my_checks") or {}).keys())
            unchecked = [s for s in legal if s not in checked]
            pool = unchecked or legal
            # Check the most-accused seat first: a pile is more often wolf-driven.
            target = most_accused(view, pool, self.rng) or self.rng.choice(pool)
            return {"tool": "night_action", "args": {"target": target}}
        if owe == "night_kill" and role == "werewolf":
            # Hit the exposed seer first; it is the pack's biggest threat.
            claimant = self._trusted_seer_claim(view)
            if claimant in legal:
                return {"tool": "night_action", "args": {"target": claimant}}
            partners = set(view.get("partners") or [])
            safe = [s for s in legal if s not in partners]
            pool = safe or legal
            return {"tool": "night_action", "args": {"target": self.rng.choice(pool)}}
        return super()._night(view, owe)


BRAINS = {
    "random": RandomBrain,
    "quiet": QuietBrain,
    "accuser": AccuserBrain,
    "baseline": BaselineBrain,
}


def make_brain(name: str, seed: int) -> BaseBrain:
    if name not in BRAINS:
        raise ValueError(f"unknown brain {name!r}; choose from {sorted(BRAINS)}")
    return BRAINS[name](seed)
