# MC Unified 插件

一个统一的 Minecraft 管理插件，整合了多个 AstrBot 插件的核心功能，支持 RCON、WebSocket、MCSManager 等多种管理方式。

## ✨ 功能特性

### 🤖 LLM 自然语言管理
- 无需命令前缀，直接与 LLM 对话即可管理服务器
- 支持玩家管理、游戏操作、服务器管理、世界操作等功能
- 权限控制：支持管理员白名单

### 🔄 QQ ↔ MC 消息互通
- MC 聊天消息同步到绑定的 QQ 群
- QQ 群消息同步到 MC 服务器
- 玩家加入/退出/死亡事件自动推送

### 🖥️ MCSManager 面板管理
- **支持多面板管理**：可配置多个 MCSManager 面板
- 实例控制：启动/停止/重启实例
- 命令执行：通过 RCON 向实例发送命令
- 日志查看：获取实例输出日志

### 🔌 多种通信方式
- **RCON**：直接管理 MC 服务器（无需安装额外模组）
- **WebSocket**：支持鹊桥模组（Queqiao）
- **MCSManager API**：管理面板上的实例

### 🔒 安全特性
- **强制权限控制**：管理员列表为空时，所有写操作自动禁用
- **分级权限**：只读操作（查看玩家列表、白名单等）和写操作（踢人、封禁等）分开控制
- **速率限制**：每分钟最多 10 次操作，防止滥用
- **操作日志**：记录权限判定和操作请求，方便审计

## 📦 安装

### 方法一：手动安装

1. 将 `mc_unified` 文件夹放置到 AstrBot 的 `data/plugins/` 目录
2. 重启 AstrBot

### 方法二：通过插件市场安装

在 AstrBot 管理面板的「插件管理」中搜索安装。

## ⚙️ 配置

在 AstrBot 管理面板中配置本插件：

### 多服务器配置

使用 `mc_servers` 添加命名服务器，每个条目均可独立设置：

- 服务器ID和显示名称
- RCON地址、端口、密码及危险命令开关
- WebSocket/鹊桥地址和令牌
- MC→QQ、QQ→MC、玩家事件同步开关
- 消息发送方式及带服务器名称占位符的前缀

`default_server` 指定默认服务器ID。一个QQ群可以绑定一个或多个服务器；管理操作
若遇到多个绑定，会要求先使用 `mc use <服务器ID>` 选择，避免误操作。

```json
{
  "default_server": "survival",
  "mc_servers": [
    {
      "__template_key": "server",
      "enabled": true,
      "server_id": "survival",
      "display_name": "生存服",
      "enable_dangerous_commands": false,
      "rcon": {
        "enabled": true,
        "host": "10.0.0.10",
        "port": 25575,
        "password": "your_rcon_password"
      },
      "websocket": {
        "enabled": true,
        "url": "ws://10.0.0.10:8080/minecraft/ws",
        "token": "your_websocket_token"
      },
      "message": {
        "sync_chat_mc_to_qq": true,
        "sync_chat_qq_to_mc": true,
        "forward_player_events": true,
        "transport": "auto",
        "mc_message_prefix": "[MC:{server}]",
        "qq_message_prefix": "[QQ]"
      }
    },
    {
      "__template_key": "server",
      "enabled": true,
      "server_id": "creative",
      "display_name": "创造服",
      "rcon": {
        "enabled": true,
        "host": "10.0.0.11",
        "port": 25575,
        "password": "another_password"
      },
      "websocket": {"enabled": false},
      "message": {
        "sync_chat_mc_to_qq": false,
        "sync_chat_qq_to_mc": true,
        "transport": "rcon"
      }
    }
  ]
}
```

> 未添加 `mc_servers` 时，插件会自动把旧版全局 RCON/WebSocket 配置作为
> `default` 服务器加载，现有单服用户无需立刻迁移。

### MCSManager 配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `mcsmanager_enabled` | 是否启用 MCSManager | `false` |
| `mcsmanager_panels` | MCSManager 面板列表（支持多个） | `[]` |

