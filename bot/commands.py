"""Single source of truth for every bot command.

Help output, the Telegram command menu, and the /admin fallback all read from
this module. Before it existed, each of those was a hand-written string, and
they drifted: bot/telegram_handlers.py carried two different "/admin <...>"
usage lines, one listing 9 subcommands and one listing 16, against a real
total of 19. Add a command here and every surface picks it up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

USER = "user"
ADMIN = "admin"


@dataclass(frozen=True)
class Command:
    name: str
    args: str
    summary: str
    scope: str = ADMIN
    group: str = "General"
    detail: str = ""

    def usage(self, parent: str = "") -> str:
        head = f"/{parent} {self.name}" if parent else f"/{self.name}"
        return f"{head} {self.args}".strip()


# --------------------------------------------------------------------------
# Top-level commands. Every one of these is registered in bot/bot_runner.py.
# --------------------------------------------------------------------------
COMMANDS: List[Command] = [
    Command(
        "start", "", "Register for picks and link your Telegram account",
        scope=USER, group="Getting started",
        detail="Creates your participant record and links this chat so the bot "
               "can send you games. Run once. Your name comes from your Telegram "
               "profile.",
    ),
    Command(
        "help", "[command]", "List commands, or show usage for one",
        scope=USER, group="Getting started",
        detail="/help lists what you can run. /help <command> shows usage and "
               "detail for one of them.",
    ),
    Command(
        "mypicks", "", "Show your picks for the current season",
        scope=USER, group="Your picks",
    ),
    Command(
        "myprops", "", "Show your prop bet picks",
        scope=USER, group="Your picks",
    ),
    Command(
        "seasonboard", "[all]", "Show the season scoreboard",
        scope=USER, group="Your picks",
        detail="Scoring is against the spread. Without arguments the board "
               "replies to you. Adding 'all' broadcasts it to every participant.",
    ),
    Command(
        "sendweek", "<week> [dry|me|<name>]", "Send a week's unpicked games",
        group="Games and picks",
        detail="Sends only games the person has not already picked, so it is "
               "safe to re-run. 'dry' counts without sending, 'me' sends to you "
               "alone, and a name sends to that participant. Name matching is "
               "exact but case-insensitive.",
    ),
    Command(
        "seepicks", "<week> <participant|all> [day] [locked] [preview]",
        "Share picks with participants",
        group="Games and picks",
        detail="Sends a grid of picks by DM to everyone. Add a day name to limit "
               "it to that day. Add 'locked' to include only games already "
               "kicked off, which keeps upcoming games hidden. Add 'preview' to "
               "reply to you and send to nobody. Modifiers combine in any order.",
    ),
    Command(
        "whoisleft", "<week>", "List participants with picks outstanding",
        group="Games and picks",
    ),
    Command(
        "remindweek", "<week> [name]", "Remind participants to make picks",
        group="Games and picks",
    ),
    Command(
        "deletepicks", '"<name>" <week>', "Delete a participant's picks for a week",
        group="Games and picks",
    ),
    Command(
        "syncscores", "<week> [season]", "Pull scores for a week from ESPN",
        group="Scores",
        detail="Writes final scores and the against-the-spread winner into the "
               "games table. Scoreboards read that stored winner.",
    ),
    Command(
        "getscores", "<week> [all]", "Show scores for a week",
        group="Scores",
        detail="Adding 'all' broadcasts the scores to every participant.",
    ),
    Command(
        "admin", "<subcommand>", "Run an admin subcommand",
        group="Admin",
        detail="Run /admin help for the full list.",
    ),
]


# --------------------------------------------------------------------------
# /admin subcommands.
# --------------------------------------------------------------------------
ADMIN_COMMANDS: List[Command] = [
    Command("help", "[group]", "List admin subcommands", group="Getting started"),
    Command("participants", "", "List participants with IDs and chat IDs",
            group="Participants"),
    Command("remove", "<id|name>", "Remove a participant and their picks",
            group="Participants"),
    Command("deletepicks", "<id|name> <week> [season] [dry]",
            "Delete a participant's picks", group="Participants"),
    Command("broadcast", "[dry|me] <message>", "Send a message to all participants",
            group="Participants",
            detail="Newlines are preserved. 'dry' shows the message and recipient "
                   "list without sending. 'me' sends to you alone. A message whose "
                   "first word is literally 'dry' or 'me' is read as that mode."),
    Command("gameids", "<week> [season]", "List game IDs for a week", group="Games"),
    Command("setspread", "<game_id> <team> <points|clear>",
            "Set or clear a game's spread", group="Games",
            detail="Writes directly and bypasses the rule that freezes a line "
                   "once a game has picks, so use it to correct a bad line."),
    Command("import upcoming", "", "Import the upcoming week from ESPN", group="Games"),
    Command("sendweek upcoming", "", "Send the upcoming week's games", group="Games"),
    Command("winners", "", "Announce weekly winners", group="Scoring"),
    Command("winnersats", "<week> [season] [debug]",
            "Calculate against-the-spread winners", group="Scoring",
            detail="Also accepts /admin winners-ats."),
    Command("sendprops", "<week> [season]", "Send prop bets to participants",
            group="Props"),
    Command("listprops", "<week> [season]", "List props with IDs and status",
            group="Props"),
    Command("gradeprop", "<prop_id> <result>", "Grade a single prop", group="Props"),
    Command("gradeallprops", "<week> <result1,result2,...>",
            "Grade every prop for a week", group="Props"),
    Command("propscores", "<week> [season]", "Show prop scores", group="Props"),
    Command("sendpropscores", "<week> [season]", "Broadcast prop scores", group="Props"),
    Command("shareprops", "<week> [season]", "Share everyone's prop picks", group="Props"),
    Command("whoisleftprops", "<week> [season]", "List who has props outstanding",
            group="Props"),
    Command("clearprops", "<week> [season]", "Delete all props for a week", group="Props"),
]


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------
def _by_group(cmds: Iterable[Command]) -> List[Tuple[str, List[Command]]]:
    order: List[str] = []
    buckets: dict = {}
    for c in cmds:
        if c.group not in buckets:
            buckets[c.group] = []
            order.append(c.group)
        buckets[c.group].append(c)
    return [(g, buckets[g]) for g in order]


def visible_commands(is_admin: bool) -> List[Command]:
    return [c for c in COMMANDS if is_admin or c.scope == USER]


def find_command(name: str) -> Optional[Command]:
    key = name.strip().lstrip("/").lower()
    for c in COMMANDS:
        if c.name == key:
            return c
    return None


def find_admin_command(name: str) -> Optional[Command]:
    key = name.strip().lower()
    for c in ADMIN_COMMANDS:
        if c.name == key or c.name.split()[0] == key:
            return c
    return None


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def render_help(is_admin: bool) -> str:
    cmds = visible_commands(is_admin)
    lines = ["📖 NFL-Picks commands", ""]
    for group, items in _by_group(cmds):
        lines.append(f"— {group} —")
        for c in items:
            lines.append(f"{c.usage()}")
            lines.append(f"    {c.summary}")
        lines.append("")
    # Draw the example from what this caller can actually run; suggesting an
    # admin command to a participant sends them to a "no such command" reply.
    example = next((c.name for c in cmds if c.name != "help"), "start")
    lines.append(f"Run /help <command> for detail, e.g. /help {example}")
    if is_admin:
        lines.append("Run /admin help for admin subcommands.")
    return "\n".join(lines).strip()


def render_command_help(name: str, is_admin: bool) -> Optional[str]:
    c = find_command(name)
    if c is None or (c.scope == ADMIN and not is_admin):
        return None
    lines = [c.usage(), "", c.summary]
    if c.detail:
        lines += ["", c.detail]
    return "\n".join(lines)


def render_admin_help(group: Optional[str] = None) -> str:
    items = ADMIN_COMMANDS
    if group:
        want = group.strip().lower()
        items = [c for c in ADMIN_COMMANDS if c.group.lower() == want]
        if not items:
            # Fall back to treating the argument as a subcommand name, so both
            # "/admin help props" and "/admin help gradeprop" do something useful.
            one = find_admin_command(want)
            if one is not None:
                lines = [one.usage("admin"), "", one.summary]
                if one.detail:
                    lines += ["", one.detail]
                return "\n".join(lines)
            groups = ", ".join(g for g, _ in _by_group(ADMIN_COMMANDS))
            return (
                f"No admin group or subcommand named '{group}'.\n"
                f"Groups: {groups}"
            )
    lines = ["🔧 Admin subcommands", ""]
    for g, cmds in _by_group(items):
        lines.append(f"— {g} —")
        for c in cmds:
            lines.append(c.usage("admin"))
            lines.append(f"    {c.summary}")
        lines.append("")
    if not group:
        groups = ", ".join(g for g, _ in _by_group(ADMIN_COMMANDS))
        lines.append(f"Filter with /admin help <group>. Groups: {groups}")
    return "\n".join(lines).strip()


def admin_usage_line() -> str:
    groups = ", ".join(g for g, _ in _by_group(ADMIN_COMMANDS))
    return (
        "Usage: /admin <subcommand>\n"
        f"Run /admin help for all {len(ADMIN_COMMANDS)} subcommands, "
        "or /admin help <group>.\n"
        f"Groups: {groups}"
    )


# --------------------------------------------------------------------------
# Telegram command menu
# --------------------------------------------------------------------------
def _menu(cmds: Iterable[Command]) -> List[Tuple[str, str]]:
    # Telegram caps descriptions at 256 characters and requires them non-empty.
    return [(c.name, (c.summary or c.name)[:256]) for c in cmds]


def telegram_user_commands() -> List[Tuple[str, str]]:
    return _menu(c for c in COMMANDS if c.scope == USER)


def telegram_admin_commands() -> List[Tuple[str, str]]:
    return _menu(COMMANDS)


# --------------------------------------------------------------------------
# Drift check
# --------------------------------------------------------------------------
def check_drift(registered: Iterable[str]) -> List[str]:
    """Compare handlers registered with PTB against this registry."""
    registered = set(registered)
    known = {c.name for c in COMMANDS}
    problems = []
    for name in sorted(registered - known):
        problems.append(f"/{name} is registered but missing from bot/commands.py")
    for name in sorted(known - registered):
        problems.append(f"/{name} is in bot/commands.py but no handler is registered")
    return problems
