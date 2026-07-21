# 更新日志

## v1.2.0

### 调整

- 明确插件主线为统一管理多台Minecraft服务器，QQ群绑定仅用于可选消息互通
- 管理目标不再受当前QQ群绑定影响，解析顺序改为显式参数、用户选择、默认服务器
- 所有Minecraft管理LLM工具新增可选`server_name`参数，可直接指定操作目标

### 新增

- `mc status [服务器ID|all]`和`minecraft_get_status`汇总连接状态
- `mc players [服务器ID|all]`及`list_players(server_name="all")`汇总在线玩家

## v1.1.0

### 新增

- 新增命名 Minecraft 服务器列表，每个服务器可独立配置 RCON、WebSocket 和消息同步
- 新增默认服务器、每用户当前服务器选择，以及 `mc servers` / `mc use` / `mc bindings` 命令
- QQ群可同时绑定多个服务器，QQ→MC消息按绑定扇出，MC→QQ消息按来源服务器隔离
- 新增 `minecraft_get_servers` 和 `minecraft_select_server` LLM工具

### 兼容性

- 未配置服务器列表时自动使用旧版单服配置，无需立即迁移
- 旧版 `default` 群绑定会自动映射到新的默认服务器
- MCSManager多面板和多实例管理保持独立可用

## v1.0.1

### 修复

- 修复 `/stop`、命名空间命令和 `execute ... run stop` 绕过危险命令开关
- 修复 RCON 连接失败时 `mc test` 仍显示成功
- MCSManager 操作不再依赖全局实例缓存，面板选择按用户隔离
- 兼容多个 websockets 版本的请求头参数，并让未知错误受重试上限约束
- 修正 AstrBot 数据目录、绑定持久化错误处理和操作日志
- 为全部 LLM 工具补充参数说明

## v1.0.0

### 新增功能

- ✨ **RCON 服务器管理**
  - 支持通过 RCON 协议直接管理 MC 服务器
  - 无需安装额外模组/插件
  - 支持玩家管理、游戏操作、服务器管理、世界操作

- ✨ **MCSManager 面板管理**
  - 支持多面板配置和管理
  - 实例控制：启动/停止/重启
  - 命令执行和日志查看

- ✨ **WebSocket 消息互通**
  - 支持鹊桥模组（Queqiao）
  - MC 聊天消息同步到 QQ 群
  - QQ 群消息同步到 MC 服务器
  - 玩家事件（加入/退出/死亡）自动推送

- ✨ **LLM 自然语言管理**
  - 20+ 个管理工具
  - 无需命令前缀，直接对话管理
  - 权限控制：管理员白名单

- ✨ **群绑定系统**
  - `mc bind` / `mc unbind` 命令
  - 支持多个 QQ 群绑定

### 核心特性

- 🎯 **统一架构**：整合多个插件的核心功能
- 🔌 **模块化设计**：后端、工具、管理器分离
- 🔒 **权限控制**：管理员白名单机制
- ⚙️ **高度可配置**：各功能独立启用/禁用

### 技术实现

- 使用抽象接口定义后端层
- MCSManager 多面板支持
- WebSocket 自动重连机制
- RCON 连接池管理

### 配置项

- RCON 配置：host、port、password
- MCSManager 配置：多面板列表（name、url、api_key）
- WebSocket 配置：url、token
- 消息互通配置：同步开关、前缀设置
- 权限配置：admin_ids、危险命令开关
