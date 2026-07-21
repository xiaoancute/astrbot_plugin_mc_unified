import ast
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _MessageChain:
    def __init__(self):
        self.messages = []

    def message(self, value):
        self.messages.append(value)
        return self


def _identity_decorator(*_args, **_kwargs):
    def decorator(function):
        return function

    return decorator


def _command_group(*_args, **_kwargs):
    def decorator(function):
        function.command = _identity_decorator
        return function

    return decorator


astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_api_module.logger = _Logger()
astrbot_api_module.AstrBotConfig = dict
astrbot_module.api = astrbot_api_module
sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)
astrbot_module = sys.modules["astrbot"]
astrbot_api_module = sys.modules["astrbot.api"]
astrbot_api_module.logger = getattr(astrbot_api_module, "logger", _Logger())
astrbot_api_module.AstrBotConfig = dict
astrbot_module.api = astrbot_api_module

astrbot_event_module = types.ModuleType("astrbot.api.event")
astrbot_event_module.AstrMessageEvent = object
astrbot_event_module.MessageChain = _MessageChain
astrbot_event_module.filter = types.SimpleNamespace(
    command_group=_command_group,
    command=_identity_decorator,
    llm_tool=_identity_decorator,
    on_llm_response=_identity_decorator,
    platform_adapter_type=_identity_decorator,
    event_message_type=_identity_decorator,
    PlatformAdapterType=types.SimpleNamespace(AIOCQHTTP="aiocqhttp"),
    EventMessageType=types.SimpleNamespace(GROUP_MESSAGE="group"),
)
sys.modules.setdefault("astrbot.api.event", astrbot_event_module)

astrbot_star_module = types.ModuleType("astrbot.api.star")
astrbot_star_module.Context = object
astrbot_star_module.Star = object
sys.modules.setdefault("astrbot.api.star", astrbot_star_module)

astrbot_core_module = types.ModuleType("astrbot.core")
astrbot_core_utils_module = types.ModuleType("astrbot.core.utils")
astrbot_path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
astrbot_path_module.get_astrbot_data_path = lambda: tempfile.gettempdir()
sys.modules.setdefault("astrbot.core", astrbot_core_module)
sys.modules.setdefault("astrbot.core.utils", astrbot_core_utils_module)
sys.modules.setdefault("astrbot.core.utils.astrbot_path", astrbot_path_module)

aiomcrcon_module = types.ModuleType("aiomcrcon")
aiomcrcon_module.Client = object
sys.modules.setdefault("aiomcrcon", aiomcrcon_module)

from backends.rcon_backend import RCONBackend  # noqa: E402
from backends.websocket_backend import WebSocketMessageBackend  # noqa: E402
from managers.binding_manager import GroupBindingManager  # noqa: E402
from managers.permission_manager import PermissionManager  # noqa: E402
from managers.server_manager import (  # noqa: E402
    ServerProfile,
    ServerRegistry,
    build_server_profiles,
)
from tools.mc_tools import MinecraftTools  # noqa: E402
from tools.mcsmanager_tools import MCSManagerTools  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "astrbot_plugin_mc_unified"
plugin_package = types.ModuleType(PACKAGE_NAME)
plugin_package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE_NAME, plugin_package)
MCUnifiedPlugin = importlib.import_module(f"{PACKAGE_NAME}.main").MCUnifiedPlugin


class _FakeEvent:
    def __init__(self, sender_id="admin", group_id="", umo=""):
        self.sender_id = sender_id
        self.group_id = group_id
        self.unified_msg_origin = umo

    def get_sender_id(self):
        return self.sender_id

    def get_group_id(self):
        return self.group_id

    def plain_result(self, message):
        return message


class _FakeRcon:
    def __init__(self):
        self.commands = []

    async def execute_command(self, command):
        self.commands.append(command)
        return "ok"


class CommandSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_dangerous_command_variants_are_blocked(self):
        rcon = _FakeRcon()
        tools = MinecraftTools(rcon)

        commands = (
            "stop",
            "/stop",
            "minecraft:stop",
            "/minecraft:reload",
            "execute as @a run stop",
            "execute as @a run /minecraft:stop",
        )
        for command in commands:
            with self.subTest(command=command):
                result = await tools.execute_command(command)
                self.assertIn("危险命令", result)

        self.assertEqual(rcon.commands, [])

    async def test_safe_command_is_forwarded(self):
        rcon = _FakeRcon()
        tools = MinecraftTools(rcon)

        result = await tools.execute_command("say stop")

        self.assertIn("ok", result)
        self.assertEqual(rcon.commands, ["say stop"])


class RconResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_checked_execution_reports_success(self):
        client = types.SimpleNamespace(
            send_cmd=AsyncMock(
                return_value=("There are 1 of 20 players online: Steve", 1)
            )
        )
        backend = RCONBackend("localhost", 25575, "secret")
        backend._ensure_connection = AsyncMock(return_value=client)

        success, message = await backend.execute_command_checked("list")

        self.assertTrue(success)
        self.assertEqual(message, "There are 1 of 20 players online: Steve")

    async def test_online_players_parse_real_client_response(self):
        client = types.SimpleNamespace(
            send_cmd=AsyncMock(
                return_value=("There are 2 of 20 players online: Alex, Steve", 1)
            )
        )
        backend = RCONBackend("localhost", 25575, "secret")
        backend._ensure_connection = AsyncMock(return_value=client)

        players = await backend.get_online_players()

        self.assertEqual(players, ["Alex", "Steve"])

    async def test_checked_execution_reports_failure_and_reconnects(self):
        client = types.SimpleNamespace(
            send_cmd=AsyncMock(side_effect=RuntimeError("bad"))
        )
        backend = RCONBackend("localhost", 25575, "secret")
        backend._ensure_connection = AsyncMock(return_value=client)
        backend._reconnect = AsyncMock()

        success, message = await backend.execute_command_checked("list")

        self.assertFalse(success)
        self.assertIn("bad", message)
        backend._reconnect.assert_awaited_once()


class PermissionTests(unittest.TestCase):
    def test_successful_and_failed_checks_are_logged(self):
        manager = PermissionManager([12345])

        self.assertTrue(manager.check_permission("12345", "kick")[0])
        self.assertFalse(manager.check_permission("guest", "kick")[0])

        self.assertEqual(
            [entry["success"] for entry in manager.action_log], [True, False]
        )

    def test_llm_defaults_to_readonly_and_denies_admin_writes(self):
        manager = PermissionManager(["admin"])

        allowed, message = manager.check_llm_write_permission("admin", "kick")

        self.assertFalse(allowed)
        self.assertIn("只读", message)
        self.assertEqual(manager.llm_permission_mode, PermissionManager.READONLY_MODE)

    def test_full_mode_still_requires_an_administrator(self):
        manager = PermissionManager(["admin"], "full")

        self.assertTrue(manager.check_llm_write_permission("admin", "kick")[0])
        self.assertFalse(manager.check_llm_write_permission("guest", "kick")[0])

    def test_llm_mode_normalization_is_fail_closed(self):
        manager = PermissionManager([], "FULL")

        self.assertEqual(manager.llm_permission_mode, PermissionManager.FULL_MODE)
        self.assertEqual(
            manager.set_llm_permission_mode("read-only"),
            PermissionManager.READONLY_MODE,
        )
        self.assertEqual(
            manager.set_llm_permission_mode("unexpected"),
            PermissionManager.READONLY_MODE,
        )


class PermissionToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_natural_language_request_cannot_enable_full_access(self):
        plugin = object.__new__(MCUnifiedPlugin)
        plugin.permission_manager = PermissionManager(["admin"])

        message = await plugin.tool_minecraft_request_full_access(_FakeEvent())

        self.assertIn("不会开启权限", message)
        self.assertFalse(plugin.permission_manager.is_llm_full_access())

    async def test_readonly_write_tool_never_reaches_backend_resolution(self):
        plugin = object.__new__(MCUnifiedPlugin)
        plugin.permission_manager = PermissionManager(["admin"])
        plugin._get_mc_tools = Mock(
            side_effect=AssertionError("backend resolution must not run")
        )

        message = await plugin.tool_kick_player(
            _FakeEvent(), "Steve", server_name="survival"
        )

        self.assertIn("只读", message)
        plugin._get_mc_tools.assert_not_called()

    async def test_manual_command_requires_exact_confirmation(self):
        plugin = object.__new__(MCUnifiedPlugin)
        plugin.permission_manager = PermissionManager(["admin"])
        plugin.config = {}
        event = _FakeEvent()

        rejected = [
            value async for value in plugin.cmd_mc_ai_mode(event, "full", "confirm")
        ]
        self.assertIn("CONFIRM", rejected[0])
        self.assertFalse(plugin.permission_manager.is_llm_full_access())

        accepted = [
            value async for value in plugin.cmd_mc_ai_mode(event, "full", "CONFIRM")
        ]
        self.assertIn("FULL", accepted[0])
        self.assertTrue(plugin.permission_manager.is_llm_full_access())


