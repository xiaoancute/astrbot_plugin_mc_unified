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

from backends.mcsmanager_backend import (  # noqa: E402
    MCSManagerBackend,
    MCSManagerMultiBackend,
)
from backends.websocket_backend import WebSocketMessageBackend  # noqa: E402


class MCSManagerContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_multi_panel_discovery_runs_concurrently_and_rejects_duplicates(self):
        first_started = asyncio.Event()
        second_started = asyncio.Event()

        class FakeBackend:
            def __init__(self, name, own_event, other_event):
                self.name = name
                self.own_event = own_event
                self.other_event = other_event

            async def get_instances(self):
                self.own_event.set()
                await asyncio.wait_for(self.other_event.wait(), timeout=1)
                return [{"name": self.name}]

        multi = MCSManagerMultiBackend()
        multi.backends = {
            "first": FakeBackend("first", first_started, second_started),
            "second": FakeBackend("second", second_started, first_started),
        }

        instances = await asyncio.wait_for(multi.get_all_instances(), timeout=2)

        self.assertEqual([item["name"] for item in instances], ["first", "second"])

        duplicate_check = MCSManagerMultiBackend()
        self.assertTrue(
            duplicate_check.add_backend("primary", "http://one.test", "test-key")
        )
        original = duplicate_check.get_backend("primary")
        self.assertFalse(
            duplicate_check.add_backend("primary", "http://two.test", "other-key")
        )
        self.assertIs(duplicate_check.get_backend("primary"), original)
        await duplicate_check.terminate_all()

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
                self.assertEqual(request.url.params["instance_name"], "")
                self.assertEqual(request.url.params["status"], "")
                self.assertEqual(request.url.params["page_size"], "50")
                page = request.url.params["page"]
                if page == "2":
                    return httpx.Response(
                        200,
                        json={
                            "status": 200,
                            "data": {
                                "maxPage": 2,
                                "data": [
                                    {
                                        "config": {"nickname": "creative"},
                                        "instanceUuid": "instance-2",
                                        "status": 3,
                                    }
                                ],
                            },
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "status": 200,
                        "data": {
                            "maxPage": 2,
                            "data": [
                                {
                                    "config": {"nickname": "survival"},
                                    "instanceUuid": "instance-1",
                                    "status": 0,
                                }
                            ],
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
        self.assertEqual(
            [instance["name"] for instance in instances], ["creative", "survival"]
        )
        self.assertEqual(instances[1]["status"], 0)
        self.assertEqual(instances[0]["panel_name"], "primary")
        self.assertTrue(started)
        self.assertEqual(len(requests), 5)

    async def test_command_and_log_requests_use_documented_size_and_headers(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.headers["X-Requested-With"], "XMLHttpRequest")
            self.assertIn("application/json", request.headers["Content-Type"])
            if request.url.path.endswith("/command"):
                self.assertEqual(request.url.params["command"], "say hello")
                return httpx.Response(200, json={"status": 200, "data": True})
            if request.url.path.endswith("/outputlog"):
                self.assertEqual(request.url.params["size"], "64")
                return httpx.Response(200, json={"status": 200, "data": "ok\n"})
            return httpx.Response(404, json={"status": 404})

        backend = MCSManagerBackend("primary", "http://mcsmanager.test", "test-key")
        await backend.http_client.aclose()
        backend.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        try:
            result = await backend.send_command_to_instance(
                "daemon-1", "instance-1", "say hello"
            )
        finally:
            await backend.terminate()

        self.assertEqual(result, "ok\n")
        self.assertEqual(len(requests), 2)

    async def test_file_list_and_read_follow_documented_contract(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.url.params["apikey"], "test-key")
            self.assertEqual(request.headers["X-Requested-With"], "XMLHttpRequest")
            if request.url.path == "/api/files/list":
                self.assertEqual(request.method, "GET")
                self.assertEqual(request.url.params["daemonId"], "daemon-1")
                self.assertEqual(request.url.params["uuid"], "instance-1")
                self.assertEqual(request.url.params["target"], "/config")
                self.assertEqual(request.url.params["page"], "0")
                self.assertEqual(request.url.params["page_size"], "25")
                self.assertEqual(request.url.params["file_name"], "server")
                return httpx.Response(
                    200,
                    json={
                        "status": 200,
                        "data": {
                            "items": [
                                {"name": "config", "size": 0, "type": 0},
                                {"name": "server.properties", "size": 10, "type": 1},
                            ],
                            "page": 0,
                            "pageSize": 25,
                            "total": 52,
                            "absolutePath": "/srv/minecraft/config",
                        },
                    },
                )
            if request.url.path == "/api/files":
                self.assertEqual(request.method, "PUT")
                self.assertEqual(request.url.params["daemonId"], "daemon-1")
                self.assertEqual(request.url.params["uuid"], "instance-1")
                self.assertEqual(
                    json.loads(request.content), {"target": "/server.properties"}
                )
                return httpx.Response(200, json={"status": 200, "data": "motd=hello"})
            return httpx.Response(404, json={"status": 404, "error": "not found"})

        backend = MCSManagerBackend("primary", "http://mcsmanager.test", "test-key")
        await backend.http_client.aclose()
        backend.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        try:
            listing = await backend.list_files(
                "daemon-1", "instance-1", "/config", 0, 25, "server"
            )
            content = await backend.read_file(
                "daemon-1", "instance-1", "/server.properties"
            )
        finally:
            await backend.terminate()

        self.assertEqual(listing["data"]["total"], 52)
        self.assertEqual(content["data"], "motd=hello")
        self.assertEqual(len(requests), 2)


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
            send_result = await backend.send_to_mc("hello from qq")
            outbound = await asyncio.wait_for(server_received.get(), timeout=5)

            keep_server_open.set()
            await backend.stop_listening()
            await asyncio.wait_for(listener, timeout=5)

        self.assertEqual(callback_args, [("Steve", "hello from minecraft")])
        self.assertEqual(outbound, {"type": "broadcast", "message": "hello from qq"})
        self.assertEqual(send_result, "✅ WebSocket消息已发送")
