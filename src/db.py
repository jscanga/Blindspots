"""
db.py — SQLite persistence for the blind-spot detector.

Two tables:
  moves      one row per move the target player made (the analysis dataset)
  eval_cache one row per (fen, depth) Stockfish result, so re-runs and
             transpositions never pay for the same evaluation twice

Idempotency: `moves` has a UNIQUE constraint on (game_id, ply); inserting
the same move twice is a no-op. `games_done` records fully-processed games
so a re-run can skip them without touching the engine at all.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS moves (
    id            INTEGER PRIMARY KEY,
    game_id       TEXT NOT NULL,
    platform      TEXT NOT NULL,
    ply           INTEGER NOT NULL,          -- half-move index within the game (0-based)
    move_number   INTEGER NOT NULL,          -- full-move number as shown in PGN
    color         TEXT NOT NULL CHECK (color IN ('white', 'black')),
    fen_before    TEXT NOT NULL,
    move_uci      TEXT NOT NULL,
    move_san      TEXT NOT NULL,
    best_move_uci TEXT,
    cp_loss       INTEGER,                   -- centipawns lost vs engine best (>= 0)
    eval_before   INTEGER,                   -- eval (cp, player POV) before the move, playing best
    eval_after    INTEGER,                   -- eval (cp, player POV) after the actual move
    phase         TEXT CHECK (phase IN ('opening', 'middlegame', 'endgame')),
    depth         INTEGER,                   -- engine depth used
    UNIQUE (game_id, ply)
);

CREATE TABLE IF NOT EXISTS eval_cache (
    fen      TEXT NOT NULL,
    depth    INTEGER NOT NULL,
    score_cp INTEGER NOT NULL,               -- side-to-move POV, mate mapped to +/-MATE_CP
    best_uci TEXT,
    PRIMARY KEY (fen, depth)
);

CREATE TABLE IF NOT EXISTS games_done (
    game_id  TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    username TEXT NOT NULL,
    result   TEXT,
    end_time TEXT
);

CREATE INDEX IF NOT EXISTS idx_moves_cp_loss ON moves (cp_loss);
CREATE INDEX IF NOT EXISTS idx_moves_phase   ON moves (phase);
"""


@dataclass
class MoveRow:
    game_id: str
    platform: str
    ply: int
    move_number: int
    color: str
    fen_before: str
    move_uci: str
    move_san: str
    best_move_uci: Optional[str]
    cp_loss: Optional[int]
    eval_before: Optional[int]
    eval_after: Optional[int]
    phase: str
    depth: int


class Database:
    def __init__(self, path: str = "data/blindspots.db"):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.execute("PRAGMA journal_mode=WAL")  # safer for long runs

    # ---- eval cache ------------------------------------------------------
    def cached_eval(self, fen: str, depth: int) -> Optional[tuple[int, Optional[str]]]:
        row = self.conn.execute(
            "SELECT score_cp, best_uci FROM eval_cache WHERE fen=? AND depth=?",
            (fen, depth),
        ).fetchone()
        return (row[0], row[1]) if row else None

    def store_eval(self, fen: str, depth: int, score_cp: int, best_uci: Optional[str]) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO eval_cache (fen, depth, score_cp, best_uci) VALUES (?,?,?,?)",
            (fen, depth, score_cp, best_uci),
        )

    # ---- moves -----------------------------------------------------------
    def insert_move(self, m: MoveRow) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO moves
               (game_id, platform, ply, move_number, color, fen_before,
                move_uci, move_san, best_move_uci, cp_loss,
                eval_before, eval_after, phase, depth)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (m.game_id, m.platform, m.ply, m.move_number, m.color, m.fen_before,
             m.move_uci, m.move_san, m.best_move_uci, m.cp_loss,
             m.eval_before, m.eval_after, m.phase, m.depth),
        )

    # ---- game bookkeeping --------------------------------------------------
    def game_done(self, game_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM games_done WHERE game_id=?", (game_id,)
        ).fetchone() is not None

    def mark_game_done(self, game_id: str, platform: str, username: str,
                       result: str = "", end_time: str = "") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO games_done (game_id, platform, username, result, end_time) "
            "VALUES (?,?,?,?,?)",
            (game_id, platform, username, result, end_time),
        )

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # ---- summary (for the CLI's end-of-run report) -------------------------
    def summary(self) -> dict:
        n_moves = self.conn.execute("SELECT COUNT(*) FROM moves").fetchone()[0]
        n_games = self.conn.execute("SELECT COUNT(*) FROM games_done").fetchone()[0]
        n_cache = self.conn.execute("SELECT COUNT(*) FROM eval_cache").fetchone()[0]
        avg_loss = self.conn.execute(
            "SELECT ROUND(AVG(cp_loss),1) FROM moves WHERE cp_loss IS NOT NULL"
        ).fetchone()[0]
        worst = self.conn.execute(
            "SELECT game_id, move_number, move_san, cp_loss FROM moves "
            "WHERE cp_loss IS NOT NULL ORDER BY cp_loss DESC LIMIT 5"
        ).fetchall()
        return {"games": n_games, "moves": n_moves, "cached_evals": n_cache,
                "avg_cp_loss": avg_loss, "worst_moves": worst}