class BindingTests(unittest.TestCase):
    def test_binding_is_persisted_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = GroupBindingManager(temp_dir)

            self.assertTrue(manager.bind_group(123456))

            binding_file = Path(temp_dir) / "bindings.json"
            self.assertTrue(binding_file.exists())
            self.assertIn("123456", binding_file.read_text(encoding="utf-8"))

    def test_failed_save_rolls_back_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = GroupBindingManager(temp_dir)
            with patch.object(manager, "_save_bindings", return_value=False):
                self.assertFalse(manager.bind_group("123456"))

            self.assertEqual(manager.get_bound_groups(), [])
            self.assertTrue(manager.last_error)

    def test_multiple_server_bindings_and_unbind_all(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = GroupBindingManager(temp_dir)

            self.assertTrue(manager.bind_group("123456", "survival"))
            self.assertTrue(manager.bind_group("123456", "creative"))
            self.assertEqual(
                manager.get_group_servers("123456"), ["survival", "creative"]
            )

            self.assertTrue(manager.unbind_group_from_all("123456"))
            self.assertEqual(manager.get_group_servers("123456"), [])

    def test_group_session_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = GroupBindingManager(temp_dir)

            self.assertTrue(manager.remember_group_session("123456", "real:umo"))

            reloaded = GroupBindingManager(temp_dir)
            self.assertEqual(reloaded.get_group_session("123456"), "real:umo")

    def test_failed_group_session_save_rolls_back_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = GroupBindingManager(temp_dir)
            with patch.object(manager, "_save_group_sessions", return_value=False):
                self.assertFalse(
                    manager.remember_group_session("123456", "unsaved:umo")
                )

            self.assertEqual(manager.get_group_session("123456"), "")


class ServerProfileTests(unittest.TestCase):
    def test_legacy_configuration_becomes_default_profile(self):
        profiles = build_server_profiles(
            {
                "rcon_enabled": True,
                "rcon_host": "legacy.example",
                "rcon_port": 25580,
                "rcon_password": "secret",
                "sync_chat_qq_to_mc": True,
            }
        )

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].server_id, "default")
        self.assertEqual(profiles[0].rcon_host, "legacy.example")
        self.assertEqual(profiles[0].rcon_port, 25580)
        self.assertTrue(profiles[0].sync_chat_qq_to_mc)

    def test_multiple_profiles_keep_independent_options(self):
        profiles = build_server_profiles(
            {
                "mc_servers": [
                    {
                        "server_id": "survival",
                        "display_name": "生存服",
                        "rcon": {
                            "enabled": True,
                            "host": "survival.example",
                            "port": 25575,
                            "password": "one",
                        },
                        "message": {
                            "sync_chat_mc_to_qq": True,
                            "forward_llm_responses_to_mc": True,
                            "mc_message_prefix": "[{server}]",
                        },
                    },
                    {
                        "server_id": "creative",
                        "display_name": "创造服",
                        "websocket": {
                            "enabled": True,
                            "url": "ws://creative.example/ws",
                        },
                        "message": {"sync_chat_qq_to_mc": True},
                    },
                    {"server_id": "disabled", "enabled": False},
                ]
            }
        )

        self.assertEqual(
            [profile.server_id for profile in profiles], ["survival", "creative"]
        )
        self.assertTrue(profiles[0].rcon_enabled)
        self.assertTrue(profiles[0].sync_chat_mc_to_qq)
        self.assertTrue(profiles[0].forward_llm_responses_to_mc)
        self.assertTrue(profiles[1].websocket_enabled)
        self.assertTrue(profiles[1].sync_chat_qq_to_mc)
        self.assertFalse(profiles[1].forward_llm_responses_to_mc)

    def test_registry_uses_explicit_selected_then_default_order(self):
        registry = ServerRegistry("survival")
        registry.add(ServerProfile("survival", "生存服"))
        registry.add(ServerProfile("creative", "创造服"))
        registry.finalize_default()

        server_id, error = registry.resolve_id()
        self.assertEqual(server_id, "survival")
        self.assertEqual(error, "")

        server_id, error = registry.resolve_id(selected="creative")
        self.assertEqual(server_id, "creative")
        self.assertEqual(error, "")

        server_id, error = registry.resolve_id(
            requested="survival", selected="creative"
        )
        self.assertEqual(server_id, "survival")
        self.assertEqual(error, "")

    def test_legacy_default_binding_maps_to_configured_default(self):
        registry = ServerRegistry("survival")
        registry.add(ServerProfile("survival", "生存服"))
        registry.add(ServerProfile("creative", "创造服"))
        registry.finalize_default()

        self.assertEqual(registry.normalize_bound_ids(["default"]), ["survival"])


