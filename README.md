# ow-lite

> 把 **OpenCode 的免费模型**接进 **WorkBuddy** 的最小可用代理。
> 纯 Python 标准库实现，零第三方依赖，不改动 OpenCode 本体，不安装 Electron 外壳。

---

## 一、它是做什么的

WorkBuddy 里可选的自定义模型，需要一个「OpenAI 兼容」的接口地址。而 OpenCode 提供了一批
**成本为 0 的免费模型**，但它的接口并不是 OpenAI 格式。

ow-lite 就干一件事：**在两者之间做翻译。**

```
WorkBuddy  ──OpenAI 格式──▶  ow-lite  ──OpenCode 会话格式──▶  opencode serve  ──▶  免费模型
           ◀──OpenAI 格式──           ◀──会话响应──────
```

---

## 二、先看清三件事（决定了必须自己写）

在动手前，这三条是**实测**出来的，不是推测：

| # | 测什么 | 结果 |
|---|---|---|
| 1 | 直接拿 `public` 当 key 请求 `opencode.ai/zen/v1/chat/completions` | 13 个 `*-free` 里 **12 个直接 403**，原文：`FreeTierError: OpenCode's free tier can only be used from within OpenCode` |
| 2 | 经**本机 OpenCode 进程**调同一个免费模型 | **HTTP 200**，正常返回，全程无 403 |
| 3 | OpenCode 有没有 OpenAI 兼容端点 | **没有**。`/doc` 里 162 个原生端点（`/session/*`、`/provider/*`…），`/v1/chat/completions` 只是网页 HTML 回退 |

**结论**：免费额度只认 OpenCode 进程本身 → 必须在本机把它跑起来；但它不说 OpenAI 的话 →
必须自己写一层翻译。市面上把「改配置就能用」当卖点的方案，今天已经失效（详见 §十一）。

---

## 三、目录结构

```
ow-lite/
├── ow-lite.py              命令行入口（init / probe / serve / install / uninstall / status）
├── config.json             配置文件（可留空用内置默认值）
├── owlite/
│   ├── config.py           配置加载
│   ├── opencode.py         隔离的 opencode serve 运行时 + HTTP 客户端
│   ├── relay.py            ★ 协议翻译层（OpenAI ↔ OpenCode）
│   ├── server.py           对外暴露 OpenAI 兼容端点的 HTTP 服务
│   └── workbuddy.py        models.json 的读写 / 备份 / 回滚
└── .data/                  运行时数据（自动创建，与你的日常 OpenCode 完全隔离）
    ├── opencode/           config / data / cache / state / project
    └── logs/               opencode.log、代理日志
```

---

## 四、快速开始

前置：本机已有一个独立的 `opencode.exe`（agent 自带 Node，无需另装 Node）。
默认路径写在 `config.json` 的 `opencode_binary`。

```bash
cd D:/OW-Bridge/ow-lite

# 1) 探测可用免费模型（会自动拉起一个隔离的 OpenCode 再停掉）
python ow-lite.py probe --stop

# 2) 启动代理（后台常驻，不占终端）
python ow-lite.py start

# 3) 把模型写进 WorkBuddy（改前自动备份）
python ow-lite.py install

# 4) 完整退出并重开 WorkBuddy（托盘右键退出，不是关窗口）
#    模型列表里会出现 "OC · xxx"
```

不再需要时：

```bash
python ow-lite.py stop          # 停止代理并清理它拉起的 OpenCode
python ow-lite.py uninstall     # 只删 ow-lite 写的条目，其它原样保留
```

---

## 五、命令参考

| 命令 | 作用 |
|---|---|
| `init [--force]` | 生成 `config.json` |
| `probe [--stop]` | 列出可用的免费模型（含是否支持工具调用 / 图片 / 上下文长度） |
| `start [--port N]` | **后台常驻启动代理**（推荐；不占终端） |
| `stop` | 停止代理，并精确清理它拉起的 OpenCode（不动你其它的 opencode 进程） |
| `serve [--port N]` | 前台启动代理（调试用；Ctrl+C 停止） |
| `install [--limit N] [--stop]` | 把模型写入 WorkBuddy 配置 |
| `uninstall` | 从 WorkBuddy 配置移除（回滚） |
| `status` | 查看代理 / 运行时 / 配置文件的状态 |

