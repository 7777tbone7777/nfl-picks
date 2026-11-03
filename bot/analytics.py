# bot/analytics.py
"""Season analytics: each participant's tendencies plus pool-wide awards.

Grading matches /seasonboard: a pick wins when it equals the ATS winner stored
in games.winner, and a final game with no winner is a push.

load_season() does the database reads. compute() and render() are pure, so
they can be tested with plain dicts.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

MIN_SHARED_FOR_TWIN = 10  # games two people both picked before calling them twins
MIN_PICKS_FOR_AWARD = 10  # keeps someone with three picks off the rate awards


@dataclass
class GameRow:
    game_id: int
    week: int
    game_time: Optional[datetime]
    home: str
    away: str
    favorite: Optional[str]
    winner: Optional[str]  # None on a final game means push


@dataclass
class PickRow:
    pid: int
    game_id: int
    team: str


@dataclass
class Player:
    pid: int
    name: str
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    fav_w: int = 0
    fav_l: int = 0
    dog_w: int = 0
    dog_l: int = 0
    home_w: int = 0
    home_l: int = 0
    away_w: int = 0
    away_l: int = 0
    backed: Counter = field(default_factory=Counter)
    backed_w: Counter = field(default_factory=Counter)
    backed_l: Counter = field(default_factory=Counter)
    faded: Counter = field(default_factory=Counter)
    week_wins: Dict[int, int] = field(default_factory=dict)
    best_win_streak: int = 0
    worst_loss_streak: int = 0
    contrarian: int = 0
    with_majority_games: int = 0
    lone_wolf_wins: int = 0
    weeks_won: int = 0
    twin: Optional[Tuple[str, float, int]] = None  # (name, agreement, shared)

    @property
    def graded(self) -> int:
        return self.wins + self.losses

    @property
    def picks(self) -> int:
        return self.wins + self.losses + self.pushes


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _pct(w: int, total: int) -> str:
    return f"{100 * w / total:.0f}%" if total else "-"


def _rec(w: int, losses: int) -> str:
    return f"{w}-{losses}"


def compute(
    games: Iterable[GameRow],
    picks: Iterable[PickRow],
    names: Dict[int, str],
    settled_weeks: Iterable[int] = (),
    in_pot: Optional[Dict[int, List[int]]] = None,
) -> dict:
    """Return per-player stats and pool-wide numbers.

    settled_weeks: weeks where every game is final, used for weeks won.
    in_pot: week -> participant IDs who owed that week. Defaults to everyone.
    """
    games_by_id = {g.game_id: g for g in games}
    picks = [p for p in picks if p.game_id in games_by_id]
    players: Dict[int, Player] = {}

    def player(pid: int) -> Player:
        if pid not in players:
            players[pid] = Player(pid, names.get(pid, f"P{pid}"))
        return players[pid]

    by_game: Dict[int, List[PickRow]] = defaultdict(list)
    for p in picks:
        by_game[p.game_id].append(p)

    team_backed = Counter()
    team_cashed = Counter()
    team_burned = Counter()
    consensus_w = consensus_l = 0

    for gid, gpicks in by_game.items():
        g = games_by_id[gid]
        sides = Counter(_norm(p.team) for p in gpicks)
        majority = None
        if len(gpicks) >= 3:
            top, top_n = sides.most_common(1)[0]
            if top_n * 2 > len(gpicks):
                majority = top
        if majority is not None and g.winner:
            if majority == _norm(g.winner):
                consensus_w += 1
            else:
                consensus_l += 1

        for p in gpicks:
            pl = player(p.pid)
            t = _norm(p.team)
            is_home = t == _norm(g.home)
            opponent = g.away if is_home else g.home
            display = g.home if is_home else g.away
            pl.backed[display] += 1
            pl.faded[opponent] += 1
            team_backed[display] += 1

            if majority is not None:
                if t == majority:
                    pl.with_majority_games += 1
                else:
                    pl.contrarian += 1

            if not g.winner:
                pl.pushes += 1
                continue

            won = t == _norm(g.winner)
            if won:
                pl.wins += 1
                pl.backed_w[display] += 1
                team_cashed[display] += 1
                pl.week_wins[g.week] = pl.week_wins.get(g.week, 0) + 1
                if sides[t] == 1 and len(gpicks) >= 3:
                    pl.lone_wolf_wins += 1
            else:
                pl.losses += 1
                pl.backed_l[display] += 1
                team_burned[display] += 1

            if is_home:
                pl.home_w += won
                pl.home_l += not won
            else:
                pl.away_w += won
                pl.away_l += not won

            if g.favorite:
                if t == _norm(g.favorite):
                    pl.fav_w += won
                    pl.fav_l += not won
                else:
                    pl.dog_w += won
                    pl.dog_l += not won

    # Streaks, in kickoff order. Pushes neither extend nor break a streak.
    def _order(p: PickRow):
        g = games_by_id[p.game_id]
        return (g.game_time or datetime.min, g.game_id)

    picks_by_pid: Dict[int, List[PickRow]] = defaultdict(list)
    for p in picks:
        picks_by_pid[p.pid].append(p)
    for pid, plist in picks_by_pid.items():
        pl = player(pid)
        w_run = l_run = 0
        for p in sorted(plist, key=_order):
            winner = games_by_id[p.game_id].winner
            if not winner:
                continue
            if _norm(p.team) == _norm(winner):
                w_run, l_run = w_run + 1, 0
            else:
                w_run, l_run = 0, l_run + 1
            pl.best_win_streak = max(pl.best_win_streak, w_run)
            pl.worst_loss_streak = max(pl.worst_loss_streak, l_run)

    # Weeks won, using the /seasonboard rule: top score among those in the pot.
    for wk in settled_weeks:
        pot = (in_pot or {}).get(wk) if in_pot is not None else list(players)
        pot = [pid for pid in (pot or []) if pid in players]
        if not pot:
            continue
        best = max(players[pid].week_wins.get(wk, 0) for pid in pot)
        if best == 0:
            continue
        for pid in pot:
            if players[pid].week_wins.get(wk, 0) == best:
                players[pid].weeks_won += 1

    # Twins: highest agreement on games both people picked.
    pick_map = {(p.pid, p.game_id): _norm(p.team) for p in picks}
    games_by_pid = {pid: {p.game_id for p in pl} for pid, pl in picks_by_pid.items()}
    for a in players:
        best = None
        for b in players:
            if a == b:
                continue
            shared = games_by_pid.get(a, set()) & games_by_pid.get(b, set())
            if len(shared) < MIN_SHARED_FOR_TWIN:
                continue
            agree = sum(pick_map[(a, g)] == pick_map[(b, g)] for g in shared) / len(shared)
            if best is None or agree > best[1]:
                best = (players[b].name, agree, len(shared))
        players[a].twin = best

    # League-wide: how favorites did against the spread.
    fav_cover = dog_cover = pushes = 0
    for g in games_by_id.values():
        if not g.favorite:
            continue
        if not g.winner:
            pushes += 1
        elif _norm(g.winner) == _norm(g.favorite):
            fav_cover += 1
        else:
            dog_cover += 1

    return {
        "players": sorted(players.values(), key=lambda p: (-p.wins, p.name.lower())),
        "team_backed": team_backed,
        "team_cashed": team_cashed,
        "team_burned": team_burned,
        "consensus": (consensus_w, consensus_l),
        "fav_cover": (fav_cover, dog_cover, pushes),
        "weeks": sorted({g.week for g in games_by_id.values()}),
    }


def _award(players: List[Player], key, fmt, eligible=lambda p: True) -> Optional[str]:
    pool = [p for p in players if eligible(p)]
    if not pool:
        return None
    best_val = max(key(p) for p in pool)
    if best_val <= 0:
        return None
    winners = [p.name for p in pool if key(p) == best_val]
    return f"{', '.join(winners)} ({fmt(best_val)})"


def render(stats: dict, season_year: int) -> str:
    players: List[Player] = [p for p in stats["players"] if p.picks]
    weeks = stats["weeks"]
    if not players:
        return f"No graded picks yet for {season_year}."

    span = f"Weeks {weeks[0]}-{weeks[-1]}" if len(weeks) > 1 else f"Week {weeks[0]}"
    out = [f"📈 {season_year} Season Analytics", f"{span}, completed games only", ""]

    enough = lambda p: p.graded >= MIN_PICKS_FOR_AWARD  # noqa: E731
    fav_rate = lambda p: (p.fav_w + p.fav_l) / max(1, p.fav_w + p.fav_l + p.dog_w + p.dog_l)  # noqa: E731
    home_rate = lambda p: (p.home_w + p.home_l) / max(1, p.graded)  # noqa: E731
    contra_rate = lambda p: p.contrarian / max(1, p.contrarian + p.with_majority_games)  # noqa: E731

    awards = [
        ("🎯 Sharpest", _award(
            players, lambda p: p.wins / max(1, p.graded), lambda v: f"{v:.0%} ATS", enough)),
        ("💰 Most weeks won", _award(players, lambda p: p.weeks_won, lambda v: f"{v}")),
        ("🔥 Longest win streak", _award(
            players, lambda p: p.best_win_streak, lambda v: f"{v} straight")),
        ("🧊 Longest cold streak", _award(
            players, lambda p: p.worst_loss_streak, lambda v: f"{v} straight")),
        ("📅 Best single week", _award(
            players, lambda p: max(p.week_wins.values(), default=0), lambda v: f"{v} wins")),
        ("🍔 Chalk eater", _award(players, fav_rate, lambda v: f"{v:.0%} favorites", enough)),
        ("🐶 Dog lover", _award(players, lambda p: 1 - fav_rate(p),
                               lambda v: f"{v:.0%} underdogs", enough)),
        ("🏠 Homer", _award(players, home_rate, lambda v: f"{v:.0%} home teams", enough)),
        ("🙃 Contrarian", _award(players, contra_rate,
                                lambda v: f"against the room {v:.0%}", enough)),
        ("🐺 Lone wolf", _award(players, lambda p: p.lone_wolf_wins,
                               lambda v: f"{v} solo wins")),
    ]
    out.append("🏅 AWARDS")
    out += [f"{label}: {who}" for label, who in awards if who]

    out += ["", "🏈 THE POOL"]
    cw, cl = stats["consensus"]
    if cw + cl:
        out.append(f"When most of us agreed: {_rec(cw, cl)} ({_pct(cw, cw + cl)})")
    fw, dw, fp = stats["fav_cover"]
    if fw + dw:
        out.append(f"Favorites covered {fw} of {fw + dw + fp} ({_pct(fw, fw + dw)})")
    if stats["team_backed"]:
        t, n = stats["team_backed"].most_common(1)[0]
        out.append(f"Most backed team: {t} ({n} picks)")
    if stats["team_cashed"]:
        t, n = stats["team_cashed"].most_common(1)[0]
        out.append(f"Paid us the most: {t} ({n} winning picks)")
    if stats["team_burned"]:
        t, n = stats["team_burned"].most_common(1)[0]
        out.append(f"Burned us the most: {t} ({n} losing picks)")

    out += ["", "👤 PLAYER CARDS"]
    for p in players:
        fav_n, dog_n = p.fav_w + p.fav_l, p.dog_w + p.dog_l
        line = [
            "",
            f"{p.name}: {_rec(p.wins, p.losses)}"
            + (f"-{p.pushes}" if p.pushes else "")
            + f" ({_pct(p.wins, p.graded)})"
            + (f", {p.weeks_won} week{'s' if p.weeks_won != 1 else ''} won" if p.weeks_won else ""),
        ]
        if fav_n + dog_n:
            line.append(
                f"  Favorites {_pct(fav_n, fav_n + dog_n)} of picks, {_rec(p.fav_w, p.fav_l)}"
                f" | Dogs {_rec(p.dog_w, p.dog_l)}"
            )
        line.append(
            f"  Home {_rec(p.home_w, p.home_l)} | Away {_rec(p.away_w, p.away_l)}"
        )
        if p.backed:
            t, n = p.backed.most_common(1)[0]
            line.append(f"  Ride or die: {t} ({n}x, {_rec(p.backed_w[t], p.backed_l[t])})")
        if p.faded:
            t, n = p.faded.most_common(1)[0]
            line.append(f"  Always fading: {t} ({n}x)")
        if p.backed_l:
            t, n = p.backed_l.most_common(1)[0]
            line.append(f"  Kryptonite: {t} ({n} losses)")
        line.append(
            f"  Streaks: {p.best_win_streak}W best, {p.worst_loss_streak}L worst"
            + (f" | Best week: {max(p.week_wins.values())}" if p.week_wins else "")
        )
        if p.contrarian + p.with_majority_games:
            line.append(f"  Against the room: {_pct(p.contrarian, p.contrarian + p.with_majority_games)}")
        if p.twin:
            name, agree, shared = p.twin
            line.append(f"  Pick twin: {name} ({agree:.0%} of {shared} games)")
        out += line

    return "\n".join(out)


def load_season(season_year: int) -> dict:
    """Read one season from the database and return compute()'s result.

    Call inside an app context.
    """
    from sqlalchemy import text as T

    from models import db

    game_rows = db.session.execute(
        T("""
            SELECT g.id, w.week_number, g.game_time, g.home_team, g.away_team,
                   g.favorite_team, g.winner
              FROM games g
              JOIN weeks w ON w.id = g.week_id
             WHERE w.season_year = :y
               AND LOWER(COALESCE(g.status,'')) = 'final'
        """),
        {"y": season_year},
    ).all()
    games = [GameRow(r[0], int(r[1]), r[2], r[3], r[4], r[5], r[6]) for r in game_rows]

    pick_rows = db.session.execute(
        T("""
            SELECT p.participant_id, p.game_id, p.selected_team
              FROM picks p
              JOIN games g ON g.id = p.game_id
              JOIN weeks w ON w.id = g.week_id
             WHERE w.season_year = :y
               AND LOWER(COALESCE(g.status,'')) = 'final'
               AND p.selected_team IS NOT NULL
        """),
        {"y": season_year},
    ).all()
    picks = [PickRow(int(r[0]), int(r[1]), r[2]) for r in pick_rows]

    people = db.session.execute(T("SELECT id, name, created_at FROM participants")).all()
    names = {int(r[0]): r[1] for r in people}
    joined = {int(r[0]): r[2] for r in people}

    # Same settlement and buy-in rules as /seasonboard.
    week_info = db.session.execute(
        T("""
            SELECT w.week_number,
                   MIN(g.game_time),
                   COUNT(*) FILTER (WHERE LOWER(COALESCE(g.status,'')) <> 'final')
              FROM weeks w
              JOIN games g ON g.week_id = w.id
             WHERE w.season_year = :y
          GROUP BY w.week_number
        """),
        {"y": season_year},
    ).all()
    settled = [int(r[0]) for r in week_info if int(r[2]) == 0]
    in_pot = {
        int(r[0]): [
            pid for pid in names
            if r[1] is None or joined.get(pid) is None or joined[pid] <= r[1]
        ]
        for r in week_info
    }

    return compute(games, picks, names, settled, in_pot)
