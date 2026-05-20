# deploy

CD 部署相关文件，用于将产物从 OSS 拉取到 ECS 并更新运行中的服务。

## 结构

```
deploy/
├── webhook/
│   ├── deploy-webhook.py       # Python webhook 主程序
│   ├── deploy-webhook.service   # systemd unit 文件
│   └── config.json.example      # 配置文件模板
├── nginx/
│   └── deploy.conf              # nginx 反向代理到 webhook 的配置片段
└── README.md
```

## 部署流程

1. GitHub Actions 构建产物，按 `{service}/{commit}.jar` 路径上传 OSS
2. Actions 调用 `POST /deploy`（经 nginx 反向代理到 webhook）
3. webhook 校验 secret → 从 OSS 下载对应产物 → 备份旧版本 → 重启 systemd 服务

## ECS 初始化步骤

```bash
# 1. 安装 ossutil
wget https://gosspublic.alicdn.com/ossutil/1.7.19/ossutil64 -O /usr/local/bin/ossutil
chmod +x /usr/local/bin/ossutil

# 2. 创建部署用户和目录
useradd -r -s /bin/false deploy
mkdir -p /opt/deploy-webhook /opt/deploy/{api-service,mng-service,mng-web}

# 3. 部署 webhook
cp deploy/webhook/deploy-webhook.py /opt/deploy-webhook/
cp deploy/webhook/config.json /opt/deploy-webhook/  # 需先创建
cp deploy/webhook/deploy-webhook.service /etc/systemd/system/
chmod 600 /opt/deploy-webhook/config.json
chown -R deploy:deploy /opt/deploy-webhook

# 4. 启动
systemctl daemon-reload
systemctl enable --now deploy-webhook

# 5. 配置 nginx
cp deploy/nginx/deploy.conf /etc/nginx/conf.d/
nginx -t && systemctl reload nginx
```

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

重启 webhook 生效：

```bash
systemctl restart deploy-webhook
```
