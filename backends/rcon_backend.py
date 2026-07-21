import asyncio
from typing import List, Optional

from aiomcrcon import Client
from astrbot.api import logger

from . import ServerBackend


class RCONBackend(ServerBackend):
    """RCON协议后端实现"""

    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password
        self._client: Optional[Client] = None
        self._lock = asyncio.Lock()

    async def _ensure_connection(self) -> Client:
        async with self._lock:
            if self._client is None:
                try:
                    self._client = Client(self.host, self.port, self.password)
                    await self._client.connect()
                    logger.info(f"RCON连接已建立: {self.host}:{self.port}")
                except Exception as e:
                    logger.error(f"RCON连接建立失败: {e}")
                    self._client = None
                    raise
            return self._client

    async def _reconnect(self):
        async with self._lock:
            if self._client:
                try:
                    await self._client.close()
                except Exception:
                    pass
                self._client = None

    async def connect(self) -> bool:
        try:
            await self._ensure_connection()
            return True
        except Exception:
            return False

    async def disconnect(self):
        async with self._lock:
            if self._client:
                try:
                    await self._client.close()
                    logger.info("RCON连接已关闭")
                except Exception:
                    pass
                self._client = None

    async def is_connected(self) -> bool:
        return self._client is not None

    async def execute_command_checked(self, command: str) -> tuple[bool, str]:
        """Execute a command and return an explicit success flag."""
        if command.startswith("/"):
            command = command[1:]

        max_retries = 2
        for attempt in range(max_retries):
            try:
                client = await self._ensure_connection()
                response = await client.send_cmd(command)
                # aio-mc-rcon returns (message, request_id).
                if hasattr(response, "msg"):
                    result = response.msg
                elif (
                    isinstance(response, tuple)
                    and response
                    and isinstance(response[0], str)
                ):
                    result = response[0]
                else:
                    result = str(response)
                logger.info(f"执行命令: {command}, 响应: {result}")
                return True, result if result else "命令执行成功（无返回信息）"
            except (
                ConnectionRefusedError,
                ConnectionResetError,
                BrokenPipeError,
                OSError,
            ) as e:
                logger.warning(f"RCON连接异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                await self._reconnect()
                if attempt == max_retries - 1:
                    error_msg = f"无法连接到服务器 {self.host}:{self.port}，请检查服务器是否启动且RCON已启用"
                    logger.error(error_msg)
                    return False, error_msg
            except Exception as e:
                error_msg = f"执行命令时出错: {str(e)}"
                logger.error(error_msg)
                await self._reconnect()
                return False, error_msg

        return False, "命令执行失败"

    async def execute_command(self, command: str) -> str:
        success, message = await self.execute_command_checked(command)
        return message if success else f"错误: {message}"

    async def get_online_players(self) -> List[str]:
        response = await self.execute_command("list")
        try:
            if "There are" in response:
                parts = response.split("players online:")
                if len(parts) > 1 and parts[1].strip():
                    players = [p.strip() for p in parts[1].split(",") if p.strip()]
                    return players
            return []
        except Exception:
            return []

    async def send_message(self, message: str, target: str = "@a") -> str:
        escaped = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        json_text = f'{{"text":"{escaped}", "color":"aqua"}}'
        command = f"tellraw {target} {json_text}"
        return await self.execute_command(command)
