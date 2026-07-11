"""
main.py — CLI for the blind-spot detector v1 pipeline.

    python main.py --username myname --platform lichess --max-games 100
    python main.py --username myname --platform chesscom --depth 12 \
                   --engine-path /usr/games/stockfish

Result: data/blindspots.db with one row per move you played, tagged with
centipawn loss, game phase, and the engine's preferred move.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.db import Database
from src.evaluate import Evaluator
from src.ingest import fetch_games
from src.parse import process_game


def main() -> int:
    ap = argparse.ArgumentParser(description="Chess blind-spot detector: v1 pipeline")
    ap.add_argument("--username", required=True)
    ap.add_argument("--platform", required=True, choices=["lichess", "chesscom"])
    ap.add_argument("--max-games", type=int, default=None,
                    help="cap on number of (most recent) games to process")
    ap.add_argument("--depth", type=int, default=16,
                    help="Stockfish search depth (12=fast pass, 16=default, 20=slow/precise)")
    ap.add_argument("--engine-path",
                    default=os.environ.get("STOCKFISH_PATH", "stockfish"),
                    help="path to stockfish binary (or set STOCKFISH_PATH)")
    ap.add_argument("--db", default="data/blindspots.db")
    ap.add_argument("--threads", type=int, default=1,
                    help="engine threads (1 keeps evals deterministic-ish)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("main")

    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
    db = Database(args.db)
    try:
        ev = Evaluator(db, engine_path=args.engine_path,
                       depth=args.depth, threads=args.threads)
    except FileNotFoundError:
        log.error("Stockfish not found at %r. Install it (e.g. `apt install "
                  "stockfish`, `brew install stockfish`) and pass --engine-path "
                  "or set STOCKFISH_PATH.", args.engine_path)
        return 1

    t0 = time.time()
    games = moves = 0
    try:
        for i, pgn in enumerate(fetch_games(args.platform, args.username,
                                            args.max_games)):
            added = process_game(pgn, args.username, args.platform, db, ev,
                                 fallback_id=f"{args.platform}:{args.username}:{i}")
            if added:
                games += 1
                moves += added
                if games % 10 == 0:
                    log.info("processed %d games, %d moves analyzed "
                             "(engine calls: %d, cache hits: %d)",
                             games, moves, ev.engine_calls, ev.cache_hits)
    except KeyboardInterrupt:
        log.warning("interrupted — progress is saved, rerun to resume")
    finally:
        ev.close()

    s = db.summary()
    db.close()
    dt = time.time() - t0
    print(f"\n=== run complete in {dt:.0f}s ===")
    print(f"games analyzed (total in db): {s['games']}")
    print(f"moves analyzed (total in db): {s['moves']}")
    print(f"cached evaluations:           {s['cached_evals']}")
    print(f"average centipawn loss:       {s['avg_cp_loss']}")
    print("worst moves:")
    for game_id, move_no, san, loss in s["worst_moves"]:
        print(f"  move {move_no:>3} {san:<8} lost {loss:>5} cp   {game_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
