import asyncio
import json
import sys
import types
import unittest

import httpx
import websockets


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_api_module.logger = _Logger()
astrbot_module.api = astrbot_api_module
sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)

from backends.mcsmanager_backend import MCSManagerBackend  # noqa: E402
from backends.websocket_backend import WebSocketMessageBackend  # noqa: E402


class MCSManagerContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_instances_and_start_requests(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.url.params["apikey"], "test-key")

            if request.url.path == "/api/overview":
                return httpx.Response(
                    200,
                    json={
                        "status": 200,
                        "data": {"remote": [{"uuid": "daemon-1", "remarks": "Node 1"}]},
                    },
                )
            if request.url.path == "/api/service/remote_service_instances":
                return httpx.Response(
                    200,
                    json={
                        "status": 200,
                        "data": {
                            "data": [
                                {
                                    "config": {"nickname": "survival"},
                                    "instanceUuid": "instance-1",
                                    "status": 3,
                                }
                            ]
                        },
                    },
                )
            if request.url.path == "/api/protected_instance/open":
                return httpx.Response(200, json={"status": 200, "data": True})
            return httpx.Response(404, json={"status": 404, "error": "not found"})

        backend = MCSManagerBackend("primary", "http://mcsmanager.test", "test-key")
        await backend.http_client.aclose()
        backend.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        try:
            overview = await backend.get_overview()
            instances = await backend.get_instances()
            started = await backend.start_instance("daemon-1", "instance-1")
        finally:
            await backend.terminate()

        self.assertEqual(overview["remote"][0]["uuid"], "daemon-1")
        self.assertEqual(instances[0]["name"], "survival")
        self.assertEqual(instances[0]["panel_name"], "primary")
        self.assertTrue(started)
        self.assertEqual(len(requests), 4)


class WebSocketContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_callback_and_outbound_broadcast(self):
        callback_received = asyncio.Event()
        server_received = asyncio.Queue()
        keep_server_open = asyncio.Event()

        async def handler(websocket, *_args):
            await websocket.send(
                json.dumps(
                    {
                        "event_name": "chat",
                        "player": {"name": "Steve"},
                        "message": "hello from minecraft",
                    }
                )
            )
            payload = json.loads(await websocket.recv())
            await server_received.put(payload)
            await keep_server_open.wait()

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            backend = WebSocketMessageBackend(
                f"ws://127.0.0.1:{port}", reconnect_interval=0, max_retries=2
            )
            callback_args = []

            async def message_callback(player: str, message: str):
                callback_args.append((player, message))
                callback_received.set()

            backend.set_message_callback(message_callback)
            listener = asyncio.create_task(backend.start_listening())

            await asyncio.wait_for(callback_received.wait(), timeout=5)
            await backend.send_to_mc("hello from qq")
            outbound = await asyncio.wait_for(server_received.get(), timeout=5)

            keep_server_open.set()
            await backend.stop_listening()
            await asyncio.wait_for(listener, timeout=5)

        self.assertEqual(callback_args, [("Steve", "hello from minecraft")])
        self.assertEqual(outbound, {"type": "broadcast", "message": "hello from qq"})
