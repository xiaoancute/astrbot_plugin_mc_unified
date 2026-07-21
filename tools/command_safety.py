DANGEROUS_COMMANDS = {"stop", "reload"}


def _command_name(token: str) -> str:
    """Normalize a Minecraft command token for policy checks."""
    token = token.lstrip("/").lower()
    return token.rsplit(":", 1)[-1]


def find_dangerous_command(command: str) -> str | None:
    """Find a blocked command, including namespaced and nested execute forms."""
    tokens = str(command or "").strip().split()
    if not tokens:
        return None

    candidate_indexes = {0}
    candidate_indexes.update(
        index + 1
        for index, token in enumerate(tokens[:-1])
        if _command_name(token) == "run"
    )
    for index in candidate_indexes:
        command_name = _command_name(tokens[index])
        if command_name in DANGEROUS_COMMANDS:
            return command_name
    return None
