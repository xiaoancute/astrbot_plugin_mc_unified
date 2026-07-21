import time
from typing import Dict
from astrbot.api import logger


class PermissionManager:
    """权限管理器 - 安全强化版"""

    def __init__(self, admin_ids: list = None):
        self.admin_ids = {str(user_id) for user_id in (admin_ids or [])}
        self.action_log: list = []
        self.rate_limits: Dict[str, dict] = {}
        self.max_actions_per_minute = 10
        self.max_log_entries = 100

    def is_admin(self, user_id: str) -> bool:
        if not self.admin_ids or user_id is None:
            return False

        user_id = str(user_id)
        if user_id in self.admin_ids:
            return True

        if user_id.startswith("mc_player_"):
            player_name = user_id.replace("mc_player_", "")
            if player_name in self.admin_ids:
                return True

        return False

    def check_permission(
        self, user_id: str, action: str = "unknown"
    ) -> tuple[bool, str]:
        if not self.is_admin(user_id):
            self._log_action(user_id, action, False, "权限不足")
            return False, f"❌ 权限不足：用户 {user_id} 不在管理员列表中"

        if not self._check_rate_limit(user_id):
            self._log_action(user_id, action, False, "操作过于频繁")
            return False, "❌ 操作过于频繁，请稍后再试"

        self._log_action(user_id, action, True, "已授权")
        return True, ""

    def check_read_only(
        self, user_id: str, action: str = "read_only"
    ) -> tuple[bool, str]:
        if self.admin_ids and not self.is_admin(user_id):
            self._log_action(user_id, action, False, "权限不足")
            return False, f"❌ 权限不足：用户 {user_id} 不在管理员列表中"
        self._log_action(user_id, action, True, "只读操作")
        return True, ""

    def _check_rate_limit(self, user_id: str) -> bool:
        now = time.time()
        user_key = str(user_id)

        if user_key not in self.rate_limits:
            self.rate_limits[user_key] = {"count": 0, "start_time": now}

        limit = self.rate_limits[user_key]
        if now - limit["start_time"] > 60:
            limit["count"] = 0
            limit["start_time"] = now

        if limit["count"] >= self.max_actions_per_minute:
            return False

        limit["count"] += 1
        return True

    def _log_action(self, user_id: str, action: str, success: bool, reason: str = ""):
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": user_id,
            "action": action,
            "success": success,
            "reason": reason,
        }
        self.action_log.append(log_entry)

        if len(self.action_log) > self.max_log_entries:
            self.action_log = self.action_log[-self.max_log_entries :]

        if success:
            logger.info(f"[权限日志] 用户 {user_id} 执行操作: {action}")
        else:
            logger.warning(f"[权限日志] 用户 {user_id} 操作失败 ({action}): {reason}")

    def add_admin(self, user_id: str):
        self.admin_ids.add(str(user_id))
        logger.info(f"[权限管理] 添加管理员: {user_id}")

    def remove_admin(self, user_id: str):
        self.admin_ids.discard(str(user_id))
        logger.info(f"[权限管理] 移除管理员: {user_id}")

    def get_action_log(self, limit: int = 20) -> str:
        recent_logs = self.action_log[-limit:]
        if not recent_logs:
            return "暂无操作日志"

        result = "📋 操作日志（最近20条）:\n"
        for log in recent_logs:
            status = "✅" if log["success"] else "❌"
            reason = f" ({log['reason']})" if log["reason"] else ""
            result += f"[{log['timestamp']}] {status} {log['user_id']} - {log['action']}{reason}\n"
        return result

    def is_security_enabled(self) -> bool:
        return len(self.admin_ids) > 0
