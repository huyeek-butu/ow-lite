#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ow-lite 命令行入口。

用法：
    python ow-lite.py init       生成 config.json（首次运行）
    python ow-lite.py probe      探测可用的免费模型
    python ow-lite.py serve      启动代理（前台常驻）
    python ow-lite.py install    把模型写入 WorkBuddy（改前自动备份）
    python ow-lite.py uninstall  从 WorkBuddy 移除（回滚）
    python ow-lite.py status     查看代理与配置状态
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from owlite import config as cfgmod            # noqa: E402
from owlite import opencode, relay, server, workbuddy   # noqa: E402


def _log(*a):
    print(*a, flush=True)


def _runtime(cfg):
    return opencode.OpenCodeRuntime(cfg, log=_log)


def _pid_file(cfg, name):
    return os.path.join(cfg['data_dir'], name)


def _read_pid(cfg, name):
    p = _pid_file(cfg, name)
    try:
        with open(p, 'r', encoding='ascii') as f:
            return int(f.read().strip())
    except Exception:
        return None


def _write_pid(cfg, name, pid):
    os.makedirs(cfg['data_dir'], exist_ok=True)
    with open(_pid_file(cfg, name), 'w', encoding='ascii') as f:
        f.write(str(pid))


def _kill(pid, force=True):
    """结束进程；返回是否成功。"""
    if not pid:
        return False
    if os.name == 'nt':
        cmd = ['taskkill', '/PID', str(pid)] + (['/F'] if force else [])
    else:
        cmd = ['kill'] + (['-9'] if force else []) + [str(pid)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _pid_alive(pid):
    """进程是否存活。注意：tasklist 在中文环境输出非 UTF-8，这里只比对字节，不解码。"""
    if not pid:
        return False
    try:
        out = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid, '/NH'],
                             capture_output=True, timeout=10).stdout
        return str(pid).encode('ascii') in out
    except Exception:
        return False


def _health(cfg):
    """取代理健康信息；不在跑则返回 None。"""
    import urllib.request
    try:
        with urllib.request.urlopen(cfgmod.proxy_base(cfg) + '/health', timeout=3) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:
        return None


def _proxy_alive(cfg) -> bool:
    return _health(cfg) is not None


def _models_via_proxy(cfg):
    """代理若在跑，直接取它的目录——避免再拉一个 OpenCode 进程。"""
    import urllib.request
    try:
        with urllib.request.urlopen(cfgmod.proxy_base(cfg) + '/ow-lite/catalog', timeout=5) as r:
            return json.loads(r.read().decode('utf-8')).get('data')
    except Exception:
        return None


# ---------------------------------------------------------------- 子命令


def cmd_init(args):
    if os.path.exists(cfgmod.CONFIG_PATH) and not args.force:
        _log('配置已存在：%s（要覆盖请加 --force）' % cfgmod.CONFIG_PATH)
        return 0
    path = cfgmod.save(cfgmod.DEFAULTS)
    _log('已生成配置：%s' % path)
    _log('请确认 opencode_binary 指向正确的 opencode.exe，再运行 `python ow-lite.py probe`。')
    return 0


def cmd_probe(args):
    cfg = cfgmod.load(args.config)
    rt = _runtime(cfg)
    rt.start()
    try:
        rt.refresh_catalog()
        models = rt.free_models()
    finally:
        if args.stop:
            rt.stop()
    if not models:
        _log('没有发现免费模型。OpenCode 上游目录可能临时不可用。')
        return 1
    _log('发现 %d 个免费模型（来自 opencode provider）：\n' % len(models))
    _log('%-34s %-8s %-6s %-6s %s' % ('modelID', '工具', '推理', '图片', '上下文'))
    _log('-' * 78)
    for m in models:
        _log('%-34s %-8s %-6s %-6s %s' % (
            m['id'],
            '√' if m['toolcall'] else '×',
            '√' if m['reasoning'] else '×',
            '√' if m['images'] else '×',
            m.get('context') or '-',
        ))
    _log('')
    _log('说明：免费模型的速度与可用性由上游随时调整；此处仅反映本次探测结果。')
    return 0


