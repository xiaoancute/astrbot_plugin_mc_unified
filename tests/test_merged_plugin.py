import ast
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_api_module.logger = _Logger()
astrbot_module.api = astrbot_api_module
sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)

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
        self.assertTrue(profiles[1].websocket_enabled)
        self.assertTrue(profiles[1].sync_chat_qq_to_mc)

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
