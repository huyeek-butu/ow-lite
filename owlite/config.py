# -*- coding: utf-8 -*-
"""ow-lite 配置：默认值 / 加载 / 保存。

配置文件默认位于项目根目录的 config.json（不存在则用内置默认值）。
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, 'config.json')

DEFAULTS = {
    # ---- 对外代理 ----
    'proxy_host': '127.0.0.1',
    'proxy_port': 41985,

    # ---- OpenCode 运行时 ----
    # 指向独立的 opencode 可执行文件（agent 版自带 Node，无需另装）。
    'opencode_binary': r'D:/OW-Bridge/runtime-standalone/pkg/package/bin/opencode.exe',
    # 运行时数据目录（配置/缓存/会话/临时工程），与你的日常 OpenCode 完全隔离。
    'data_dir': os.path.join(ROOT, '.data'),
    # 0 = 每次启动自动挑一个空闲端口。
    'opencode_port': 0,

    # ---- 模型呈现 ----
    # WorkBuddy 里看到的模型 id 前缀（用于识别“哪些条目是 ow-lite 写的”）。
    'model_id_prefix': 'ow-',
    # WorkBuddy 里看到的模型显示名前缀。
    'model_name_prefix': 'OC · ',
    # 只暴露这些模型（空列表 = 暴露全部免费模型）。
    'model_allowlist': [],

    # ---- 行为 ----
    # 单次推理超时（秒）。免费模型可能很慢，给足余量。
    'request_timeout': 300,
    # 并发上限（本地单用户，够用即可）。
    'max_workers': 8,
    # 是否在请求结束后删除 OpenCode 会话（无状态模式，避免会话堆积）。
    'delete_session_after': True,
    # 日志级别：DEBUG / INFO / WARNING
    'log_level': 'INFO',
}


def load(path: str | None = None) -> dict:
    """读取配置；缺失键用默认值补齐。"""
    path = path or CONFIG_PATH
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update({k: v for k, v in user.items() if v is not None})
        except Exception as exc:  # 配置坏了不该拖垮服务
            print('[ow-lite] 配置读取失败，改用默认值: %s' % exc)
    return cfg


def save(cfg: dict, path: str | None = None) -> str:
    """写出配置（仅用于 `init` 命令生成样例）。"""
    path = path or CONFIG_PATH
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return path


def proxy_base(cfg: dict) -> str:
    return 'http://%s:%d' % (cfg['proxy_host'], cfg['proxy_port'])


def chat_url(cfg: dict) -> str:
    return proxy_base(cfg) + '/v1/chat/completions'
