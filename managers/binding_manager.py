import json
import os
from copy import deepcopy
from typing import Dict, List

from astrbot.api import logger


class GroupBindingManager:
    """群绑定管理器"""

    def __init__(
        self,
        data_dir: str,
        configured_bindings: Dict[str, List[str]] | None = None,
    ):
        self.data_dir = data_dir
        self.bindings: Dict[str, List[str]] = {}
        self.configured_bindings: Dict[str, List[str]] = {
            str(server_id): list(dict.fromkeys(str(group_id) for group_id in groups))
            for server_id, groups in (configured_bindings or {}).items()
        }
        self.group_sessions: Dict[str, str] = {}
        self.last_error = ""
        self._load_bindings()
        self._load_group_sessions()

    @staticmethod
    def normalize_configured_bindings(
        raw_bindings, valid_server_ids: set[str]
    ) -> tuple[Dict[str, List[str]], List[str]]:
        """Convert legacy group-centric WebUI rows into server-to-groups mapping."""
        normalized: Dict[str, List[str]] = {}
        warnings = []
        if not raw_bindings:
            return normalized, warnings
        if not isinstance(raw_bindings, list):
            return normalized, ["QQ群绑定配置必须是列表"]

        for index, entry in enumerate(raw_bindings, 1):
            if not isinstance(entry, dict):
                warnings.append(f"忽略无效的QQ群绑定配置 #{index}")
                continue
            if not entry.get("enabled", True):
                continue
            group_id = str(entry.get("group_id", "") or "").strip()
            raw_server_ids = entry.get("server_ids", []) or []
            if isinstance(raw_server_ids, str):
                raw_server_ids = [raw_server_ids]
            if not group_id or not isinstance(raw_server_ids, list):
                warnings.append(
                    f"忽略不完整的QQ群绑定配置 #{index}: 需要 group_id 和 server_ids"
                )
                continue

            matched_count = 0
            for raw_server_id in raw_server_ids:
                server_id = str(raw_server_id or "").strip()
                if not server_id:
                    continue
                if server_id not in valid_server_ids:
                    warnings.append(
                        f"QQ群绑定 #{index} 引用了不存在的服务器ID: {server_id}"
                    )
                    continue
                groups = normalized.setdefault(server_id, [])
                if group_id not in groups:
                    groups.append(group_id)
                matched_count += 1
            if matched_count == 0:
                warnings.append(f"QQ群绑定 #{index} 没有可用的服务器ID")

        return normalized, warnings

    @staticmethod
    def normalize_server_bindings(
        raw_servers, valid_server_ids: set[str]
    ) -> tuple[Dict[str, List[str]], List[str]]:
        """Read the server-centric QQ group lists shown in the current WebUI."""
        normalized: Dict[str, List[str]] = {}
        warnings = []
        if not raw_servers:
            return normalized, warnings
        if not isinstance(raw_servers, list):
            return normalized, ["Minecraft服务器配置必须是列表"]

        seen_server_ids = set()
        for index, entry in enumerate(raw_servers, 1):
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            server_id = str(entry.get("server_id", "") or "").strip()
            if not server_id:
                continue
            if server_id in seen_server_ids:
                warnings.append(f"忽略重复服务器 #{index} 的QQ群列表: {server_id}")
                continue
            seen_server_ids.add(server_id)
            if server_id not in valid_server_ids:
                continue

            raw_group_ids = entry.get("qq_group_ids", []) or []
            if isinstance(raw_group_ids, str):
                raw_group_ids = [raw_group_ids]
            if not isinstance(raw_group_ids, list):
                warnings.append(f"服务器 {server_id} 的QQ群号配置必须是列表")
                continue

            groups = normalized.setdefault(server_id, [])
            for raw_group_id in raw_group_ids:
                group_id = str(raw_group_id or "").strip()
                if group_id and group_id not in groups:
                    groups.append(group_id)
            if not groups:
                normalized.pop(server_id, None)

        return normalized, warnings

    @staticmethod
    def merge_configured_bindings(
        *binding_maps: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        merged: Dict[str, List[str]] = {}
        for binding_map in binding_maps:
            for server_id, group_ids in binding_map.items():
                groups = merged.setdefault(str(server_id), [])
                for group_id in group_ids:
                    group_id = str(group_id)
                    if group_id not in groups:
                        groups.append(group_id)
        return merged

    @staticmethod
    def migrate_legacy_config(
        raw_servers, raw_bindings
    ) -> tuple[list, list, int, bool, List[str]]:
        """Move valid v1.5.0 group-centric routes into their server profiles."""
        warnings = []
        if not raw_bindings:
            return raw_servers, raw_bindings, 0, False, warnings
        if not isinstance(raw_servers, list) or not isinstance(raw_bindings, list):
            warnings.append("旧版QQ群绑定无法自动迁移：服务器或绑定配置不是列表")
            return raw_servers, raw_bindings, 0, False, warnings

        migrated_servers = deepcopy(raw_servers)
        server_entries = {}
        for entry in migrated_servers:
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            server_id = str(entry.get("server_id", "") or "").strip()
            if server_id and server_id not in server_entries:
                server_entries[server_id] = entry

        remaining_bindings = []
        migrated_count = 0
        changed = False
        for index, entry in enumerate(raw_bindings, 1):
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                remaining_bindings.append(deepcopy(entry))
                continue

            group_id = str(entry.get("group_id", "") or "").strip()
            raw_server_ids = entry.get("server_ids", []) or []
            if isinstance(raw_server_ids, str):
                raw_server_ids = [raw_server_ids]
            if not group_id or not isinstance(raw_server_ids, list):
                remaining_bindings.append(deepcopy(entry))
                warnings.append(f"旧版QQ群绑定 #{index} 不完整，已保留原配置")
                continue

            unresolved_server_ids = []
            for raw_server_id in raw_server_ids:
                server_id = str(raw_server_id or "").strip()
                server_entry = server_entries.get(server_id)
                if not server_entry:
                    unresolved_server_ids.append(server_id)
                    continue

                raw_group_ids = server_entry.get("qq_group_ids", []) or []
                if isinstance(raw_group_ids, str):
                    raw_group_ids = [raw_group_ids]
                if not isinstance(raw_group_ids, list):
                    raw_group_ids = []
                group_ids = list(
                    dict.fromkeys(
                        str(value or "").strip()
                        for value in raw_group_ids
                        if str(value or "").strip()
                    )
                )
                if group_id not in group_ids:
                    group_ids.append(group_id)
                    migrated_count += 1
                server_entry["qq_group_ids"] = group_ids
                changed = True

            if unresolved_server_ids:
                remaining_entry = deepcopy(entry)
                remaining_entry["server_ids"] = unresolved_server_ids
                remaining_bindings.append(remaining_entry)
                if len(unresolved_server_ids) != len(raw_server_ids):
                    changed = True
                warnings.append(
                    f"旧版QQ群绑定 #{index} 仍引用不存在的服务器: "
                    + ", ".join(value or "<空ID>" for value in unresolved_server_ids)
                )
            elif raw_server_ids:
                changed = True
            else:
                remaining_bindings.append(deepcopy(entry))

        return (
            migrated_servers,
            remaining_bindings,
            migrated_count,
            changed,
            warnings,
        )

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
        if group_id in self.configured_bindings.get(server_name, []):
            self.last_error = "该群与服务器已由WebUI配置绑定，无需重复添加"
            return False
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
        if group_id in self.configured_bindings.get(server_name, []):
            if group_id in self.bindings.get(server_name, []):
                self.bindings[server_name].remove(group_id)
                if not self.bindings[server_name]:
                    del self.bindings[server_name]
                if self._save_bindings():
                    self.last_error = "已清除重复的指令绑定；WebUI配置仍然生效"
                    return True
                self.bindings.setdefault(server_name, []).append(group_id)
                self.last_error = "绑定配置保存失败，请检查 AstrBot 日志"
                return False
            self.last_error = "该绑定来自WebUI配置，请在插件配置页删除"
            return False
        if server_name in self.bindings and group_id in self.bindings[server_name]:
            self.bindings[server_name].remove(group_id)
            if self._save_bindings():
                return True
            self.bindings[server_name].append(group_id)
            self.last_error = "绑定配置保存失败，请检查 AstrBot 日志"
        return False

    def is_group_bound(self, group_id: str, server_name: str = "default") -> bool:
        group_id = str(group_id)
        return group_id in self.get_bound_groups(server_name)

    def get_bound_groups(self, server_name: str = "default") -> List[str]:
        return list(
            dict.fromkeys(
                self.configured_bindings.get(server_name, [])
                + self.bindings.get(server_name, [])
            )
        )

    def get_binding_sources(self, group_id: str, server_name: str) -> List[str]:
        group_id = str(group_id)
        sources = []
        if group_id in self.configured_bindings.get(server_name, []):
            sources.append("WebUI")
        if group_id in self.bindings.get(server_name, []):
            sources.append("指令")
        return sources

    def get_group_servers(self, group_id: str) -> List[str]:
        """Return every server currently associated with a group."""
        group_id = str(group_id)
        server_names = list(self.configured_bindings) + list(self.bindings)
        return [
            server_name
            for server_name in dict.fromkeys(server_names)
            if group_id in self.get_bound_groups(server_name)
        ]

    def get_all_group_ids(self) -> List[str]:
        group_ids = []
        for server_name in list(self.configured_bindings) + list(self.bindings):
            group_ids.extend(self.get_bound_groups(server_name))
        return list(dict.fromkeys(group_ids))

    def unbind_group_from_all(self, group_id: str) -> bool:
        """Remove command-created routes; WebUI routes remain authoritative."""
        self.last_error = ""
        group_id = str(group_id)
        configured_servers = [
            server_name
            for server_name, groups in self.configured_bindings.items()
            if group_id in groups
        ]
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
            if configured_servers:
                self.last_error = "该群的绑定全部来自WebUI配置，请在插件配置页删除"
            return False
        if self._save_bindings():
            if configured_servers:
                self.last_error = "已清除指令添加的绑定；WebUI配置的绑定仍然生效"
            return True

        self.bindings = previous
        self.last_error = "绑定配置保存失败，请检查 AstrBot 日志"
        return False