**多面板配置示例（在 WebUI 中添加 template_list 条目）：**
```json
{
  "mcsmanager_panels": [
    {
      "__template_key": "panel",
      "panel_name": "主面板",
      "url": "http://localhost:23333",
      "api_key": "your_api_key_1"
    },
    {
      "__template_key": "panel",
      "panel_name": "备用面板",
      "url": "http://192.168.1.100:23333",
      "api_key": "your_api_key_2"
    }
  ]
}
```

> 在 WebUI 中通过「添加模板」按钮即可可视化配置，无需手写 JSON。

### 旧版单服连接配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `rcon_enabled` / `rcon_host` / `rcon_port` / `rcon_password` | 旧版单服RCON | - |
| `websocket_enabled` | 是否启用 WebSocket | `false` |
| `websocket_url` | WebSocket 地址 | `ws://127.0.0.1:8080/minecraft/ws` |
| `websocket_token` | 认证令牌 | - |

### 旧版单服消息互通配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `sync_chat_mc_to_qq` | MC → QQ 聊天同步 | `false` |
| `sync_chat_qq_to_mc` | QQ → MC 聊天同步 | `false` |
| `mc_message_prefix` | MC 消息前缀 | `[MC]` |
| `qq_message_prefix` | QQ 消息前缀 | `[QQ]` |

### 权限配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `admin_ids` | 管理员列表（QQ号或 MC 玩家名）。**留空则所有写操作禁用** | `[]` |
| `enable_dangerous_commands` | 启用危险命令（如 stop） | `false` |

## 📖 使用方法

### 群绑定

```
mc bind      - 绑定当前群到 MC 服务器
mc servers   - 查看全部服务器、默认项、当前选择和本群绑定
mc use survival - 选择当前管理员后续操作的服务器
mc bind survival - 将当前群绑定到生存服，可重复绑定多个服
mc bindings  - 查看当前群绑定的全部服务器
mc unbind survival - 解除指定服务器绑定
mc unbind all - 解除当前群的全部服务器绑定
mc test survival - 测试指定服务器的 RCON 连接
mc log       - 查看操作日志（最近20条）
mc security  - 查看当前安全状态
```

### LLM 自然语言交互

直接与 LLM 对话即可管理服务器：

```
查看在线玩家
把 Steve 踢出服务器，原因是挂机太久
给 Alex 64 个钻石
把时间调成白天
```

### MCSManager 面板管理

```
查看所有 MCSManager 面板
查看主面板的实例列表
启动备用面板上的生存服务器
```

面板选择按用户隔离，不会影响其他群或管理员。实例操作支持名称、UUID、列表序号，
同名实例跨面板重复时必须同时指定面板名称或使用 UUID。

## 🛠️ 支持的 LLM 工具

### Minecraft 服务器管理

| 工具名 | 功能 | 权限 |
|--------|------|------|
| `minecraft_get_servers` | 查看服务器、选择和群绑定 | 无需权限 |
| `minecraft_select_server` | 选择后续管理操作的服务器 | 管理员 |
| `list_players` | 查看在线玩家 | 无需权限 |
| `kick_player` | 踢出玩家 | 管理员 |
| `ban_player` | 封禁玩家 | 管理员 |
| `pardon_player` | 解封玩家 | 管理员 |
| `op_player` | 授予 OP 权限 | 管理员 |
| `deop_player` | 移除 OP 权限 | 管理员 |
| `whitelist_add` | 添加白名单 | 管理员 |
| `whitelist_remove` | 移除白名单 | 管理员 |
| `whitelist_list` | 查看白名单 | 无需权限 |
| `banlist` | 查看封禁列表 | 无需权限 |
| `give_item` | 给予物品 | 管理员 |
| `teleport_player` | 传送玩家 | 管理员 |
| `set_gamemode` | 设置游戏模式 | 管理员 |
| `say_message` | 服务器广播 | 管理员 |
| `execute_command` | 执行自定义命令 | 管理员 |
| `set_weather` | 设置天气 | 管理员 |
| `set_time` | 设置时间 | 管理员 |
| `set_difficulty` | 设置难度 | 管理员 |
| `set_gamerule` | 设置游戏规则 | 管理员 |

