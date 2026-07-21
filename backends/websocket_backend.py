import asyncio
import inspect
import json

import websockets
from astrbot.api import logger

from . import MessageBackend


class WebSocketMessageBackend(MessageBackend):
    """WebSocket消息互通后端实现（支持鹊桥模组）"""

    FATAL_CLOSE_CODES = {1008, 1003, 1010}
    FATAL_STATUS_CODES = {401, 403, 404}

    def __init__(
        self,
        ws_url: str,
        token: str = "",
        reconnect_interval: int = 10,
        max_retries: int = 5,
    ):
        super().__init__()
        self.ws_url = ws_url
        self.token = token
        self.reconnect_interval = reconnect_interval
        self.max_retries = max_retries

        self.connected = False
        self.websocket = None
        self.should_reconnect = True
        self.total_retries = 0

        self.headers = {}
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

    def _is_fatal_error(self, error) -> bool:
        if isinstance(error, websockets.exceptions.ConnectionClosed):
            return error.code in self.FATAL_CLOSE_CODES
        current_invalid_status = getattr(websockets.exceptions, "InvalidStatus", None)
        legacy_invalid_status = (
            None
            if isinstance(current_invalid_status, type)
            else getattr(websockets.exceptions, "InvalidStatusCode", None)
        )
        invalid_status_types = tuple(
            error_type
            for error_type in (current_invalid_status, legacy_invalid_status)
            if isinstance(error_type, type)
        )
        if invalid_status_types and isinstance(error, invalid_status_types):
            status_code = getattr(error, "status_code", None)
            if status_code is None and getattr(error, "response", None):
                status_code = error.response.status_code
            return status_code in self.FATAL_STATUS_CODES
        return False

    def _connect_kwargs(self) -> dict:
        """Build arguments compatible with legacy and current websockets APIs."""
        parameters = inspect.signature(websockets.connect).parameters
        kwargs = {"ping_interval": 30, "ping_timeout": 10}
        header_key = (
            "additional_headers"
            if "additional_headers" in parameters
            else "extra_headers"
        )
        kwargs[header_key] = self.headers
        if "proxy" in parameters:
            kwargs["proxy"] = None
        return kwargs

    async def _handle_connection_error(self, error: Exception) -> bool:
        self.connected = False
        self.websocket = None

        if self._is_fatal_error(error):
            logger.error(f"致命错误，停止重试: {error}")
            self.should_reconnect = False
            return False

        self.total_retries += 1
        if self.total_retries >= self.max_retries:
            logger.error(
                f"WebSocket连接失败次数已达到最大限制({self.max_retries}次)，停止重试"
            )
            self.should_reconnect = False
            return False

        wait_time = min(self.reconnect_interval * self.total_retries, 60)
        logger.error(
            f"WebSocket连接错误: {error}, 将在{wait_time}秒后尝试重新连接..."
            f"(第{self.total_retries}次)"
        )
        await asyncio.sleep(wait_time)
        return True

    async def start_listening(self):
        self.should_reconnect = True
        while self.should_reconnect:
            try:
                async with websockets.connect(
                    self.ws_url, **self._connect_kwargs()
                ) as websocket:
                    self.websocket = websocket
                    self.connected = True
                    self.total_retries = 0
                    logger.info("成功连接到WebSocket服务器")

                    async for message in websocket:
                        await self._handle_message(message)

            except asyncio.CancelledError:
                raise
            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                ConnectionRefusedError,
                asyncio.TimeoutError,
            ) as e:
                if not await self._handle_connection_error(e):
                    break
            except Exception as e:
                if not await self._handle_connection_error(e):
                    break
        self.connected = False
        self.websocket = None

    async def _handle_message(self, message: str):
        try:
            data = json.loads(message)
            event_name = data.get("event_name", "")
            player_data = data.get("player", "")

            if isinstance(player_data, dict):
                player_name = (
                    player_data.get("display_name")
                    or player_data.get("nickname")
                    or player_data.get("name")
                    or ""
                )
            else:
                player_name = str(player_data)

            if event_name == "chat":
                message_content = data.get("message", "")
                if self._message_callback and message_content:
                    await self._message_callback(player_name, message_content)

            elif event_name == "player_join" or event_name == "join":
                if self._player_join_callback and player_name:
                    await self._player_join_callback(player_name)

            elif event_name == "player_quit" or event_name == "quit":
                if self._player_leave_callback and player_name:
                    await self._player_leave_callback(player_name)

            elif event_name == "player_death" or event_name == "death":
                death_text = data.get("death", {}).get("text", "") or data.get(
                    "message", ""
                )
                if self._player_death_callback and death_text:
                    await self._player_death_callback(player_name, death_text)

        except json.JSONDecodeError:
            logger.error(f"无法解析JSON消息: {message}")
        except Exception as e:
            logger.error(f"处理WebSocket消息时出错: {str(e)}")

    async def stop_listening(self):
        self.should_reconnect = False
        if self.websocket:
            await self.websocket.close()
        logger.info("WebSocket监听已停止")

    async def send_to_mc(self, message: str):
        if not self.connected or not self.websocket:
            logger.error("无法发送消息：WebSocket未连接")
            return

        try:
            await self.websocket.send(
                json.dumps({"type": "broadcast", "message": message})
            )
        except Exception as e:
            logger.error(f"发送消息到MC失败: {e}")

    async def send_to_qq(self, message: str, group_id: str):
        logger.info(f"WebSocket不直接发送到QQ，消息: {message}, 群号: {group_id}")

    async def is_connected(self) -> bool:
        return self.connected
