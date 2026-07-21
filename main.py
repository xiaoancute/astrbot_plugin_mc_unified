import asyncio
from contextlib import suppress
from functools import partial
import os

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import AstrBotConfig, logger

from .backends.rcon_backend import RCONBackend
from .backends.mcsmanager_backend import MCSManagerMultiBackend
from .backends.websocket_backend import WebSocketMessageBackend
from .tools.mc_tools import MinecraftTools
from .tools.mcsmanager_tools import MCSManagerTools
from .managers.permission_manager import PermissionManager
from .managers.binding_manager import GroupBindingManager
from .managers.server_manager import ServerRegistry, build_server_profiles
from .utils.message_utils import MessageUtils


@register(
    "mc_unified",
    "AstrBot Community",
    "统一的Minecraft管理插件，支持RCON、WebSocket、MCSManager等多种管理方式，集成LLM自然语言管理和QQ↔MC消息互通",
    "1.1.0",
    "https://github.com/xiaoancute/astrbot_plugin_mc_unified",
)
class MCUnifiedPlugin(Star):
    """统一的Minecraft管理插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.context = context

        # 持久化数据存储于 AstrBot 的 data 目录下，而非插件自身目录
        astrbot_data_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self.data_dir = os.path.join(astrbot_data_dir, "mc_unified")
        os.makedirs(self.data_dir, exist_ok=True)

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
        self._init_managers()

        if not self.permission_manager.is_security_enabled():
            logger.warning(
                "[安全警告] 管理员列表为空，所有写操作将被禁用！请在配置中添加 admin_ids"
            )

        logger.info("MC Unified插件已加载")

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
                for panel in panels:
                    # template_list 格式: panel_name 字段对应原 name
                    name = panel.get("panel_name", "") or panel.get("name", "")
                    url = panel.get("url", "")
                    api_key = panel.get("api_key", "")
                    if name and url and api_key:
                        self.mcsmanager_multi_backend.add_backend(name, url, api_key)
                logger.info(f"MCSManager多面板后端已初始化，共 {len(panels)} 个面板")

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

    def _init_managers(self):
        admin_ids = self.config.get("admin_ids", [])
        self.permission_manager = PermissionManager(admin_ids)
        self.binding_manager = GroupBindingManager(self.data_dir)

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

    async def _send_to_qq_group(self, group_id: str, message: str):
        try:
            from astrbot.core.star.star_tools import StarTools
            from astrbot.core.message.components import Plain

            message_chain = await StarTools.create_message(
                type="GroupMessage",
                self_id="astrbot_mc_plugin",
                session_id=f"aiocqhttp_default:GroupMessage:{group_id}",
                sender=None,
                message=[Plain(message)],
                message_str=message,
                group_id=group_id,
            )
            await self.context.send_message(
                f"aiocqhttp_default:GroupMessage:{group_id}", message_chain
            )
        except Exception as e:
            logger.error(f"发送消息到QQ群失败: {e}")

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

    def _resolve_server_id(
        self, event: AstrMessageEvent, requested: str = ""
    ) -> tuple[str | None, str]:
        user_id = str(event.get_sender_id() or "")
        group_id = event.get_group_id()
        return self.server_registry.resolve_id(
            requested=requested,
            selected=self._selected_servers.get(user_id),
            bound_server_ids=self._get_group_server_ids(group_id),
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
                flags.append("本群已绑定")
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

    def _get_selected_panel(
        self, event: AstrMessageEvent, panel_name: str = None
    ) -> str | None:
        if panel_name:
            return panel_name
        return self._selected_mcsmanager_panels.get(str(event.get_sender_id()))

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response):
        sender_id = event.get_sender_id()
        if sender_id and sender_id.startswith("mc_player_"):
            if self.config.get("enable_chat_response", True):
                response_text = str(response) if response else ""
                if response_text and response_text.strip() not in ["*No response*", ""]:
                    server_id, _ = self._resolve_server_id(event)
                    if server_id:
                        await self._send_to_mc(server_id, response_text)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_qq_group_message(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            return

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

    @filter.command("mc servers")
    async def cmd_mc_servers(self, event: AstrMessageEvent):
        has_permission, error_msg = self._check_read_only(event, "mc_servers")
        if not has_permission:
            yield event.plain_result(error_msg)
            return
        yield event.plain_result(self._format_server_list(event))

    @filter.command("mc use")
    async def cmd_mc_use(self, event: AstrMessageEvent, server_name: str):
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

    @filter.command("mc bindings")
    async def cmd_mc_bindings(self, event: AstrMessageEvent):
        has_permission, error_msg = self._check_read_only(event, "mc_bindings")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 请在QQ群中使用此命令")
            return

        server_ids = self._get_group_server_ids(group_id)
        if not server_ids:
            yield event.plain_result("ℹ️ 当前群尚未绑定任何服务器")
            return

        labels = [
            f"{self.server_registry.get(server_id).label} ({server_id})"
            for server_id in server_ids
        ]
        yield event.plain_result("🔗 当前群已绑定:\n- " + "\n- ".join(labels))

    @filter.command("mc bind")
    async def cmd_mc_bind(self, event: AstrMessageEvent, server_name: str = ""):
        has_permission, error_msg = self._check_permission(event, "mc_bind")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 请在QQ群中使用此命令")
            return

        server_id, resolve_error = self._resolve_server_id(event, server_name)
        if not server_id:
            yield event.plain_result(resolve_error)
            return

        profile = self.server_registry.get(server_id)
        success = self.binding_manager.bind_group(group_id, server_id)
        if success:
            yield event.plain_result(
                f"✅ 已将当前群绑定到 {profile.label} ({server_id})"
            )
        elif self.binding_manager.last_error:
            yield event.plain_result(f"❌ {self.binding_manager.last_error}")
        else:
            yield event.plain_result(f"⚠️ 当前群已绑定 {profile.label}")

    @filter.command("mc unbind")
    async def cmd_mc_unbind(self, event: AstrMessageEvent, server_name: str = ""):
        has_permission, error_msg = self._check_permission(event, "mc_unbind")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 请在QQ群中使用此命令")
            return

        if server_name.casefold() == "all":
            success = self.binding_manager.unbind_group_from_all(group_id)
            target_label = "全部服务器"
        else:
            bound_ids = self._get_group_server_ids(group_id)
            if not server_name and len(bound_ids) > 1:
                yield event.plain_result(
                    "❌ 当前群绑定了多个服务器，请使用 mc unbind <服务器ID> 或 mc unbind all"
                )
                return
            requested = server_name or (bound_ids[0] if bound_ids else "")
            server_id, resolve_error = self._resolve_server_id(event, requested)
            if not server_id:
                yield event.plain_result(resolve_error)
                return
            profile = self.server_registry.get(server_id)
            success = self.binding_manager.unbind_group(group_id, server_id)
            if (
                not success
                and server_id == self.server_registry.default_server_id
                and server_id != "default"
            ):
                success = self.binding_manager.unbind_group(group_id, "default")
            target_label = profile.label

        if success:
            yield event.plain_result(f"✅ 已解除当前群与{target_label}的绑定")
        elif self.binding_manager.last_error:
            yield event.plain_result(f"❌ {self.binding_manager.last_error}")
        else:
            yield event.plain_result(f"⚠️ 当前群未绑定{target_label}")

    @filter.command("mc test")
    async def cmd_mc_test(self, event: AstrMessageEvent, server_name: str = ""):
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

    @filter.command("mc log")
    async def cmd_mc_log(self, event: AstrMessageEvent):
        has_permission, error_msg = self._check_permission(event, "mc_log")
        if not has_permission:
            yield event.plain_result(error_msg)
            return

        log = self.permission_manager.get_action_log()
        yield event.plain_result(log)

    @filter.command("mc security")
    async def cmd_mc_security(self, event: AstrMessageEvent):
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

        if not security_enabled:
            result += "\n⚠️ 警告：管理员列表为空，所有写操作已禁用！"

        yield event.plain_result(result)

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

    @filter.llm_tool(name="minecraft_select_server")
    async def tool_minecraft_select_server(
        self, event: AstrMessageEvent, server_name: str
    ) -> str:
        """选择后续 Minecraft 管理工具操作的服务器。

        Args:
            server_name(string): 配置中的服务器ID或显示名称。
        """
        has_permission, error_msg = self._check_permission(
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
    async def tool_list_players(self, event: AstrMessageEvent) -> str:
        """查看 Minecraft 服务器当前在线玩家列表。"""
        has_permission, error_msg = self._check_read_only(event, "list_players")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.list_players()

    @filter.llm_tool(name="kick_player")
    async def tool_kick_player(
        self, event: AstrMessageEvent, player: str, reason: str = "被管理员踢出"
    ) -> str:
        """踢出指定玩家。

        Args:
            player(string): 玩家名称。
            reason(string): 踢出原因。
        """
        has_permission, error_msg = self._check_permission(event, "kick_player")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.kick_player(player, reason)

    @filter.llm_tool(name="ban_player")
    async def tool_ban_player(
        self, event: AstrMessageEvent, player: str, reason: str = "违反服务器规则"
    ) -> str:
        """封禁指定玩家。

        Args:
            player(string): 玩家名称。
            reason(string): 封禁原因。
        """
        has_permission, error_msg = self._check_permission(event, "ban_player")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.ban_player(player, reason)

    @filter.llm_tool(name="pardon_player")
    async def tool_pardon_player(self, event: AstrMessageEvent, player: str) -> str:
        """解除玩家封禁。

        Args:
            player(string): 玩家名称。
        """
        has_permission, error_msg = self._check_permission(event, "pardon_player")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.pardon_player(player)

    @filter.llm_tool(name="op_player")
    async def tool_op_player(self, event: AstrMessageEvent, player: str) -> str:
        """授予玩家 OP 权限。

        Args:
            player(string): 玩家名称。
        """
        has_permission, error_msg = self._check_permission(event, "op_player")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.op_player(player)

    @filter.llm_tool(name="deop_player")
    async def tool_deop_player(self, event: AstrMessageEvent, player: str) -> str:
        """移除玩家 OP 权限。

        Args:
            player(string): 玩家名称。
        """
        has_permission, error_msg = self._check_permission(event, "deop_player")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.deop_player(player)

    @filter.llm_tool(name="whitelist_add")
    async def tool_whitelist_add(self, event: AstrMessageEvent, player: str) -> str:
        """将玩家加入服务器白名单。

        Args:
            player(string): 玩家名称。
        """
        has_permission, error_msg = self._check_permission(event, "whitelist_add")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.whitelist_add(player)

    @filter.llm_tool(name="whitelist_remove")
    async def tool_whitelist_remove(self, event: AstrMessageEvent, player: str) -> str:
        """将玩家移出服务器白名单。

        Args:
            player(string): 玩家名称。
        """
        has_permission, error_msg = self._check_permission(event, "whitelist_remove")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.whitelist_remove(player)

    @filter.llm_tool(name="whitelist_list")
    async def tool_whitelist_list(self, event: AstrMessageEvent) -> str:
        """查看服务器白名单。"""
        has_permission, error_msg = self._check_read_only(event, "whitelist_list")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.whitelist_list()

    @filter.llm_tool(name="banlist")
    async def tool_banlist(
        self, event: AstrMessageEvent, ban_type: str = "players"
    ) -> str:
        """查看玩家或 IP 封禁列表。

        Args:
            ban_type(string): 列表类型，使用 players 或 ips。
        """
        has_permission, error_msg = self._check_read_only(event, "banlist")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.banlist(ban_type)

    @filter.llm_tool(name="give_item")
    async def tool_give_item(
        self, event: AstrMessageEvent, player: str, item: str, count: int = 1
    ) -> str:
        """给予玩家物品。

        Args:
            player(string): 玩家名称或目标选择器。
            item(string): 物品 ID，例如 minecraft:diamond。
            count(number): 给予数量。
        """
        has_permission, error_msg = self._check_permission(event, "give_item")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.give_item(player, item, count)

    @filter.llm_tool(name="teleport_player")
    async def tool_teleport_player(
        self, event: AstrMessageEvent, player: str, target: str
    ) -> str:
        """传送玩家到坐标或其他玩家。

        Args:
            player(string): 要传送的玩家名称。
            target(string): 目标玩家或坐标，例如 100 64 200。
        """
        has_permission, error_msg = self._check_permission(event, "teleport_player")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.teleport_player(player, target)

    @filter.llm_tool(name="set_gamemode")
    async def tool_set_gamemode(
        self, event: AstrMessageEvent, player: str, mode: str
    ) -> str:
        """设置玩家游戏模式。

        Args:
            player(string): 玩家名称。
            mode(string): survival、creative、adventure 或 spectator。
        """
        has_permission, error_msg = self._check_permission(event, "set_gamemode")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.set_gamemode(player, mode)

    @filter.llm_tool(name="say_message")
    async def tool_say_message(self, event: AstrMessageEvent, message: str) -> str:
        """向服务器内所有玩家广播消息。

        Args:
            message(string): 广播内容。
        """
        has_permission, error_msg = self._check_permission(event, "say_message")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.say_message(message)

    @filter.llm_tool(name="execute_command")
    async def tool_execute_command(self, event: AstrMessageEvent, command: str) -> str:
        """通过 RCON 执行自定义 Minecraft 命令。

        Args:
            command(string): 不带或带斜杠的服务器命令。
        """
        has_permission, error_msg = self._check_permission(event, "execute_command")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.execute_command(command)

    @filter.llm_tool(name="set_weather")
    async def tool_set_weather(
        self, event: AstrMessageEvent, weather_type: str, duration: int = None
    ) -> str:
        """设置服务器天气。

        Args:
            weather_type(string): clear、rain 或 thunder。
            duration(number, optional): 持续秒数。
        """
        has_permission, error_msg = self._check_permission(event, "set_weather")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.set_weather(weather_type, duration)

    @filter.llm_tool(name="set_time")
    async def tool_set_time(self, event: AstrMessageEvent, time_value: str) -> str:
        """设置服务器时间。

        Args:
            time_value(string): day、night、noon、midnight 或刻数。
        """
        has_permission, error_msg = self._check_permission(event, "set_time")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.set_time(time_value)

    @filter.llm_tool(name="set_difficulty")
    async def tool_set_difficulty(
        self, event: AstrMessageEvent, difficulty: str
    ) -> str:
        """设置服务器难度。

        Args:
            difficulty(string): peaceful、easy、normal 或 hard。
        """
        has_permission, error_msg = self._check_permission(event, "set_difficulty")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
        if not mc_tools:
            return error
        return await mc_tools.set_difficulty(difficulty)

    @filter.llm_tool(name="set_gamerule")
    async def tool_set_gamerule(
        self, event: AstrMessageEvent, rule: str, value: str
    ) -> str:
        """修改 Minecraft 游戏规则。

        Args:
            rule(string): 游戏规则名称，例如 keepInventory。
            value(string): 规则值。
        """
        has_permission, error_msg = self._check_permission(event, "set_gamerule")
        if not has_permission:
            return error_msg
        mc_tools, error, _ = self._get_mc_tools(event)
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
        has_permission, error_msg = self._check_permission(
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
        has_permission, error_msg = self._check_permission(
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
        has_permission, error_msg = self._check_permission(
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
        has_permission, error_msg = self._check_permission(
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
        has_permission, error_msg = self._check_permission(
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
        has_permission, error_msg = self._check_permission(event, "mcsmanager_get_log")
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
