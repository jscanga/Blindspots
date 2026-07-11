"""
parse.py — turn a PGN string into analyzed MoveRow records for one player.

Kept separate from ingest (network) and evaluate (engine) so the parsing
logic is testable offline with fixture PGNs.
"""
from __future__ import annotations

import io
import logging
from typing import Iterator, Optional

import chess
import chess.pgn

from .db import Database, MoveRow
from .evaluate import Evaluator, game_phase

log = logging.getLogger(__name__)


def detect_color(game: chess.pgn.Game, username: str) -> Optional[chess.Color]:
    """Which side did `username` play? None if they're not in this game."""
    white = (game.headers.get("White") or "").lower()
    black = (game.headers.get("Black") or "").lower()
    u = username.lower()
    if white == u:
        return chess.WHITE
    if black == u:
        return chess.BLACK
    return None


def game_identifier(game: chess.pgn.Game, fallback: str) -> str:
    """Stable id: Lichess/Chess.com put a unique URL in the Site or Link tag."""
    for tag in ("Site", "Link", "GameId"):
        v = game.headers.get(tag, "")
        if "://" in v or (tag == "GameId" and v):
            return v
    return fallback


def process_game(pgn_text: str, username: str, platform: str,
                 db: Database, ev: Evaluator,
                 fallback_id: str) -> int:
    """Analyze every move `username` played in one game. Returns rows added.

    Malformed PGNs and games the user isn't in are skipped with a log line,
    never an exception — a 1,000-game run must not die on game 734.
    """
    try:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
    except Exception as e:  # noqa: BLE001
        log.warning("unparseable PGN (%s) — skipped", e)
        return 0
    if game is None:
        log.warning("empty PGN — skipped")
        return 0

    color = detect_color(game, username)
    if color is None:
        log.info("user %s not a player in game %s — skipped",
                 username, game.headers.get("Site", "?"))
        return 0

    game_id = game_identifier(game, fallback_id)
    if db.game_done(game_id):
        return 0

    board = game.board()
    rows = 0
    for ply, move in enumerate(game.mainline_moves()):
        if board.turn == color:
            fen_before = board.fen()
            try:
                san = board.san(move)
            except ValueError:
                log.warning("illegal move in %s at ply %d — abandoning game",
                            game_id, ply)
                break
            cp_loss, best_uci, ev_before, ev_after = ev.centipawn_loss(board, move)
            db.insert_move(MoveRow(
                game_id=game_id,
                platform=platform,
                ply=ply,
                move_number=board.fullmove_number,
                color="white" if color == chess.WHITE else "black",
                fen_before=fen_before,
                move_uci=move.uci(),
                move_san=san,
                best_move_uci=best_uci,
                cp_loss=cp_loss,
                eval_before=ev_before,
                eval_after=ev_after,
                phase=game_phase(board),
                depth=ev.depth,
            ))
            rows += 1
        try:
            board.push(move)
        except ValueError:
            log.warning("illegal move in %s at ply %d — abandoning game", game_id, ply)
            break

    db.mark_game_done(
        game_id, platform, username,
        result=game.headers.get("Result", ""),
        end_time=game.headers.get("UTCDate", game.headers.get("Date", "")),
    )
    db.commit()  # commit per game: a killed run loses at most one game
    return rows
