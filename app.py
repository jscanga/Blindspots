"""
app.py — web interface for the blind-spot detector.

    python app.py                 # http://127.0.0.1:5000
    STOCKFISH_PATH=/usr/games/stockfish python app.py

Flow: form (platform, username, games, depth) → POST /analyze starts a
background job → the page polls /status/<job> → redirects to /report/<job>.

Analysis runs in a thread so long jobs don't hold an HTTP request open;
progress (games/moves/engine calls) streams to the UI. The same SQLite DB
as the CLI is used, so cached evaluations are shared between both.

`?demo=1` on the form (or the Demo button) analyzes bundled fixture games
instead of calling the chess APIs — useful offline and for a quick look.
"""
from __future__ import annotations

import os
import threading
import uuid

from flask import Flask, jsonify, redirect, render_template, request, url_for

from src.db import Database
from src.evaluate import Evaluator
from src.ingest import fetch_games
from src.parse import process_game
from src.report import build_report
from src.demo_games import DEMO_PGNS

app = Flask(__name__)

DB_PATH = os.environ.get("BLINDSPOTS_DB", "data/blindspots.db")
ENGINE = os.environ.get("STOCKFISH_PATH", "stockfish")

# Qualitative depth presets. Depth is THE speed/precision dial:
#   10 ≈ instant triage, 14 ≈ balanced club-level judgment,
#   18 ≈ near-max useful precision for blunder detection (much slower).
DEPTH_PRESETS = {"low": 10, "balanced": 14, "high": 18}

JOBS: dict[str, dict] = {}          # job_id -> status dict
JOBS_LOCK = threading.Lock()


def _run_job(job_id: str, platform: str, username: str,
             max_games: int, depth: int, demo: bool) -> None:
    job = JOBS[job_id]
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = Database(DB_PATH)            # thread-local connection
    try:
        ev = Evaluator(db, engine_path=ENGINE, depth=depth)
    except (FileNotFoundError, PermissionError, OSError) as e:
        job.update(state="error",
                   error=f"Could not start Stockfish at '{ENGINE}' "
                         f"({type(e).__name__}: {e}). STOCKFISH_PATH must "
                         f"point to the stockfish executable FILE itself "
                         f"(on Windows, the full path to the .exe — not the "
                         f"folder containing it).")
        db.close()
        return
    try:
        job["state"] = "analyzing"
        if demo:
            source = iter(DEMO_PGNS)
            username, platform = "demo_player", "demo"
            job["username"], job["platform"] = username, platform
        else:
            source = fetch_games(platform, username, max_games)
        for i, pgn in enumerate(source):
            if i >= max_games:
                break
            added = process_game(pgn, username, platform, db, ev,
                                 fallback_id=f"{platform}:{username}:{i}")
            job["games"] += 1 if added else 0
            job["moves"] += added
            job["engine_calls"] = ev.engine_calls
            job["cache_hits"] = ev.cache_hits
        job["state"] = "done"
    except Exception as e:  # noqa: BLE001 — surface anything to the UI
        job.update(state="error", error=f"{type(e).__name__}: {e}")
    finally:
        ev.close()
        db.close()


@app.get("/")
def index():
    return render_template("index.html", presets=DEPTH_PRESETS)


@app.post("/analyze")
def analyze():
    demo = request.form.get("demo") == "1"
    platform = request.form.get("platform", "lichess")
    username = (request.form.get("username") or "").strip()
    if not demo and not username:
        return render_template("index.html", presets=DEPTH_PRESETS,
                               error="Enter your username (or run the demo).")

    # depth: qualitative preset, or the custom number if provided
    preset = request.form.get("depth_preset", "balanced")
    custom = (request.form.get("depth_custom") or "").strip()
    if custom:
        try:
            depth = max(6, min(24, int(custom)))
        except ValueError:
            return render_template("index.html", presets=DEPTH_PRESETS,
                                   error="Custom depth must be a number (6-24).")
    else:
        depth = DEPTH_PRESETS.get(preset, 14)

    try:
        max_games = max(1, min(1000, int(request.form.get("max_games", "50"))))
    except ValueError:
        max_games = 50

    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"state": "starting", "games": 0, "moves": 0,
                        "engine_calls": 0, "cache_hits": 0,
                        "username": username, "platform": platform,
                        "depth": depth, "max_games": max_games, "error": ""}
    threading.Thread(target=_run_job,
                     args=(job_id, platform, username, max_games, depth, demo),
                     daemon=True).start()
    return redirect(url_for("progress", job_id=job_id))


@app.get("/progress/<job_id>")
def progress(job_id: str):
    if job_id not in JOBS:
        return redirect(url_for("index"))
    return render_template("progress.html", job_id=job_id, job=JOBS[job_id])


@app.get("/status/<job_id>")
def status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"state": "unknown"}), 404
    return jsonify(job)


@app.get("/report/<job_id>")
def report(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return redirect(url_for("index"))
    db = Database(DB_PATH)
    rep = build_report(db, job["username"], job["platform"])
    db.close()
    return render_template("report.html", r=rep, job=job)


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", "5000")))
