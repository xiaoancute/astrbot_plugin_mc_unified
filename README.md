# MC Unified

统一的 Minecraft 管理插件，集成多服务器 RCON、MCSManager 面板、QQ↔MC 消息互通和 LLM 自然语言管理。

## 功能一览

| 功能 | 说明 |
|------|------|
| 多服务器管理 | 一台 AstrBot 管理多台 MC 服务器，各自独立 RCON/WebSocket 配置 |
| QQ↔MC 消息互通 | 多对多群服绑定，聊天/事件双向转发 |
| MCSManager 面板 | 多面板管理，实例启停/命令/日志/文件浏览 |
| LLM 自然语言 | 默认只读，管理员可明确开启 FULL 模式执行写操作 |
| 安全控制 | 管理员白名单、速率限制、操作日志、危险命令拦截 |

## 安装

1. 下载 Release ZIP，将插件文件夹放入 AstrBot 的 `data/plugins/` 目录
2. 重启 AstrBot，在 WebUI 的插件管理中配置

升级手工安装版本时，确认 `data/plugins/` 下只保留一个插件目录；不要同时留下旧的 `mc_unified/` 和新的 `astrbot_plugin_mc_unified/`，否则 AstrBot 可能重复加载。

## 配置

所有配置项在 AstrBot WebUI 的插件配置页面完成，无需手动编辑 JSON。

### 全局设置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| **管理员ID列表** | 允许使用管理功能的用户ID（QQ号）。留空则所有写操作禁用 | `[]` |
| **AI管理权限模式** | `readonly`（LLM只能查询）或 `full`（LLM可执行写操作） | `readonly` |
| **默认服务器ID** | 未指定服务器时的默认操作目标。留空则使用列表第一项 | `""` |

### Minecraft 服务器列表

每张卡片代表一台服务器，包含：

- **服务器ID** — 唯一标识，用于命令和 LLM 工具（如 `survival`、`creative`）
- **显示名称** — 消息中展示的名称
- **QQ群号** — 绑定的群列表，同一群号可填入多台服务器实现多对多
- **RCON** — 地址、端口、密码
- **WebSocket** — 鹊桥模组地址和令牌
- **消息互通** — MC→QQ、QQ→MC、玩家事件、LLM回复转发开关，前缀模板支持 `{server}` 占位符
- **自定义指令** — 管理员预配置固定命令模板和参数占位符

### MCSManager 面板列表

添加面板后管理功能自动启用，无需额外开关。每张面板卡片包含：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| 面板名称 | 标识名称，区分不同面板 | `""` |
| 面板地址 | MCSManager Web 地址 | `""` |
| API密钥 | 管理员账户生成的 API Key | `""` |
| **SSL验证模式** | 见下表 | `default` |
| 自定义CA证书路径 | 仅 `custom` 模式需要填 | `""` |
| 允许危险命令 | 是否允许 stop/reload 等命令 | `false` |

#### SSL 验证模式

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `default` | 系统CA验证 | Let's Encrypt 等正规证书 |
| `auto_trust` | 首次连接获取并锁定服务器证书（TOFU），后续验证该证书 | 自签名证书；首次连接必须处于可信网络 |
| `disable` | 完全跳过验证 | 仅限可信内网临时排查，不建议长期使用 |
| `custom` | 使用指定路径的 CA 证书文件 | 有证书文件 (.pem/.crt) |

> `auto_trust` 类似 SSH 首次连接：第一次连接时获取服务器证书并缓存到插件数据目录的 `certs/`，之后每次连接都验证证书是否匹配。获取失败时插件会保持严格校验，不会自动降级为跳过TLS验证。服务器更换证书后需删除对应缓存并在可信网络中重新信任。

如果日志出现 `CERTIFICATE_VERIFY_FAILED` 或 `self-signed certificate`，说明请求没有成功，不能据此判断实例为空。请将该面板的SSL模式改为 `auto_trust`，或用 `custom` 指定可信CA证书，然后重启插件。修复后的工具会直接返回连接错误，不再把失败显示成空概览或空实例列表。

## 使用方法

### 命令

```text
/mc servers              查看全部服务器
/mc status [all|服务器ID]  检查连接状态
/mc players [all|服务器ID] 查看在线玩家
/mc use <服务器ID>        切换当前操作目标
/mc test [服务器ID]       测试RCON连接

/mc bind <服务器ID> [群号]  绑定当前群到服务器
/mc unbind <服务器ID> [群号] 解除绑定
/mc bindings [all]        查看绑定关系

/mc myid <游戏ID>         绑定QQ用户到MC游戏ID
/mc myid                  查看当前绑定
/mc myid clear            解除绑定

/mc cmd                   列出当前服务器的自定义指令
/mc cmd <名称> [参数...]   执行自定义指令模板

/mc ai-mode status        查看AI权限
/mc ai-mode full CONFIRM  开启FULL模式（需确认）
/mc ai-mode readonly      恢复只读

/mc log                   查看操作日志
/mc security              查看安全状态
```

### LLM 自然语言

默认只读，直接对话即可查询：

```
查看在线玩家
查看全部服务器状态
查看主面板的实例列表
读取生存服的 /server.properties
```

写操作需要管理员执行 `/mc ai-mode full CONFIRM` 开启。FULL 是全局配置并会保持到管理员主动恢复；完成操作后应立即执行 `/mc ai-mode readonly`。

> ⚠️ FULL 模式下模型可执行踢人、封禁、OP、命令、世界修改和实例启停。模型可能产生幻觉或选错目标，风险由用户承担。

