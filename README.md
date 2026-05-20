# auto-local-deploy-webhook

基于 Python3 的 Webhook 部署服务，用于将产物从阿里云 OSS 拉取到 ECS 并更新运行中的服务。

## 结构

```
├── webhook/
│   ├── deploy-webhook.py        # Python webhook 主程序
│   ├── deploy-webhook.service   # webhook 自身的 systemd unit
│   └── config.json.example      # 配置文件模板
├── nginx/
│   └── deploy.conf              # nginx 反向代理配置片段
└── README.md
```

## 部署流程

1. GitHub Actions 构建产物，按 `{service}/{commit}.jar` 路径上传 OSS
2. Actions 调用 `POST /deploy`（经 nginx 反向代理到 webhook）
3. webhook 校验 secret → 从 OSS 下载产物 → 备份旧版本 → 重启 systemd 服务

## ECS 初始化步骤

```bash
# 1. 安装 ossutil
wget https://gosspublic.alicdn.com/ossutil/2.3.0/ossutil64 -O /usr/local/bin/ossutil
chmod +x /usr/local/bin/ossutil

# 2. 创建部署用户和目录
useradd -r -s /bin/false deploy
mkdir -p /opt/deploy-webhook

# 3. 部署 webhook
cp webhook/deploy-webhook.py /opt/deploy-webhook/
cp webhook/config.json /opt/deploy-webhook/          # 需先基于 config.json.example 创建
cp webhook/deploy-webhook.service /etc/systemd/system/
chmod 600 /opt/deploy-webhook/config.json
chown -R deploy:deploy /opt/deploy-webhook

# 4. 启动 webhook
systemctl daemon-reload
systemctl enable --now deploy-webhook

# 5. 配置 nginx（按需修改 server_name 和 secret 校验逻辑）
cp nginx/deploy.conf /etc/nginx/conf.d/
nginx -t && systemctl reload nginx
```

## 被部署服务需要准备什么

每个被部署的 Java/go 服务需要**提前在 ECS 上放置好对应的 systemd unit**，例如：

```ini
# /etc/systemd/system/deploy-api.service
[Unit]
Description=API Service
After=network.target

[Service]
Type=simple
User=deploy
WorkingDirectory=/opt/deploy/api-service
ExecStart=/usr/bin/java -jar /opt/deploy/api-service/api-service.jar
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`systemctl enable deploy-api` 后，webhook 部署时即可通过 `systemd_unit` 配置项自动重启该服务。日志通过 `journalctl -u deploy-api -f` 查看。

## 新增服务

在 `config.json` 的 `services` 中添加条目：

```json
"my-service": {
    "oss_path": "my-service/{commit}.jar",
    "deploy_dir": "/opt/deploy/my-service",
    "artifact_name": "my-service.jar",
    "systemd_unit": "deploy-my-service",
    "backup": true
}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `oss_path` | 是 | OSS 产物路径，`{commit}` 会被替换为请求中的 commit 值 |
| `deploy_dir` | 是 | 产物部署到的本地目录 |
| `artifact_name` | 否 | 部署后的文件名，不填则保留原始文件名 |
| `systemd_unit` | 否 | 部署完成后要重启的 systemd unit 名 |
| `deploy_cmd` | 否 | 自定义部署命令，`{artifact}` 会被替换为产物路径 |
| `backup` | 否 | 是否备份旧版本（需配合 `artifact_name` 使用） |

修改后重启 webhook 生效：

```bash
systemctl restart deploy-webhook
```
