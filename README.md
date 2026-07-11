# ♟ Blind Spots

**Find out where you actually lose your chess games.** Point this at your
Lichess or Chess.com account and every move you've played gets re-examined by
Stockfish. The report shows where your evaluation leaked, by game phase, by
color, and move by move, with your worst positions drawn on boards.

![Overview](https://i.imgur.com/CU1qfiY.png)

## Why

Most chess tools tell you a move was bad _after_ you look it up. This finds
the patterns across your whole history: not "you blundered here," but "you
lose 2.4x more evaluation in endgames than anywhere else, and it's costing
you roughly one game in five." It turns a pile of PGNs into a ranked list of
what to actually work on.

## Features

- **Two platforms** — pulls your games from the Lichess or Chess.com public APIs (no key required)
- **Full-history engine review** — every position you faced, evaluated by Stockfish, scored by centipawn loss vs. the engine's preferred move
- **Adjustable depth** — qualitative presets (Low / Balanced / High) or an exact depth (6-24); depth is the speed/precision dial
- **Written feedback** — plain-language insights about your biggest leaks (phase weakness, blunder rate, color asymmetry, missed mates)
- **Breakdowns** — evaluation lost by game phase and by color, with inline eval-bars
- **Worst positions** — your top blunders rendered as boards, with what you played vs. what the engine wanted
- **Fast re-runs** — every (position, depth) evaluation is cached in SQLite, so re-running deeper or with more games only pays for new work
- **Resumable & robust** — interrupt any time; malformed games and API hiccups are logged and skipped, never fatal

## Quickstart

```bash
# 1. dependencies
pip install -r requirements.txt          # python-chess, flask

# 2. Stockfish (the one non-Python dependency)
#    macOS:         brew install stockfish
#    Ubuntu/WSL:    sudo apt install stockfish      -> /usr/games/stockfish
#    Windows:       download from stockfishchess.org, unzip, note the .exe path

# 3. run (point STOCKFISH_PATH at the executable file itself)
STOCKFISH_PATH=/usr/games/stockfish python app.py
#   Windows PowerShell:
#   $env:STOCKFISH_PATH="C:\path\to\stockfish-windows-x86-64-avx2.exe"; python app.py

# 4. open http://127.0.0.1:5000  ->  click "Run the demo instead" to verify setup
```

The demo analyzes bundled games with no network needed, if its report
renders, your whole setup works.
![Form](https://i.imgur.com/rmBdqRw.png)
![Worst Moves](https://i.imgur.com/orjqTu0.png)

## Performance

Measured on a 40-game corpus of distinct games (depth 12, single thread,
commodity CPU):

| metric                               | value                                                         |
| ------------------------------------ | ------------------------------------------------------------- |
| position cache hit rate, warm re-run | **99.9%** (≈**130× faster** than the cold run)                |
| position cache hit rate, cold run    | ~21% from opening-phase transpositions across games           |
| depth cost (per-position)            | 15 ms (depth 10) → 115 ms (14) → 702 ms (18), ≈**45× spread** |
| throughput                           | ~28 engine evaluations/sec at depth 12                        |

The cache is why raising depth or adding games later is cheap: only new
(position, depth) pairs hit the engine.

## Command line

The web app and CLI share one database and evaluation cache.

```bash
python main.py --username you --platform lichess --max-games 100 --depth 16
python main.py --username you --platform chesscom --depth 12 --max-games 500
```

Then query the results directly:

```sql
SELECT phase, COUNT(*), ROUND(AVG(cp_loss),1) FROM moves GROUP BY phase;
SELECT move_number, move_san, cp_loss, game_id FROM moves
  ORDER BY cp_loss DESC LIMIT 20;
```

## How it works

```
Lichess / Chess.com API
        | PGN
        v
   parse (python-chess)        detect your color, iterate your moves
        |
        v
   Stockfish eval  <---------  cache (fen, depth) in SQLite
        |                      centipawn loss = eval(best) - eval(played),
        v                      your POV; mate mapped to +/-10,000cp
   SQLite: moves table         one row per move, with cp_loss + phase
        |
        v
   report (pure SQL)           stats, phase/color breakdown, insights
        |
        v
   Flask + templates           background job, live progress, report page
```

**Centipawn loss** is measured from your perspective in the position before
your move: how much evaluation the engine's preferred move would have kept
versus what you actually played. Playing the top move scores 0; hanging a
queen scores several hundred; missing a forced mate scores ~10,000.

**The expensive path (ingest + engine eval) is separated from the cheap path
(SQL reporting).** Analysis is a background thread that streams progress;
opening a report is an instant query over already-computed data.

## Project layout

```
app.py                 Flask app: form, background jobs, progress, report
main.py                CLI entry point
src/
  ingest.py            Lichess + Chess.com API clients (pagination, rate limits)
  parse.py             PGN -> analyzed move rows (offline-testable)
  evaluate.py          Stockfish wrapper, centipawn loss, mate mapping, caching
  report.py            stats + rule-based feedback (pure SQL, no engine)
  db.py                SQLite schema, eval cache, idempotent writes
  demo_games.py        bundled fixture games for offline demo
templates/             base / index / progress / report
tests/test_pipeline.py PGN parsing, cp-loss on known positions, cache, idempotency
```

## Tests

```bash
STOCKFISH_PATH=/usr/games/stockfish python tests/test_pipeline.py
```

Verifies centipawn-loss math against known positions for both colors
(hanging pieces -> large loss, engine-best -> ~0, missed mate-in-1 -> ~10,000,
delivering mate -> 0), plus PGN parsing, cache behavior, idempotent
re-processing, and malformed-input handling.

## Roadmap

- **v2** — feature-engineer positions (material, king safety, tactical motifs) and cluster high-loss positions into _named_ recurring weaknesses
- **v3** — deployable multi-user version; track improvement on each weakness over time

## Notes

Uses the public read APIs of Lichess and Chess.com; be reasonable with
request volume. Stockfish is GPLv3. This project is for personal game review.

## License

MIT
