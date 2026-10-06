# -*- coding: utf-8 -*-
"""对外暴露 OpenAI 兼容端点的 HTTP 服务（零第三方依赖）。

端点：
    GET  /health                 健康检查
    GET  /v1/models              OpenAI 风格模型列表
    POST /v1/chat/completions    OpenAI 风格对话补全（支持 stream=true）

一次请求的完整链路：
    WorkBuddy → 本服务 → [翻译] → opencode serve（隔离进程）→ 免费模型
                              ← [翻译] ←
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import relay as relay_mod


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, cfg, runtime, models, log=None):
        super().__init__(addr, _Handler)
        self.cfg = cfg
        self.runtime = runtime
        self.log = log or (lambda *a: None)
        self._lock = threading.Lock()
        self._models = list(models or [])
        self.started_at = time.time()

    # 模型目录（供 /v1/models 与请求校验共用）
    @property
    def models(self):
        with self._lock:
            return list(self._models)

    def model_ids(self) -> set:
        return {m['id'] for m in self.models}

    def refresh_models(self):
        found = self.runtime.free_models()
        with self._lock:
            self._models = found
        return found


class _Handler(BaseHTTPRequestHandler):
    server_version = 'ow-lite'
    protocol_version = 'HTTP/1.1'

    # ---------------------------------------------------------------- 工具

    def _log(self, fmt, *a):
        self.server.log('[%s] %s' % (time.strftime('%H:%M:%S'), fmt % a if a else fmt))

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode('utf-8', 'replace'))

    # HTTP 方法

    def do_GET(self):
        path = self.path.split('?', 1)[0].rstrip('/') or '/'
        if path == '/health':
            srv = self.server
            self._send_json({
                'status': 'ok',
                'proxy': 'ow-lite/%s' % __import__('owlite').__version__,
                'uptime': int(time.time() - srv.started_at),
                'models': len(srv.models),
                'opencode': srv.runtime.status(),
            })
        elif path in ('/v1/models', '/models'):
            now = int(time.time())
            self._send_json({
                'object': 'list',
                'data': [{
                    'id': m['id'], 'object': 'model', 'created': now,
                    'owned_by': 'opencode',
                    'permission': [],
                } for m in self.server.models],
            })
        elif path == '/ow-lite/catalog':
            # 给 install 用的完整目录（含工具/图片/推理能力标志）。
            self._send_json({'object': 'list', 'data': self.server.models})
        else:
            self._send_json({'error': {'message': 'not found', 'type': 'invalid_request_error'}}, 404)

    def do_POST(self):
        path = self.path.split('?', 1)[0].rstrip('/') or '/'
        if path in ('/v1/chat/completions', '/chat/completions'):
            self._handle_chat()
        else:
            self._send_json({'error': {'message': 'not found', 'type': 'invalid_request_error'}}, 404)

    # 主逻辑

    def _handle_chat(self):
        srv = self.server
        cfg = srv.cfg
        try:
            body = self._read_json()
        except Exception as exc:
            self._send_json(relay_mod.error_payload(
                relay_mod.RelayError('请求体不是合法 JSON: %s' % exc)), 400)
            return

        try:
            prepared = relay_mod.prepare(body, cfg, srv.model_ids())
        except relay_mod.RelayError as exc:
            self._log('请求被拒: %s', exc.message)
            self._send_json(relay_mod.error_payload(exc), exc.status)
            return

        self._log('→ %s（工具 %d 个，%s）', prepared.client_model,
                  len(prepared.tools), 'stream' if prepared.stream else 'single')

        try:
            result = self._run(prepared)
        except relay_mod.RelayError as exc:
            self._log('上游/协议错误: %s', exc.message)
            self._send_json(relay_mod.error_payload(exc), exc.status)
            return
        except Exception as exc:
            self._log('内部错误: %s\n%s', exc, traceback.format_exc())
            self._send_json(relay_mod.error_payload(exc), 502)
            return

        if prepared.stream:
            self._send_stream(result)
        else:
            self._send_json(result)

    def _run(self, prepared: relay_mod.Prepared) -> dict:
        """跑一次完整推理，返回 OpenAI 响应对象。"""
        srv = self.server
        rt = srv.runtime
        rt.ensure()

        session_id = rt.create_session()
        try:
            payload = {
                'model': {'providerID': 'opencode', 'modelID': prepared.model_id},
                'agent': srv.cfg.get('agent_name_override') or 'ow-lite-relay',
                'system': prepared.system + (
                    '\nUse StructuredOutput to return this envelope. All other native '
                    'tools are forbidden; do not perform the external actions yourself.'),
                'parts': [{'type': 'text', 'text': prepared.text}],
            }
            if prepared.schema is not None:
                payload['format'] = {'type': 'json_schema', 'retryCount': 0,
                                     'schema': prepared.schema}
            response = rt.prompt(session_id, payload)
        finally:
            if srv.cfg.get('delete_session_after', True):
                rt.delete_session(session_id)

        if not isinstance(response, dict):
            raise relay_mod.RelayError('OpenCode 返回了非预期内容', 502, 'upstream_error')

        info = response.get('info') or {}
        if info.get('error'):
            raise relay_mod.RelayError(
                '上游模型错误: %s' % json.dumps(info['error'], ensure_ascii=False)[:200],
                502, 'upstream_error')

        parts = response.get('parts') or []
        # 原生工具活动 = 模型试图在本机执行动作 → 一律拒绝。
        strays = [p for p in parts
                  if p.get('type') == 'tool' and p.get('tool') not in ('StructuredOutput', 'invalid')]
        if strays:
            raise relay_mod.RelayError(
                '检测到原生工具活动（%s），已拒绝该响应' % strays[0].get('tool'),
                502, 'native_tool_activity')

        envelope = None
        for p in parts:
            state = p.get('state') or {}
            if (p.get('type') == 'tool' and p.get('tool') == 'StructuredOutput'
                    and state.get('status') == 'completed' and state.get('input')):
                envelope = state['input']
                break

        text = ''.join((p.get('text') or '') for p in parts if p.get('type') == 'text')
        message = relay_mod.decode_model_output(prepared, envelope, text)
        result = relay_mod.completion(prepared.client_model, message, info.get('tokens'))
        self._log('← %s（%s）', prepared.client_model,
                  'tool_calls' if message.get('tool_calls') else 'text')
        return result

    def _send_stream(self, result: dict):
        include_usage = True
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            for piece in relay_mod.sse_chunks(result, include_usage):
                self.wfile.write(piece.encode('utf-8'))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    # 静默默认日志（我们用自己的格式）
    def log_message(self, fmt, *args):
        pass


def build(cfg: dict, runtime, models, log=None) -> RelayServer:
    srv = RelayServer((cfg['proxy_host'], int(cfg['proxy_port'])), cfg, runtime, models, log)
    return srv