### MCSManager 面板操作

```
查看所有MCSManager面板
查看乌托邦面板的实例列表
启动乌托邦面板上的生存服务器
查看生存服务器的根目录文件
读取生存服的 /server.properties
```

实例操作支持名称、UUID 或列表序号。同名实例跨面板重复时需指定面板名称或使用 UUID。

### 玩家绑定

绑定QQ用户到MC游戏ID，用于自定义指令模板的 `{sender}` 占位符：

```text
/mc myid Steve        绑定当前QQ用户到游戏ID Steve
/mc myid              查看当前绑定
/mc myid clear        解除绑定
```

一个游戏ID只能被一个QQ用户绑定，防止冒充。

### 自定义指令模板

在服务器配置的 `custom_commands` 中预配置指令模板。管理员手动执行 `/mc cmd` 不受 LLM 权限模式影响；LLM 调用 `minecraft_run_custom_command` 仍要求 **FULL + 管理员**。模板限制了命令骨架，参数数量必须与占位符一致，最终命令仍会经过危险命令检查。

模板支持两种占位符：
- `{sender}` → 用户的绑定游戏ID（需先 `/mc myid` 绑定）
- `<&参数名&>` → 用户提供的参数

配置示例：
```json
{
  "custom_commands": [
    {
      "name": "tpa",
      "description": "传送到指定玩家",
      "command": "tpa {sender} <&target&>"
    },
    {
      "name": "home",
      "description": "回家",
      "command": "home {sender}"
    }
  ]
}
```

使用：
```text
/mc cmd tpa Steve    → 执行 tpa <绑定ID> Steve
/mc cmd home         → 执行 home <绑定ID>
```

## LLM 工具列表

### Minecraft 服务器（只读）

| 工具 | 功能 |
|------|------|
| `minecraft_get_servers` | 服务器清单 |
| `minecraft_get_status` | 连接状态 |
| `minecraft_select_server` | 选择操作目标 |
| `minecraft_get_ai_permission` | AI权限状态 |
| `minecraft_request_full_access` | 返回开启FULL的说明 |
| `list_players` | 在线玩家 |
| `whitelist_list` | 白名单 |
| `banlist` | 封禁列表 |
| `minecraft_get_player_id` | 查询QQ用户绑定的游戏ID |
| `minecraft_list_custom_commands` | 列出可用的自定义指令 |

### Minecraft 服务器（FULL + 管理员）

| 工具 | 功能 |
|------|------|
| `kick_player` | 踢出玩家 |
| `ban_player` / `pardon_player` | 封禁/解封 |
| `op_player` / `deop_player` | 授予/移除OP |
| `whitelist_add` / `whitelist_remove` | 白名单增删 |
| `give_item` | 给予物品 |
| `teleport_player` | 传送 |
| `set_gamemode` | 游戏模式 |
| `say_message` | 广播 |
| `tellraw` | 富文本消息 |
| `title` | 显示标题 |
| `kill_entity` | 杀死实体 |
| `clear_inventory` | 清空背包 |
| `set_experience` | 设置经验 |
| `summon_entity` | 生成实体 |
| `save_world` | 保存世界数据 |
| `send_to_qq_group` | 向绑定群推送消息 |
| `execute_command` | 自定义命令 |
| `set_weather` / `set_time` / `set_difficulty` / `set_gamerule` | 世界设置 |
| `minecraft_run_custom_command` | 执行自定义指令模板 |

### MCSManager 面板

| 工具 | 功能 | 权限 |
|------|------|------|
| `mcsmanager_get_panels` | 面板列表 | 只读 |
| `mcsmanager_select_panel` | 选择面板 | 只读 |
| `mcsmanager_get_instances` | 实例列表 | 只读 |
| `mcsmanager_get_overview` | 面板概览 | 只读 |
| `mcsmanager_get_log` | 实例日志 | 只读 |
| `mcsmanager_list_files` | 目录浏览 | 只读 |
| `mcsmanager_read_file` | 读取文件 | 管理员 |
| `mcsmanager_start_instance` | 启动实例 | FULL |
| `mcsmanager_stop_instance` | 停止实例 | FULL |
| `mcsmanager_restart_instance` | 重启实例 | FULL |
| `mcsmanager_send_command` | 发送命令 | FULL |

## 安全建议

1. `admin_ids` 只填可信用户，生产环境不要留空
2. `llm_permission_mode` 保持 `readonly`，仅在需要时临时开启 FULL
3. RCON 不加密，不要暴露到公网，优先用内网或 VPN
4. 远程 MCSManager 使用 HTTPS，SSL 模式选 `auto_trust` 或 `default`
5. API Key / RCON 密码使用强密码，不要提交到仓库或发到聊天
6. `enable_dangerous_commands` 保持 `false`
7. MCSManager 官方接口会把 `apikey` 放在查询参数中；插件会脱敏本地 httpx 日志，但反向代理和面板访问日志也应限制权限并配置脱敏

## 配置示例

```json
{
  "admin_ids": ["123456789"],
  "llm_permission_mode": "readonly",
  "default_server": "survival",
  "mc_servers": [
    {
      "__template_key": "server",
      "server_id": "survival",
      "display_name": "生存服",
      "qq_group_ids": ["10001"],
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
  "mcsmanager_panels": [
    {
      "__template_key": "panel",
      "panel_name": "主面板",
      "url": "https://mcsm.example.com:23333",
      "api_key": "your_api_key",
      "ssl_mode": "auto_trust",
      "enable_dangerous_commands": false
    }
  ]
}
```

## 许可证

MIT License
