# -*- coding: utf-8 -*-
"""WorkBuddy 模型配置的读写 / 备份 / 回滚。

安全约定（比原件更严格）：
  * 任何写入前先做带时间戳的备份；
  * 只增删「自己写的」条目（id 以 model_id_prefix 开头），其余原样保留；
  * 写入走 临时文件 + 原子替换，避免半截文件；
  * 顶层结构自适应（当前 WorkBuddy 是「顶层数组」）。
"""
from __future__ import annotations

import json
import os
import shutil
import time

BACKUP_DIRNAME = 'models.json.backups'


def models_path() -> str:
    return os.path.join(os.path.expanduser('~'), '.workbuddy', 'models.json')


def _read_raw(path: str) -> str:
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()


def load(path: str | None = None):
    path = path or models_path()
    if not os.path.exists(path):
        return []
    raw = _read_raw(path)
    if not raw.strip():
        return []
    data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get('models'), list):
        return data['models']
    raise ValueError('无法识别的 models.json 结构（既不是数组，也没有 models 数组）')


def backup(path: str | None = None) -> str:
    path = path or models_path()
    bkdir = os.path.join(os.path.dirname(path), BACKUP_DIRNAME)
    os.makedirs(bkdir, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    dst = os.path.join(bkdir, 'models.json.%s.bak' % stamp)
    shutil.copy2(path, dst)
    return dst


def _write_atomic(path: str, entries: list):
    tmp = path + '.ow-lite.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, path)


def is_ours(entry: dict, cfg: dict) -> bool:
    prefix = cfg.get('model_id_prefix') or ''
    return bool(prefix) and str(entry.get('id', '')).startswith(prefix)


def build_entries(cfg: dict, models: list[dict]) -> list[dict]:
    url = 'http://%s:%d/v1/chat/completions' % (cfg['proxy_host'], cfg['proxy_port'])
    prefix = cfg.get('model_id_prefix') or ''
    name_prefix = cfg.get('model_name_prefix') or ''
    entries = []
    for m in models:
        entries.append({
            'id': prefix + m['id'],
            'name': name_prefix + (m.get('name') or m['id']),
            'vendor': 'Custom',
            'url': url,
            'apiKey': 'ow-lite-local',
            'supportsToolCall': bool(m.get('toolcall')),
            'supportsImages': bool(m.get('images')),
            'supportsReasoning': bool(m.get('reasoning')),
            'useCustomProtocol': False,
        })
    return entries


def install(cfg: dict, models: list[dict], path: str | None = None) -> dict:
    """把 ow-lite 的模型写入 WorkBuddy 配置（保留其它条目）。"""
    path = path or models_path()
    existing = load(path)
    backup_path = backup(path) if os.path.exists(path) else None

    kept = [e for e in existing if not is_ours(e, cfg)]
    removed = len(existing) - len(kept)
    ours = build_entries(cfg, models)

    _write_atomic(path, kept + ours)
    return {
        'path': path,
        'backup': backup_path,
        'kept': len(kept),
        'removed': removed,
        'added': len(ours),
        'total': len(kept) + len(ours),
        'ids': [e['id'] for e in ours],
    }


def uninstall(cfg: dict, path: str | None = None) -> dict:
    """移除 ow-lite 写过的条目（其余原样保留）。"""
    path = path or models_path()
    if not os.path.exists(path):
        return {'path': path, 'removed': 0, 'total': 0, 'backup': None}
    existing = load(path)
    kept = [e for e in existing if not is_ours(e, cfg)]
    removed = len(existing) - len(kept)
    backup_path = None
    if removed:
        backup_path = backup(path)
        _write_atomic(path, kept)
    return {'path': path, 'removed': removed, 'total': len(kept), 'backup': backup_path}


def status(cfg: dict, path: str | None = None) -> dict:
    path = path or models_path()
    if not os.path.exists(path):
        return {'path': path, 'exists': False, 'ours': [], 'others': 0, 'backups': []}
    existing = load(path)
    ours = [e.get('id') for e in existing if is_ours(e, cfg)]
    bkdir = os.path.join(os.path.dirname(path), BACKUP_DIRNAME)
    backups = sorted(os.listdir(bkdir)) if os.path.isdir(bkdir) else []
    return {
        'path': path,
        'exists': True,
        'ours': ours,
        'others': len(existing) - len(ours),
        'backups': backups,
    }
