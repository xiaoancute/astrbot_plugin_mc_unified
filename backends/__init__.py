from abc import ABC, abstractmethod
from typing import List, Dict, Any


class ServerBackend(ABC):
    """服务器后端抽象接口"""

    @abstractmethod
    async def connect(self) -> bool:
        """连接服务器"""
        pass

    @abstractmethod
    async def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    async def is_connected(self) -> bool:
        """检查连接状态"""
        pass

    @abstractmethod
    async def execute_command(self, command: str) -> str:
        """执行命令"""
        pass

    @abstractmethod
    async def get_online_players(self) -> List[str]:
        """获取在线玩家列表"""
        pass

    @abstractmethod
    async def send_message(self, message: str, target: str = "@a") -> str:
        """发送消息"""
        pass


class InstanceBackend(ABC):
    """实例管理后端抽象接口"""

    @abstractmethod
    async def get_instances(self) -> List[Dict[str, Any]]:
        """获取实例列表"""
        pass

    @abstractmethod
    async def start_instance(self, daemon_id: str, instance_uuid: str) -> bool:
        """启动实例"""
        pass

    @abstractmethod
    async def stop_instance(self, daemon_id: str, instance_uuid: str) -> bool:
        """停止实例"""
        pass

    @abstractmethod
    async def restart_instance(self, daemon_id: str, instance_uuid: str) -> bool:
        """重启实例"""
        pass

    @abstractmethod
    async def send_command_to_instance(
        self, daemon_id: str, instance_uuid: str, command: str
    ) -> str:
        """向实例发送命令"""
        pass

    @abstractmethod
    async def get_instance_log(
        self, daemon_id: str, instance_uuid: str, size: int = 100
    ) -> str:
        """获取实例日志"""
        pass

    @abstractmethod
    async def get_overview(self) -> Dict[str, Any]:
        """获取概览信息"""
        pass


class MessageBackend(ABC):
    """消息互通后端抽象接口"""

    def __init__(self):
        self._message_callback = None
        self._player_join_callback = None
        self._player_leave_callback = None
        self._player_death_callback = None

    @abstractmethod
    async def start_listening(self):
        """开始监听消息"""
        pass

    @abstractmethod
    async def stop_listening(self):
        """停止监听消息"""
        pass

    @abstractmethod
    async def send_to_mc(self, message: str):
        """发送消息到MC"""
        pass

    @abstractmethod
    async def send_to_qq(self, message: str, group_id: str):
        """发送消息到QQ群"""
        pass

    def set_message_callback(self, callback):
        """设置消息回调"""
        self._message_callback = callback

    def set_player_join_callback(self, callback):
        """设置玩家加入回调"""
        self._player_join_callback = callback

    def set_player_leave_callback(self, callback):
        """设置玩家离开回调"""
        self._player_leave_callback = callback

    def set_player_death_callback(self, callback):
        """设置玩家死亡回调"""
        self._player_death_callback = callback