> 代理必须**在 WorkBuddy 使用期间保持运行**。`start` 是分离进程（关掉终端也不受影响），
> 日志在 `.data/logs/proxy.log` 与 `.data/logs/opencode.log`。

---

## 六、配置项

| 键 | 默认值 | 说明 |
|---|---|---|
| `proxy_host` / `proxy_port` | `127.0.0.1` / `41985` | 代理监听地址 |
| `opencode_binary` | `D:/OW-Bridge/runtime-standalone/pkg/package/bin/opencode.exe` | 独立运行时路径 |
| `data_dir` | `<项目>/.data` | 运行时数据目录（隔离） |
| `opencode_port` | `0` | `0` = 每次自动挑空闲端口 |
| `model_id_prefix` | `ow-` | 写入 WorkBuddy 的模型 id 前缀（**用于识别"哪些条目是本工具写的"**） |
| `model_name_prefix` | `OC · ` | 模型显示名前缀 |
| `model_allowlist` | `[]` | 只暴露这些模型（空 = 全部免费模型） |
| `request_timeout` | `300` | 单次推理超时（秒）。免费模型可能很慢 |
| `delete_session_after` | `true` | 请求结束即删会话（无状态模式，避免堆积） |

---

## 七、工作原理（翻译层详解）

OpenCode 的 `POST /session/{id}/message` 只接受**它自己的工具**（`tools` 字段仅是一个
`{工具名: 布尔}` 的开关，不能注入任意工具定义）。所以要让它反过来服务 WorkBuddy，必须
用「**提示词级工具调用**」：

1. **请求侧**（`relay.prepare`）
   - 把 WorkBuddy 的整段 `messages`（含 system、工具调用历史、工具结果）**序列化成 JSON**，
     作为一条 `text` part 发过去；
   - 用 `system` 字段注入一段**适配器指令**：说明"你只是推理组件，执行由外部完成，
     必须从本轮提供的工具里选"，并把本轮工具定义以 JSON 附上；
   - 借 OpenCode 的 `format: {type: "json_schema"}` **强制**模型只能输出一个信封：
     ```json
     {"content": "给用户的话", "calls": [{"name": "工具名", "arguments": {}}]}
     ```
     其中 `calls` 的 `items` 用 `anyOf` 精确枚举了本轮允许的工具名与参数 schema。

2. **响应侧**（`relay.decode_model_output`）
   - 优先取 `StructuredOutput` 工具调用的 `state.input`（结构化信封）；
   - 退回解析文本里的 JSON；再退回纯文本；
   - 把信封翻成 OpenAI 的 `message.tool_calls`（`arguments` 序列化成字符串）。

3. **纪律**（都在 system 指令里，并由代理二次校验）
   - 模型只能从**本轮**工具里选，名字要逐字一致；
   - 一旦响应里出现 `StructuredOutput` 之外的**原生工具调用**，直接判为
     `native_tool_activity` 并拒绝该响应——因为那意味着模型试图在本机执行动作。

---

## 八、安全设计

| 项 | 做法 |
|---|---|
| 运行时隔离 | `XDG_*_HOME` 全部指向 `.data/opencode/`，**不碰**你日常的 OpenCode 配置 |
| 环境变量 | 白名单透传（PATH/HOME/TEMP/SystemRoot…），**绝不继承**其它 provider 的密钥 |
| 外部注入 | `OPENCODE_DISABLE_PROJECT_CONFIG` / `_CLAUDE_CODE` / `_EXTERNAL_SKILLS` 全开 |
| 权限 | 会话权限设为「全 deny」；代理**不回复**任何权限请求 → 任何原生动作都会卡死 = 全面禁止 |
| 写 WorkBuddy | 改前带时间戳备份；**只增删自己前缀的条目**；临时文件 + 原子替换 |
| 网络 | 只连 `registry.npmjs.org` / `registry.npmmirror.com`（下运行时）与 `opencode.ai`；无遥测 |

