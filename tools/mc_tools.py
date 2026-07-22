import json
import math
import re

from .command_safety import find_dangerous_command


TEXT_COLORS = {
    "black",
    "dark_blue",
    "dark_green",
    "dark_aqua",
    "dark_red",
    "dark_purple",
    "gold",
    "gray",
    "dark_gray",
    "blue",
    "green",
    "aqua",
    "red",
    "light_purple",
    "yellow",
    "white",
}
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
COORDINATE_PATTERN = re.compile(r"^[~^]?(?:-?(?:\d+(?:\.\d*)?|\.\d+))?$")


class MinecraftTools:
    """Minecraft服务器管理工具集"""

    def __init__(self, rcon_backend):
        self.rcon = rcon_backend
        self.dangerous_commands_enabled = False

    def set_dangerous_commands_enabled(self, enabled: bool):
        self.dangerous_commands_enabled = enabled

    @staticmethod
    def _command_target(value: str) -> str | None:
        target = str(value or "").strip()
        if (
            not target
            or len(target) > 512
            or "\x00" in target
            or any(character.isspace() for character in target)
        ):
            return None
        return target

    @staticmethod
    def _single_line_text(value: str, allow_empty: bool = False) -> str | None:
        text = str(value or "").strip()
        if any(character in text for character in "\x00\r\n"):
            return None
        if not text and not allow_empty:
            return None
        return text

    @staticmethod
    def _teleport_destination(value: str) -> str | None:
        destination = str(value or "").strip()
        parts = destination.split()
        if len(parts) == 1:
            return MinecraftTools._command_target(parts[0])
        if len(parts) == 3 and all(
            COORDINATE_PATTERN.fullmatch(part) for part in parts
        ):
            return " ".join(parts)
        return None

    @staticmethod
    def _coordinate(value) -> str | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return str(value)

    @staticmethod
    def _text_color(value: str) -> str | None:
        color = str(value or "").strip().casefold()
        if color in TEXT_COLORS or HEX_COLOR_PATTERN.fullmatch(color):
            return color
        return None

    async def list_players(self) -> str:
        result = await self.rcon.execute_command("list")
        return result

    async def kick_player(self, player: str, reason: str = "被管理员踢出") -> str:
        target = self._command_target(player)
        reason = self._single_line_text(reason, allow_empty=True)
        if not target or reason is None:
            return "错误: 玩家名称或踢出原因格式无效"
        suffix = f" {reason}" if reason else ""
        result = await self.rcon.execute_command(f"kick {target}{suffix}")
        return f"踢出玩家 {target}: {result}"

    async def ban_player(self, player: str, reason: str = "违反服务器规则") -> str:
        target = self._command_target(player)
        reason = self._single_line_text(reason, allow_empty=True)
        if not target or reason is None:
            return "错误: 玩家名称或封禁原因格式无效"
        suffix = f" {reason}" if reason else ""
        result = await self.rcon.execute_command(f"ban {target}{suffix}")
        return f"封禁玩家 {target}: {result}"

    async def pardon_player(self, player: str) -> str:
        target = self._command_target(player)
        if not target:
            return "错误: 玩家名称格式无效"
        result = await self.rcon.execute_command(f"pardon {target}")
        return f"解封玩家 {target}: {result}"

    async def op_player(self, player: str) -> str:
        target = self._command_target(player)
        if not target:
            return "错误: 玩家名称格式无效"
        result = await self.rcon.execute_command(f"op {target}")
        return f"给予玩家 {target} OP权限: {result}"

    async def deop_player(self, player: str) -> str:
        target = self._command_target(player)
        if not target:
            return "错误: 玩家名称格式无效"
        result = await self.rcon.execute_command(f"deop {target}")
        return f"移除玩家 {target} OP权限: {result}"

    async def whitelist_add(self, player: str) -> str:
        target = self._command_target(player)
        if not target:
            return "错误: 玩家名称格式无效"
        result = await self.rcon.execute_command(f"whitelist add {target}")
        return f"添加玩家 {target} 到白名单: {result}"

    async def whitelist_remove(self, player: str) -> str:
        target = self._command_target(player)
        if not target:
            return "错误: 玩家名称格式无效"
        result = await self.rcon.execute_command(f"whitelist remove {target}")
        return f"从白名单移除玩家 {target}: {result}"

    async def whitelist_list(self) -> str:
        result = await self.rcon.execute_command("whitelist list")
        return result

    async def banlist(self, ban_type: str = "players") -> str:
        ban_type = str(ban_type or "").strip().casefold()
        if ban_type not in {"players", "ips"}:
            return "错误: 封禁列表类型只能是 players 或 ips"
        result = await self.rcon.execute_command(f"banlist {ban_type}")
        return result

    async def give_item(self, player: str, item: str, count: int = 1) -> str:
        target = self._command_target(player)
        item_id = self._command_target(item)
        try:
            count = int(count)
        except (TypeError, ValueError):
            return "错误: 物品数量必须是整数"
        if not target or not item_id or not 1 <= count <= 2_147_483_647:
            return "错误: 玩家、物品ID或数量格式无效"
        result = await self.rcon.execute_command(f"give {target} {item_id} {count}")
        return f"给予 {target} {count}个 {item_id}: {result}"

    async def teleport_player(self, player: str, target: str) -> str:
        player_target = self._command_target(player)
        destination = self._teleport_destination(target)
        if not player_target or not destination:
            return "错误: 玩家或传送目标格式无效"
        result = await self.rcon.execute_command(f"tp {player_target} {destination}")
        return f"传送玩家 {player_target} 到 {destination}: {result}"

    async def set_gamemode(self, player: str, mode: str) -> str:
        target = self._command_target(player)
        mode = str(mode or "").strip().casefold()
        if not target or mode not in {"survival", "creative", "adventure", "spectator"}:
            return "错误: 玩家名称或游戏模式无效"
        result = await self.rcon.execute_command(f"gamemode {mode} {target}")
        return f"设置玩家 {target} 游戏模式为 {mode}: {result}"

    async def kill_entity(self, target: str) -> str:
        target = self._command_target(target)
        if not target:
            return "错误: 目标选择器格式无效"
        result = await self.rcon.execute_command(f"kill {target}")
        return f"杀死目标 {target}: {result}"

    async def clear_inventory(self, player: str, item: str = None) -> str:
        player = self._command_target(player)
        if not player:
            return "错误: 玩家名称或目标选择器格式无效"
        if item:
            item = self._command_target(item)
            if not item:
                return "错误: 物品ID格式无效"
            result = await self.rcon.execute_command(f"clear {player} {item}")
        else:
            result = await self.rcon.execute_command(f"clear {player}")
        return f"清空玩家 {player} 背包: {result}"

    async def set_experience(
        self, player: str, amount: int, operation: str = "set", unit: str = "points"
    ) -> str:
        target = self._command_target(player)
        if not target:
            return "错误: 玩家名称或目标选择器格式无效"
        operation = str(operation or "").strip().casefold()
        if operation not in {"add", "set"}:
            return "错误: 经验操作只能是 add 或 set"
        unit = str(unit or "").strip().casefold()
        if unit not in {"points", "levels"}:
            return "错误: 经验单位只能是 points 或 levels"
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return "错误: 经验数量必须是整数"
        if operation == "set" and amount < 0:
            return "错误: set 操作的经验数量不能为负数"

        result = await self.rcon.execute_command(
            f"experience {operation} {target} {amount} {unit}"
        )
        return f"{operation}玩家 {target} 经验 {amount} {unit}: {result}"

    async def say_message(self, message: str) -> str:
        message = self._single_line_text(message)
        if not message:
            return "错误: 广播消息不能为空或包含换行符"
        result = await self.rcon.execute_command(f"say {message}")
        return f"广播消息: {result}"

    async def tellraw(
        self,
        message: str,
        sender: str = "Bot",
        color: str = "yellow",
        target: str = "@a",
    ) -> str:
        target = self._command_target(target)
        if not target:
            return "错误: 目标选择器格式无效"
        color = self._text_color(color)
        if not color:
            return "错误: 文字颜色无效"
        json_text = json.dumps(
            {"text": f"[{sender}] {message}", "color": color},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = await self.rcon.execute_command(f"tellraw {target} {json_text}")
        return f"发送消息到 {target}: {result}"

    async def title(
        self,
        title_text: str,
        subtitle_text: str = "",
        color: str = "white",
        target: str = "@a",
        fade_in: int = 10,
        stay: int = 70,
        fade_out: int = 20,
    ) -> str:
        target = self._command_target(target)
        if not target:
            return "错误: 目标选择器格式无效"
        color = self._text_color(color)
        if not color:
            return "错误: 文字颜色无效"
        try:
            timings = tuple(int(value) for value in (fade_in, stay, fade_out))
        except (TypeError, ValueError):
            return "错误: 标题时间必须是整数"
        if any(value < 0 for value in timings):
            return "错误: 标题时间不能为负数"

        title_json = json.dumps(
            {"text": str(title_text), "color": color},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        times_result = await self.rcon.execute_command(
            f"title {target} times {timings[0]} {timings[1]} {timings[2]}"
        )
        if str(times_result).startswith("错误:"):
            return f"设置标题时间失败: {times_result}"
        if subtitle_text:
            subtitle_json = json.dumps(
                {"text": str(subtitle_text), "color": color},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            subtitle_result = await self.rcon.execute_command(
                f"title {target} subtitle {subtitle_json}"
            )
            if str(subtitle_result).startswith("错误:"):
                return f"设置副标题失败: {subtitle_result}"
        title_result = await self.rcon.execute_command(
            f"title {target} title {title_json}"
        )
        if str(title_result).startswith("错误:"):
            return f"显示标题失败: {title_result}"
        return f"显示标题给 {target}: {title_result}"

    async def save_world(self) -> str:
        result = await self.rcon.execute_command("save-all")
        return f"保存世界: {result}"

    async def execute_command(self, command: str) -> str:
        command = self._single_line_text(command)
        if not command:
            return "错误: 命令不能为空"
        command = command.removeprefix("/").lstrip()
        if not command:
            return "错误: 命令不能为空"

        if not self.dangerous_commands_enabled:
            dangerous_command = find_dangerous_command(command)
            if dangerous_command:
                return (
                    f"错误: 命令 '{dangerous_command}' 被标记为危险命令，"
                    "请先启用危险命令"
                )

        result = await self.rcon.execute_command(command)
        return f"执行命令 '{command}': {result}"

    async def set_weather(self, weather_type: str, duration: int = None) -> str:
        weather_type = str(weather_type or "").strip().casefold()
        if weather_type not in {"clear", "rain", "thunder"}:
            return "错误: 天气类型只能是 clear、rain 或 thunder"
        if duration is not None:
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                return "错误: 天气持续时间必须是整数"
            if not 0 <= duration <= 1_000_000:
                return "错误: 天气持续时间必须在 0 至 1000000 秒之间"
            result = await self.rcon.execute_command(
                f"weather {weather_type} {duration}"
            )
        else:
            result = await self.rcon.execute_command(f"weather {weather_type}")
        return f"设置天气为 {weather_type}: {result}"

    async def set_time(self, time_value: str) -> str:
        time_value = str(time_value or "").strip().casefold()
        valid_named_times = {"day", "night", "noon", "midnight"}
        if time_value not in valid_named_times:
            try:
                ticks = int(time_value)
            except (TypeError, ValueError):
                return "错误: 时间只能是 day、night、noon、midnight 或非负刻数"
            if not 0 <= ticks <= 2_147_483_647:
                return "错误: 时间刻数必须在 0 至 2147483647 之间"
            time_value = str(ticks)
        result = await self.rcon.execute_command(f"time set {time_value}")
        return f"设置时间为 {time_value}: {result}"

    async def set_difficulty(self, difficulty: str) -> str:
        difficulty = str(difficulty or "").strip().casefold()
        if difficulty not in {"peaceful", "easy", "normal", "hard"}:
            return "错误: 难度只能是 peaceful、easy、normal 或 hard"
        result = await self.rcon.execute_command(f"difficulty {difficulty}")
        return f"设置难度为 {difficulty}: {result}"

    async def set_gamerule(self, rule: str, value: str) -> str:
        rule = self._command_target(rule)
        value = self._command_target(value)
        if not rule or not value:
            return "错误: 游戏规则名称或值格式无效"
        result = await self.rcon.execute_command(f"gamerule {rule} {value}")
        return f"设置游戏规则 {rule} = {value}: {result}"

    async def summon_entity(
        self, entity: str, x: float = None, y: float = None, z: float = None
    ) -> str:
        entity = self._command_target(entity)
        if not entity:
            return "错误: 实体ID格式无效"
        coordinates = (x, y, z)
        supplied = [value is not None for value in coordinates]
        if any(supplied) and not all(supplied):
            return "错误: 生成实体时必须同时提供 x、y、z 三个坐标"
        if all(supplied):
            normalized = tuple(self._coordinate(value) for value in coordinates)
            if any(value is None for value in normalized):
                return "错误: 实体坐标必须是有限数字"
            result = await self.rcon.execute_command(
                f"summon {entity} {normalized[0]} {normalized[1]} {normalized[2]}"
            )
        else:
            result = await self.rcon.execute_command(f"summon {entity}")
        return f"生成实体 {entity}: {result}"
