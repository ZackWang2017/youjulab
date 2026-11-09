from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def available() -> dict:
    binary = shutil.which("docker")
    image = os.environ.get("OFFICE_QUANT_IMAGE", "youju-quant:1")
    if not binary:
        return {"ready": False, "message": "自定义 Python 需要后台 Docker 沙箱；当前未安装 Docker。"}
    try:
        result = subprocess.run([binary, "image", "inspect", image], capture_output=True, timeout=5)
        ready = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ready = False
    return {"ready": ready, "message": "隔离执行环境就绪" if ready else "沙箱镜像尚未准备，请构建并配置 OFFICE_QUANT_IMAGE。"}


def run_code(code: str, dataset: dict, parameters: dict, run_id: str, cancelled) -> list:
    status = available()
    if not status["ready"]:
        raise ValueError(status["message"])
    name = "youju-quant-" + run_id
    with tempfile.TemporaryDirectory(prefix="youju-quant-") as directory:
        root = Path(directory)
        (root / "strategy.py").write_text(code, encoding="utf-8")
        (root / "input.json").write_text(json.dumps({"dataset": dataset, "parameters": parameters}), encoding="utf-8")
        os.chmod(root, 0o755)
        for p in root.iterdir():
            os.chmod(p, 0o644)
        command = ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only", "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "1g", "--cpus", "1", "--pids-limit", "64", "--log-driver", "none", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m", "-v", f"{root}:/input:ro", os.environ.get("OFFICE_QUANT_IMAGE", "youju-quant:1")]
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            deadline = time.monotonic() + 120
            try:
                while process.poll() is None:
                    if cancelled() or time.monotonic() > deadline or stdout.tell() > 5_000_000 or stderr.tell() > 200_000:
                        raise ValueError("已取消或执行超时/输出超限。")
                    time.sleep(0.1)
                stdout.seek(0); stderr.seek(0)
                if process.returncode:
                    raise ValueError("沙箱程序失败：" + stderr.read(2000).decode("utf-8", "replace"))
                result = json.loads(stdout.read(5_000_001))
                if not isinstance(result, list):
                    raise ValueError("沙箱输出格式无效。")
                return result
            finally:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
                if process.poll() is None:
                    process.kill()
                process.wait()

