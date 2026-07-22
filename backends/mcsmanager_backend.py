import asyncio
from typing import List, Dict, Any, Optional
import httpx
from astrbot.api import logger


class MCSManagerBackend:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        dangerous_commands_enabled: bool = False,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dangerous_commands_enabled = bool(dangerous_commands_enabled)
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def _make_request(
        self, endpoint: str, method: str = "GET", params: dict = None, data: dict = None
    ) -> dict:
        if not endpoint.startswith("/api/"):
            url = f"{self.base_url}/api{endpoint}"
        else:
            url = f"{self.base_url}{endpoint}"

        query_params = {"apikey": self.api_key}
        if params:
            query_params.update(params)

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            if method.upper() == "GET":
                response = await self.http_client.get(
                    url, params=query_params, headers=headers
                )
            elif method.upper() == "POST":
                response = await self.http_client.post(
                    url, params=query_params, json=data, headers=headers
                )
            elif method.upper() == "PUT":
                response = await self.http_client.put(
                    url, params=query_params, json=data, headers=headers
                )
            elif method.upper() == "DELETE":
                response = await self.http_client.delete(
                    url, params=query_params, json=data, headers=headers
                )
            else:
                return {"status": 400, "error": "不支持的请求方法"}

            if response.status_code != 200:
                try:
                    return response.json()
                except Exception:
                    return {
                        "status": response.status_code,
                        "error": f"HTTP Error {response.status_code}",
                    }

            try:
                return response.json()
            except Exception:
                return {"status": 500, "error": "JSON解析失败"}

        except httpx.ConnectTimeout:
            return {"status": 504, "error": "连接超时"}
        except httpx.ReadTimeout:
            return {"status": 504, "error": "读取超时"}
        except Exception as e:
            logger.error(f"MCSM [{self.name}] API请求失败: {str(e)}")
            return {"status": 500, "error": str(e)}

    async def get_overview(self) -> Dict[str, Any]:
        data = await self._make_request("/overview")
        return data.get("data", {}) if data.get("status") == 200 else {}

    async def get_instances(self) -> List[Dict[str, Any]]:
        overview = await self.get_overview()
        nodes = overview.get("remote", [])
        all_instances = []

        for node in nodes:
            node_uuid = node.get("uuid")
            page = 1
            while True:
                instances_resp = await self._make_request(
                    "/service/remote_service_instances",
                    params={
                        "daemonId": node_uuid,
                        "page": page,
                        "page_size": 50,
                        "instance_name": "",
                        "status": "",
                    },
                )

                if instances_resp.get("status") != 200:
                    break

                data_block = instances_resp.get("data", {})
                instances = (
                    data_block.get("data", [])
                    if isinstance(data_block, dict)
                    else data_block
                )

                for instance in instances:
                    status = instance.get("status")
                    if status is None:
                        status = instance.get("info", {}).get("status")
                    all_instances.append(
                        {
                            "name": instance.get("config", {}).get("nickname")
                            or "未命名",
                            "uuid": instance.get("instanceUuid"),
                            "daemon_id": node_uuid,
                            "status": status,
                            "node_name": node.get("remarks")
                            or node.get("hostname")
                            or "未知节点",
                            "panel_name": self.name,
                        }
                    )

                if not isinstance(data_block, dict):
                    break
                try:
                    max_page = max(1, int(data_block.get("maxPage", page)))
                except (TypeError, ValueError):
                    max_page = page
                if page >= max_page:
                    break
                page += 1

        all_instances.sort(key=lambda x: x["name"])
        return all_instances

    async def start_instance(self, daemon_id: str, instance_uuid: str) -> bool:
        resp = await self._make_request(
            "/protected_instance/open",
            method="GET",
            params={"uuid": instance_uuid, "daemonId": daemon_id},
        )
        return resp.get("status") == 200

    async def stop_instance(self, daemon_id: str, instance_uuid: str) -> bool:
        resp = await self._make_request(
            "/protected_instance/stop",
            method="GET",
            params={"uuid": instance_uuid, "daemonId": daemon_id},
        )
        return resp.get("status") == 200

    async def restart_instance(self, daemon_id: str, instance_uuid: str) -> bool:
        resp = await self._make_request(
            "/protected_instance/restart",
            method="GET",
            params={"uuid": instance_uuid, "daemonId": daemon_id},
        )
        return resp.get("status") == 200

    async def send_command_to_instance(
        self, daemon_id: str, instance_uuid: str, command: str
    ) -> str:
        resp = await self._make_request(
            "/protected_instance/command",
            method="GET",
            params={"daemonId": daemon_id, "uuid": instance_uuid, "command": command},
        )
        if resp.get("status") != 200:
            return f"发送失败: {resp.get('error', '未知错误')}"

        await asyncio.sleep(1)
        log_resp = await self._make_request(
            "/protected_instance/outputlog",
            method="GET",
            params={"daemonId": daemon_id, "uuid": instance_uuid, "size": 64},
        )
        return (
            log_resp.get("data", "无返回数据")
            if log_resp.get("status") == 200
            else "获取日志失败"
        )

    async def get_instance_log(
        self, daemon_id: str, instance_uuid: str, size: int = 100
    ) -> str:
        max_lines = max(1, int(size))
        request_size_kb = min(2048, max_lines)
        resp = await self._make_request(
            "/protected_instance/outputlog",
            method="GET",
            params={
                "daemonId": daemon_id,
                "uuid": instance_uuid,
                "size": request_size_kb,
            },
        )
        if resp.get("status") != 200:
            return f"获取日志失败: {resp.get('error', '未知错误')}"

        log_data = resp.get("data", "")
        if not log_data:
            return "该实例当前没有最新日志"

        lines = log_data.strip().split("\n")
        if len(lines) > max_lines:
            lines = lines[-max_lines:]

        return "\n".join(lines)

    async def list_files(
        self,
        daemon_id: str,
        instance_uuid: str,
        target: str = "/",
        page: int = 0,
        page_size: int = 50,
        file_name: str = "",
    ) -> Dict[str, Any]:
        """List one page of files for an instance."""
        try:
            page = max(0, int(page))
        except (TypeError, ValueError):
            page = 0
        try:
            page_size = min(100, max(1, int(page_size)))
        except (TypeError, ValueError):
            page_size = 50

        return await self._make_request(
            "/files/list",
            params={
                "daemonId": daemon_id,
                "uuid": instance_uuid,
                "target": target or "/",
                "page": page,
                "page_size": page_size,
                "file_name": file_name or "",
            },
        )

    async def read_file(
        self, daemon_id: str, instance_uuid: str, target: str
    ) -> Dict[str, Any]:
        """Read a file using MCSManager's file endpoint."""
        return await self._make_request(
            "/files",
            method="PUT",
            params={"daemonId": daemon_id, "uuid": instance_uuid},
            data={"target": target},
        )

    async def terminate(self):
        await self.http_client.aclose()
        logger.info(f"MCSManager [{self.name}] HTTP客户端已关闭")