class ToolTargetingTests(unittest.TestCase):
    def test_minecraft_management_tools_accept_explicit_server_name(self):
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        module = ast.parse(main_path.read_text(encoding="utf-8"))
        exempt = {
            "tool_minecraft_get_servers",
            "tool_minecraft_select_server",
            "tool_minecraft_get_ai_permission",
            "tool_minecraft_request_full_access",
        }

        missing = []
        for node in module.body:
            if not isinstance(node, ast.ClassDef) or node.name != "MCUnifiedPlugin":
                continue
            for function in node.body:
                if not isinstance(function, ast.AsyncFunctionDef):
                    continue
                decorators = [ast.unparse(value) for value in function.decorator_list]
                is_minecraft_tool = any(
                    "filter.llm_tool" in value and "mcsmanager_" not in value
                    for value in decorators
                )
                if not is_minecraft_tool or function.name in exempt:
                    continue
                argument_names = [argument.arg for argument in function.args.args]
                if "server_name" not in argument_names:
                    missing.append(function.name)

        self.assertEqual(missing, [])

    def test_all_llm_tools_use_the_expected_permission_gate(self):
        module = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        read_only = {
            "tool_minecraft_get_servers",
            "tool_minecraft_get_status",
            "tool_minecraft_get_ai_permission",
            "tool_minecraft_request_full_access",
            "tool_minecraft_select_server",
            "tool_list_players",
            "tool_whitelist_list",
            "tool_banlist",
            "tool_mcsmanager_get_panels",
            "tool_mcsmanager_select_panel",
            "tool_mcsmanager_get_instances",
            "tool_mcsmanager_log",
            "tool_mcsmanager_overview",
        }
        llm_functions = {}
        for node in ast.walk(module):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            decorators = [ast.unparse(value) for value in node.decorator_list]
            if any("filter.llm_tool" in value for value in decorators):
                llm_functions[node.name] = node

        self.assertEqual(set(llm_functions) & read_only, read_only)
        for name, function in llm_functions.items():
            calls = {
                ast.unparse(node.func)
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
            }
            expected = (
                "self._check_read_only"
                if name in read_only
                else "self._check_llm_write_permission"
            )
            self.assertIn(expected, calls, name)
            self.assertNotIn("self._set_llm_permission_mode", calls, name)

    def test_official_command_group_and_metadata_discovery_are_used(self):
        module = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        decorators = [
            ast.unparse(value)
            for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for value in node.decorator_list
        ]

        self.assertEqual(decorators.count("filter.command_group('mc')"), 1)
        self.assertFalse(
            any(value.startswith("filter.command(") for value in decorators)
        )
        self.assertNotIn(
            "register",
            {node.id for node in ast.walk(module) if isinstance(node, ast.Name)},
        )


