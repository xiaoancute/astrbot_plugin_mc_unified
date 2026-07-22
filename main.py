import asyncio
import shutil
from contextlib import suppress
from functools import partial
from pathlib import Path

from astrbot.api.star import Context, Star
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api import AstrBotConfig, logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .backends.rcon_backend import RCONBackend
from .backends.mcsmanager_backend import MCSManagerMultiBackend
from .backends.websocket_backend import WebSocketMessageBackend
from .tools.mc_tools import MinecraftTools
from .tools.mcsmanager_tools import MCSManagerTools
from .managers.permission_manager import PermissionManager
from .managers.binding_manager import GroupBindingManager
from .managers.server_manager import ServerRegistry, build_server_profiles
from .utils.message_utils import MessageUtils


class MCUnifiedPlugin(Star):
    """统一的Minecraft管理插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.context = context

        data_root = Path(get_astrbot_data_path())
        data_dir = data_root / "plugin_data" / "mc_unified"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_data(data_root / "mc_unified", data_dir)
        self.data_dir = str(data_dir)

        self.server_registry = ServerRegistry(self.config.get("default_server", ""))
        self._selected_servers = {}
        self._websocket_tasks = {}

        # Legacy aliases point to the configured default profile.
        self.rcon_backend = None
        self.mcsmanager_multi_backend = None
        self.websocket_backend = None
        self._selected_mcsmanager_panels = {}

        self.mc_tools = None
        self.mcsmanager_tools = None

        self.permission_manager = None
        self.binding_manager = None

        self._init_backends()
        self._init_tools()
        self._migrate_legacy_group_config()
        self._init_managers()

        if not self.permission_manager.is_security_enabled():
            logger.warning(
                "[安全警告] 管理员列表为空，所有写操作将被禁用！请在配置中添加 admin_ids"
            )

        logger.info("MC Unified插件已加载")

    @staticmethod
    def _migrate_legacy_data(legacy_dir: Path, data_dir: Path) -> None:
        """Copy legacy state into the standard plugin data directory once."""
        if legacy_dir == data_dir or not legacy_dir.is_dir():
            return
        for filename in ("bindings.json", "group_sessions.json"):
            source = legacy_dir / filename
            target = data_dir / filename
            if not source.is_file() or target.exists():
                continue
            try:
                shutil.copy2(source, target)
                logger.info(f"已迁移旧版插件数据: {source} -> {target}")
            except OSError as error:
                logger.warning(f"迁移旧版插件数据失败 {source}: {error}")

    def _init_backends(self):
        for profile in build_server_profiles(self.config):
            if not self.server_registry.add(profile):
                logger.warning(f"忽略重复的服务器ID: {profile.server_id}")
                continue

            if profile.rcon_enabled:
                profile.rcon_backend = RCONBackend(
                    profile.rcon_host,
                    profile.rcon_port,
                    profile.rcon_password,
                )
                logger.info(
                    f"[{profile.server_id}] RCON后端已初始化: "
                    f"{profile.rcon_host}:{profile.rcon_port}"
                )

            if profile.websocket_enabled:
                profile.websocket_backend = WebSocketMessageBackend(
                    profile.websocket_url, profile.websocket_token
                )
                profile.websocket_backend.set_message_callback(
                    partial(self._on_mc_chat, profile.server_id)
                )
                profile.websocket_backend.set_player_join_callback(
                    partial(self._on_player_join, profile.server_id)
                )
                profile.websocket_backend.set_player_leave_callback(
                    partial(self._on_player_leave, profile.server_id)
                )
                profile.websocket_backend.set_player_death_callback(
                    partial(self._on_player_death, profile.server_id)
                )
                logger.info(
                    f"[{profile.server_id}] WebSocket后端已初始化: "
                    f"{profile.websocket_url}"
                )

        self.server_registry.finalize_default()
        default_profile = self.server_registry.get(
            self.server_registry.default_server_id
        )
        if default_profile:
            self.rcon_backend = default_profile.rcon_backend
            self.websocket_backend = default_profile.websocket_backend

        logger.info(
            f"Minecraft服务器配置已加载，共 {len(self.server_registry.profiles)} 个，"
            f"默认服务器: {self.server_registry.default_server_id or '无'}"
        )

        if self.config.get("mcsmanager_enabled", False):
            self.mcsmanager_multi_backend = MCSManagerMultiBackend()
            panels = self.config.get("mcsmanager_panels", [])
            if panels:
                for index, panel in enumerate(panels, 1):
                    if not isinstance(panel, dict):
                        logger.warning(f"忽略无效的MCSManager面板配置 #{index}")
                        continue
                    # template_list 格式: panel_name 字段对应原 name
                    name = panel.get("panel_name", "") or panel.get("name", "")
                    url = panel.get("url", "")
                    api_key = panel.get("api_key", "")
                    if name and url and api_key:
                        dangerous_commands_enabled = panel.get(
                            "enable_dangerous_commands",
                            self.config.get("enable_dangerous_commands", False),
                        )
                        self.mcsmanager_multi_backend.add_backend(
                            name, url, api_key, dangerous_commands_enabled
                        )
                    else:
                        logger.warning(
                            f"忽略不完整的MCSManager面板配置 #{index}: "
                            "需要 panel_name、url 和 api_key"
                        )
                logger.info(
                    "MCSManager多面板后端已初始化，共 "
                    f"{len(self.mcsmanager_multi_backend.backends)} 个面板"
                )

    def _init_tools(self):
        for profile in self.server_registry.all():
            if profile.rcon_backend:
                profile.mc_tools = MinecraftTools(profile.rcon_backend)
                profile.mc_tools.set_dangerous_commands_enabled(
                    profile.enable_dangerous_commands
                )

        default_profile = self.server_registry.get(
            self.server_registry.default_server_id
        )
        self.mc_tools = default_profile.mc_tools if default_profile else None

        if self.mcsmanager_multi_backend:
            self.mcsmanager_tools = MCSManagerTools(self.mcsmanager_multi_backend)

    def _migrate_legacy_group_config(self) -> None:
        previous_servers = self.config.get("mc_servers", [])
        previous_bindings = self.config.get("qq_group_bindings", [])
        (
            migrated_servers,
            remaining_bindings,
            migrated_count,
            changed,
            warnings,
        ) = GroupBindingManager.migrate_legacy_config(
            previous_servers, previous_bindings
        )
        for warning in warnings:
            logger.warning(warning)
        if not changed:
            return

        self.config["mc_servers"] = migrated_servers
        self.config["qq_group_bindings"] = remaining_bindings
        save_config = getattr(self.config, "save_config", None)
        if callable(save_config):
            try:
                save_config()
            except Exception as error:
                self.config["mc_servers"] = previous_servers
                self.config["qq_group_bindings"] = previous_bindings
                logger.warning(f"旧版QQ群绑定自动迁移保存失败，继续使用旧配置: {error}")
                return
        logger.info(f"已将 {migrated_count} 条旧版QQ群绑定迁移到对应服务器配置")

    def _init_managers(self):
        admin_ids = self.config.get("admin_ids", [])
        llm_mode = self.config.get("llm_permission_mode", "readonly")
        self.permission_manager = PermissionManager(admin_ids, llm_mode)
        server_bindings, server_binding_warnings = (
            GroupBindingManager.normalize_server_bindings(
                self.config.get("mc_servers", []),
                set(self.server_registry.profiles),
            )
        )
        legacy_bindings, legacy_binding_warnings = (
            GroupBindingManager.normalize_configured_bindings(
                self.config.get("qq_group_bindings", []),
                set(self.server_registry.profiles),
            )
        )
        configured_bindings = GroupBindingManager.merge_configured_bindings(
            server_bindings, legacy_bindings
        )
        for warning in server_binding_warnings + legacy_binding_warnings:
            logger.warning(warning)
        self.binding_manager = GroupBindingManager(
            self.data_dir, configured_bindings=configured_bindings
        )
        configured_count = sum(len(groups) for groups in configured_bindings.values())
        logger.info(f"WebUI已配置 {configured_count} 条QQ群与服务器绑定关系")
        if self.permission_manager.is_llm_full_access():
            logger.warning(PermissionManager.FULL_WARNING)

    async def initialize(self):
        for profile in self.server_registry.all():
            if profile.websocket_backend:
                self._websocket_tasks[profile.server_id] = asyncio.create_task(
                    profile.websocket_backend.start_listening()
                )
                logger.info(f"[{profile.server_id}] WebSocket监听已启动")

    async def terminate(self):
        for profile in self.server_registry.all():
            if profile.rcon_backend:
                await profile.rcon_backend.disconnect()

        if self.mcsmanager_multi_backend:
            await self.mcsmanager_multi_backend.terminate_all()

        for profile in self.server_registry.all():
            if profile.websocket_backend:
                await profile.websocket_backend.stop_listening()
            websocket_task = self._websocket_tasks.get(profile.server_id)
            if websocket_task:
                try:
                    await asyncio.wait_for(websocket_task, timeout=5)
                except TimeoutError:
                    websocket_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await websocket_task
        self._websocket_tasks.clear()

        logger.info("MC Unified插件已卸载")

    async def _on_mc_chat(self, server_id: str, player: str, message: str):
        profile = self.server_registry.get(server_id)
        if not profile or not profile.sync_chat_mc_to_qq:
            return

        prefix = profile.format_prefix(profile.mc_message_prefix)
        formatted = MessageUtils.format_mc_message(player, message, prefix)

        bound_groups = self._get_bound_groups(server_id)
        for group_id in bound_groups:
            await self._send_to_qq_group(group_id, formatted)

    async def _on_player_join(self, server_id: str, player: str):
        await self._send_player_event(server_id, f"{player} 加入了游戏")

    async def _on_player_leave(self, server_id: str, player: str):
        await self._send_player_event(server_id, f"{player} 离开了游戏")

    async def _on_player_death(self, server_id: str, player: str, reason: str):
        await self._send_player_event(server_id, reason)

    async def _send_player_event(self, server_id: str, event_text: str):
        profile = self.server_registry.get(server_id)
        if not profile or not profile.forward_player_events:
            return
        message = f"[系统消息][{profile.label}] {event_text}"
        await self._send_to_bound_groups(server_id, message)

    async def _send_to_bound_groups(self, server_id: str, message: str):
        bound_groups = self._get_bound_groups(server_id)
        for group_id in bound_groups:
            await self._send_to_qq_group(group_id, message)

    def _get_bound_groups(self, server_id: str) -> list[str]:
        groups = list(self.binding_manager.get_bound_groups(server_id))
        if (
            server_id == self.server_registry.default_server_id
            and server_id != "default"
        ):
            groups.extend(self.binding_manager.get_bound_groups("default"))
        return list(dict.fromkeys(groups))

    def _remember_group_session(self, event: AstrMessageEvent) -> None:
        group_id = event.get_group_id()
        unified_msg_origin = getattr(event, "unified_msg_origin", "")
        if group_id and unified_msg_origin:
            self.binding_manager.remember_group_session(
                str(group_id), str(unified_msg_origin)
            )

    async def _send_to_qq_group(self, group_id: str, message: str) -> bool:
        """Send proactively through AstrBot's public MessageChain API."""
        unified_msg_origin = self.binding_manager.get_group_session(group_id)
        if not unified_msg_origin:
            logger.warning(
                f"无法主动发送到群 {group_id}: 尚未记录真实会话，请先在群内发送一条消息"
            )
            return False
        try:
            message_chain = MessageChain().message(message)
            return bool(
                await self.context.send_message(unified_msg_origin, message_chain)
            )
        except Exception as e:
            logger.error(f"发送消息到QQ群失败: {e}")
            return False

    async def _send_to_mc(self, server_id: str, message: str) -> str:
        profile = self.server_registry.get(server_id)
        if not profile:
            return f"❌ 找不到服务器: {server_id}"

        prefix = profile.format_prefix(profile.qq_message_prefix)
        formatted = f"{prefix} {message}"

        transport = profile.message_transport
        if transport == "websocket":
            if profile.websocket_backend:
                return await profile.websocket_backend.send_to_mc(formatted)
            return f"❌ 服务器 {profile.label} 未启用 WebSocket"
        if transport == "rcon":
            if profile.rcon_backend:
                return await profile.rcon_backend.send_message(formatted)
            return f"❌ 服务器 {profile.label} 未启用 RCON"
        if profile.rcon_backend:
            return await profile.rcon_backend.send_message(formatted)
        if profile.websocket_backend:
            return await profile.websocket_backend.send_to_mc(formatted)
        return f"❌ 服务器 {profile.label} 未配置 RCON 或 WebSocket"

    def _get_group_server_ids(self, group_id: str | None) -> list[str]:
        if not group_id:
            return []
        return self.server_registry.normalize_bound_ids(
            self.binding_manager.get_group_servers(group_id)
        )

    def _format_group_bindings(self, group_ids: list[str]) -> str:
        lines = ["🔗 QQ群与Minecraft服务器绑定:"]
        for group_id in group_ids:
            server_ids = self._get_group_server_ids(group_id)
            if not server_ids:
                lines.append(f"- 群 {group_id}: 无有效服务器绑定")
                continue
            lines.append(f"- 群 {group_id}:")
            for server_id in server_ids:
                profile = self.server_registry.get(server_id)
                sources = "/".join(
                    self.binding_manager.get_binding_sources(group_id, server_id)
                )
                if (
                    not sources
                    and server_id == self.server_registry.default_server_id
                    and server_id != "default"
                ):
                    sources = "/".join(
                        self.binding_manager.get_binding_sources(group_id, "default")
                    )
                lines.append(
                    f"  - {profile.label} ({server_id}) [{sources or '未知来源'}]"
                )
        return "\n".join(lines)

    def _resolve_server_id(
        self, event: AstrMessageEvent, requested: str = ""
    ) -> tuple[str | None, str]:
        user_id = str(event.get_sender_id() or "")
        return self.server_registry.resolve_id(
            requested=requested,
            selected=self._selected_servers.get(user_id),
        )

    def _get_mc_tools(
        self, event: AstrMessageEvent, requested: str = ""
    ) -> tuple[object | None, str, str | None]:
        server_id, error = self._resolve_server_id(event, requested)
        if not server_id:
            return None, error, None
        profile = self.server_registry.get(server_id)
        if not profile or not profile.mc_tools:
            label = profile.label if profile else server_id
            return None, f"❌ 服务器 {label} 未启用 RCON", server_id
        return profile.mc_tools, "", server_id

    def _format_server_list(self, event: AstrMessageEvent) -> str:
        selected_id = self._selected_servers.get(str(event.get_sender_id() or ""))
        bound_ids = set(self._get_group_server_ids(event.get_group_id()))
        lines = ["🖥️ Minecraft 服务器列表:"]
        for profile in self.server_registry.all():
            flags = []
            if profile.server_id == self.server_registry.default_server_id:
                flags.append("默认")
            if profile.server_id == selected_id:
                flags.append("当前")
            if profile.server_id in bound_ids:
                flags.append("本群聊天已绑定")
            transports = []
            if profile.rcon_backend:
                transports.append("RCON")
            if profile.websocket_backend:
                transports.append("WS")
            suffix = f" [{' / '.join(flags)}]" if flags else ""
            lines.append(
                f"- {profile.server_id}: {profile.label} "
                f"({'+'.join(transports) or '未配置连接'}){suffix}"
            )
        return "\n".join(lines)

    async def _server_status_line(self, server_id: str) -> str:
        profile = self.server_registry.get(server_id)
        if not profile:
            return f"❌ {server_id}: 配置不存在"

        states = []
        if profile.rcon_backend:
            success, message = await profile.rcon_backend.execute_command_checked(
                "list"
            )
            states.append(f"RCON{'正常' if success else '失败'}")
            if not success:
                states.append(message)
        else:
            states.append("RCON未启用")

        if profile.websocket_backend:
            connected = await profile.websocket_backend.is_connected()
            states.append(f"WebSocket{'已连接' if connected else '未连接'}")
        else:
            states.append("WebSocket未启用")

        healthy = any("正常" in state or "已连接" in state for state in states)
        return (
            f"{'✅' if healthy else 'ℹ️'} {profile.label} ({server_id}): "
            + "；".join(states)
        )

    async def _format_server_status(self, requested: str = "all") -> str:
        if requested.casefold() == "all":
            server_ids = [profile.server_id for profile in self.server_registry.all()]
        else:
            server_id = self.server_registry.match_id(requested)
            if not server_id:
                return f"❌ 找不到服务器: {requested}"
            server_ids = [server_id]

        if not server_ids:
            return "❌ 尚未配置服务器"
        lines = await asyncio.gather(
            *(self._server_status_line(server_id) for server_id in server_ids)
        )
        return "🩺 服务器连接状态:\n" + "\n".join(lines)

    async def _list_players_for_servers(
        self, event: AstrMessageEvent, requested: str = ""
    ) -> str:
        if requested.casefold() != "all":
            mc_tools, error, server_id = self._get_mc_tools(event, requested)
            if not mc_tools:
                return error
            profile = self.server_registry.get(server_id)
            return f"👥 {profile.label} ({server_id}):\n{await mc_tools.list_players()}"

        profiles = [
            profile for profile in self.server_registry.all() if profile.mc_tools
        ]
        if not profiles:
            return "❌ 没有服务器启用 RCON，无法查询在线玩家"
        results = await asyncio.gather(
            *(profile.mc_tools.list_players() for profile in profiles)
        )
        return "\n\n".join(
            f"👥 {profile.label} ({profile.server_id}):\n{result}"
            for profile, result in zip(profiles, results)
        )

    def _check_permission(
        self, event: AstrMessageEvent, action: str = "unknown"
    ) -> tuple[bool, str]:
        user_id = event.get_sender_id()
        return self.permission_manager.check_permission(user_id, action)

    def _check_read_only(
        self, event: AstrMessageEvent, action: str = "read_only"
    ) -> tuple[bool, str]:
        user_id = event.get_sender_id()
        return self.permission_manager.check_read_only(user_id, action)

    def _check_llm_write_permission(
        self, event: AstrMessageEvent, action: str
    ) -> tuple[bool, str]:
        user_id = event.get_sender_id()
        return self.permission_manager.check_llm_write_permission(user_id, action)

    def _format_llm_permission_status(self) -> str:
        if self.permission_manager.is_llm_full_access():
            return (
                "⚠️ AI权限模式: FULL\n"
                f"{PermissionManager.FULL_WARNING}\n"
                "管理员可随时使用 /mc ai-mode readonly 恢复只读。"
            )
        return (
            "🔒 AI权限模式: READONLY（默认）\n"
            "LLM只能查询状态、玩家、日志和配置目标，不能执行任何写操作。\n"
            "如确需开启，请由管理员使用 /mc ai-mode full CONFIRM。"
        )

    def _set_llm_permission_mode(self, mode: str) -> tuple[bool, str]:
        normalized = self.permission_manager.normalize_llm_mode(mode)
        previous = self.permission_manager.llm_permission_mode
        previous_config = self.config.get("llm_permission_mode", previous)
        self.permission_manager.set_llm_permission_mode(normalized)
        self.config["llm_permission_mode"] = normalized
        save_config = getattr(self.config, "save_config", None)
        if callable(save_config):
            try:
                save_config()
            except Exception as error:
                self.permission_manager.set_llm_permission_mode(previous)
                self.config["llm_permission_mode"] = previous_config
                logger.error(f"保存AI权限模式失败: {error}")
                return False, "❌ 权限模式保存失败，请检查AstrBot日志"
        return True, normalized

    def _get_selected_panel(
        self, event: AstrMessageEvent, panel_name: str = None
    ) -> str | None:
        if panel_name:
            return panel_name
        return self._selected_mcsmanager_panels.get(str(event.get_sender_id()))

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response):
        """Optionally forward a completed group LLM response to bound servers."""
        if not response or getattr(response, "is_chunk", False):
            return

        group_id = event.get_group_id()
        if not group_id:
            return
        self._remember_group_session(event)

        response_text = str(getattr(response, "completion_text", "") or "").strip()
        if not response_text or response_text == "*No response*":
            return

        for server_id in self._get_group_server_ids(group_id):
            profile = self.server_registry.get(server_id)
            if profile and profile.forward_llm_responses_to_mc:
                await self._send_to_mc(server_id, f"AI: {response_text}")

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_qq_group_message(self, event: AstrMessageEvent):
        """Remember the real QQ session and optionally forward group chat to MC."""
        group_id = event.get_group_id()
        if not group_id:
            return
        self._remember_group_session(event)

        server_ids = self._get_group_server_ids(group_id)
        if not server_ids:
            return

        message_text = (event.message_str or "").strip()
        if not message_text:
            return

        sender_name = event.get_sender_name() or event.get_sender_id()
        for server_id in server_ids:
            profile = self.server_registry.get(server_id)
            if profile and profile.sync_chat_qq_to_mc:
                await self._send_to_mc(server_id, f"{sender_name}: {message_text}")

    @filter.command_group("mc")
    def mc(self):
        """Minecraft统一管理命令。"""
        pass

    @mc.command("servers")
    async def cmd_mc_servers(self, event: AstrMessageEvent):
        """查看全部Minecraft服务器及当前选择。"""
        has_permission, error_msg = self._check_read_only(event, "mc_servers")
        if not has_permission:
            yield event.plain_result(error_msg)
            return
        yield event.plain_result(self._format_server_list(event))

    @mc.command("status")
    async def cmd_mc_status(self, event: AstrMessageEvent, server_name: str = "all"):
        """检查一台或全部服务器连接状态。"""
        has_permission, error_msg = self._check_read_only(event, "mc_status")
        if not has_permission:
            yield event.plain_result(error_msg)
            return
        yield event.plain_result(await self._format_server_status(server_name))

    @mc.command("players")
    async def cmd_mc_players(self, event: AstrMessageEvent, server_name: str = "all"):
        """查看一台或全部服务器在线玩家。"""
        has_permission, error_msg = self._check_read_only(event, "mc_players")
        if not has_permission:
            yield event.plain_result(error_msg)
            return
        yield event.plain_result(
            await self._list_players_for_servers(event, server_name)
        )

    @mc.command("use")
    async def cmd_mc_use(self, event: AstrMessageEvent, server_name: str):
        """选择当前管理员后续操作的默认服务器。"""
        has_permission, error_msg = self._check_permission(event, "mc_use")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        server_id = self.server_registry.match_id(server_name)
        if not server_id:
            yield event.plain_result(f"❌ 找不到服务器: {server_name}")
            return

        self._selected_servers[str(event.get_sender_id())] = server_id
        profile = self.server_registry.get(server_id)
        yield event.plain_result(
            f"✅ 当前管理服务器已切换为 {profile.label} ({server_id})"
        )

    @mc.command("bindings")
    async def cmd_mc_bindings(self, event: AstrMessageEvent, group_id: str = ""):
        """查看当前群、指定群或全部QQ群的消息转发绑定。"""
        has_permission, error_msg = self._check_read_only(event, "mc_bindings")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        requested_group = str(group_id or "").strip()
        current_group = str(event.get_group_id() or "")
        if requested_group.casefold() == "all":
            group_ids = self.binding_manager.get_all_group_ids()
        else:
            target_group = requested_group or current_group
            group_ids = [target_group] if target_group else []
        if not group_ids:
            yield event.plain_result("ℹ️ 尚未配置任何QQ群与服务器绑定")
            return

        if len(group_ids) == 1 and not self._get_group_server_ids(group_ids[0]):
            yield event.plain_result(f"ℹ️ 群 {group_ids[0]} 尚未绑定任何服务器")
            return
        yield event.plain_result(self._format_group_bindings(group_ids))

    @mc.command("bind")
    async def cmd_mc_bind(
        self,
        event: AstrMessageEvent,
        server_name: str = "",
        group_id: str = "",
    ):
        """将当前或指定QQ群绑定到服务器的消息转发。"""
        has_permission, error_msg = self._check_permission(event, "mc_bind")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        current_group = str(event.get_group_id() or "")
        target_group = str(group_id or current_group).strip()
        if not target_group:
            yield event.plain_result("❌ 请在QQ群中使用，或提供目标QQ群号")
            return
        if target_group == current_group:
            self._remember_group_session(event)

        server_id, resolve_error = self._resolve_server_id(event, server_name)
        if not server_id:
            yield event.plain_result(resolve_error)
            return

        profile = self.server_registry.get(server_id)
        success = self.binding_manager.bind_group(target_group, server_id)
        if success:
            message = f"✅ 已将群 {target_group} 绑定到 {profile.label} ({server_id})"
            if target_group != current_group:
                message += "\nℹ️ 该群需要先发送一条消息，MC→QQ主动转发才能建立会话。"
            yield event.plain_result(message)
        elif self.binding_manager.last_error:
            yield event.plain_result(f"❌ {self.binding_manager.last_error}")
        else:
            yield event.plain_result(f"⚠️ 群 {target_group} 已绑定 {profile.label}")

    @mc.command("unbind")
    async def cmd_mc_unbind(
        self,
        event: AstrMessageEvent,
        server_name: str = "",
        group_id: str = "",
    ):
        """解除当前或指定QQ群的指令绑定；WebUI绑定需在配置页删除。"""
        has_permission, error_msg = self._check_permission(event, "mc_unbind")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        target_group = str(group_id or event.get_group_id() or "").strip()
        if not target_group:
            yield event.plain_result("❌ 请在QQ群中使用，或提供目标QQ群号")
            return

        if server_name.casefold() == "all":
            success = self.binding_manager.unbind_group_from_all(target_group)
            target_label = "全部服务器"
        else:
            bound_ids = self._get_group_server_ids(target_group)
            if not server_name and len(bound_ids) > 1:
                yield event.plain_result(
                    "❌ 目标群绑定了多个服务器，请使用 mc unbind <服务器ID> [群号] 或 mc unbind all [群号]"
                )
                return
            requested = server_name or (bound_ids[0] if bound_ids else "")
            server_id, resolve_error = self._resolve_server_id(event, requested)
            if not server_id:
                yield event.plain_result(resolve_error)
                return
            profile = self.server_registry.get(server_id)
            success = self.binding_manager.unbind_group(target_group, server_id)
            if (
                not success
                and server_id == self.server_registry.default_server_id
                and server_id != "default"
            ):
                success = self.binding_manager.unbind_group(target_group, "default")
            target_label = profile.label

        if success:
            message = f"✅ 已解除群 {target_group} 与{target_label}的指令绑定"
            if self.binding_manager.last_error:
                message += f"\nℹ️ {self.binding_manager.last_error}"
            yield event.plain_result(message)
        elif self.binding_manager.last_error:
            yield event.plain_result(f"❌ {self.binding_manager.last_error}")
        else:
            yield event.plain_result(
                f"⚠️ 群 {target_group} 未通过指令绑定{target_label}"
            )

    @mc.command("test")
    async def cmd_mc_test(self, event: AstrMessageEvent, server_name: str = ""):
        """测试指定Minecraft服务器的RCON连接。"""
        has_permission, error_msg = self._check_permission(event, "mc_test")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        mc_tools, resolve_error, server_id = self._get_mc_tools(event, server_name)
        if not mc_tools:
            yield event.plain_result(resolve_error)
            return

        profile = self.server_registry.get(server_id)
        success, message = await self._test_rcon_connection(server_id)
        if success:
            yield event.plain_result(f"✅ {profile.label} RCON连接成功\n{message}")
        else:
            yield event.plain_result(f"❌ {profile.label} RCON连接失败\n{message}")

    @mc.command("log")
    async def cmd_mc_log(self, event: AstrMessageEvent):
        """查看最近的权限与管理操作日志。"""
        has_permission, error_msg = self._check_permission(event, "mc_log")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        log = self.permission_manager.get_action_log()
        yield event.plain_result(log)

    @mc.command("security")
    async def cmd_mc_security(self, event: AstrMessageEvent):
        """查看管理员、危险命令和AI权限安全状态。"""
        has_permission, error_msg = self._check_permission(event, "mc_security")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        security_enabled = self.permission_manager.is_security_enabled()
        admin_count = len(self.permission_manager.admin_ids)
        admins = ", ".join(self.permission_manager.admin_ids)

        result = "🔒 安全状态:\n"
        result += f"  安全模式: {'✅ 已启用' if security_enabled else '❌ 未启用'}\n"
        result += f"  管理员数量: {admin_count}\n"
        result += f"  管理员列表: {admins if admins else '无'}\n"
        result += (
            "  AI权限: "
            f"{'⚠️ FULL' if self.permission_manager.is_llm_full_access() else '🔒 READONLY'}\n"
        )

        if not security_enabled:
            result += "\n⚠️ 警告：管理员列表为空，所有写操作已禁用！"
        elif self.permission_manager.is_llm_full_access():
            result += f"\n{PermissionManager.FULL_WARNING}"

        yield event.plain_result(result)

    @mc.command("ai-mode")
    async def cmd_mc_ai_mode(
        self,
        event: AstrMessageEvent,
        mode: str = "status",
        confirmation: str = "",
    ):
        """查看或切换AI的READONLY/FULL权限模式。"""
        has_permission, error_msg = self._check_permission(event, "mc_ai_mode")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        requested = str(mode or "status").strip().casefold()
        if requested in {"status", "show", "查看", "状态"}:
            yield event.plain_result(self._format_llm_permission_status())
            return

        if requested in {"readonly", "read-only", "read_only", "只读"}:
            success, result = self._set_llm_permission_mode("readonly")
            yield event.plain_result(
                "✅ AI权限已恢复为READONLY，所有LLM写操作立即禁用。"
                if success
                else result
            )
            return

        if requested != "full":
            yield event.plain_result(
                "❌ 可用模式: readonly、full。查看状态: /mc ai-mode status"
            )
            return

        if confirmation != PermissionManager.FULL_CONFIRMATION:
            yield event.plain_result(
                f"{PermissionManager.FULL_WARNING}\n\n"
                "如确认启用，请由管理员完整输入：\n"
                "/mc ai-mode full CONFIRM\n"
                "未提供精确确认词时不会修改权限。"
            )
            return

        success, result = self._set_llm_permission_mode("full")
        if not success:
            yield event.plain_result(result)
            return
        logger.warning(
            f"管理员 {event.get_sender_id()} 已启用LLM FULL权限；用户已确认自行承担风险"
        )
        yield event.plain_result(
            "⚠️ AI权限已切换为FULL。\n"
            f"{PermissionManager.FULL_WARNING}\n"
            "建议完成操作后立即执行 /mc ai-mode readonly。"
        )

    async def _test_rcon_connection(self, server_id: str) -> tuple[bool, str]:
        profile = self.server_registry.get(server_id)
        if not profile or not profile.rcon_backend:
            return False, "RCON后端未启用"
        return await profile.rcon_backend.execute_command_checked("list")

    @filter.llm_tool(name="minecraft_get_servers")
    async def tool_minecraft_get_servers(self, event: AstrMessageEvent) -> str:
        """查看所有 Minecraft 服务器和当前选择、群绑定及连接方式。"""
        has_permission, error_msg = self._check_read_only(
            event, "minecraft_get_servers"
        )
        if not has_permission:
            return error_msg
        return self._format_server_list(event)

    @filter.llm_tool(name="minecraft_get_status")
    async def tool_minecraft_get_status(
        self, event: AstrMessageEvent, server_name: str = "all"
    ) -> str:
        """检查一台或全部 Minecraft 服务器的连接状态。

        Args:
            server_name(string): 服务器ID或显示名称；使用all检查全部服务器。
        """
        has_permission, error_msg = self._check_read_only(event, "minecraft_get_status")
        if not has_permission:
            return error_msg
        return await self._format_server_status(server_name)

    @filter.llm_tool(name="minecraft_get_ai_permission")
    async def tool_minecraft_get_ai_permission(self, event: AstrMessageEvent) -> str:
        """查看当前AI管理权限模式和安全提示。"""
        has_permission, error_msg = self._check_read_only(
            event, "minecraft_get_ai_permission"
        )
        if not has_permission:
            return error_msg
        return self._format_llm_permission_status()

    @filter.llm_tool(name="minecraft_request_full_access")
    async def tool_minecraft_request_full_access(self, event: AstrMessageEvent) -> str:
        """获取开启AI FULL权限的人工确认说明；此工具不会修改权限。"""
        has_permission, error_msg = self._check_read_only(
            event, "minecraft_request_full_access"
        )
        if not has_permission:
            return error_msg
        return (
            f"{PermissionManager.FULL_WARNING}\n"
            "为防止模型幻觉或自行提权，必须由管理员手动输入 "
            "/mc ai-mode full CONFIRM；本工具不会开启权限。"
        )

    @filter.llm_tool(name="minecraft_select_server")
    async def tool_minecraft_select_server(
        self, event: AstrMessageEvent, server_name: str
    ) -> str:
        """选择后续 Minecraft 管理工具操作的服务器。

        Args:
            server_name(string): 配置中的服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_read_only(
            event, "minecraft_select_server"
        )
        if not has_permission:
            return error_msg
        server_id = self.server_registry.match_id(server_name)
        if not server_id:
            return f"❌ 找不到服务器: {server_name}"
        self._selected_servers[str(event.get_sender_id())] = server_id
        profile = self.server_registry.get(server_id)
        return f"✅ 已选择服务器: {profile.label} ({server_id})"

    @filter.llm_tool(name="list_players")
    async def tool_list_players(
        self, event: AstrMessageEvent, server_name: str = "all"
    ) -> str:
        """查看一台或全部 Minecraft 服务器的在线玩家。

        Args:
            server_name(string): 服务器ID或显示名称；默认all汇总全部服务器。
        """
        has_permission, error_msg = self._check_read_only(event, "list_players")
        if not has_permission:
            return error_msg
        return await self._list_players_for_servers(event, server_name)

    @filter.llm_tool(name="kick_player")
    async def tool_kick_player(
        self,
        event: AstrMessageEvent,
        player: str,
        reason: str = "被管理员踢出",
        server_name: str = "",
    ) -> str:
        """踢出指定玩家。

        Args:
            player(string): 玩家名称。
            reason(string): 踢出原因。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "kick_player"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.kick_player(player, reason)

    @filter.llm_tool(name="ban_player")
    async def tool_ban_player(
        self,
        event: AstrMessageEvent,
        player: str,
        reason: str = "违反服务器规则",
        server_name: str = "",
    ) -> str:
        """封禁指定玩家。

        Args:
            player(string): 玩家名称。
            reason(string): 封禁原因。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "ban_player"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.ban_player(player, reason)

    @filter.llm_tool(name="pardon_player")
    async def tool_pardon_player(
        self, event: AstrMessageEvent, player: str, server_name: str = ""
    ) -> str:
        """解除玩家封禁。

        Args:
            player(string): 玩家名称。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "pardon_player"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.pardon_player(player)

    @filter.llm_tool(name="op_player")
    async def tool_op_player(
        self, event: AstrMessageEvent, player: str, server_name: str = ""
    ) -> str:
        """授予玩家 OP 权限。

        Args:
            player(string): 玩家名称。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(event, "op_player")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.op_player(player)

    @filter.llm_tool(name="deop_player")
    async def tool_deop_player(
        self, event: AstrMessageEvent, player: str, server_name: str = ""
    ) -> str:
        """移除玩家 OP 权限。

        Args:
            player(string): 玩家名称。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "deop_player"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.deop_player(player)

    @filter.llm_tool(name="whitelist_add")
    async def tool_whitelist_add(
        self, event: AstrMessageEvent, player: str, server_name: str = ""
    ) -> str:
        """将玩家加入服务器白名单。

        Args:
            player(string): 玩家名称。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "whitelist_add"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.whitelist_add(player)

    @filter.llm_tool(name="whitelist_remove")
    async def tool_whitelist_remove(
        self, event: AstrMessageEvent, player: str, server_name: str = ""
    ) -> str:
        """将玩家移出服务器白名单。

        Args:
            player(string): 玩家名称。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "whitelist_remove"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.whitelist_remove(player)

    @filter.llm_tool(name="whitelist_list")
    async def tool_whitelist_list(
        self, event: AstrMessageEvent, server_name: str = ""
    ) -> str:
        """查看指定服务器白名单。

        Args:
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_read_only(event, "whitelist_list")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.whitelist_list()

    @filter.llm_tool(name="banlist")
    async def tool_banlist(
        self,
        event: AstrMessageEvent,
        ban_type: str = "players",
        server_name: str = "",
    ) -> str:
        """查看玩家或 IP 封禁列表。

        Args:
            ban_type(string): 列表类型，使用 players 或 ips。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_read_only(event, "banlist")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.banlist(ban_type)

    @filter.llm_tool(name="give_item")
    async def tool_give_item(
        self,
        event: AstrMessageEvent,
        player: str,
        item: str,
        count: int = 1,
        server_name: str = "",
    ) -> str:
        """给予玩家物品。

        Args:
            player(string): 玩家名称或目标选择器。
            item(string): 物品 ID，例如 minecraft:diamond。
            count(number): 给予数量。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(event, "give_item")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.give_item(player, item, count)

    @filter.llm_tool(name="teleport_player")
    async def tool_teleport_player(
        self,
        event: AstrMessageEvent,
        player: str,
        target: str,
        server_name: str = "",
    ) -> str:
        """传送玩家到坐标或其他玩家。

        Args:
            player(string): 要传送的玩家名称。
            target(string): 目标玩家或坐标，例如 100 64 200。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "teleport_player"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.teleport_player(player, target)

    @filter.llm_tool(name="set_gamemode")
    async def tool_set_gamemode(
        self,
        event: AstrMessageEvent,
        player: str,
        mode: str,
        server_name: str = "",
    ) -> str:
        """设置玩家游戏模式。

        Args:
            player(string): 玩家名称。
            mode(string): survival、creative、adventure 或 spectator。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "set_gamemode"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.set_gamemode(player, mode)

    @filter.llm_tool(name="say_message")
    async def tool_say_message(
        self, event: AstrMessageEvent, message: str, server_name: str = ""
    ) -> str:
        """向服务器内所有玩家广播消息。

        Args:
            message(string): 广播内容。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "say_message"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.say_message(message)

    @filter.llm_tool(name="execute_command")
    async def tool_execute_command(
        self, event: AstrMessageEvent, command: str, server_name: str = ""
    ) -> str:
        """通过 RCON 执行自定义 Minecraft 命令。

        Args:
            command(string): 不带或带斜杠的服务器命令。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "execute_command"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.execute_command(command)

    @filter.llm_tool(name="set_weather")
    async def tool_set_weather(
        self,
        event: AstrMessageEvent,
        weather_type: str,
        duration: int = None,
        server_name: str = "",
    ) -> str:
        """设置服务器天气。

        Args:
            weather_type(string): clear、rain 或 thunder。
            duration(number, optional): 持续秒数。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "set_weather"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.set_weather(weather_type, duration)

    @filter.llm_tool(name="set_time")
    async def tool_set_time(
        self, event: AstrMessageEvent, time_value: str, server_name: str = ""
    ) -> str:
        """设置服务器时间。

        Args:
            time_value(string): day、night、noon、midnight 或刻数。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(event, "set_time")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.set_time(time_value)

    @filter.llm_tool(name="set_difficulty")
    async def tool_set_difficulty(
        self, event: AstrMessageEvent, difficulty: str, server_name: str = ""
    ) -> str:
        """设置服务器难度。

        Args:
            difficulty(string): peaceful、easy、normal 或 hard。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "set_difficulty"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.set_difficulty(difficulty)

    @filter.llm_tool(name="set_gamerule")
    async def tool_set_gamerule(
        self,
        event: AstrMessageEvent,
        rule: str,
        value: str,
        server_name: str = "",
    ) -> str:
        """修改 Minecraft 游戏规则。

        Args:
            rule(string): 游戏规则名称，例如 keepInventory。
            value(string): 规则值。
            server_name(string, optional): 目标服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "set_gamerule"
        )
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event, server_name)
        if not mc_tools:
            return error
        return await mc_tools.set_gamerule(rule, value)

    @filter.llm_tool(name="mcsmanager_get_panels")
    async def tool_mcsmanager_get_panels(self, event: AstrMessageEvent) -> str:
        """查看已配置的 MCSManager 面板列表。"""
        has_permission, error_msg = self._check_read_only(
            event, "mcsmanager_get_panels"
        )
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        return self.mcsmanager_tools.get_panel_list()

    @filter.llm_tool(name="mcsmanager_select_panel")
    async def tool_mcsmanager_select_panel(
        self, event: AstrMessageEvent, panel_name: str
    ) -> str:
        """为当前用户选择后续操作使用的面板。

        Args:
            panel_name(string): 配置中的面板名称。
        """
        has_permission, error_msg = self._check_read_only(
            event, "mcsmanager_select_panel"
        )
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        if self.mcsmanager_tools.has_panel(panel_name):
            user_id = str(event.get_sender_id())
            self._selected_mcsmanager_panels[user_id] = panel_name
            return f"✅ 已切换到面板: {panel_name}"
        return f"❌ 找不到面板: {panel_name}"

    @filter.llm_tool(name="mcsmanager_get_instances")
    async def tool_mcsmanager_get_instances(
        self, event: AstrMessageEvent, panel_name: str = None
    ) -> str:
        """查看 MCSManager 实例列表。

        Args:
            panel_name(string, optional): 指定面板；不填则使用当前用户选择的面板。
        """
        has_permission, error_msg = self._check_read_only(
            event, "mcsmanager_get_instances"
        )
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        selected_panel = self._get_selected_panel(event, panel_name)
        return await self.mcsmanager_tools.get_instances(selected_panel)

    @filter.llm_tool(name="mcsmanager_start_instance")
    async def tool_mcsmanager_start(
        self, event: AstrMessageEvent, identifier: str, panel_name: str = None
    ) -> str:
        """启动 MCSManager 实例。

        Args:
            identifier(string): 实例名称、UUID 或列表序号。
            panel_name(string, optional): 实例所属面板。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "mcsmanager_start_instance"
        )
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        selected_panel = self._get_selected_panel(event, panel_name)
        return await self.mcsmanager_tools.start_instance(identifier, selected_panel)

    @filter.llm_tool(name="mcsmanager_stop_instance")
    async def tool_mcsmanager_stop(
        self, event: AstrMessageEvent, identifier: str, panel_name: str = None
    ) -> str:
        """停止 MCSManager 实例。

        Args:
            identifier(string): 实例名称、UUID 或列表序号。
            panel_name(string, optional): 实例所属面板。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "mcsmanager_stop_instance"
        )
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        selected_panel = self._get_selected_panel(event, panel_name)
        return await self.mcsmanager_tools.stop_instance(identifier, selected_panel)

    @filter.llm_tool(name="mcsmanager_restart_instance")
    async def tool_mcsmanager_restart(
        self, event: AstrMessageEvent, identifier: str, panel_name: str = None
    ) -> str:
        """重启 MCSManager 实例。

        Args:
            identifier(string): 实例名称、UUID 或列表序号。
            panel_name(string, optional): 实例所属面板。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "mcsmanager_restart_instance"
        )
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        selected_panel = self._get_selected_panel(event, panel_name)
        return await self.mcsmanager_tools.restart_instance(identifier, selected_panel)

    @filter.llm_tool(name="mcsmanager_send_command")
    async def tool_mcsmanager_cmd(
        self,
        event: AstrMessageEvent,
        identifier: str,
        command: str,
        panel_name: str = None,
    ) -> str:
        """向 MCSManager 实例控制台发送命令。

        Args:
            identifier(string): 实例名称、UUID 或列表序号。
            command(string): 要发送的控制台命令。
            panel_name(string, optional): 实例所属面板。
        """
        has_permission, error_msg = self._check_llm_write_permission(
            event, "mcsmanager_send_command"
        )
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        selected_panel = self._get_selected_panel(event, panel_name)
        return await self.mcsmanager_tools.send_command(
            identifier, command, selected_panel
        )

    @filter.llm_tool(name="mcsmanager_get_log")
    async def tool_mcsmanager_log(
        self,
        event: AstrMessageEvent,
        identifier: str,
        size: int = 100,
        panel_name: str = None,
    ) -> str:
        """获取 MCSManager 实例最近日志。

        Args:
            identifier(string): 实例名称、UUID 或列表序号。
            size(number): 最多返回的日志行数。
            panel_name(string, optional): 实例所属面板。
        """
        has_permission, error_msg = self._check_read_only(event, "mcsmanager_get_log")
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        selected_panel = self._get_selected_panel(event, panel_name)
        return await self.mcsmanager_tools.get_instance_log(
            identifier, size, selected_panel
        )

    @filter.llm_tool(name="mcsmanager_get_overview")
    async def tool_mcsmanager_overview(
        self, event: AstrMessageEvent, panel_name: str = None
    ) -> str:
        """查看 MCSManager 面板运行概览。

        Args:
            panel_name(string, optional): 指定面板；不填则使用当前用户选择的面板。
        """
        has_permission, error_msg = self._check_read_only(
            event, "mcsmanager_get_overview"
        )
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        selected_panel = self._get_selected_panel(event, panel_name)
        return await self.mcsmanager_tools.get_overview(selected_panel)

    @filter.llm_tool(name="mcsmanager_list_files")
    async def tool_mcsmanager_list_files(
        self,
        event: AstrMessageEvent,
        identifier: str,
        target: str = "",
        page: int = 1,
        page_size: int = 50,
        panel_name: str = None,
        file_name: str = "",
    ) -> str:
        """查看 MCSManager 实例目录中的文件和文件夹（只读）。

        Args:
            identifier(string): 实例名称、UUID 或列表序号。
            target(string, optional): 目录路径，留空表示实例根目录。
            page(number, optional): 页码，从 1 开始。
            page_size(number, optional): 每页条目数，最多 100。
            panel_name(string, optional): 实例所属面板。
            file_name(string, optional): 按文件名过滤。
        """
        has_permission, error_msg = self._check_read_only(
            event, "mcsmanager_list_files"
        )
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        selected_panel = self._get_selected_panel(event, panel_name)
        return await self.mcsmanager_tools.list_files(
            identifier, target, page, page_size, selected_panel, file_name
        )

    @filter.llm_tool(name="mcsmanager_read_file")
    async def tool_mcsmanager_read_file(
        self,
        event: AstrMessageEvent,
        identifier: str,
        target: str,
        max_chars: int = 12000,
        panel_name: str = None,
    ) -> str:
        """读取 MCSManager 实例中的文本文件，不提供写入或删除能力。

        Args:
            identifier(string): 实例名称、UUID 或列表序号。
            target(string): 文件路径，例如 /server.properties。
            max_chars(number, optional): 最多返回字符数，硬上限为 12000。
            panel_name(string, optional): 实例所属面板。
        """
        # File contents may expose server secrets, so require an administrator,
        # while deliberately keeping this operation outside LLM FULL mode.
        has_permission, error_msg = self._check_permission(
            event, "mcsmanager_read_file"
        )
        if not has_permission:
            return error_msg
        if not self.mcsmanager_tools:
            return "❌ MCSManager工具未初始化，请先启用MCSManager"
        selected_panel = self._get_selected_panel(event, panel_name)
        return await self.mcsmanager_tools.read_file(
            identifier, target, selected_panel, max_chars
        )
