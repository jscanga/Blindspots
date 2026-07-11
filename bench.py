"""
bench.py — measure real performance numbers for the README/resume.

Measures, against the actual Stockfish engine:
  1. cold-run throughput (moves/sec, evals/sec) and unique-position count
  2. cache hit rate from transpositions within a single cold run
  3. warm re-run speedup (re-processing the same games -> ~all cache hits)
  4. depth cost curve (relative time per position at depth 10/14/18)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.db import Database
from src.evaluate import Evaluator
from src.parse import process_game
from src.demo_games import DEMO_PGNS

ENGINE = os.environ.get("STOCKFISH_PATH", "/usr/games/stockfish")


def make_corpus(n_games, seed=0):
    """Generate genuinely distinct games that share only realistic opening
    overlap — the honest scenario for measuring the transposition cache.

    Each game starts from one of a few common openings (a player's
    repertoire), then plays random-but-legal moves so games diverge after
    the opening exactly as real games do. This makes the cache-hit number
    reflect true shared-position overlap, not artificial duplication.
    """
    import random
    import chess
    import chess.pgn

    openings = [
        ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],   # Ruy Lopez
        ["e2e4", "c7c5", "g1f3", "d7d6", "d2d4"],   # Sicilian
        ["d2d4", "d7d5", "c2c4", "e7e6", "b1c3"],   # QGD
        ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"],   # Italian
    ]
    rng = random.Random(seed)
    out = []
    for i in range(n_games):
        board = chess.Board()
        for uci in openings[i % len(openings)]:
            board.push(chess.Move.from_uci(uci))
        for _ in range(20):  # diverge after the opening
            if board.is_game_over():
                break
            board.push(rng.choice(list(board.legal_moves)))
        game = chess.pgn.Game.from_board(board)
        game.headers["White"] = "demo_player" if i % 2 == 0 else "opponent"
        game.headers["Black"] = "opponent" if i % 2 == 0 else "demo_player"
        game.headers["Site"] = f"bench/g{i}"
        out.append(str(game))
    return out


def run(db_path, corpus, depth, username="demo_player"):
    if os.path.exists(db_path):
        os.remove(db_path)
    db = Database(db_path)
    ev = Evaluator(db, engine_path=ENGINE, depth=depth)
    t0 = time.time()
    moves = 0
    for i, pgn in enumerate(corpus):
        moves += process_game(pgn, username, "demo", db, ev, f"bench{i}")
    dt = time.time() - t0
    stats = dict(games=db.summary()["games"], moves=moves,
                 calls=ev.engine_calls, hits=ev.cache_hits,
                 unique=db.summary()["cached_evals"], secs=dt)
    ev.close()
    db.close()
    return stats


def rerun_warm(db_path, corpus, depth, username="demo_player"):
    """Re-process the SAME games against the populated cache (warm)."""
    db = Database(db_path)
    ev = Evaluator(db, engine_path=ENGINE, depth=depth)
    # force re-analysis by clearing games_done but keeping eval_cache
    db.conn.execute("DELETE FROM games_done")
    db.conn.execute("DELETE FROM moves")
    db.commit()
    t0 = time.time()
    moves = 0
    for i, pgn in enumerate(corpus):
        moves += process_game(pgn, username, "demo", db, ev, f"bench{i}")
    dt = time.time() - t0
    stats = dict(moves=moves, calls=ev.engine_calls, hits=ev.cache_hits, secs=dt)
    ev.close()
    db.close()
    return stats


if __name__ == "__main__":
    corpus = make_corpus(40)
    print(f"corpus: {len(corpus)} games\n")

    print("=== depth 12 cold run ===")
    cold = run("data/bench.db", corpus, depth=12)
    hit_rate = 100 * cold["hits"] / (cold["hits"] + cold["calls"])
    print(f"games={cold['games']} moves={cold['moves']} "
          f"engine_calls={cold['calls']} cache_hits={cold['hits']} "
          f"unique_positions={cold['unique']}")
    print(f"cold cache-hit rate (transpositions): {hit_rate:.1f}%")
    print(f"throughput: {cold['moves']/cold['secs']:.1f} moves/s, "
          f"{cold['calls']/cold['secs']:.1f} engine-evals/s, "
          f"total {cold['secs']:.1f}s")

    print("\n=== warm re-run (same games, populated cache) ===")
    warm = run("data/bench.db", corpus, depth=12)  # rebuild cold first
    warm2 = rerun_warm("data/bench.db", corpus, depth=12)
    warm_hit = 100 * warm2["hits"] / (warm2["hits"] + max(1, warm2["calls"]))
    speedup = cold["secs"] / max(0.01, warm2["secs"])
    print(f"warm re-run: engine_calls={warm2['calls']} cache_hits={warm2['hits']} "
          f"({warm_hit:.1f}% hits), {warm2['secs']:.2f}s vs cold {cold['secs']:.1f}s "
          f"-> {speedup:.0f}x faster")

    print("\n=== depth cost curve (per-position time) ===")
    small = make_corpus(8)
    for d in (10, 14, 18):
        s = run(f"data/bench_d{d}.db", small, depth=d)
        per = s["secs"] / max(1, s["calls"])
        print(f"depth {d:>2}: {s['calls']} evals, {per*1000:.0f} ms/position")
        os.remove(f"data/bench_d{d}.db")

    if os.path.exists("data/bench.db"):
        os.remove("data/bench.db")
