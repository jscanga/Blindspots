"""
ingest.py — pull a user's games as PGN from Lichess or Chess.com.

Both APIs are free and keyless:

  Lichess:   GET https://lichess.org/api/games/user/{username}
             Streams all games as one PGN document. `max` caps the count.
             Rate limit: if you receive HTTP 429, stop and wait a full
             minute (per Lichess API docs) before retrying.

  Chess.com: GET https://api.chess.com/pub/player/{username}/games/archives
             Returns a list of monthly archive URLs; each returns JSON with
             a `games` array whose entries contain a `pgn` field.
             Requires a User-Agent header or it may 403.

Both functions yield PGN strings, one complete game at a time, newest first
where the API allows, so `--max-games` grabs the most recent games.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Iterator

log = logging.getLogger(__name__)

USER_AGENT = "chess-blindspots/0.1 (personal analysis tool)"


def _get(url: str, accept: str = "*/*", retries: int = 3) -> bytes:
    """GET with retry + Lichess-style 429 handling."""
    for attempt in range(retries):
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": accept}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Lichess docs: on 429, wait a full minute before resuming.
                wait = 60
                log.warning("rate limited (429) on %s — sleeping %ss", url, wait)
                time.sleep(wait)
                continue
            if e.code in (500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"exhausted retries for {url}")


def _split_pgn_stream(text: str) -> Iterator[str]:
    """Split a multi-game PGN document into individual game strings.

    A new game starts at an [Event ...] tag that follows a blank line (or
    the start of the document). Simple and robust for API-produced PGN.
    """
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("[Event ") and current and current[-1] == "":
            game = "\n".join(current).strip()
            if game:
                yield game
            current = []
        current.append(line)
    tail = "\n".join(current).strip()
    if tail:
        yield tail


def lichess_games(username: str, max_games: int | None = None) -> Iterator[str]:
    """Yield PGN strings for a Lichess user's games (most recent first)."""
    url = f"https://lichess.org/api/games/user/{username}?moves=true&tags=true"
    if max_games:
        url += f"&max={max_games}"
    log.info("fetching Lichess games for %s ...", username)
    text = _get(url, accept="application/x-chess-pgn").decode("utf-8", "replace")
    count = 0
    for pgn in _split_pgn_stream(text):
        yield pgn
        count += 1
        if max_games and count >= max_games:
            return
    log.info("fetched %d games from Lichess", count)


def chesscom_games(username: str, max_games: int | None = None) -> Iterator[str]:
    """Yield PGN strings for a Chess.com user's games (most recent first)."""
    archives_url = f"https://api.chess.com/pub/player/{username}/games/archives"
    log.info("fetching Chess.com archive list for %s ...", username)
    archives = json.loads(_get(archives_url, accept="application/json"))["archives"]
    count = 0
    # newest month first so --max-games grabs recent games
    for month_url in reversed(archives):
        try:
            data = json.loads(_get(month_url, accept="application/json"))
        except Exception as e:  # noqa: BLE001 - skip a bad month, keep going
            log.warning("skipping archive %s: %s", month_url, e)
            continue
        # newest game in a month is last in the array
        for g in reversed(data.get("games", [])):
            pgn = g.get("pgn")
            if not pgn:
                continue
            yield pgn
            count += 1
            if max_games and count >= max_games:
                return
        time.sleep(0.5)  # be polite between monthly archive requests
    log.info("fetched %d games from Chess.com", count)


def fetch_games(platform: str, username: str,
                max_games: int | None = None) -> Iterator[str]:
    if platform == "lichess":
        return lichess_games(username, max_games)
    if platform == "chesscom":
        return chesscom_games(username, max_games)
    raise ValueError(f"unknown platform: {platform!r} (use 'lichess' or 'chesscom')")
