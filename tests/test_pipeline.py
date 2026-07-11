"""
Tests for the v1 pipeline. Run:  python tests/test_pipeline.py

Covers:
  1. PGN parsing / color detection / game id extraction
  2. Centipawn-loss math on KNOWN positions (the sign-convention test):
       - hanging a queen must produce a huge cp_loss
       - playing the engine's own best move must produce ~0 cp_loss
       - missing a mate-in-1 must produce a huge cp_loss (mate mapping)
       - the same logic must hold for BLACK (POV/negation correctness)
  3. Eval cache: second identical evaluation must not call the engine
  4. Idempotency: re-processing the same game adds no duplicate rows
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import chess.pgn

from src.db import Database
from src.evaluate import Evaluator, MATE_CP, game_phase, score_to_cp
from src.parse import detect_color, game_identifier, process_game

ENGINE = os.environ.get("STOCKFISH_PATH", "/usr/games/stockfish")
DEPTH = 12  # fast for tests; correctness is depth-independent

FIXTURE_PGN = """\
[Event "Test Casual Game"]
[Site "https://lichess.org/abcd1234"]
[White "testuser"]
[Black "opponent"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O 1-0
"""


def test_pgn_parsing():
    game = chess.pgn.read_game(io.StringIO(FIXTURE_PGN))
    assert game is not None
    assert detect_color(game, "testuser") == chess.WHITE
    assert detect_color(game, "TESTUSER") == chess.WHITE  # case-insensitive
    assert detect_color(game, "opponent") == chess.BLACK
    assert detect_color(game, "nobody") is None
    assert game_identifier(game, "fb") == "https://lichess.org/abcd1234"
    moves = list(game.mainline_moves())
    assert len(moves) == 9
    print("  pgn parsing ok")


def test_phase_heuristic():
    assert game_phase(chess.Board()) == "opening"
    # K+R vs K+R -> endgame
    assert game_phase(chess.Board("4k3/8/8/8/8/8/8/R3K2R w KQ - 0 40")) == "endgame"
    print("  phase heuristic ok")


def test_mate_score_mapping():
    import chess.engine as ce
    # mate in 2 for white, from white POV -> large positive, < mate in 1
    m2 = ce.PovScore(ce.Mate(2), chess.WHITE)
    m1 = ce.PovScore(ce.Mate(1), chess.WHITE)
    assert score_to_cp(m1, chess.WHITE) > score_to_cp(m2, chess.WHITE) > 9000
    # same scores from black POV -> large negative
    assert score_to_cp(m1, chess.BLACK) < -9000
    print("  mate score mapping ok")


def test_cp_loss_known_positions():
    db = Database(":memory:")
    ev = Evaluator(db, engine_path=ENGINE, depth=DEPTH)
    try:
        # --- 1. Hanging the queen as WHITE must be a huge loss. ---
        # Italian-ish position; Qh5?? just drops the queen to g6/Nxh5 ideas —
        # use a cleaner construction: white queen on d1 can be placed en prise.
        board = chess.Board(
            "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
        # Qd1-h5 attacks e5/f7 but here just test a genuine blunder: Nf3xe5??
        # Nxe5 Nxe5 loses a knight for a pawn.
        blunder = chess.Move.from_uci("f3e5")
        loss, best, e_before, e_after = ev.centipawn_loss(board, blunder)
        assert loss > 120, f"expected >120cp loss for Nxe5??, got {loss}"

        # --- 2. Playing the engine's own best move -> ~0 loss. ---
        board2 = chess.Board()
        _, best_uci = ev.eval_position(board2)
        loss2, _, _, _ = ev.centipawn_loss(board2, chess.Move.from_uci(best_uci))
        assert loss2 <= 30, f"engine best move should lose ~0cp, got {loss2}"

        # --- 3. Missing mate in 1 -> enormous loss (mate mapping works). ---
        # Scholar's mate position: white to move, Qxf7# available.
        board3 = chess.Board(
            "r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
        assert board3.is_legal(chess.Move.from_uci("h5f7"))
        quiet = chess.Move.from_uci("g1f3")  # develop instead of mating
        loss3, best3, _, _ = ev.centipawn_loss(board3, quiet)
        assert best3 == "h5f7", f"engine should find Qxf7#, found {best3}"
        assert loss3 > 5000, f"missing mate-in-1 should be huge, got {loss3}"

        # --- 4. Playing the mate itself -> 0 loss. ---
        loss4, _, _, _ = ev.centipawn_loss(board3, chess.Move.from_uci("h5f7"))
        assert loss4 == 0, f"delivering mate should lose 0cp, got {loss4}"

        # --- 5. BLACK POV correctness: black hanging a rook is a big loss. ---
        # After 1.e4 e5 2.Nf3, black playing Ra8-a6?? (loses to Bxa6 later
        # isn't immediate; use a directly hanging move instead): Qd8-g5??
        # hangs the queen to Nxg5.
        board5 = chess.Board(
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2")
        qg5 = chess.Move.from_uci("d8g5")
        assert board5.is_legal(qg5)
        loss5, _, _, _ = ev.centipawn_loss(board5, qg5)
        assert loss5 > 400, f"black hanging queen should be >400cp, got {loss5}"

        print(f"  cp-loss known positions ok "
              f"(blunder={loss}, best={loss2}, missed-mate={loss3}, "
              f"mate=0, black-blunder={loss5})")
    finally:
        ev.close()


def test_cache_and_idempotency():
    db = Database(":memory:")
    ev = Evaluator(db, engine_path=ENGINE, depth=DEPTH)
    try:
        board = chess.Board()
        ev.eval_position(board)
        calls_after_first = ev.engine_calls
        ev.eval_position(board)  # identical -> must hit cache
        assert ev.engine_calls == calls_after_first, "cache miss on repeat eval"
        assert ev.cache_hits >= 1

        # idempotent game processing
        n1 = process_game(FIXTURE_PGN, "testuser", "lichess", db, ev, "fb1")
        assert n1 == 5  # white played 5 moves
        n2 = process_game(FIXTURE_PGN, "testuser", "lichess", db, ev, "fb1")
        assert n2 == 0, "re-processing a done game must add nothing"
        total = db.conn.execute("SELECT COUNT(*) FROM moves").fetchone()[0]
        assert total == 5, f"expected 5 rows after double-processing, got {total}"
        print(f"  cache + idempotency ok (5 rows, no dupes, "
              f"{ev.cache_hits} cache hits)")
    finally:
        ev.close()


def test_malformed_pgn_does_not_crash():
    db = Database(":memory:")
    ev = Evaluator(db, engine_path=ENGINE, depth=DEPTH)
    try:
        assert process_game("not a pgn at all", "testuser", "lichess", db, ev, "x") == 0
        assert process_game("", "testuser", "lichess", db, ev, "y") == 0
        # valid pgn, user not in it
        other = FIXTURE_PGN.replace("testuser", "someoneelse")
        assert process_game(other, "testuser", "lichess", db, ev, "z") == 0
        print("  malformed/foreign pgn handling ok")
    finally:
        ev.close()


if __name__ == "__main__":
    print("running pipeline tests (engine: %s, depth %d):" % (ENGINE, DEPTH))
    test_pgn_parsing()
    test_phase_heuristic()
    test_mate_score_mapping()
    test_cp_loss_known_positions()
    test_cache_and_idempotency()
    test_malformed_pgn_does_not_crash()
    print("ALL TESTS PASSED")