### MCSManager 面板管理

| 工具名 | 功能 | 权限 |
|--------|------|------|
| `mcsmanager_get_panels` | 获取面板列表 | 无需权限 |
| `mcsmanager_select_panel` | 切换当前面板 | 管理员 |
| `mcsmanager_get_instances` | 获取实例列表 | 无需权限 |
| `mcsmanager_start_instance` | 启动实例 | 管理员 |
| `mcsmanager_stop_instance` | 停止实例 | 管理员 |
| `mcsmanager_restart_instance` | 重启实例 | 管理员 |
| `mcsmanager_send_command` | 发送命令 | 管理员 |
| `mcsmanager_get_log` | 获取日志 | 管理员 |
| `mcsmanager_get_overview` | 获取概览 | 无需权限 |

## 🔧 配置示例

```json
{
  "default_server": "survival",
  "mc_servers": [
    {
      "__template_key": "server",
      "server_id": "survival",
      "display_name": "生存服",
      "rcon": {
        "enabled": true,
        "host": "127.0.0.1",
        "port": 25575,
        "password": "your_password"
      },
      "message": {
        "sync_chat_mc_to_qq": true,
        "sync_chat_qq_to_mc": true
      }
    }
  ],
  "mcsmanager_enabled": true,
  "mcsmanager_panels": [
    {
      "__template_key": "panel",
      "panel_name": "主面板",
      "url": "http://localhost:23333",
      "api_key": "your_api_key"
    }
  ],
  "admin_ids": ["123456789", "Steve"],
  "enable_dangerous_commands": false
}
```

## 🔐 安全建议

1. 设置强密码：RCON 和 MCSManager API 密码应使用强密码
2. 限制管理员：在 `admin_ids` 中明确指定可以使用的用户
3. 生产环境必须配置管理员列表：不要留空 `admin_ids`
4. 禁用危险命令：保持 `enable_dangerous_commands` 为 `false`
5. 防火墙配置：确保端口安全

## 🧪 CI 集成测试

常规 `Quality` 工作流会在 Python 3.10/3.12 下运行代码检查、单元测试、
MCSManager API 合约测试和真实本地 WebSocket 通信测试。

`Full Integration` 工作流可从 GitHub Actions 页面手动触发。它会：

- 分别在 AstrBot 4.10.4 和当前支持版本下导入、初始化并卸载插件
- 在 GitHub 托管运行器中临时启动 Minecraft 1.21.1 Docker 服务器
- 通过真实 RCON 协议执行 `list` 和 `say` 冒烟测试
- 测试完成后自动销毁 Minecraft 容器，不连接任何生产服务器

## 📝 更新日志

### v1.1.0
- 🖥️ 新增命名多服务器配置，每服独立RCON、WebSocket和消息选项
- 🔗 支持一个QQ群绑定一个或多个服务器并按来源隔离消息
- 🎯 新增默认服务器、每用户选择和多绑定歧义保护
- 🤖 新增服务器列表与选择LLM工具
- ♻️ 自动兼容旧版单服配置和`default`群绑定

### v1.0.1
- 🔒 修复危险命令斜杠、命名空间和嵌套执行绕过
- 🖥️ MCSManager 实例改为实时解析，面板选择按用户隔离
- 🔌 兼容不同 WebSocket 客户端参数并限制失败重试
- 🧾 修复连接测试、权限日志和群绑定持久化

### v1.0.0
- ✨ 初始版本
- ✨ 支持 RCON 协议管理 MC 服务器
- ✨ 支持 MCSManager 面板管理（多面板）
- ✨ 支持 WebSocket 消息互通（鹊桥模组）
- ✨ QQ ↔ MC 消息互通
- ✨ LLM 自然语言管理（20+ 个工具）

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！
