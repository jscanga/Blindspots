"""
demo_games.py — bundled fixture games for the Demo button / offline use.

Short real-looking games with deliberate errors in different phases so the
demo report has something to say. The demo user plays both colors.
"""

DEMO_PGNS = [
    # Scholar's-mate-adjacent disaster: demo_player (black) misses the threat.
    """[Event "Demo Blitz"]
[Site "demo/game1"]
[White "opponent"]
[Black "demo_player"]
[Result "1-0"]

1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0
""",
    # demo_player (white) wins material then hangs it back in the middlegame.
    """[Event "Demo Blitz"]
[Site "demo/game2"]
[White "demo_player"]
[Black "opponent"]
[Result "0-1"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Bxc6 dxc6 5. Nxe5 Qd4 6. Nf3 Qxe4+
7. Qe2 Qxe2+ 8. Kxe2 Bg4 9. d3 O-O-O 10. Be3 Nf6 11. h3 Bh5
12. g4 Nxg4 13. hxg4 Bxg4 0-1
""",
    # A quieter game: demo_player (white) drifts in the middlegame.
    """[Event "Demo Rapid"]
[Site "demo/game3"]
[White "demo_player"]
[Black "opponent"]
[Result "1/2-1/2"]

1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. cxd5 exd5 5. Bg5 Be7 6. e3 O-O
7. Bd3 Nbd7 8. Nf3 Re8 9. O-O c6 10. Qc2 Nf8 11. Rab1 Ng6
12. b4 a6 13. a4 Bd6 14. b5 axb5 15. axb5 Bg4 16. bxc6 bxc6
17. Ne2 Qc7 18. Ng3 Bxf3 19. gxf3 Qd7 1/2-1/2
""",
    # demo_player (black) plays a clean game (contrast for the stats).
    """[Event "Demo Rapid"]
[Site "demo/game4"]
[White "opponent"]
[Black "demo_player"]
[Result "0-1"]

1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 6. Be2 e5
7. Nb3 Be7 8. O-O O-O 9. Be3 Be6 10. Qd2 Nbd7 11. f3 b5
12. a4 b4 13. Nd5 Bxd5 14. exd5 Nb6 15. Bxb6 Qxb6+ 16. Kh1 a5 0-1
""",
]
