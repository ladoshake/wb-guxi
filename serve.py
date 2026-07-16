#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地服务器：提供 index.html 访问，并暴露两个接口：

  GET  /api/data      读取最新 data.json 返回（供页面优先加载）
  POST /api/refresh   重跑 fetch_data.py + build_html.py，生成最新榜单

运行方式：
  python serve.py
然后浏览器打开 http://localhost:8000 ，点击页面右上角「刷新数据」即可实时刷新。

说明：
  - 刷新复用已有 dividends_cache.json（若存在），仅实时重新拉取市值并重算，
    因此通常几秒内完成；新披露的分红需等每日自动化（删缓存全量重抓）才会入账。
  - 直接双击打开 index.html（file://）也能正常显示，但刷新按钮会提示需启动服务器。
"""
import http.server
import socketserver
import os
import sys
import json
import subprocess

WORKDIR = "/Users/green/WorkBuddy/2026-07-11-16-35-47"
VENV_PY = "/Users/green/.workbuddy/binaries/python/envs/default/bin/python"
PORT = 8000


def run_pipeline():
    py = VENV_PY if os.path.exists(VENV_PY) else sys.executable
    subprocess.run([py, "fetch_data.py"], cwd=WORKDIR, check=True)
    subprocess.run([py, "build_html.py"], cwd=WORKDIR, check=True)


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/data":
            try:
                with open(os.path.join(WORKDIR, "data.json"), encoding="utf-8") as f:
                    self._send(200, f.read())
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))
            return

        path = self.path.split("?")[0]
        if path == "/":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(WORKDIR, path.lstrip("/")))
        if not fp.startswith(WORKDIR) or not os.path.isfile(fp):
            self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))
            return
        ctype = "text/html; charset=utf-8" if fp.endswith(".html") else "application/octet-stream"
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_POST(self):
        if self.path.split("?")[0] == "/api/refresh":
            try:
                run_pipeline()
                self._send(200, json.dumps({"ok": True}, ensure_ascii=False))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))
            return
        self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    os.chdir(WORKDIR)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"服务已启动: http://localhost:{PORT}  (Ctrl+C 停止)")
        httpd.serve_forever()
