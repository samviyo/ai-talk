# Finale runbook

The finale is one 12-seat game on the projector. Every team plays from its own laptop with whatever bot
it has; there is no submission. Two baseline house bots fill the empty seats, and any team not connected
in time is replaced by a house bot too.

## Before the day

1. **`teams.toml`** (repo root, untracked): one `[teams.<name>]` per team with its `bifrost_key_env`,
   and each of those env vars set in the shell that runs the finale. See `teams.example.toml`.
2. **`final.toml`** (anywhere; scratch is fine): one entry per team, names matching `teams.toml`.

   ```toml
   seats = 12
   seed = 2026
   [[teams]]
   name = "wolfpack"
   [[teams]]
   name = "silverbullet"
   # ...one per team, no repo
   ```

3. **Network check** on the venue Wi-Fi. Office networks can block laptop-to-laptop traffic. Start any
   server with `--host 0.0.0.0`, then from another laptop run `curl http://<organiser-ip>:<port>/`.
   If it can't connect, use a hotspot, or fall back to repo seats (add `repo` and `cmd` to a team; see
   `docs/spec.md`).
4. **Zero-spend rehearsal** with the same `final.toml`:

   ```sh
   uv run ww final final.toml --fake-llm --host 0.0.0.0 --port 8046 --ready-s 60 --auto-start
   ```

   Run one team's starter bot with the printed `WW_SERVER`/`WW_TOKEN` and check that it says
   `seat N role ...`. Under `--fake-llm` the starter bot never nominates; that's expected.

## On the day

1. In the repo root: `source scripts/bifrost-env.sh`, then export every team key env var named in `teams.toml`.
2. Launch. `--ready-s` is how long teams get to connect before house bots take their seats:

   ```sh
   uv run ww final final.toml --host 0.0.0.0 --port 8046 --ready-s 300
   ```

   It prints the spectator URL and, per team, the command it runs:

   ```
   wolfpack (seat 3):
     WW_SERVER=http://10.x.x.x:8046 WW_TOKEN=... uv run bot
   ```

   The same values are in `runs/final/laptop_seats.json`. Tokens are new on every launch.
3. Open the spectator URL on the projector. The lobby shows each seat as `joining…` or `ready`.
4. DM each team its command line (the indented one, without the team label). They run it in `starter/`. The bot should print `seat N role ...`, then wait.
5. When the lobby is all `ready` (or `--ready-s` has passed and house bots have filled the gaps), start the game:

   ```sh
   uv run ww start --server http://127.0.0.1:8046
   ```

6. When the game ends, the board stays up until you Ctrl-C. The run is written under `runs/`.

## When something goes wrong

| Symptom | Meaning / fix |
|---|---|
| Team's bot prints `get_my_role failed: {'error': 'unauthorized'...}` | It joined after `--ready-s`; a house bot has the seat. Nothing to fix mid-game. |
| Team's bot can't connect | Wrong IP or blocked network; see the network check above. |
| `teams.toml has no usable key env var for: X` | Export X's key env var, or fix the name in `final.toml`. |
| A team's bot crashes mid-game | They can rerun the same command; until then their seat misses deadlines. |