> 写进 WorkBuddy 的 `apiKey` 是本地的固定占位串（`ow-lite-local`），**不是任何真实凭证**；
> 代理不做鉴权校验（仅监听 `127.0.0.1`）。

---

## 九、实测记录（2026-10-05）

| 用例 | 结果 |
|---|---|
| 纯文本问答 | HTTP 200 / 3.9s / 中文回答正确 |
| 工具调用 | HTTP 200 / 1.6s / 正确返回 `Read({"file_path":"D:/demo/notes.txt"})` |
| 工具结果回传 | HTTP 200 / 5.8s / 基于结果作答正确 |
| 流式 SSE | 5 个事件 + `data: [DONE]`，格式合规 |

**发现 10 个免费模型**（本次探测）：`big-pickle`、`fledge-alpha-free`、
`ling-3.0-flash-fin-free`、`ling-3.1-flash-free`、`longcat-2.5-preview-free`、
`mimo-v2.6-flash-free`、`muse-spark-1.3-contributor-free`、`nemotron-3-ultra-free`、
`nemotron-3.5-lightning-free`、`space-bunny-free`。**全部支持工具调用。**

> 数量与可用性由上游随时调整，请以 `probe` 的当次结果为准。

---

## 十、故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `install` 后 WorkBuddy 里看不到模型 | 没重启 WorkBuddy，或 `--stop` 把运行时停了。**启动 `serve` 并完整重启 WorkBuddy** |
| 请求报 `model_not_found` | WorkBuddy 传的模型名不在本次目录里。重跑 `probe` 确认，必要时重新 `install` |
| 报「检测到原生工具活动」 | 该模型不守适配器纪律（少见）。换一个模型，或在 `model_allowlist` 里避开它 |
| 报「上游模型错误」 | 免费模型上游排队/限速/临时下线。重试或换模型 |
| 响应很慢 | 正常。免费模型有排队；实测从 1s 到 2 分钟都有。**定位是"非紧急任务"** |
| 上下文被吃掉很多 | 适配器指令 + 工具定义会占 prompt（实测约 4.5–5K tokens）。正常 |

---

## 十一、与同类方案对比

| | `free-buddy-skills` | `ow-bridge` | **ow-lite** |
|---|---|---|---|
| 形态 | 纯 Skill（1 个 py 脚本） | Electron 托盘 App（152 MB） | **Python 脚本，无壳** |
| 今天还能用吗 | ❌ 直连已被上游封死 | ✅ | ✅ |
| 协议翻译 | 无 | 有（约 40 KB） | 有（精简版） |
| 依赖 | 需 Python | 内置 Node | **需 Python** |
| 写配置 | 直接 append，无备份 | 有备份 / 只动自己条目 | 有备份 / 只动自己条目 |
| 常驻 | 不常驻 | 得一直开着 App | **随用随起** |
| 维护 | ★0，建完即停更 | ★107，在更 | 本仓库 |

---

## 十二、已知限制

- **不支持图片输入**：传 `image_url` 会明确报错（不静默丢弃）。需要视觉时请用支持视觉的模型。
- **不转发 reasoning 内容**：推理型模型的 `reasoning` part 会被丢弃（只保留最终答复）。
- **流式为缓冲式**：先完整拿到并校验结果，再按 SSE 分片下发——不逐 token 推送，
  换来的是「绝不把未校验的工具调用当成已执行」。
- **免费模型不承诺 SLA**：排队、限速、随时下架都是常态。
- **数据会发往 `opencode.ai`**：敏感内容不要走免费模型。

---

## 十三、许可

本目录代码可自由使用与修改。它调用的 OpenCode 运行时、以及免费模型服务，遵循其各自的
许可与服务条款。
