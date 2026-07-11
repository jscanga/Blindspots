"""
report.py — turn the analyzed moves table into human feedback.

Everything here is derived from the v1 schema; no engine calls. The insight
rules are deliberately simple and transparent (thresholds a chess player
would recognize), so the feedback is explainable:

    blunder   cp_loss >= 200   (roughly: dropped a piece / missed a mate idea)
    mistake   100 <= cp_loss < 200
    inaccuracy 50 <= cp_loss < 100
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .db import Database

BLUNDER, MISTAKE, INACCURACY = 200, 100, 50


@dataclass
class Report:
    username: str
    platform: str
    games: int = 0
    moves: int = 0
    avg_loss: float = 0.0
    blunders: int = 0
    mistakes: int = 0
    inaccuracies: int = 0
    blunder_rate: float = 0.0          # blunders per 100 moves
    by_phase: list = field(default_factory=list)   # (phase, moves, avg, blunders)
    by_color: list = field(default_factory=list)   # (color, moves, avg)
    worst: list = field(default_factory=list)      # worst move dicts
    insights: list = field(default_factory=list)   # ordered feedback strings


def _user_filter(db: Database, username: str, platform: str) -> str:
    """moves rows for games this user played on this platform."""
    return (
        "FROM moves m JOIN games_done g ON m.game_id = g.game_id "
        "WHERE g.username = ? AND g.platform = ?"
    )


def build_report(db: Database, username: str, platform: str,
                 worst_n: int = 6) -> Report:
    f = _user_filter(db, username, platform)
    p = (username, platform)
    r = Report(username=username, platform=platform)

    row = db.conn.execute(
        f"SELECT COUNT(DISTINCT m.game_id), COUNT(*), "
        f"COALESCE(ROUND(AVG(m.cp_loss),1),0), "
        f"SUM(m.cp_loss >= {BLUNDER}), "
        f"SUM(m.cp_loss >= {MISTAKE} AND m.cp_loss < {BLUNDER}), "
        f"SUM(m.cp_loss >= {INACCURACY} AND m.cp_loss < {MISTAKE}) {f}", p
    ).fetchone()
    r.games, r.moves, r.avg_loss = row[0], row[1], row[2]
    r.blunders, r.mistakes, r.inaccuracies = row[3] or 0, row[4] or 0, row[5] or 0
    if r.moves == 0:
        r.insights.append("No analyzed moves found for this user yet.")
        return r
    r.blunder_rate = round(100.0 * r.blunders / r.moves, 1)

    r.by_phase = db.conn.execute(
        f"SELECT m.phase, COUNT(*), ROUND(AVG(m.cp_loss),1), "
        f"SUM(m.cp_loss >= {BLUNDER}) {f} "
        f"GROUP BY m.phase ORDER BY AVG(m.cp_loss) DESC", p
    ).fetchall()

    r.by_color = db.conn.execute(
        f"SELECT m.color, COUNT(*), ROUND(AVG(m.cp_loss),1) {f} "
        f"GROUP BY m.color", p
    ).fetchall()

    r.worst = [
        {"game_id": g, "move_number": n, "san": s, "uci": u, "best": b or "?",
         "cp_loss": l, "phase": ph, "fen": fen, "color": c}
        for g, n, s, u, b, l, ph, fen, c in db.conn.execute(
            f"SELECT m.game_id, m.move_number, m.move_san, m.move_uci, "
            f"m.best_move_uci, m.cp_loss, m.phase, m.fen_before, m.color {f} "
            f"ORDER BY m.cp_loss DESC LIMIT ?", (*p, worst_n)
        ).fetchall()
    ]

    r.insights = _insights(r)
    return r


def _insights(r: Report) -> list[str]:
    out: list[str] = []

    # Overall calibration against rough rating-band expectations.
    if r.avg_loss < 25:
        out.append(f"Average loss of {r.avg_loss}cp per move is strong — "
                   "engine-agreement territory. Your losses come from a few "
                   "large errors, not general drift.")
    elif r.avg_loss < 55:
        out.append(f"Average loss of {r.avg_loss}cp per move is solid club "
                   "level; the biggest gains are in cutting the worst "
                   "single moves, not playing 'better' overall.")
    else:
        out.append(f"Average loss of {r.avg_loss}cp per move suggests "
                   "systematic drift, not just occasional blunders — "
                   "slow down on non-forcing moves.")

    # Phase-specific gap: call out the worst phase if it's meaningfully worse.
    if len(r.by_phase) >= 2:
        worst_phase, wp_moves, wp_avg, wp_bl = r.by_phase[0]
        rest_avg = sum(a * m for _, m, a, _ in r.by_phase[1:]) / max(
            1, sum(m for _, m, _, _ in r.by_phase[1:]))
        if wp_moves >= 20 and wp_avg > 1.5 * rest_avg:
            out.append(
                f"Your {worst_phase} is the clear weak point: {wp_avg}cp "
                f"average loss vs {rest_avg:.1f}cp elsewhere, with "
                f"{wp_bl} blunders in {wp_moves} {worst_phase} moves. "
                f"Targeted {worst_phase} study is your highest-leverage fix.")

    # Blunder concentration.
    if r.blunder_rate >= 5:
        out.append(f"{r.blunder_rate} blunders per 100 moves is the "
                   "dominant leak — roughly one game-losing move every "
                   f"{max(1, round(100 / r.blunder_rate))} moves. A blunder-"
                   "check habit (candidate move → what does it hang?) "
                   "outweighs any opening work.")
    elif r.blunder_rate >= 2:
        out.append(f"{r.blunder_rate} blunders per 100 moves: real but "
                   "fixable. Most players this range lose to one-move "
                   "tactics; puzzle streaks target exactly this.")
    else:
        out.append(f"Only {r.blunder_rate} blunders per 100 moves — your "
                   "losses are more positional than tactical. The clustering "
                   "layer (v2) will matter more for you than raw blunder "
                   "counting.")

    # Color asymmetry.
    if len(r.by_color) == 2:
        (c1, m1, a1), (c2, m2, a2) = r.by_color
        if min(m1, m2) >= 30 and max(a1, a2) > 1.4 * min(a1, a2):
            weaker = c1 if a1 > a2 else c2
            out.append(f"You lose noticeably more evaluation as {weaker} "
                       f"({max(a1,a2)}cp vs {min(a1,a2)}cp per move) — "
                       "worth reviewing your repertoire on that side.")

    # Mate-scale losses (missed mates / allowed mates).
    huge = sum(1 for w in r.worst if w["cp_loss"] >= 5000)
    if huge:
        out.append(f"{huge} of your top losses are mate-scale (missed or "
                   "allowed a forced mate). These are check-every-check "
                   "moments, not knowledge gaps.")
    return out
