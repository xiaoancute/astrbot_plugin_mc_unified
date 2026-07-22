import asyncio
import importlib
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "astrbot_plugin_mc_unified"


class DummyContext:
    async def send_message(self, *_args, **_kwargs):
        return True


async def main() -> None:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE_NAME] = package

    plugin_module = importlib.import_module(f"{PACKAGE_NAME}.main")
    plugin = plugin_module.MCUnifiedPlugin(DummyContext(), {})

    assert plugin.rcon_backend is None
    assert plugin.mcsmanager_multi_backend is None
    assert plugin.websocket_backend is None
    assert len(plugin.permission_manager.admin_ids) == 0
    assert not plugin.permission_manager.is_llm_full_access()

    await plugin.initialize()
    await plugin.terminate()

    multi_plugin = plugin_module.MCUnifiedPlugin(
        DummyContext(),
        {
            "default_server": "creative",
            "mc_servers": [
                {
                    "server_id": "survival",
                    "display_name": "Survival",
                    "rcon": {
                        "enabled": True,
                        "host": "127.0.0.1",
                        "port": 25575,
                        "password": "smoke-only",
                    },
                },
                {
                    "server_id": "creative",
                    "display_name": "Creative",
                    "message": {
                        "sync_chat_qq_to_mc": True,
                        "forward_llm_responses_to_mc": True,
                    },
                },
            ],
            "qq_group_bindings": [
                {
                    "group_id": "10001",
                    "server_ids": ["survival", "creative"],
                },
                {"group_id": "10002", "server_ids": ["survival"]},
            ],
        },
    )
    assert set(multi_plugin.server_registry.profiles) == {"survival", "creative"}
    assert multi_plugin.server_registry.default_server_id == "creative"
    assert multi_plugin.server_registry.get("survival").mc_tools is not None
    assert multi_plugin.server_registry.get("creative").forward_llm_responses_to_mc
    assert multi_plugin.rcon_backend is None
    assert multi_plugin.binding_manager.get_group_servers("10001") == [
        "survival",
        "creative",
    ]
    assert multi_plugin.binding_manager.get_bound_groups("survival") == [
        "10001",
        "10002",
    ]

    await multi_plugin.initialize()
    await multi_plugin.terminate()

    panel_plugin = plugin_module.MCUnifiedPlugin(
        DummyContext(),
        {
            "mcsmanager_enabled": True,
            "mcsmanager_panels": [
                {
                    "panel_name": "primary",
                    "url": "http://127.0.0.1:23333",
                    "api_key": "smoke-only",
                },
                {
                    "panel_name": "primary",
                    "url": "http://127.0.0.1:24444",
                    "api_key": "duplicate-smoke-only",
                },
                {"panel_name": "incomplete"},
                "invalid",
            ],
        },
    )
    assert panel_plugin.mcsmanager_multi_backend.get_backend_names() == ["primary"]
    await panel_plugin.initialize()
    await panel_plugin.terminate()
    print("AstrBot plugin load smoke passed")


if __name__ == "__main__":
    asyncio.run(main())
