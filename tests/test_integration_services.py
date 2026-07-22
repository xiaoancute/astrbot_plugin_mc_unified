import asyncio
import json
import logging
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

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
    MCSManagerRequestError,
    _MCSManagerApiKeyRedactionFilter,
)
from backends.websocket_backend import WebSocketMessageBackend  # noqa: E402


class MCSManagerContractTests(unittest.IsolatedAsyncioTestCase):
    def test_httpx_request_logging_redacts_mcsmanager_api_keys(self):
        secret = "super-secret-api-key"
        record = logging.LogRecord(
            "httpx",
            logging.INFO,
            __file__,
            1,
            'HTTP Request: %s %s "%s %d %s"',
            (
                "GET",
                httpx.URL(f"https://panel.test/api/overview?apikey={secret}&page=1"),
                "HTTP/1.1",
                200,
                "OK",
            ),
            None,
        )

        self.assertTrue(_MCSManagerApiKeyRedactionFilter().filter(record))
        message = record.getMessage()

        self.assertNotIn(secret, message)
        self.assertIn("apikey=***", message)
        self.assertIn("page=1", message)

    async def test_tls_failure_is_actionable_instead_of_becoming_empty_data(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                "self-signed certificate",
                request=request,
            )

        backend = MCSManagerBackend(
            "self-signed", "https://mcsmanager.test", "test-key"
        )
        await backend.http_client.aclose()
        backend.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        try:
            with self.assertRaises(MCSManagerRequestError) as context:
                await backend.get_overview()
        finally:
            await backend.terminate()

        message = str(context.exception)
        self.assertIn("TLS证书验证失败", message)
        self.assertIn("auto_trust", message)
        self.assertIn("custom", message)

    async def test_auto_trust_failure_keeps_tls_verification_enabled(self):
        with (
            patch.object(
                MCSManagerBackend, "_fetch_and_cache_cert", return_value=None
            ) as fetch_certificate,
            patch(
                "backends.mcsmanager_backend.asyncio.to_thread",
                new=AsyncMock(return_value=None),
            ) as to_thread,
        ):
            backend = MCSManagerBackend(
                "offline",
                "https://mcsmanager.test",
                "test-key",
                ssl_mode="auto_trust",
            )

            try:
                fetch_certificate.assert_not_called()
                await backend._ensure_ssl_ready()
                self.assertIs(backend.ssl_verify, True)
                self.assertFalse(backend._ssl_auto_trust_pending)
                to_thread.assert_awaited_once_with(
                    fetch_certificate, "https://mcsmanager.test"
                )
            finally:
                await backend.terminate()

    async def test_auto_trust_switch_survives_old_client_close_failure(self):
        backend = MCSManagerBackend(
            "self-signed",
            "https://mcsmanager.test",
            "test-key",
            ssl_mode="auto_trust",
        )
        await backend.http_client.aclose()
        old_client = types.SimpleNamespace(
            aclose=AsyncMock(side_effect=OSError("close failed"))
        )
        new_client = types.SimpleNamespace(aclose=AsyncMock())
        backend.http_client = old_client

        with (
            patch(
                "backends.mcsmanager_backend.asyncio.to_thread",
                new=AsyncMock(return_value="/tmp/test-panel.pem"),
            ),
            patch(
                "backends.mcsmanager_backend.httpx.AsyncClient",
                return_value=new_client,
            ) as client_factory,
        ):
            await backend._ensure_ssl_ready()

        self.assertIs(backend.http_client, new_client)
        self.assertEqual(backend.ssl_verify, "/tmp/test-panel.pem")
        client_factory.assert_called_once_with(
            timeout=30.0, verify="/tmp/test-panel.pem"
        )
        old_client.aclose.assert_awaited_once()
        await backend.terminate()
        new_client.aclose.assert_awaited_once()

    async def test_terminate_waits_for_auto_trust_and_closes_replacement(self):
        backend = MCSManagerBackend(
            "self-signed",
            "https://mcsmanager.test",
            "test-key",
            ssl_mode="auto_trust",
        )
        await backend.http_client.aclose()
        old_client = types.SimpleNamespace(aclose=AsyncMock())
        new_client = types.SimpleNamespace(aclose=AsyncMock())
        backend.http_client = old_client
        fetch_started = asyncio.Event()
        allow_fetch_to_finish = asyncio.Event()

        async def delayed_to_thread(*_args):
            fetch_started.set()
            await allow_fetch_to_finish.wait()
            return "/tmp/test-panel.pem"

        with (
            patch(
                "backends.mcsmanager_backend.asyncio.to_thread",
                new=AsyncMock(side_effect=delayed_to_thread),
            ),
            patch(
                "backends.mcsmanager_backend.httpx.AsyncClient",
                return_value=new_client,
            ),
        ):
            ensure_task = asyncio.create_task(backend._ensure_ssl_ready())
            await fetch_started.wait()
            terminate_task = asyncio.create_task(backend.terminate())
            await asyncio.sleep(0)

            self.assertFalse(terminate_task.done())
            allow_fetch_to_finish.set()
            await asyncio.gather(ensure_task, terminate_task)

        self.assertIs(backend.http_client, new_client)
        old_client.aclose.assert_awaited_once()
        new_client.aclose.assert_awaited_once()

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

    async def test_malformed_instance_payload_is_an_explicit_request_failure(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/overview":
                return httpx.Response(
                    200,
                    json={"status": 200, "data": {"remote": ["bad-node"]}},
                )
            return httpx.Response(404, json={"status": 404})

        backend = MCSManagerBackend("primary", "http://mcsmanager.test", "test-key")
        await backend.http_client.aclose()
        backend.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        try:
            with self.assertRaises(MCSManagerRequestError) as context:
                await backend.get_instances()
        finally:
            await backend.terminate()

        self.assertIn("节点不是对象", str(context.exception))

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

    async def test_command_success_is_not_reclassified_when_log_fetch_fails(self):
        backend = MCSManagerBackend("primary", "http://mcsmanager.test", "test-key")
        backend._make_request = AsyncMock(
            side_effect=[
                {"status": 200, "data": True},
                MCSManagerRequestError("primary", "读取日志超时"),
            ]
        )

        try:
            result = await backend.send_command_to_instance(
                "daemon-1", "instance-1", "say hello"
            )
        finally:
            await backend.terminate()

        self.assertIn("命令已提交", result)
        self.assertIn("读取日志超时", result)
        self.assertEqual(backend._make_request.await_count, 2)

    async def test_instance_log_line_count_is_bounded_and_invalid_size_fails_soft(self):
        requested_sizes = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requested_sizes.append(request.url.params["size"])
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": "\n".join(f"line-{i}" for i in range(600)),
                },
            )

        backend = MCSManagerBackend("primary", "http://mcsmanager.test", "test-key")
        await backend.http_client.aclose()
        backend.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        try:
            bounded = await backend.get_instance_log("daemon-1", "instance-1", 10_000)
            defaulted = await backend.get_instance_log("daemon-1", "instance-1", "bad")
        finally:
            await backend.terminate()

        self.assertEqual(requested_sizes, ["500", "100"])
        self.assertEqual(len(bounded.splitlines()), 500)
        self.assertEqual(len(defaulted.splitlines()), 100)

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
