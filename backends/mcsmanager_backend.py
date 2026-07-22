import asyncio
import logging
import os
import re
import ssl
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from astrbot.api import logger


MAX_INSTANCE_LOG_LINES = 500


class _MCSManagerApiKeyRedactionFilter(logging.Filter):
    """Redact MCSManager's query-string API key from dependency request logs."""

    _mc_unified_apikey_redactor = True
    _pattern = re.compile(r"([?&]apikey=)[^&#\s\"']*", re.IGNORECASE)

    @classmethod
    def _redact(cls, value):
        rendered = str(value)
        if "apikey=" not in rendered.casefold():
            return value
        return cls._pattern.sub(r"\1***", rendered)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: self._redact(value) for key, value in record.args.items()
            }
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(
    getattr(log_filter, "_mc_unified_apikey_redactor", False)
    for log_filter in _httpx_logger.filters
):
    _httpx_logger.addFilter(_MCSManagerApiKeyRedactionFilter())


class MCSManagerRequestError(RuntimeError):
    """A user-actionable MCSManager request failure."""

    def __init__(self, panel_name: str, message: str):
        self.panel_name = panel_name
        self.message = message
        super().__init__(f"[{panel_name}] {message}")


class MCSManagerBackend:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        dangerous_commands_enabled: bool = False,
        ssl_mode: str = "default",
        ssl_cert: str = "",
        cert_dir: str = "",
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dangerous_commands_enabled = bool(dangerous_commands_enabled)
        self.cert_dir = cert_dir
        self._ssl_auto_trust_pending = False
        self._ssl_auto_trust_lock = asyncio.Lock()
        self.ssl_mode = str(ssl_mode or "default").strip().casefold()
        if self.ssl_mode not in {"default", "auto_trust", "disable", "custom"}:
            logger.warning(
                f"MCSM [{self.name}] 未知SSL验证模式 {self.ssl_mode!r}，使用default"
            )
            self.ssl_mode = "default"

        self.ssl_verify = self._resolve_ssl_verify(base_url, self.ssl_mode, ssl_cert)
        self.http_client = httpx.AsyncClient(timeout=30.0, verify=self.ssl_verify)

    def _resolve_ssl_verify(self, base_url: str, ssl_mode: str, ssl_cert: str):
        # 非 HTTPS 不需要验证
        if not base_url.casefold().startswith("https://"):
            return True

        if ssl_mode == "disable":
            logger.warning(f"MCSM [{self.name}] SSL验证已关闭，连接不受保护")
            return False

        if ssl_mode == "custom" or (ssl_mode == "default" and ssl_cert):
            if ssl_cert and os.path.isfile(ssl_cert):
                logger.info(f"MCSM [{self.name}] 使用自定义证书: {ssl_cert}")
                return ssl_cert
            if ssl_mode == "custom":
                logger.warning(
                    f"MCSM [{self.name}] 自定义证书路径无效: {ssl_cert}，回退到默认验证"
                )

        if ssl_mode == "auto_trust":
            cert_path = self._get_cached_cert_path()
            if cert_path and os.path.isfile(cert_path):
                logger.info(f"MCSM [{self.name}] 使用已缓存的TOFU证书: {cert_path}")
                return cert_path
            self._ssl_auto_trust_pending = True
            logger.info(f"MCSM [{self.name}] 将在首次API请求时异步获取TOFU证书")
            return True

        # default: 系统CA验证
        return True

    def _get_cached_cert_path(self) -> Optional[str]:
        if not self.cert_dir:
            return None
        safe_name = "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in self.name
        )
        return os.path.join(self.cert_dir, f"mcsm_{safe_name}.pem")

    def _fetch_and_cache_cert(self, base_url: str) -> Optional[str]:
        """通过 TLS 握手获取服务器自签名证书并缓存（TOFU 机制）"""
        try:
            parsed = urlparse(base_url)
            host = parsed.hostname
            if not host:
                logger.warning(f"MCSM [{self.name}] 面板地址缺少有效主机名")
                return None
            port = parsed.port or 443

            cert_pem = ssl.get_server_certificate((host, port), timeout=10.0)

            cert_path = self._get_cached_cert_path()
            if not cert_path:
                logger.warning(
                    f"MCSM [{self.name}] 未配置证书缓存目录，无法保存TOFU证书"
                )
                return None

            os.makedirs(self.cert_dir, exist_ok=True)
            with open(cert_path, "w", encoding="utf-8") as f:
                f.write(cert_pem)

            logger.info(f"MCSM [{self.name}] 已缓存服务器证书到 {cert_path}")
            return cert_path

        except Exception as e:
            logger.error(f"MCSM [{self.name}] 获取服务器证书失败: {e}")
            return None

    async def _ensure_ssl_ready(self) -> None:
        """Resolve an uncached TOFU certificate without blocking plugin startup."""
        if not self._ssl_auto_trust_pending:
            return
        async with self._ssl_auto_trust_lock:
            if not self._ssl_auto_trust_pending:
                return
            cert_path = await asyncio.to_thread(
                self._fetch_and_cache_cert, self.base_url
            )
            self._ssl_auto_trust_pending = False
            if not cert_path:
                logger.warning(
                    f"MCSM [{self.name}] TOFU获取证书失败，保持系统CA验证；"
                    "不会自动跳过TLS校验"
                )
                return

            try:
                new_client = httpx.AsyncClient(timeout=30.0, verify=cert_path)
            except Exception as error:
                raise self._request_error(f"自动信任证书加载失败: {error}") from error

            old_client = self.http_client
            self.http_client = new_client
            self.ssl_verify = cert_path
            try:
                await old_client.aclose()
            except Exception as error:
                logger.warning(
                    f"MCSM [{self.name}] 旧HTTP客户端关闭失败，继续使用新客户端: "
                    f"{error}"
                )
            logger.info(
                f"MCSM [{self.name}] 已自动获取并信任服务器证书（TOFU）: {cert_path}"
            )

    async def _make_request(
        self, endpoint: str, method: str = "GET", params: dict = None, data: dict = None
    ) -> dict:
        await self._ensure_ssl_ready()
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
            request_method = method.upper()
            if request_method == "GET":
                response = await self.http_client.get(
                    url, params=query_params, headers=headers
                )
            elif request_method == "POST":
                response = await self.http_client.post(
                    url, params=query_params, json=data, headers=headers
                )
            elif request_method == "PUT":
                response = await self.http_client.put(
                    url, params=query_params, json=data, headers=headers
                )
            elif request_method == "DELETE":
                response = await self.http_client.delete(
                    url, params=query_params, json=data, headers=headers
                )
            else:
                raise MCSManagerRequestError(self.name, "插件使用了不支持的请求方法")
        except httpx.ConnectTimeout as error:
            raise self._request_error("连接超时，请检查面板地址和网络") from error
        except httpx.ReadTimeout as error:
            raise self._request_error("读取超时，面板响应时间过长") from error
        except httpx.ConnectError as error:
            raw_message = str(error)
            if "certificate verify failed" in raw_message.casefold():
                message = (
                    "TLS证书验证失败。若面板使用自签名证书，请在此面板配置中"
                    "将SSL验证模式设为 auto_trust，或使用 custom 指定可信CA证书；"
                    "disable 仅限可信内网临时排查"
                )
            else:
                message = f"连接失败: {raw_message or '无法连接面板'}"
            raise self._request_error(message) from error
        except httpx.HTTPError as error:
            raise self._request_error(f"网络请求失败: {error}") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise self._request_error(
                f"面板返回了无效JSON（HTTP {response.status_code}）"
            ) from error

        if not isinstance(payload, dict):
            raise self._request_error("面板返回格式错误：响应不是JSON对象")

        api_status = payload.get("status")
        if response.status_code != 200 or api_status != 200:
            detail = self._extract_error_detail(payload)
            status_text = f"HTTP {response.status_code}"
            if api_status is not None:
                status_text += f" / API {api_status}"
            raise self._request_error(f"API请求失败（{status_text}）: {detail}")

        return payload

    def _request_error(self, message: str) -> MCSManagerRequestError:
        logger.error(f"MCSM [{self.name}] API请求失败: {message}")
        return MCSManagerRequestError(self.name, message)

    @staticmethod
    def _extract_error_detail(payload: Dict[str, Any]) -> str:
        for key in ("error", "message"):
            value = payload.get(key)
            if value:
                return str(value)
        data = payload.get("data")
        if isinstance(data, str) and data:
            return data
        return "面板未提供错误详情"

    async def get_overview(self) -> Dict[str, Any]:
        response = await self._make_request("/overview")
        data = response.get("data", {})
        if not isinstance(data, dict):
            raise self._request_error("概览响应格式错误：data不是对象")
        return data

    async def get_instances(self) -> List[Dict[str, Any]]:
        overview = await self.get_overview()
        nodes = overview.get("remote", [])
        if nodes is None:
            nodes = []
        if not isinstance(nodes, list):
            raise self._request_error("实例响应格式错误：remote不是列表")
        all_instances = []

        for node_index, node in enumerate(nodes, 1):
            if not isinstance(node, dict):
                raise self._request_error(
                    f"实例响应格式错误：第 {node_index} 个节点不是对象"
                )
            node_uuid = str(node.get("uuid") or "").strip()
            if not node_uuid:
                raise self._request_error(
                    f"实例响应格式错误：第 {node_index} 个节点缺少uuid"
                )
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

                data_block = instances_resp.get("data", {})
                instances = (
                    data_block.get("data", [])
                    if isinstance(data_block, dict)
                    else data_block
                )
                if not isinstance(instances, list):
                    raise self._request_error("实例列表响应格式错误：data不是列表")

                for instance_index, instance in enumerate(instances, 1):
                    if not isinstance(instance, dict):
                        raise self._request_error(
                            "实例列表响应格式错误："
                            f"节点 {node_uuid} 第 {page} 页第 {instance_index} 项不是对象"
                        )
                    config = instance.get("config") or {}
                    info = instance.get("info") or {}
                    if not isinstance(config, dict) or not isinstance(info, dict):
                        raise self._request_error(
                            "实例列表响应格式错误：config或info不是对象"
                        )
                    instance_uuid = str(instance.get("instanceUuid") or "").strip()
                    if not instance_uuid:
                        raise self._request_error(
                            "实例列表响应格式错误：实例缺少instanceUuid"
                        )
                    status = instance.get("status")
                    if status is None:
                        status = info.get("status")
                    try:
                        status = int(status)
                    except (TypeError, ValueError):
                        status = -1
                    all_instances.append(
                        {
                            "name": str(config.get("nickname") or "未命名"),
                            "uuid": instance_uuid,
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

        all_instances.sort(key=lambda item: item["name"].casefold())
        return all_instances

    async def start_instance(self, daemon_id: str, instance_uuid: str) -> bool:
        await self._make_request(
            "/protected_instance/open",
            method="GET",
            params={"uuid": instance_uuid, "daemonId": daemon_id},
        )
        return True

    async def stop_instance(self, daemon_id: str, instance_uuid: str) -> bool:
        await self._make_request(
            "/protected_instance/stop",
            method="GET",
            params={"uuid": instance_uuid, "daemonId": daemon_id},
        )
        return True

    async def restart_instance(self, daemon_id: str, instance_uuid: str) -> bool:
        await self._make_request(
            "/protected_instance/restart",
            method="GET",
            params={"uuid": instance_uuid, "daemonId": daemon_id},
        )
        return True

    async def send_command_to_instance(
        self, daemon_id: str, instance_uuid: str, command: str
    ) -> str:
        await self._make_request(
            "/protected_instance/command",
            method="GET",
            params={"daemonId": daemon_id, "uuid": instance_uuid, "command": command},
        )

        await asyncio.sleep(1)
        try:
            log_resp = await self._make_request(
                "/protected_instance/outputlog",
                method="GET",
                params={"daemonId": daemon_id, "uuid": instance_uuid, "size": 64},
            )
        except Exception as error:
            return f"命令已提交，但无法获取执行后日志: {error}"
        log_data = log_resp.get("data", "无返回数据")
        return log_data if isinstance(log_data, str) else str(log_data)

    async def get_instance_log(
        self, daemon_id: str, instance_uuid: str, size: int = 100
    ) -> str:
        try:
            max_lines = int(size)
        except (TypeError, ValueError):
            max_lines = 100
        max_lines = min(MAX_INSTANCE_LOG_LINES, max(1, max_lines))
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
        async with self._ssl_auto_trust_lock:
            self._ssl_auto_trust_pending = False
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
        ssl_mode: str = "default",
        ssl_cert: str = "",
        cert_dir: str = "",
    ) -> bool:
        if name in self.backends:
            logger.warning(f"忽略重复的MCSManager面板名称: {name}")
            return False
        self.backends[name] = MCSManagerBackend(
            name,
            url,
            api_key,
            dangerous_commands_enabled,
            ssl_mode,
            ssl_cert,
            cert_dir,
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

    async def get_all_instances_report(
        self,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        """Return successful instances plus explicit per-panel failures."""
        all_instances = []
        errors = []
        backends = list(self.backends.values())
        results = await asyncio.gather(
            *(backend.get_instances() for backend in backends),
            return_exceptions=True,
        )
        for backend, result in zip(backends, results):
            if isinstance(result, Exception):
                error_text = str(result)
                logger.error(f"获取面板 [{backend.name}] 实例失败: {error_text}")
                errors.append(error_text)
                continue
            all_instances.extend(result)
        return all_instances, errors

    async def get_all_instances(self) -> List[Dict[str, Any]]:
        instances, errors = await self.get_all_instances_report()
        if errors and not instances:
            raise MCSManagerRequestError("全部面板", "；".join(errors))
        return instances

    async def terminate_all(self):
        for backend in self.backends.values():
            await backend.terminate()
        logger.info("所有MCSManager面板客户端已关闭")
