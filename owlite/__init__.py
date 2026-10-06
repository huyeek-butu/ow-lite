# -*- coding: utf-8 -*-
"""ow-lite —— 把 OpenCode 的免费模型接进 WorkBuddy 的最小可用代理。

模块划分：
    config      配置加载
    opencode    隔离的 opencode serve 运行时 + HTTP 客户端
    relay       OpenAI 协议 ↔ OpenCode 会话协议的翻译层
    workbuddy   WorkBuddy models.json 的读写 / 备份 / 回滚
    server      对外暴露 OpenAI 兼容端点的 HTTP 服务
"""

__version__ = '0.1.0'
__all__ = ['config', 'opencode', 'relay', 'workbuddy', 'server']
