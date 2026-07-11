"""
evaluate.py — Stockfish evaluation and centipawn-loss computation.

Centipawn loss definition used here (the standard one):

    cp_loss = eval(position, engine plays best)  -  eval(position after YOUR move)

both measured from YOUR perspective, in the position BEFORE your move.
If you played the engine's best move, cp_loss == 0 (never negative:
by definition no legal move evaluates better than the best move at the
same depth; we clamp tiny negative artifacts from search instability).

Mate handling: mate-in-N scores are mapped to +/-MATE_CP (a large capped
value, further shrunk slightly by N so faster mates score higher). This
keeps "missed mate in 2" quantitatively enormous without infinities.

Depth tradeoff: depth 16 (default) is a good blunder-detection setting —
roughly ~2600+ Elo judgment at well under a second per position on modern
hardware. Depth 12 is ~3-4x faster and fine for a first pass over
thousands of games; depth 20+ doubles-plus the runtime for marginal gain
on the "did I blunder" question. Every (fen, depth) result is cached in
SQLite, so raising depth later only costs the delta.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import chess
import chess.engine

from .db import Database

log = logging.getLogger(__name__)

MATE_CP = 10_000  # cap for mate scores, in centipawns


def score_to_cp(score: chess.engine.PovScore, pov: chess.Color) -> int:
    """Convert a python-chess PovScore to capped centipawns from `pov`."""
    s = score.pov(pov)
    if s.is_mate():
        mate_in = s.mate()
        # mate FOR pov -> large positive; mate AGAINST pov -> large negative.
        # Subtract |N| so mate-in-1 > mate-in-5.
        if mate_in > 0:
            return MATE_CP - min(abs(mate_in), 100)
        return -MATE_CP + min(abs(mate_in), 100)
    return s.score()  # already centipawns


class Evaluator:
    """Wraps a UCI engine with a SQLite-backed (fen, depth) cache."""

    def __init__(self, db: Database, engine_path: str = "stockfish",
                 depth: int = 16, threads: int = 1, hash_mb: int = 128):
        self.db = db
        self.depth = depth
        self.engine = chess.engine.SimpleEngine.popen_uci(engine_path)
        self.engine.configure({"Threads": threads, "Hash": hash_mb})
        self.cache_hits = 0
        self.engine_calls = 0

    def close(self) -> None:
        try:
            self.engine.quit()
        except chess.engine.EngineError:
            pass

    def eval_position(self, board: chess.Board) -> tuple[int, Optional[str]]:
        """Return (score_cp from side-to-move POV, best move uci) at self.depth.

        Cached by (fen, depth). Side-to-move POV is the natural caching key
        orientation: it's independent of which player we're analyzing.
        """
        fen = board.fen()
        cached = self.db.cached_eval(fen, self.depth)
        if cached is not None:
            self.cache_hits += 1
            return cached

        self.engine_calls += 1
        info = self.engine.analyse(board, chess.engine.Limit(depth=self.depth))
        score_cp = score_to_cp(info["score"], board.turn)
        pv = info.get("pv")
        best_uci = pv[0].uci() if pv else None
        self.db.store_eval(fen, self.depth, score_cp, best_uci)
        return score_cp, best_uci

    def centipawn_loss(self, board_before: chess.Board,
                       move: chess.Move) -> tuple[int, Optional[str], int, int]:
        """Compute cp loss for `move` played from `board_before`.

        Returns (cp_loss, best_move_uci, eval_before, eval_after), all evals
        from the MOVER's perspective.

        eval_before: what the mover could have had playing the engine line.
        eval_after:  what the mover actually has after their move.
        """
        mover = board_before.turn

        # Eval of the position before the move, side-to-move (=mover) POV.
        eval_before, best_uci = self.eval_position(board_before)

        # Eval after the actual move: now it's the opponent to move, so the
        # cached score is from the OPPONENT's POV — negate for the mover.
        board_after = board_before.copy(stack=False)
        board_after.push(move)
        if board_after.is_checkmate():
            # Mover delivered mate: best possible outcome, no engine needed.
            eval_after = MATE_CP - 1
        elif board_after.is_stalemate() or board_after.is_insufficient_material():
            eval_after = 0
        else:
            opp_score, _ = self.eval_position(board_after)
            eval_after = -opp_score

        # By definition the best move at equal depth can't be worse than the
        # played move; clamp small negatives caused by search instability.
        cp_loss = max(0, eval_before - eval_after)
        return cp_loss, best_uci, eval_before, eval_after


def game_phase(board: chess.Board) -> str:
    """Crude but standard phase heuristic (queens + minor/rook count).

    opening:    both queens on, >= 10 non-pawn/king pieces, before move 12
    endgame:    <= 6 non-pawn/king pieces, or no queens and <= 8
    middlegame: everything else
    """
    non_pk = sum(
        1 for p in board.piece_map().values()
        if p.piece_type not in (chess.PAWN, chess.KING)
    )
    queens = sum(1 for p in board.piece_map().values()
                 if p.piece_type == chess.QUEEN)
    if board.fullmove_number <= 12 and non_pk >= 10:
        return "opening"
    if non_pk <= 6 or (queens == 0 and non_pk <= 8):
        return "endgame"
    return "middlegame"
