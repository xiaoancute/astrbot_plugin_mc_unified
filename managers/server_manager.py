from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class ServerProfile:
    """Configuration and runtime resources for one Minecraft server."""

    server_id: str
    display_name: str
    rcon_enabled: bool = False
    rcon_host: str = "localhost"
    rcon_port: int = 25575
    rcon_password: str = ""
    websocket_enabled: bool = False
    websocket_url: str = "ws://127.0.0.1:8080/minecraft/ws"
    websocket_token: str = ""
    sync_chat_mc_to_qq: bool = False
    sync_chat_qq_to_mc: bool = False
    forward_llm_responses_to_mc: bool = False
    forward_player_events: bool = True
    mc_message_prefix: str = "[MC:{server}]"
    qq_message_prefix: str = "[QQ]"
    message_transport: str = "auto"
    enable_dangerous_commands: bool = False
    rcon_backend: Any = None
    websocket_backend: Any = None
    mc_tools: Any = None

    @property
    def label(self) -> str:
        return self.display_name or self.server_id

    def format_prefix(self, value: str) -> str:
        return value.replace("{server}", self.label).replace(
            "{server_id}", self.server_id
        )


def build_server_profiles(config: Any) -> list[ServerProfile]:
    """Build normalized profiles, falling back to the legacy single-server keys."""

    raw_profiles = config.get("mc_servers", []) or []
    if raw_profiles:
        profiles = []
        for raw in raw_profiles:
            if not isinstance(raw, dict) or not raw.get("enabled", True):
                continue

            server_id = str(raw.get("server_id", "")).strip()
            if not server_id:
                continue

            rcon = raw.get("rcon", {}) or {}
            websocket = raw.get("websocket", {}) or {}
            message = raw.get("message", {}) or {}
            profiles.append(
                ServerProfile(
                    server_id=server_id,
                    display_name=str(raw.get("display_name", "") or server_id).strip(),
                    rcon_enabled=bool(rcon.get("enabled", False)),
                    rcon_host=str(rcon.get("host", "localhost")).strip(),
                    rcon_port=int(rcon.get("port", 25575)),
                    rcon_password=str(rcon.get("password", "")),
                    websocket_enabled=bool(websocket.get("enabled", False)),
                    websocket_url=str(
                        websocket.get("url", "ws://127.0.0.1:8080/minecraft/ws")
                    ).strip(),
                    websocket_token=str(websocket.get("token", "")),
                    sync_chat_mc_to_qq=bool(message.get("sync_chat_mc_to_qq", False)),
                    sync_chat_qq_to_mc=bool(message.get("sync_chat_qq_to_mc", False)),
                    forward_llm_responses_to_mc=bool(
                        message.get("forward_llm_responses_to_mc", False)
                    ),
                    forward_player_events=bool(
                        message.get("forward_player_events", True)
                    ),
                    mc_message_prefix=str(
                        message.get("mc_message_prefix", "[MC:{server}]")
                    ),
                    qq_message_prefix=str(message.get("qq_message_prefix", "[QQ]")),
                    message_transport=str(
                        message.get("transport", "auto") or "auto"
                    ).lower(),
                    enable_dangerous_commands=bool(
                        raw.get(
                            "enable_dangerous_commands",
                            config.get("enable_dangerous_commands", False),
                        )
                    ),
                )
            )
        return profiles

    return [
        ServerProfile(
            server_id="default",
            display_name=str(
                config.get("server_display_name", "默认服务器") or "默认服务器"
            ),
            rcon_enabled=bool(config.get("rcon_enabled", False)),
            rcon_host=str(config.get("rcon_host", "localhost")),
            rcon_port=int(config.get("rcon_port", 25575)),
            rcon_password=str(config.get("rcon_password", "")),
            websocket_enabled=bool(config.get("websocket_enabled", False)),
            websocket_url=str(
                config.get("websocket_url", "ws://127.0.0.1:8080/minecraft/ws")
            ),
            websocket_token=str(config.get("websocket_token", "")),
            sync_chat_mc_to_qq=bool(config.get("sync_chat_mc_to_qq", False)),
            sync_chat_qq_to_mc=bool(config.get("sync_chat_qq_to_mc", False)),
            forward_llm_responses_to_mc=bool(config.get("enable_chat_response", False)),
            forward_player_events=True,
            mc_message_prefix=str(config.get("mc_message_prefix", "[MC]")),
            qq_message_prefix=str(config.get("qq_message_prefix", "[QQ]")),
            message_transport="auto",
            enable_dangerous_commands=bool(
                config.get("enable_dangerous_commands", False)
            ),
        )
    ]


class ServerRegistry:
    """Resolve named server profiles without sharing mutable selection globally."""

    def __init__(self, default_server_id: str = ""):
        self.profiles: dict[str, ServerProfile] = {}
        self.default_server_id = str(default_server_id or "").strip()

    def add(self, profile: ServerProfile) -> bool:
        if profile.server_id in self.profiles:
            return False
        self.profiles[profile.server_id] = profile
        return True

    def finalize_default(self) -> None:
        matched = self.match_id(self.default_server_id)
        self.default_server_id = matched or next(iter(self.profiles), "")

    def match_id(self, identifier: str | None) -> str | None:
        value = str(identifier or "").strip()
        if not value:
            return None
        if value in self.profiles:
            return value

        folded = value.casefold()
        matches = [
            profile.server_id
            for profile in self.profiles.values()
            if profile.server_id.casefold() == folded
            or profile.label.casefold() == folded
        ]
        return matches[0] if len(matches) == 1 else None

    def get(self, identifier: str | None) -> ServerProfile | None:
        server_id = self.match_id(identifier)
        return self.profiles.get(server_id) if server_id else None

    def normalize_bound_ids(self, server_ids: Iterable[str]) -> list[str]:
        normalized = []
        for raw_id in server_ids:
            if raw_id == "default" and "default" not in self.profiles:
                server_id = self.default_server_id
            else:
                server_id = self.match_id(raw_id)
            if server_id and server_id not in normalized:
                normalized.append(server_id)
        return normalized

    def resolve_id(
        self,
        requested: str | None = None,
        selected: str | None = None,
    ) -> tuple[str | None, str]:
        if requested:
            matched = self.match_id(requested)
            if not matched:
                return None, f"❌ 找不到服务器: {requested}"
            return matched, ""

        matched_selected = self.match_id(selected)
        if matched_selected:
            return matched_selected, ""

        if self.default_server_id:
            return self.default_server_id, ""
        return None, "❌ 尚未配置可用的 Minecraft 服务器"

    def all(self) -> list[ServerProfile]:
        return list(self.profiles.values())