class PluginMessagingTests(unittest.IsolatedAsyncioTestCase):
    async def test_proactive_group_send_uses_saved_umo_and_message_chain(self):
        plugin = object.__new__(MCUnifiedPlugin)
        plugin.binding_manager = types.SimpleNamespace(
            get_group_session=lambda _group_id: "real:group:umo"
        )
        plugin.context = types.SimpleNamespace(
            send_message=AsyncMock(return_value=True)
        )

        sent = await plugin._send_to_qq_group("123456", "hello")

        self.assertTrue(sent)
        umo, chain = plugin.context.send_message.await_args.args
        self.assertEqual(umo, "real:group:umo")
        self.assertIsInstance(chain, _MessageChain)
        self.assertEqual(chain.messages, ["hello"])

    async def test_llm_response_uses_completion_text_and_opt_in_routing(self):
        plugin = object.__new__(MCUnifiedPlugin)
        remembered = []
        plugin.binding_manager = types.SimpleNamespace(
            remember_group_session=lambda group_id, umo: remembered.append(
                (group_id, umo)
            )
        )
        plugin._get_group_server_ids = lambda _group_id: ["enabled", "disabled"]
        profiles = {
            "enabled": types.SimpleNamespace(forward_llm_responses_to_mc=True),
            "disabled": types.SimpleNamespace(forward_llm_responses_to_mc=False),
        }
        plugin.server_registry = types.SimpleNamespace(get=profiles.get)
        plugin._send_to_mc = AsyncMock()
        event = _FakeEvent(group_id="123456", umo="real:group:umo")
        response = types.SimpleNamespace(
            completion_text="final answer",
            is_chunk=False,
        )

        await plugin.on_llm_response(event, response)

        self.assertEqual(remembered, [("123456", "real:group:umo")])
        plugin._send_to_mc.assert_awaited_once_with("enabled", "AI: final answer")

        plugin._send_to_mc.reset_mock()
        await plugin.on_llm_response(
            event,
            types.SimpleNamespace(completion_text="partial", is_chunk=True),
        )
        plugin._send_to_mc.assert_not_awaited()


class _FakePanel:
    def __init__(self, name, instances):
        self.name = name
        self.instances = instances
        self.stopped = []

    async def get_instances(self):
        return list(self.instances)

    async def stop_instance(self, daemon_id, instance_uuid):
        self.stopped.append((daemon_id, instance_uuid))
        return True


class _FakeMultiBackend:
    def __init__(self, panels):
        self.panels = {panel.name: panel for panel in panels}

    def get_backend_names(self):
        return list(self.panels)

    def get_backend(self, name):
        return self.panels.get(name)

    def get_all_backends(self):
        return list(self.panels.values())

    async def get_all_instances(self):
        instances = []
        for panel in self.panels.values():
            instances.extend(await panel.get_instances())
        return instances


def _instance(name, uuid, panel):
    return {
        "name": name,
        "uuid": uuid,
        "daemon_id": f"daemon-{panel}",
        "status": 3,
        "node_name": f"node-{panel}",
        "panel_name": panel,
    }


class MCSManagerTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_names_require_panel_or_uuid(self):
        primary = _FakePanel("primary", [_instance("survival", "uuid-1", "primary")])
        backup = _FakePanel("backup", [_instance("survival", "uuid-2", "backup")])
        tools = MCSManagerTools(_FakeMultiBackend([primary, backup]))

        result = await tools.stop_instance("survival")

        self.assertIn("多个面板", result)
        self.assertEqual(primary.stopped, [])
        self.assertEqual(backup.stopped, [])

    async def test_explicit_panel_targets_only_that_panel(self):
        primary = _FakePanel("primary", [_instance("survival", "uuid-1", "primary")])
        backup = _FakePanel("backup", [_instance("survival", "uuid-2", "backup")])
        tools = MCSManagerTools(_FakeMultiBackend([primary, backup]))

        result = await tools.stop_instance("survival", "backup")

        self.assertIn("backup", result)
        self.assertEqual(primary.stopped, [])
        self.assertEqual(backup.stopped, [("daemon-backup", "uuid-2")])


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    def test_connect_kwargs_match_installed_api(self):
        backend = WebSocketMessageBackend("ws://localhost", token="token")

        kwargs = backend._connect_kwargs()

        self.assertTrue({"extra_headers", "additional_headers"} & kwargs.keys())
        self.assertFalse({"extra_headers", "additional_headers"} <= kwargs.keys())

    async def test_unknown_errors_obey_retry_limit(self):
        backend = WebSocketMessageBackend("ws://localhost", max_retries=1)

        should_continue = await backend._handle_connection_error(TypeError("bad api"))

        self.assertFalse(should_continue)
        self.assertFalse(backend.should_reconnect)


if __name__ == "__main__":
    unittest.main()