def cmd_serve(args):
    cfg = cfgmod.load(args.config)
    cfg['proxy_port'] = args.port or cfg['proxy_port']
    rt = _runtime(cfg)
    _log('启动 OpenCode 运行时...')
    rt.start()
    _write_pid(cfg, 'opencode.pid', rt.proc.pid if rt.proc else 0)
    rt.refresh_catalog()
    models = rt.free_models()
    srv = server.build(cfg, rt, models, log=_log)
    _log('')
    _log('─' * 66)
    _log('  ow-lite 代理已就绪')
    _log('  地址   : %s' % cfgmod.proxy_base(cfg))
    _log('  模型   : %d 个免费模型' % len(models))
    _log('  依赖   : opencode pid=%s / port=%s' % (rt.proc.pid if rt.proc else '-', rt.port))
    _log('  数据   : %s' % rt.root)
    _log('  停止   : Ctrl+C')
    _log('─' * 66)
    _log('')
    _log('下一步：另开一个终端运行 `python ow-lite.py install` 写入 WorkBuddy。')
    _log('')

    def _bye(signum, frame):
        _log('\n收到退出信号，正在停止...')
        try:
            srv.shutdown()
        except Exception:
            pass

    try:
        signal.signal(signal.SIGINT, _bye)
        signal.signal(signal.SIGTERM, _bye)
    except Exception:
        pass
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
        rt.stop()
        try:
            os.remove(_pid_file(cfg, 'opencode.pid'))
        except Exception:
            pass
        _log('已停止。')
    return 0


def cmd_start(args):
    """后台常驻启动（不占终端，适合装好之后就丢着跑）。"""
    cfg = cfgmod.load(args.config)
    cfg['proxy_port'] = args.port or cfg['proxy_port']
    if _proxy_alive(cfg):
        _log('代理已在运行：%s' % cfgmod.proxy_base(cfg))
        return 0
    log_dir = os.path.join(cfg['data_dir'], 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'proxy.log')
    lf = open(log_path, 'a', encoding='utf-8')
    lf.write('\n===== %s 后台启动代理 =====\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
    lf.flush()
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, 'ow-lite.py'), 'serve'],
        cwd=HERE, stdout=lf, stderr=subprocess.STDOUT,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
    )
    _write_pid(cfg, 'proxy.pid', proc.pid)
    _log('代理已后台启动 pid=%d，等待就绪...' % proc.pid)
    for _ in range(60):
        if _proxy_alive(cfg):
            _log('就绪：%s（日志 %s）' % (cfgmod.proxy_base(cfg), log_path))
            return 0
        if proc.poll() is not None:
            _log('启动失败（退出码 %s），见 %s' % (proc.returncode, log_path))
            return 1
        time.sleep(1)
    _log('等待超时，见 %s' % log_path)
    return 1


def cmd_stop(args):
    """停止代理，并精确清理它拉起的 OpenCode（不动你其它的 opencode 进程）。"""
    cfg = cfgmod.load(args.config)
    results = []
    for name in ('proxy.pid', 'opencode.pid'):
        pid = _read_pid(cfg, name)
        if not pid:
            continue
        if _pid_alive(pid):
            ok = _kill(pid)
            results.append('%s(pid=%d) %s' % (name, pid, '已停止' if ok else '停止失败'))
        else:
            results.append('%s(pid=%d) 已不在运行' % (name, pid))
        try:
            os.remove(_pid_file(cfg, name))
        except Exception:
            pass
    if results:
        _log('已处理：%s' % '；'.join(results))
        return 0
    if _proxy_alive(cfg):
        _log('代理在运行，但没有 PID 记录（例如它是手动 `serve` 启动的）。')
        _log('请结束监听 %s 的进程；其 OpenCode 子进程需另行结束。' % cfgmod.proxy_base(cfg))
        return 1
    _log('代理未在运行。')
    return 0


