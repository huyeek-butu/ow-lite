# AGENTS.md — ow-lite 项目指令

> 本文件是 ow-lite 的**口径基准**。任何在 `D:/OW-Bridge/ow-lite` 目录下发起的会话，
> 开工前先读本文件，再读 `README.md`。

## 一、项目身份

| 项 | 值 |
|---|---|
| 项目名 | **ow-lite** |
| WorkBuddy 任务名 | **开发 ow-lite 模型桥接代理** |
| 一句话 | 把 OpenCode 的免费模型，以 OpenAI 兼容接口接进 WorkBuddy 的最小可用代理 |
| 技术栈 | 纯 Python 标准库，零第三方依赖；不改 OpenCode 本体；不装 Electron 外壳 |
| 仓库根 | `D:/OW-Bridge/ow-lite` |

## 二、三条铁律（实测得来，不许推翻）

1. **免费额度只认本机 OpenCode 进程** —— 直连 `opencode.ai/zen/v1/chat/completions` 一律 403
   （`FreeTierError: OpenCode's free tier can only be used from within OpenCode`）。
   必须在本机拉起 `opencode serve`。
2. **OpenCode 没有 OpenAI 兼容端点** —— `/doc` 暴露 162 个原生端点（`/session/*`、`/provider/*`…），
   `/v1/chat/completions` 只是网页 HTML 回退。翻译层必须自己写。
3. **OpenCode message API 不接受任意工具定义** —— `tools` 只是 `{name: bool}` 开关。
   工具调用必须走「**提示词级工具调用 + `json_schema` 信封**」：整段 messages 序列化成一条 text part，
   `system` 注入适配器指令，强制输出 `{"content":…,"calls":[…]}`，再翻回 OpenAI `tool_calls`。

## 三、目录约定

```
ow-lite/
├── ow-lite.py            CLI 入口
├── config.json           配置（留空则用内置默认）
├── owlite/               config / opencode / relay★ / server / workbuddy
├── .data/                运行时数据（隔离，勿手工改动）
└── README.md / README.en.md
```

## 四、常用命令

```bash
cd D:/OW-Bridge/ow-lite
python ow-lite.py status      # 代理是否活着（默认 127.0.0.1:41985）
python ow-lite.py probe --stop  # 探测可用免费模型
python ow-lite.py start       # 后台常驻
python ow-lite.py install     # 写入 WorkBuddy models.json（改前自动备份）
python ow-lite.py stop / uninstall
```

## 五、改动红线

- 改 `~/.workbuddy/models.json` **只允许动 `ow-` 前缀条目**，改前必须备份（`owlite/workbuddy.py` 已内置备份与回滚）。
- 运行数据全部落在 `.data/`，通过 `XDG_*_HOME` 重定向，与用户日常 OpenCode **完全隔离**。
- 上层只认 `StructuredOutput`；若冒出原生工具调用 → 判 `native_tool_activity` 拒绝，不得漏给上层。
- 新增能力前先实测，不要凭推测改协议层。

## 六、验收方式

四项实测：① 纯文本 ② 工具调用往返 ③ 工具结果回传 ④ 流式 SSE（`[DONE]` 收尾）。
