import asyncio
import os
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_api_module.logger = _Logger()
astrbot_module.api = astrbot_api_module
sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)

from backends.rcon_backend import RCONBackend  # noqa: E402


async def main() -> None:
    backend = RCONBackend(
        os.environ.get("RCON_HOST", "127.0.0.1"),
        int(os.environ.get("RCON_PORT", "25575")),
        os.environ["RCON_PASSWORD"],
    )
    try:
        success, response = await backend.execute_command_checked("list")
        if not success:
            raise RuntimeError(f"RCON list failed: {response}")
        print(f"RCON list response: {response}")

        success, response = await backend.execute_command_checked(
            "say GitHub Actions RCON smoke test"
        )
        if not success:
            raise RuntimeError(f"RCON say failed: {response}")
        print(f"RCON say response: {response}")
    finally:
        await backend.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