def cmd_install(args):
    cfg = cfgmod.load(args.config)
    models = _models_via_proxy(cfg)
    if models:
        _log('从运行中的代理取到 %d 个模型。' % len(models))
    else:
        _log('代理未运行，临时拉起 OpenCode 探测目录...')
        rt = _runtime(cfg)
        rt.start()
        rt.refresh_catalog()
        models = rt.free_models()
        if args.stop:
            rt.stop()
    if not models:
        _log('没有发现免费模型，已放弃写入。')
        return 1
    if args.limit:
        models = models[:args.limit]
    res = workbuddy.install(cfg, models)
    _log('')
    _log('已写入 WorkBuddy 配置：%s' % res['path'])
    if res['backup']:
        _log('改前备份：%s' % res['backup'])
    _log('保留原有条目 %d 个，清理旧 ow-lite 条目 %d 个，新增 %d 个，合计 %d 个。'
         % (res['kept'], res['removed'], res['added'], res['total']))
    for mid in res['ids']:
        _log('   + %s' % mid)
    _log('')
    _log('现在启动代理并重启 WorkBuddy：')
    _log('  1) python ow-lite.py serve')
    _log('  2) 完整退出并重开 WorkBuddy（托盘右键退出，不是关窗口）')
    return 0


def cmd_uninstall(args):
    cfg = cfgmod.load(args.config)
    res = workbuddy.uninstall(cfg)
    if res['removed'] == 0:
        _log('没有发现 ow-lite 写入的条目（%s）' % res['path'])
        return 0
    _log('已移除 %d 个 ow-lite 条目，保留 %d 个原有条目。' % (res['removed'], res['total']))
    if res['backup']:
        _log('改前备份：%s' % res['backup'])
    _log('请重启 WorkBuddy 生效。')
    return 0


def cmd_status(args):
    cfg = cfgmod.load(args.config)
    health = _health(cfg)
    rt = _runtime(cfg)
    _log('== ow-lite 状态 ==')
    _log('  代理地址 : %s' % cfgmod.proxy_base(cfg))
    if health:
        _log('  代理运行 : 是（模型 %d 个，已运行 %ds）' % (health.get('models', 0), health.get('uptime', 0)))
    else:
        _log('  代理运行 : 否（运行 `python ow-lite.py start` 启动）')
    oc = (health or {}).get('opencode') or {}
    if oc.get('running'):
        _log('  OpenCode : 运行中 127.0.0.1:%s' % oc.get('port'))
    else:
        _log('  OpenCode : 未运行（代理会在首次请求时按需拉起）')
    _log('  数据目录 : %s' % rt.root)
    _log('  可执行   : %s %s' % (cfg['opencode_binary'],
                                 '(存在)' if os.path.exists(cfg['opencode_binary']) else '(缺失!)'))
    st = workbuddy.status(cfg)
    _log('== WorkBuddy 配置 ==')
    _log('  文件     : %s %s' % (st['path'], '(存在)' if st['exists'] else '(不存在)'))
    if st['exists']:
        _log('  本工具条目 : %d 个 %s' % (len(st['ours']), st['ours'][:6]))
        _log('  其它条目   : %d 个（原样保留）' % st['others'])
    if st['backups']:
        _log('  历史备份   : %d 份（最新 %s）' % (len(st['backups']), st['backups'][-1]))
    return 0


# ---------------------------------------------------------------- 入口


def main(argv=None):
    p = argparse.ArgumentParser(
        prog='ow-lite',
        description='把 OpenCode 的免费模型接进 WorkBuddy 的最小可用代理',
    )
    p.add_argument('--config', help='配置文件路径', default=None)
    sub = p.add_subparsers(dest='cmd', required=True)

    sp = sub.add_parser('init', help='生成 config.json')
    sp.add_argument('--force', action='store_true', help='覆盖已有配置')
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser('probe', help='探测可用的免费模型')
    sp.add_argument('--stop', action='store_true', help='探测后停止 OpenCode')
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser('serve', help='启动代理（前台常驻）')
    sp.add_argument('--port', type=int, help='覆盖代理端口')
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser('start', help='后台常驻启动（不占终端）')
    sp.add_argument('--port', type=int, help='覆盖代理端口')
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser('stop', help='停止代理并清理其拉起的 OpenCode')
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser('install', help='把模型写入 WorkBuddy 配置')
    sp.add_argument('--limit', type=int, help='只写入前 N 个模型')
    sp.add_argument('--stop', action='store_true', help='写完停止 OpenCode')
    sp.set_defaults(func=cmd_install)

    sp = sub.add_parser('uninstall', help='从 WorkBuddy 配置移除（回滚）')
    sp.set_defaults(func=cmd_uninstall)

    sp = sub.add_parser('status', help='查看状态')
    sp.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
