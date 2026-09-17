"""
Turn a PGN into something a browser can step through without a chess engine.

WHY THIS IS ON THE SERVER
=========================
The game viewer used to do this work in the browser, which cost every visitor
three CDN requests before a single piece appeared:

    jquery-3.7.1.min.js          ~87 KB   (chessboard.js depends on it)
    chess.js 0.10.3              ~40 KB   (parses the PGN, generates positions)
    chessboard-1.0.0.min.js      ~30 KB   (draws the board)
    12 piece PNGs from unpkg     ~30 KB

Roughly 190 KB and three third-party hosts, to replay a game whose moves were
fixed the moment it was played. The PGN never changes, so parsing it on every
page load in every visitor's phone is work done thousands of times that could
be done once.

python-chess does the same job here. The page ships a list of positions and
the browser only has to draw them, which is about seventy lines of vanilla
JavaScript and no dependencies at all.

THE BOARD ENCODING
==================
A position is 64 characters, index 0 = a8 through index 63 = h1, which is
reading order: the same order the CSS grid lays the squares out, so the
renderer needs no coordinate arithmetic. Piece letters follow FEN, uppercase
for White, and "." is an empty square.

That is 65 bytes per half-move. A 40 move game is about 5 KB before gzip,
against 190 KB of libraries to compute the same thing in the browser.
"""
import chess
import chess.pgn
import io


def board_string(board):
    """A chess.Board as 64 characters, a8 first, h1 last."""
    out = []
    for rank in range(7, -1, -1):          # rank 8 down to rank 1
        for file in range(8):              # file a through h
            piece = board.piece_at(chess.square(file, rank))
            out.append(piece.symbol() if piece else '.')
    return ''.join(out)


def _index(square):
    """chess.square index (a1=0) to our reading-order index (a8=0)."""
    return (7 - chess.square_rank(square)) * 8 + chess.square_file(square)


def replay(pgn_text):
    """Parse a PGN into positions, moves and per-move highlights.

    Returns None when the PGN cannot be read, which the template renders as
    an honest "the moves could not be read" rather than an empty board. It
    returns None rather than raising because a malformed PGN on one match is
    not a reason to 500 the page: the result, the players and the ratings on
    it are all still worth showing.
    """
    if not pgn_text or not pgn_text.strip():
        return None

    try:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
    except Exception:
        return None
    if game is None:
        return None

    # python-chess STOPS AT THE FIRST ILLEGAL MOVE and carries on as though
    # the game simply ended there, recording what happened in game.errors.
    # Without checking this, a record that is corrupt from move 17 renders as
    # a game that finished at move 17, and nothing on the page says otherwise.
    # Found while writing the viewer, by seeding it with a PGN that turned out
    # to be illegal halfway through: the board showed a clean, plausible,
    # wrong game. This project has shipped that shape of bug four times.
    truncated = bool(game.errors)

    board = game.board()
    positions = [board_string(board)]
    moves = []
    # Highlights are the from and to squares of the move that PRODUCED each
    # position, so the opening position has none.
    highlights = [None]

    for move in game.mainline_moves():
        try:
            san = board.san(move)
        except Exception:
            truncated = True
            break
        board.push(move)
        moves.append(san)
        positions.append(board_string(board))
        highlights.append([_index(move.from_square), _index(move.to_square)])

    if not moves:
        return None

    headers = game.headers
    return {
        'moves': moves,
        'positions': positions,
        'highlights': highlights,
        'truncated': truncated,
        'result': headers.get('Result', ''),
        'opening': headers.get('Opening', ''),
        'time_control': headers.get('TimeControl', ''),
    }
