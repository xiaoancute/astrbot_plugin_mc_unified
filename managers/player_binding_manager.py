import json
import os
import re

from astrbot.api import logger


class PlayerBindingManager:
    """QQ用户与Minecraft游戏ID的双向绑定"""

    PLAYER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$")

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.user_to_player: dict[str, str] = {}
        self.player_to_user: dict[str, str] = {}
        self._load()

    def _load(self):
        path = os.path.join(self.data_dir, "player_bindings.json")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for raw_user_id, raw_player_name in data.items():
                        user_id = str(raw_user_id or "").strip()
                        player_name = str(raw_player_name or "").strip()
                        if not user_id or not self._valid_player_name(player_name):
                            logger.warning(f"忽略无效玩家绑定: {user_id!r}")
                            continue
                        player_key = self._player_key(player_name)
                        if player_key in self.player_to_user:
                            logger.warning(f"忽略重复游戏ID绑定: {player_name}")
                            continue
                        self.user_to_player[user_id] = player_name
                        self.player_to_user[player_key] = user_id
        except Exception as e:
            logger.warning(f"读取玩家绑定失败: {e}")

    @classmethod
    def _valid_player_name(cls, player_name: str) -> bool:
        return bool(cls.PLAYER_NAME_PATTERN.fullmatch(player_name))

    @staticmethod
    def _player_key(player_name: str) -> str:
        return str(player_name or "").strip().casefold()

    def _save(self) -> bool:
        path = os.path.join(self.data_dir, "player_bindings.json")
        tmp = f"{path}.tmp"
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.user_to_player, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return True
        except Exception as e:
            logger.error(f"保存玩家绑定失败: {e}")
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return False

    def bind(self, user_id: str, player_name: str) -> tuple[bool, str]:
        user_id = str(user_id or "").strip()
        player_name = str(player_name or "").strip()
        if not user_id or not player_name:
            return False, "用户ID和游戏ID不能为空"
        if not self._valid_player_name(player_name):
            return False, "游戏ID只能包含字母、数字、下划线、点和连字符，长度1至32"

        existing_player = self.user_to_player.get(user_id)
        if existing_player and self._player_key(existing_player) == self._player_key(
            player_name
        ):
            return True, f"已绑定: {user_id} → {player_name}"

        player_key = self._player_key(player_name)
        existing_user = self.player_to_user.get(player_key)
        if existing_user and existing_user != user_id:
            return False, f"游戏ID {player_name} 已被 {existing_user} 绑定"

        existing_player_key = (
            self._player_key(existing_player) if existing_player else None
        )
        if existing_player_key:
            self.player_to_user.pop(existing_player_key, None)
        self.user_to_player[user_id] = player_name
        self.player_to_user[player_key] = user_id
        if self._save():
            return True, f"✅ 已绑定 {user_id} → {player_name}"
        self.user_to_player.pop(user_id, None)
        self.player_to_user.pop(player_key, None)
        if existing_player:
            self.user_to_player[user_id] = existing_player
            self.player_to_user[self._player_key(existing_player)] = user_id
        return False, "保存失败，请检查日志"

    def unbind(self, user_id: str) -> tuple[bool, str]:
        user_id = str(user_id or "").strip()
        player_name = self.user_to_player.pop(user_id, None)
        if not player_name:
            return False, f"用户 {user_id} 未绑定游戏ID"
        self.player_to_user.pop(self._player_key(player_name), None)
        if self._save():
            return True, f"✅ 已解除绑定 {user_id} → {player_name}"
        self.user_to_player[user_id] = player_name
        self.player_to_user[self._player_key(player_name)] = user_id
        return False, "保存失败，请检查日志"

    def get_player(self, user_id: str) -> str | None:
        return self.user_to_player.get(str(user_id or "").strip())

    def get_user(self, player_name: str) -> str | None:
        return self.player_to_user.get(self._player_key(player_name))

    def all_bindings(self) -> dict[str, str]:
        return dict(self.user_to_player)
