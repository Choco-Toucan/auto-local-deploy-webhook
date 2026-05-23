#!/usr/bin/env python3
"""
部署 Webhook
配置驱动：新增服务只需在 config.json 的 services 中添加一条。
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

# ─── 配置加载 ───────────────────────────────────────────────

CONFIG_PATH = os.environ.get(
    "DEPLOY_WEBHOOK_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("deploy-webhook")


def load_config():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    required = ["secret", "listen", "oss", "services"]
    for key in required:
        if key not in cfg:
            raise ValueError(f"config.json 缺少必填字段: {key}")
    return cfg


# ─── OSS 操作 ───────────────────────────────────────────────

def _oss_credentials(cfg):
    """提取 OSS 通用参数，适配 ossutil v2.x 的 -e/-i/-k 传参方式"""
    return [
        "-e", cfg["oss"]["endpoint"],
        "-i", cfg["oss"]["access_key_id"],
        "-k", cfg["oss"]["access_key_secret"],
    ]


def oss_download(cfg, oss_path, local_path):
    """从 OSS 下载文件，返回 True/False"""
    bucket = cfg["oss"]["bucket"]
    uri = f"oss://{bucket}/{oss_path}"
    cmd = ["ossutil", "cp", uri, local_path, *_oss_credentials(cfg), "--update"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        log.error("ossutil 下载失败: %s → %s\nstderr: %s", uri, local_path, result.stderr)
        return False
    log.info("OSS 下载完成: %s → %s", uri, local_path)
    return True


def oss_file_exists(cfg, oss_path):
    """检查 OSS 文件是否存在"""
    bucket = cfg["oss"]["bucket"]
    uri = f"oss://{bucket}/{oss_path}"
    cmd = ["ossutil", "ls", uri, *_oss_credentials(cfg)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    log.info("ossutil ls 结果: returncode=%s stdout=%s stderr=%s",
             result.returncode, result.stdout.strip(), result.stderr.strip())
    return result.returncode == 0 and "Object Number is: 1" in result.stdout


# ─── 部署操作 ───────────────────────────────────────────────

def deploy_service(cfg, service_name, commit):
    """部署单个服务，返回 {"ok": bool, "message": str}"""
    if service_name not in cfg["services"]:
        return {"ok": False, "message": f"未知服务: {service_name}"}

    svc = cfg["services"][service_name]
    oss_path = svc["oss_path"].replace("{commit}", commit)

    # 检查 OSS 产物是否存在
    if not oss_file_exists(cfg, oss_path):
        return {"ok": False, "message": f"OSS 产物不存在: {oss_path}"}

    deploy_dir = Path(svc["deploy_dir"])
    deploy_dir.mkdir(parents=True, exist_ok=True)

    # 确定下载文件路径
    if svc.get("artifact_name"):
        local_file = str(deploy_dir / f"{svc['artifact_name']}-{commit}.tmp")
        target_file = str(deploy_dir / svc["artifact_name"])
        backup_file = str(deploy_dir / f"{svc['artifact_name']}.bak")
    else:
        ext = os.path.splitext(oss_path)[1]
        local_file = str(deploy_dir / f"artifact-{commit}{ext}")

    # 下载
    if not oss_download(cfg, oss_path, local_file):
        return {"ok": False, "message": f"下载失败: {oss_path}"}

    # 备份 + 替换
    if svc.get("artifact_name"):
        if svc.get("backup") and os.path.exists(target_file):
            os.rename(target_file, backup_file)
            log.info("已备份: %s → %s", target_file, backup_file)
        os.rename(local_file, target_file)
        log.info("已替换: %s", target_file)

    # 执行自定义部署命令
    if svc.get("deploy_cmd"):
        artifact_path = target_file if svc.get("artifact_name") else local_file
        cmd = svc["deploy_cmd"].replace("{artifact}", artifact_path)
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            log.error("部署命令失败: %s\nstderr: %s", cmd, result.stderr)
            return {"ok": False, "message": f"部署命令失败: {result.stderr}"}
        log.info("部署命令完成: %s", cmd)

    # 重启 systemd 服务
    if svc.get("systemd_unit"):
        result = subprocess.run(
            ["systemctl", "restart", svc["systemd_unit"]],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.error("重启失败: %s\nstderr: %s", svc["systemd_unit"], result.stderr)
            return {"ok": False, "message": f"重启失败: {result.stderr}"}
        log.info("已重启: %s", svc["systemd_unit"])

    return {"ok": True, "message": f"{service_name} 部署成功"}


# ─── 部署执行（HTTP / CLI 共用） ─────────────────────────

def execute_deploy(cfg, commit, services):
    """执行部署并返回结果字典"""
    log.info("收到部署请求: commit=%s services=%s", commit, services)

    results = {}
    for svc_name in services:
        log.info("开始部署: %s", svc_name)
        t0 = time.time()
        result = deploy_service(cfg, svc_name, commit)
        elapsed = time.time() - t0
        result["elapsed_sec"] = round(elapsed, 1)
        results[svc_name] = result

    return results


# ─── HTTP Handler ──────────────────────────────────────────

class DeployHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        log.info("HTTP %s", fmt % args)

    def _json_response(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _verify_secret(self):
        expected = self.server.config["secret"]
        actual = self.headers.get("X-Deploy-Secret", "")
        return expected == actual

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._json_response(200, {"status": "ok"})
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path

        if path != "/deploy":
            self._json_response(404, {"error": "not found"})
            return

        if not self._verify_secret():
            log.warning("secret 校验失败，来源: %s", self.client_address)
            self._json_response(403, {"error": "forbidden"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            body = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as e:
            self._json_response(400, {"error": f"请求体解析失败: {e}"})
            return

        commit = body.get("commit")
        services = body.get("services")

        if not commit:
            self._json_response(400, {"error": "缺少 commit 参数"})
            return
        if not services or not isinstance(services, list):
            self._json_response(400, {"error": "缺少 services 列表"})
            return

        results = execute_deploy(self.server.config, commit, services)

        all_ok = all(r["ok"] for r in results.values())
        status_code = 200 if all_ok else 500
        self._json_response(status_code, {"services": results})


# ─── 入口 ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="部署 Webhook / 本地触发")
    sub = parser.add_subparsers(dest="mode", help="运行模式")

    # 本地触发子命令
    local = sub.add_parser("deploy", help="本地触发部署")
    local.add_argument("--commit", required=True, help="构建 commit hash")
    local.add_argument("--services", required=True, nargs="+", help="要部署的服务名列表")

    # HTTP 服务子命令
    sub.add_parser("serve", help="启动 HTTP Webhook 服务")

    args = parser.parse_args()
    cfg = load_config()

    if args.mode == "deploy":
        results = execute_deploy(cfg, args.commit, args.services)
        all_ok = all(r["ok"] for r in results.values())
        status = "成功" if all_ok else "失败"
        log.info("部署%s", status)
        for name, r in results.items():
            flag = "✓" if r["ok"] else "✗"
            log.info("  %s %s (%ss): %s", flag, name, r["elapsed_sec"], r["message"])
        sys.exit(0 if all_ok else 1)

    # 默认: HTTP 服务模式
    host = cfg["listen"]["host"]
    port = cfg["listen"]["port"]

    server = HTTPServer((host, port), DeployHandler)
    server.config = cfg

    log.info("Deploy Webhook 启动: %s:%d", host, port)
    log.info("已注册服务: %s", list(cfg["services"].keys()))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("收到退出信号，正在关闭...")
        server.shutdown()


if __name__ == "__main__":
    main()
