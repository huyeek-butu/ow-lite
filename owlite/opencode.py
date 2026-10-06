# -*- coding: utf-8 -*-
"""隔离的 OpenCode 运行时 + HTTP 客户端。

设计要点（照抄 ow-bridge 已经验证过的做法）：
  1. 独立数据目录：XDG_*_HOME 全部指向 .data/opencode/，不碰你日常的 OpenCode 配置。
  2. 环境变量白名单：只保留系统/网络必需项，绝不继承其它 provider 的密钥。
  3. 关掉一切“外部注入”：项目配置、Claude Code 技能、外部 skills。
  4. 专用 agent + 全 deny 权限：模型只能推理，不能在本机执行任何动作。
  5. 先用 `models opencode --refresh` 刷新目录，避免拿到内置的过期快照。
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

# 仅保留这些环境变量（其余一律不透传）。
ENV_ALLOW = [
    'PATH', 'HOME', 'USER', 'USERNAME', 'LANG', 'TMPDIR', 'SHELL',
    'SSL_CERT_FILE', 'NODE_EXTRA_CA_CERTS',
    'SystemRoot', 'WINDIR', 'SystemDrive', 'TEMP', 'TMP',
    'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PATHEXT', 'COMSPEC',
    'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'OS',
]

# 权限：默认都要“问”，且把我们不希望出现的原生能力一律 deny。
# 代理并不回复权限请求，所以任何原生动作都会卡住 → 等于全面禁止。
NATIVE_PERMISSIONS = {
    '*': 'ask',
    'question': 'deny',
    'websearch': 'deny',
    'codesearch': 'deny',
    'webfetch': 'deny',
    'task': 'deny',
    'plan_enter': 'deny',
    'plan_exit': 'deny',
    'todowrite': 'deny',
}

AGENT_NAME = 'ow-lite-relay'

AGENT_PROMPT = (
    'You are the reasoning component of an external assistant. '
    'Never invoke native OpenCode tools. Describe external tool calls only in the '
    'requested JSON response. The external client owns execution and supplies tool '
    'results on the next request.'
)


class RuntimeError_(Exception):
    """运行时错误（启动失败、上游不可达等）。"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class OpenCodeRuntime:
    """管理一个隔离的 `opencode serve` 进程，并提供访问它的客户端方法。"""

    def __init__(self, cfg: dict, log=None):
        self.cfg = cfg
        self.log = log or (lambda *a: None)
        self.root = os.path.join(cfg['data_dir'], 'opencode')
        self.log_dir = os.path.join(cfg['data_dir'], 'logs')
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.password: str | None = None
        self.base: str | None = None

    # ------------------------------------------------------------------ 环境

    def _ensure_dirs(self):
        for sub in ('config', 'data', 'cache', 'state', 'project'):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)

    def _env(self) -> dict:
        env = {k: os.environ[k] for k in ENV_ALLOW if k in os.environ}
        for name in ('config', 'data', 'cache', 'state'):
            env['XDG_%s_HOME' % name.upper()] = os.path.join(self.root, name)
        env['OPENCODE_SERVER_USERNAME'] = 'opencode'
        env['OPENCODE_SERVER_PASSWORD'] = self.password
        env['OPENCODE_DISABLE_AUTOUPDATE'] = 'true'
        env['OPENCODE_DISABLE_PROJECT_CONFIG'] = 'true'
        env['OPENCODE_DISABLE_CLAUDE_CODE'] = 'true'
        env['OPENCODE_DISABLE_EXTERNAL_SKILLS'] = 'true'
        env['OPENCODE_CONFIG_CONTENT'] = json.dumps({
            'permission': NATIVE_PERMISSIONS,
            'autoupdate': False,
            'share': 'disabled',
            'agent': {
                AGENT_NAME: {
                    'mode': 'primary',
                    'description': 'External client inference only',
                    'prompt': AGENT_PROMPT,
                    'permission': NATIVE_PERMISSIONS,
                },
            },
        })
        return env

    def _binary(self) -> str:
        exe = self.cfg['opencode_binary']
        if not os.path.exists(exe):
            raise RuntimeError_('找不到 OpenCode 可执行文件：%s' % exe)
        return exe

    # ------------------------------------------------------------------ 生命周期

    def refresh_catalog(self):
        """刷新免费模型目录（避免内置快照过期）。失败不致命。"""
        exe = self._binary()
        try:
            r = subprocess.run(
                [exe, 'models', 'opencode', '--refresh', '--pure'],
                cwd=os.path.join(self.root, 'project'), env=self._env(),
                capture_output=True, timeout=90,
            )
            self.log('目录刷新返回码 = %s' % r.returncode)
        except Exception as exc:
            self.log('目录刷新失败（忽略）：%s' % exc)

    def start(self):
        if self.is_up():
            return self
        self._ensure_dirs()
        self.password = secrets.token_hex(24)
        self.port = int(self.cfg.get('opencode_port') or 0) or _free_port()
        self.base = 'http://127.0.0.1:%d' % self.port
        exe = self._binary()
        log_path = os.path.join(self.log_dir, 'opencode.log')
        self._logf = open(log_path, 'a', encoding='utf-8')
        self._logf.write('\n===== %s 启动 opencode serve :%d =====\n'
                         % (time.strftime('%Y-%m-%d %H:%M:%S'), self.port))
        self._logf.flush()
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        self.proc = subprocess.Popen(
            [exe, 'serve', '--pure', '--hostname', '127.0.0.1', '--port', str(self.port)],
            cwd=os.path.join(self.root, 'project'), env=self._env(),
            stdout=self._logf, stderr=subprocess.STDOUT,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        )
        self.log('opencode serve 已启动 pid=%d port=%d' % (self.proc.pid, self.port))
        for i in range(120):
            if self.is_up():
                self.log('opencode 就绪（%.1fs）' % (i * 0.5))
                return self
            if self.proc.poll() is not None:
                raise RuntimeError_('opencode serve 提前退出（码 %s），见 %s'
                                    % (self.proc.returncode, log_path))
            time.sleep(0.5)
        raise RuntimeError_('opencode serve 启动超时，见 %s' % log_path)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.log('opencode serve 已停止')
        self.proc = None
        try:
            if getattr(self, '_logf', None):
                self._logf.close()
        except Exception:
            pass

    def is_up(self) -> bool:
        if not self.base:
            return False
        try:
            self.request('GET', '/global/health', timeout=5)
            return True
        except Exception:
            return False

    def ensure(self):
        """确保运行时可用（幂等）。"""
        if not self.is_up():
            self.start()
        return self

    # ------------------------------------------------------------------ 客户端

    def _headers(self) -> dict:
        token = base64.b64encode(('opencode:%s' % self.password).encode()).decode()
        return {'Content-Type': 'application/json', 'Authorization': 'Basic ' + token}

    def request(self, method: str, path: str, body=None, timeout: int = 60):
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:400]
            raise RuntimeError_('OpenCode %s %s -> HTTP %s %s' % (method, path, e.code, detail))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw

    # ---- 模型目录 ----

    def providers(self):
        return self.request('GET', '/config/providers', timeout=60)

    def free_models(self) -> list[dict]:
        """列出 opencode provider 下成本全为 0 的可用模型。"""
        data = self.providers() or {}
        provs = data.get('providers') or data.get('all') or []
        if isinstance(provs, dict):
            provs = list(provs.values())
        provider = next((p for p in provs if p.get('id') == 'opencode'), None)
        if not provider:
            raise RuntimeError_('OpenCode provider 缺失（上游目录异常）')
        models = provider.get('models') or {}
        out = []
        allow = set(self.cfg.get('model_allowlist') or [])
        for mid, m in models.items():
            cost = m.get('cost') or {}
            cache = cost.get('cache') or {}
            caps = m.get('capabilities') or {}
            limit = m.get('limit') or {}
            is_free = (cost.get('input') == 0 and cost.get('output') == 0
                       and (cache.get('read') or 0) == 0 and (cache.get('write') or 0) == 0
                       and (caps.get('output') or {}).get('text') is not False
                       and m.get('status') != 'deprecated')
            if not is_free:
                continue
            if allow and mid not in allow:
                continue
            out.append({
                'id': mid,
                'name': m.get('name') or mid,
                'toolcall': (caps.get('toolcall') is True),
                'reasoning': (caps.get('reasoning') is True),
                'images': ((caps.get('input') or {}).get('image') is True),
                'context': limit.get('context'),
                'output': limit.get('output'),
                'variants': m.get('variants') or {},
            })
        out.sort(key=lambda x: x['id'])
        return out

    # ---- 会话 ----

    def create_session(self, title: str = 'ow-lite') -> str:
        perm = [{'permission': k, 'pattern': '*', 'action': v}
                for k, v in NATIVE_PERMISSIONS.items()]
        session = self.request('POST', '/session',
                               {'title': title, 'permission': perm}, timeout=30)
        sid = (session or {}).get('id')
        if not sid:
            raise RuntimeError_('创建 OpenCode 会话失败：%s' % session)
        return sid

    def prompt(self, session_id: str, payload: dict, timeout: int | None = None):
        return self.request('POST', '/session/%s/message' % session_id, payload,
                            timeout=timeout or int(self.cfg['request_timeout']))

    def delete_session(self, session_id: str):
        try:
            self.request('DELETE', '/session/%s' % session_id, timeout=15)
        except Exception as exc:
            self.log('删除会话失败（忽略）：%s' % exc)

    # ---- 进程级查找/停止（给 CLI 用）----

    def status(self) -> dict:
        return {
            'running': self.is_up(),
            'port': self.port,
            'base': self.base,
            'data_dir': self.root,
            'binary': self.cfg['opencode_binary'],
        }