class MCSManagerMultiBackend:
    def __init__(self):
        self.backends: Dict[str, MCSManagerBackend] = {}

    def add_backend(
        self,
        name: str,
        url: str,
        api_key: str,
        dangerous_commands_enabled: bool = False,
    ) -> bool:
        if name in self.backends:
            logger.warning(f"忽略重复的MCSManager面板名称: {name}")
            return False
        self.backends[name] = MCSManagerBackend(
            name, url, api_key, dangerous_commands_enabled
        )
        logger.info(f"MCSManager面板 [{name}] 已添加")
        return True

    def remove_backend(self, name: str):
        if name in self.backends:
            backend = self.backends.pop(name)
            asyncio.create_task(backend.terminate())
            logger.info(f"MCSManager面板 [{name}] 已移除")

    def get_backend(self, name: str) -> Optional[MCSManagerBackend]:
        return self.backends.get(name)

    def get_backend_names(self) -> List[str]:
        return list(self.backends.keys())

    def get_all_backends(self) -> List[MCSManagerBackend]:
        return list(self.backends.values())

    async def get_all_instances(self) -> List[Dict[str, Any]]:
        all_instances = []
        backends = list(self.backends.values())
        results = await asyncio.gather(
            *(backend.get_instances() for backend in backends),
            return_exceptions=True,
        )
        for backend, result in zip(backends, results):
            if isinstance(result, Exception):
                logger.error(f"获取面板 [{backend.name}] 实例失败: {result}")
                continue
            all_instances.extend(result)
        return all_instances

    async def terminate_all(self):
        for backend in self.backends.values():
            await backend.terminate()
        logger.info("所有MCSManager面板客户端已关闭")
