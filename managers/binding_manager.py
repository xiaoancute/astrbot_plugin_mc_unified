import json
import os
from typing import Dict, List

from astrbot.api import logger


class GroupBindingManager:
    """群绑定管理器"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.bindings: Dict[str, List[str]] = {}
        self.group_sessions: Dict[str, str] = {}
        self.last_error = ""
        self._load_bindings()
        self._load_group_sessions()

    def _load_bindings(self):
        try:
            binding_file = os.path.join(self.data_dir, "bindings.json")
            if os.path.exists(binding_file):
                with open(binding_file, "r", encoding="utf-8") as f:
                    bindings = json.load(f)
                if not isinstance(bindings, dict) or not all(
                    isinstance(groups, list) for groups in bindings.values()
                ):
                    raise ValueError("绑定文件格式无效")
                self.bindings = {
                    str(server): [str(group_id) for group_id in groups]
                    for server, groups in bindings.items()
                }
        except Exception as e:
            logger.warning(f"读取群绑定配置失败，将使用空配置: {e}")
            self.bindings = {}

    def _save_bindings(self) -> bool:
        binding_file = os.path.join(self.data_dir, "bindings.json")
        temp_file = f"{binding_file}.tmp"
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.bindings, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, binding_file)
            return True
        except Exception as e:
            logger.error(f"保存群绑定配置失败: {e}")
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except OSError:
                pass
            return False

    def _load_group_sessions(self):
        try:
            session_file = os.path.join(self.data_dir, "group_sessions.json")
            if os.path.exists(session_file):
                with open(session_file, "r", encoding="utf-8") as f:
                    sessions = json.load(f)
                if not isinstance(sessions, dict):
                    raise ValueError("群会话文件格式无效")
                self.group_sessions = {
                    str(group_id): str(umo)
                    for group_id, umo in sessions.items()
                    if str(group_id) and str(umo)
                }
        except Exception as e:
            logger.warning(f"读取群会话配置失败，将等待群内新消息刷新: {e}")
            self.group_sessions = {}

    def _save_group_sessions(self) -> bool:
        session_file = os.path.join(self.data_dir, "group_sessions.json")
        temp_file = f"{session_file}.tmp"
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.group_sessions, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, session_file)
            return True
        except Exception as e:
            logger.error(f"保存群会话配置失败: {e}")
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except OSError:
                pass
            return False

    def remember_group_session(self, group_id: str, unified_msg_origin: str) -> bool:
        """Persist a real AstrBot UMO for future proactive group messages."""
        group_id = str(group_id or "")
        unified_msg_origin = str(unified_msg_origin or "")
        if not group_id or not unified_msg_origin:
            return False
        if self.group_sessions.get(group_id) == unified_msg_origin:
            return True

        previous = self.group_sessions.get(group_id)
        self.group_sessions[group_id] = unified_msg_origin
        if self._save_group_sessions():
            return True

        if previous is None:
            self.group_sessions.pop(group_id, None)
        else:
            self.group_sessions[group_id] = previous
        return False

    def get_group_session(self, group_id: str) -> str:
        return self.group_sessions.get(str(group_id), "")

    def bind_group(self, group_id: str, server_name: str = "default") -> bool:
        self.last_error = ""
        group_id = str(group_id)
        if server_name not in self.bindings:
            self.bindings[server_name] = []

        if group_id not in self.bindings[server_name]:
            self.bindings[server_name].append(group_id)
            if self._save_bindings():
                return True
            self.bindings[server_name].remove(group_id)
            if not self.bindings[server_name]:
                del self.bindings[server_name]
            self.last_error = "绑定配置保存失败，请检查 AstrBot 日志"
        return False

    def unbind_group(self, group_id: str, server_name: str = "default") -> bool:
        self.last_error = ""
        group_id = str(group_id)
        if server_name in self.bindings and group_id in self.bindings[server_name]:
            self.bindings[server_name].remove(group_id)
            if self._save_bindings():
                return True
            self.bindings[server_name].append(group_id)
            self.last_error = "绑定配置保存失败，请检查 AstrBot 日志"
        return False

    def is_group_bound(self, group_id: str, server_name: str = "default") -> bool:
        return (
            server_name in self.bindings and str(group_id) in self.bindings[server_name]
        )

    def get_bound_groups(self, server_name: str = "default") -> List[str]:
        return self.bindings.get(server_name, [])

    def get_group_servers(self, group_id: str) -> List[str]:
        """Return every server currently associated with a group."""
        group_id = str(group_id)
        return [
            server_name
            for server_name, groups in self.bindings.items()
            if group_id in groups
        ]

    def unbind_group_from_all(self, group_id: str) -> bool:
        """Remove a group from every server using one atomic persistence update."""
        self.last_error = ""
        group_id = str(group_id)
        previous = {server: list(groups) for server, groups in self.bindings.items()}
        changed = False

        for server_name in list(self.bindings):
            groups = self.bindings[server_name]
            if group_id in groups:
                groups.remove(group_id)
                changed = True
            if not groups:
                del self.bindings[server_name]

        if not changed:
            return False
        if self._save_bindings():
            return True

        self.bindings = previous
        self.last_error = "绑定配置保存失败，请检查 AstrBot 日志"
        return False
