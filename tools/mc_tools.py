class MinecraftTools:
    """Minecraft服务器管理工具集"""

    DANGEROUS_COMMANDS = {"stop", "reload"}

    def __init__(self, rcon_backend):
        self.rcon = rcon_backend
        self.dangerous_commands_enabled = False

    def set_dangerous_commands_enabled(self, enabled: bool):
        self.dangerous_commands_enabled = enabled

    @staticmethod
    def _command_name(token: str) -> str:
        """Normalize a command token for safety checks."""
        token = token.lstrip("/").lower()
        return token.rsplit(":", 1)[-1]

    def _find_dangerous_command(self, command: str) -> str | None:
        tokens = command.strip().split()
        if not tokens:
            return None

        candidate_indexes = {0}
        candidate_indexes.update(
            index + 1
            for index, token in enumerate(tokens[:-1])
            if self._command_name(token) == "run"
        )
        for index in candidate_indexes:
            command_name = self._command_name(tokens[index])
            if command_name in self.DANGEROUS_COMMANDS:
                return command_name
        return None

    async def list_players(self) -> str:
        result = await self.rcon.execute_command("list")
        return result

    async def kick_player(self, player: str, reason: str = "被管理员踢出") -> str:
        result = await self.rcon.execute_command(f"kick {player} {reason}")
        return f"踢出玩家 {player}: {result}"

    async def ban_player(self, player: str, reason: str = "违反服务器规则") -> str:
        result = await self.rcon.execute_command(f"ban {player} {reason}")
        return f"封禁玩家 {player}: {result}"

    async def pardon_player(self, player: str) -> str:
        result = await self.rcon.execute_command(f"pardon {player}")
        return f"解封玩家 {player}: {result}"

    async def op_player(self, player: str) -> str:
        result = await self.rcon.execute_command(f"op {player}")
        return f"给予玩家 {player} OP权限: {result}"

    async def deop_player(self, player: str) -> str:
        result = await self.rcon.execute_command(f"deop {player}")
        return f"移除玩家 {player} OP权限: {result}"

    async def whitelist_add(self, player: str) -> str:
        result = await self.rcon.execute_command(f"whitelist add {player}")
        return f"添加玩家 {player} 到白名单: {result}"

    async def whitelist_remove(self, player: str) -> str:
        result = await self.rcon.execute_command(f"whitelist remove {player}")
        return f"从白名单移除玩家 {player}: {result}"

    async def whitelist_list(self) -> str:
        result = await self.rcon.execute_command("whitelist list")
        return result

    async def banlist(self, ban_type: str = "players") -> str:
        result = await self.rcon.execute_command(f"banlist {ban_type}")
        return result

    async def give_item(self, player: str, item: str, count: int = 1) -> str:
        result = await self.rcon.execute_command(f"give {player} {item} {count}")
        return f"给予 {player} {count}个 {item}: {result}"

    async def teleport_player(self, player: str, target: str) -> str:
        result = await self.rcon.execute_command(f"tp {player} {target}")
        return f"传送玩家 {player} 到 {target}: {result}"

    async def set_gamemode(self, player: str, mode: str) -> str:
        result = await self.rcon.execute_command(f"gamemode {mode} {player}")
        return f"设置玩家 {player} 游戏模式为 {mode}: {result}"

    async def kill_entity(self, target: str) -> str:
        result = await self.rcon.execute_command(f"kill {target}")
        return f"杀死目标 {target}: {result}"

    async def clear_inventory(self, player: str, item: str = None) -> str:
        if item:
            result = await self.rcon.execute_command(f"clear {player} {item}")
        else:
            result = await self.rcon.execute_command(f"clear {player}")
        return f"清空玩家 {player} 背包: {result}"

    async def set_experience(
        self, player: str, amount: int, operation: str = "set", unit: str = "points"
    ) -> str:
        if operation == "add":
            result = await self.rcon.execute_command(f"xp {amount}{unit} {player}")
        else:
            result = await self.rcon.execute_command(f"xp {amount}{unit}L {player}")
        return f"{operation}玩家 {player} 经验 {amount}{unit}: {result}"

    async def say_message(self, message: str) -> str:
        result = await self.rcon.execute_command(f"say {message}")
        return f"广播消息: {result}"

    async def tellraw(
        self,
        message: str,
        sender: str = "Bot",
        color: str = "yellow",
        target: str = "@a",
    ) -> str:
        escaped = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        json_text = f'{{"text":"[{sender}] {escaped}", "color":"{color}"}}'
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
        await self.rcon.execute_command(
            f'title {target} title {{{{"text":"{title_text}","color":"{color}"}}}}'
        )
        if subtitle_text:
            await self.rcon.execute_command(
                f'title {target} subtitle {{{{"text":"{subtitle_text}","color":"{color}"}}}}'
            )
        await self.rcon.execute_command(
            f"title {target} times {fade_in} {stay} {fade_out}"
        )
        return f"显示标题给 {target}"

    async def save_world(self) -> str:
        result = await self.rcon.execute_command("save-all")
        return f"保存世界: {result}"

    async def execute_command(self, command: str) -> str:
        if not command or not command.strip():
            return "错误: 命令不能为空"

        if not self.dangerous_commands_enabled:
            dangerous_command = self._find_dangerous_command(command)
            if dangerous_command:
                return (
                    f"错误: 命令 '{dangerous_command}' 被标记为危险命令，"
                    "请先启用危险命令"
                )

        result = await self.rcon.execute_command(command)
        return f"执行命令 '{command}': {result}"

    async def set_weather(self, weather_type: str, duration: int = None) -> str:
        if duration:
            result = await self.rcon.execute_command(
                f"weather {weather_type} {duration}"
            )
        else:
            result = await self.rcon.execute_command(f"weather {weather_type}")
        return f"设置天气为 {weather_type}: {result}"

    async def set_time(self, time_value: str) -> str:
        result = await self.rcon.execute_command(f"time set {time_value}")
        return f"设置时间为 {time_value}: {result}"

    async def set_difficulty(self, difficulty: str) -> str:
        result = await self.rcon.execute_command(f"difficulty {difficulty}")
        return f"设置难度为 {difficulty}: {result}"

    async def set_gamerule(self, rule: str, value: str) -> str:
        result = await self.rcon.execute_command(f"gamerule {rule} {value}")
        return f"设置游戏规则 {rule} = {value}: {result}"

    async def summon_entity(
        self, entity: str, x: float = None, y: float = None, z: float = None
    ) -> str:
        if x is not None and y is not None and z is not None:
            result = await self.rcon.execute_command(f"summon {entity} {x} {y} {z}")
        else:
            result = await self.rcon.execute_command(f"summon {entity}")
        return f"生成实体 {entity}: {result}"
