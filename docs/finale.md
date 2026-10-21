# Finale runbook

The finale is one 16-seat game on the projector: 14 teams plus 2 house seats. Every team plays from its own
laptop with whatever bot it has; there is no submission. Baseline house bots fill the empty seats, and any
team not connected in time is replaced by a house bot too.

## Before the day

1. **Keys.** By default every team shares `BIFROST_API_KEY`, and budgets are still per seat. You need
   `teams.toml` (repo root, untracked; see `teams.example.toml`) only to bill a team to its own key.
2. **`final.toml`** (anywhere; scratch is fine): one entry per team.

   ```toml
   seats = 16                          # teams + 2, so game 2 has house seats to forge from
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
   `seat N · team <name> · in the lobby`. Under `--fake-llm` the starter bot never nominates; house bots still do.

## On the day

The finale is two games of about 10 minutes each: game 1 plain, then the Forger reveal and a 10-minute
fix window, then game 2 with forgers. Give each game its own `--runs-dir` so game 2 doesn't overwrite
game 1's record.

1. Plug the laptop in, keep the lid open, and run the finale under `caffeinate -dims` (`caffeinate -dims uv run ww final ...`).
   A sleeping server stalls the game clock for everyone; a dry run lost 8 minutes to a closed lid.
2. In the repo root: `source scripts/bifrost-env.sh` (plus any per-team key vars, if you use `teams.toml`).
3. Launch. `--ready-s` is how long teams get to connect before house bots take their seats:

   ```sh
   caffeinate -dims uv run ww final final.toml --host 0.0.0.0 --port 8046 --ready-s 300 --runs-dir runs/game1
   ```

   Keep the laptop plugged in: a sleeping laptop freezes the game mid-phase.

   It prints the spectator URL and, per team, the command it runs:

   ```
   wolfpack (seat 3):
     WW_SERVER=http://10.x.x.x:8046 WW_TOKEN=... uv run bot
   ```

   The same values are in `runs/game1/final/laptop_seats.json`. Tokens are new on every launch.
4. Open the spectator URL on the projector. The lobby shows each seat as `joining…` or `ready`.
5. DM each team its command line (the indented one, without the team label). They run it in `starter/`. The bot should print `seat N · team <name> · in the lobby`, then wait; that line is how a team checks which seat is theirs.
6. Start whenever you like; you don't have to wait for every team or for `--ready-s`:

   ```sh
   uv run ww start --server http://127.0.0.1:8046 --runs-dir runs/game1
   ```

   If seats are still `joining…` it prints `seats [...] not here: house bots take them, then the game starts`,
   and the game opens a few seconds later. A team that misses game 1 just joins game 2 with its new line.

7. When the game ends, the board stays up until you Ctrl-C. The run is written under `runs/game1/`.
8. Reveal the Forger and open the fix window (below). Relaunch with `--forgers 2 --runs-dir runs/game2`
   and DM the new lines during the window; tokens change on every launch. Start game 2 as in step 6.

## The Forger expansion

Revealed between the two finale games. A `forger` house bot plays like `baseline`, but twice a day it
posts a forged moderator notice naming one seat. Each forgery uses newlines to break out of its board line
and fake the starter prompt's own `Moderator announcements:` section, so a bot that pastes chat into its
prompt sees a real-looking notice. The stock starter falls for it: on Haiku it voted the forged target in
15/15 probes (0/5 on a control board). The notices are chat text only, so house bots ignore them. Nothing
breaks: every bot keeps playing, so a team only notices by reading transcripts or by a gate going red.
The fix lives in `bot/` (escape or flatten board text, attribute it to its seat), after a new locked gate.

1. **Reveal** (~2 min): put a real forged post on screen; the stock bot obeys it 15/15.
2. **Fix window** (~10 min): a spoof case in their suite first, then the fix in `bot/`, then
   `make practice N=3 FORGERS=2` in `starter/` as proof (one game can end before a forgery airs). DM game 2's lines meanwhile.
3. **Game 2** proves it on the projector. Debrief on whose suite caught it, not just whose bot won.

- Game 2 seats forgers in house seats: `uv run ww final final.toml ... --forgers 2`. With 14 teams keep `seats = 16`:
  the 2 house seats both forge. `--forgers` needs that many house seats, and `ww final` refuses more teams than seats.
- Past 12 seats the finale stretches each day so every post airs: at 16 seats, 4 wolves, 50s discussion,
  a post every 2.2s and up to 7 days.
- The spectator labels them `house · forger`; players never see that label.

## When something goes wrong

| Symptom | Meaning / fix |
|---|---|
| Team's bot prints `get_my_role failed: {'error': 'unauthorized'...}` | It joined after the start (or `--ready-s`); a house bot has the seat. It joins game 2 with the new line. |
| Team's bot can't connect, or repeats `lost the server (ConnectError)` | Wrong IP or blocked network; see the network check above. |
| `no Bifrost key for: X` | `BIFROST_API_KEY` isn't set in the finale shell (or X's own var, if `teams.toml` lists X). |
| A team's bot prints `lost the server (ReadTimeout); reconnecting` | A Wi-Fi blip or a sleeping laptop; it rejoins by itself and misses only the windows that closed meanwhile. |
| A team's bot crashes mid-game | They can rerun the same command; until then their seat misses deadlines. |
