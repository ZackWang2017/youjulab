#!/usr/bin/env python3
"""Gitee Push Webhook 示例：校验 token 后调用 deploy-mcp.sh。

  DEPLOY_WEBHOOK_SECRET=你的密钥 python3 deploy/webhook-mcp.example.py
  默认监听 0.0.0.0:9000 路径 /hooks/deploy-mcp
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = os.environ.get("DEPLOY_WEBHOOK_SECRET", "").strip()
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "deploy", "deploy-mcp.sh")
HOST = os.environ.get("DEPLOY_WEBHOOK_HOST", "0.0.0.0")
PORT = int(os.environ.get("DEPLOY_WEBHOOK_PORT", "9000"))
PATH = os.environ.get("DEPLOY_WEBHOOK_PATH", "/hooks/deploy-mcp")
BRANCH = os.environ.get("DEPLOY_BRANCH", "main")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def do_POST(self):
        if self.path.split("?", 1)[0] != PATH:
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        token = (self.headers.get("X-Gitee-Token") or self.headers.get("X-Gitee-Token".lower()) or "").strip()
        if not SECRET or token != SECRET:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        ref = str(payload.get("ref") or "")
        if ref and ref != f"refs/heads/{BRANCH}":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ignored branch")
            return

        def run():
            subprocess.run([SCRIPT], cwd=ROOT, check=False)

        threading.Thread(target=run, daemon=True).start()
        self.send_response(202)
        self.end_headers()
        self.wfile.write(b"accepted")


if __name__ == "__main__":
    if not SECRET:
        raise SystemExit("请设置环境变量 DEPLOY_WEBHOOK_SECRET")
    print(f"listening http://{HOST}:{PORT}{PATH}")
    HTTPServer((HOST, PORT), Handler).serve_forever()
