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

    await plugin.initialize()
    await plugin.terminate()
    print("AstrBot plugin load smoke passed")


if __name__ == "__main__":
    asyncio.run(main())
