import json
from typing import Dict, Any

from .command_safety import find_dangerous_command


class MCSManagerTools:
    """MCSManager面板管理工具集（支持多面板）"""

    def __init__(self, multi_backend):
        self.multi_backend = multi_backend

    def has_panel(self, panel_name: str) -> bool:
        return panel_name in self.multi_backend.get_backend_names()

    def get_panel_list(self) -> str:
        panels = self.multi_backend.get_backend_names()
        if not panels:
            return "暂无可用的MCSManager面板"
        return "可用面板: " + ", ".join(panels)

    async def get_overview(self, panel_name: str = None) -> str:
        backend = self.multi_backend.get_backend(panel_name) if panel_name else None
        if backend is None and not panel_name:
            backends = self.multi_backend.get_all_backends()
            backend = backends[0] if backends else None
        if not backend:
            return f"找不到面板: {panel_name or '当前面板'}"
        data = await backend.get_overview()
        return f"📊 [{backend.name}] 概览:\n{json.dumps(data, ensure_ascii=False, indent=2)}"

    async def get_instances(self, panel_name: str = None) -> str:
        if panel_name:
            backend = self.multi_backend.get_backend(panel_name)
            if not backend:
                return f"找不到面板: {panel_name}"
            instances = await backend.get_instances()
            prefix = f"[{backend.name}] "
        else:
            instances = await self.multi_backend.get_all_instances()
            prefix = ""

        if not instances:
            return f"🖥️ {prefix}实例列表为空"

        result = f"🖥️ {prefix}实例列表:\n"

        for i, instance in enumerate(instances, 1):
            status_icon = {3: "🟢", 0: "🔴", 1: "🟠", 2: "🟡", -1: "⚪"}.get(
                instance["status"], "⚪"
            )
            panel_name_display = (
                f" ({instance.get('panel_name', '')})" if not panel_name else ""
            )
            result += (
                f"[{i}] {status_icon} {instance['name']} "
                f"(节点: {instance['node_name']}){panel_name_display} "
                f"UUID: {instance['uuid']}\n"
            )

        return result

    async def start_instance(self, identifier: str, panel_name: str = None) -> str:
        instance, error = await self._resolve_instance(identifier, panel_name)
        if not instance:
            return error

        panel_name = instance.get("panel_name")
        backend = self.multi_backend.get_backend(panel_name)
        if not backend:
            return f"找不到实例所属的面板: {panel_name}"

        success = await backend.start_instance(instance["daemon_id"], instance["uuid"])
        if success:
            return f"✅ [{panel_name}] 正在启动实例: {instance['name']}"
        return f"❌ [{panel_name}] 启动实例失败: {instance['name']}"

    async def stop_instance(self, identifier: str, panel_name: str = None) -> str:
        instance, error = await self._resolve_instance(identifier, panel_name)
        if not instance:
            return error

        panel_name = instance.get("panel_name")
        backend = self.multi_backend.get_backend(panel_name)
        if not backend:
            return f"找不到实例所属的面板: {panel_name}"

        success = await backend.stop_instance(instance["daemon_id"], instance["uuid"])
        if success:
            return f"✅ [{panel_name}] 正在停止实例: {instance['name']}"
        return f"❌ [{panel_name}] 停止实例失败: {instance['name']}"

    async def restart_instance(self, identifier: str, panel_name: str = None) -> str:
        instance, error = await self._resolve_instance(identifier, panel_name)
        if not instance:
            return error

        panel_name = instance.get("panel_name")
        backend = self.multi_backend.get_backend(panel_name)
        if not backend:
            return f"找不到实例所属的面板: {panel_name}"

        success = await backend.restart_instance(
            instance["daemon_id"], instance["uuid"]
        )
        if success:
            return f"✅ [{panel_name}] 正在重启实例: {instance['name']}"
        return f"❌ [{panel_name}] 重启实例失败: {instance['name']}"

    async def send_command(
        self, identifier: str, command: str, panel_name: str = None
    ) -> str:
        instance, error = await self._resolve_instance(identifier, panel_name)
        if not instance:
            return error

        panel_name = instance.get("panel_name")
        backend = self.multi_backend.get_backend(panel_name)
        if not backend:
            return f"找不到实例所属的面板: {panel_name}"

        if not getattr(backend, "dangerous_commands_enabled", False):
            dangerous_command = find_dangerous_command(command)
            if dangerous_command:
                return (
                    f"错误: 命令 '{dangerous_command}' 被标记为危险命令，"
                    f"请在MCSManager面板 {panel_name} 的配置中显式启用危险命令"
                )

        result = await backend.send_command_to_instance(
            instance["daemon_id"], instance["uuid"], command
        )
        return f"📢 [{panel_name}] 命令已发送到 {instance['name']}:\n{result}"

    async def get_instance_log(
        self, identifier: str, size: int = 100, panel_name: str = None
    ) -> str:
        instance, error = await self._resolve_instance(identifier, panel_name)
        if not instance:
            return error

        panel_name = instance.get("panel_name")
        backend = self.multi_backend.get_backend(panel_name)
        if not backend:
            return f"找不到实例所属的面板: {panel_name}"

        log = await backend.get_instance_log(
            instance["daemon_id"], instance["uuid"], size
        )
        return f"📝 [{panel_name}] {instance['name']} 最近日志:\n{log}"

    async def list_files(
        self, identifier: str, target: str = "", page: int = 1, page_size: int = 50
    ) -> str:
        return "文件管理功能暂未实现"

    async def read_file(self, identifier: str, target: str) -> str:
        return "文件管理功能暂未实现"

    async def write_file(self, identifier: str, target: str, text: str) -> str:
        return "文件管理功能暂未实现"

    async def delete_files(self, identifier: str, targets: list) -> str:
        return "文件管理功能暂未实现"

    async def create_folder(self, identifier: str, target: str) -> str:
        return "文件管理功能暂未实现"

    async def _resolve_instance(
        self, identifier: str, panel_name: str = None
    ) -> tuple[Dict[str, Any] | None, str]:
        """Resolve an instance from a fresh panel snapshot to avoid stale targets."""
        identifier = str(identifier).strip()
        if not identifier:
            return None, "实例标识不能为空"

        if panel_name is None and ":" in identifier:
            possible_panel, possible_identifier = identifier.split(":", 1)
            if self.has_panel(possible_panel):
                panel_name = possible_panel
                identifier = possible_identifier

        if panel_name:
            backend = self.multi_backend.get_backend(panel_name)
            if not backend:
                return None, f"找不到面板: {panel_name}"
            instances = await backend.get_instances()
        else:
            instances = await self.multi_backend.get_all_instances()

        if not instances:
            return None, f"找不到实例: {identifier}"

        if identifier.isdigit():
            index = int(identifier) - 1
            if 0 <= index < len(instances):
                return instances[index], ""

        uuid_matches = [
            instance
            for instance in instances
            if str(instance.get("uuid", "")) == identifier
        ]
        if len(uuid_matches) == 1:
            return uuid_matches[0], ""

        name_matches = [
            instance
            for instance in instances
            if str(instance.get("name", "")) == identifier
        ]
        if len(name_matches) == 1:
            return name_matches[0], ""
        if len(name_matches) > 1:
            panels = ", ".join(
                sorted(
                    {str(instance.get("panel_name", "")) for instance in name_matches}
                )
            )
            return (
                None,
                f"实例名称 {identifier} 在多个面板中重复（{panels}），"
                "请指定 panel_name 或使用 UUID",
            )

        return None, f"找不到实例: {identifier}"
