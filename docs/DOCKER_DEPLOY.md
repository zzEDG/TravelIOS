# Docker 部署

使用当前工作区源码构建 Vue/Nginx、FastAPI 镜像；PostgreSQL 16 使用独立数据卷。

## 配置

- 根目录 `.env`：随机 `POSTGRES_PASSWORD` 和 `HTTP_PORT`（默认 80）。
- `deploy/backend.env`：后端服务密钥、独立随机 `AUTH_CSRF_SECRET`、`CORS_ORIGINS`。
- 两份文件均被 Git 忽略，不会进入镜像；传输时使用 SSH，并保持权限 600。
- 当前按 `http://47.114.72.128` 配置，`AUTH_COOKIE_SECURE=false` 才能在 HTTP 下登录。HTTP 不加密登录信息；配置域名和 HTTPS 后应改为 true，并同步更新 CORS_ORIGINS。
- 云端使用新建数据库，不自动导入本机账号和历史记录。

## 启动

服务器需安装 Docker Engine 和 Docker Compose 插件，且 HTTP_PORT 没有被其他服务占用。

```bash
docker compose config -q
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1/health
```

数据库就绪后，migrate 服务执行幂等结构迁移；迁移成功才启动后端，后端健康后才启动前端。仅前端端口暴露给主机。

如果使用已打包镜像：

```bash
gzip -dc deploy/travelos-images.tar.gz | docker load
docker compose up -d --no-build
```

## 维护

```bash
docker compose logs --tail=100 backend
docker compose restart backend
docker compose exec -T db pg_dump -U travelos travelos > travelos-backup.sql
docker compose down
```

`down` 保留数据卷；不要使用 `down -v`，它会删除数据库。

## 发布文件

发布只需要 compose.yaml、backend/ 和 frontend/ 构建源码、根目录 .env 与 deploy/backend.env。排除 .git、node_modules、本机运行数据、简历和文档附件。本次 deploy/travelos-release.tar.gz 是私有部署包，包含运行所需环境配置，已设置权限 600 并通过阿里云 Workbench 上传；不得公开分享。镜像归档不含运行密钥。