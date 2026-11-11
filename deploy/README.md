# 部署说明

本文描述当前 MySQL 版本的 Compose 组合。日常操作见根目录 `how_use.md`。

## 1. Compose 文件

| 文件 | 作用 |
| --- | --- |
| `compose.yaml` | MySQL + 平台基础服务，默认 `127.0.0.1:8000` |
| `compose.dev.yaml` | 只读挂载 `app/`、`migrations/`，启用热重载 |
| `compose.server.yaml` | 服务器发布 `0.0.0.0:18000`，限制 Docker 日志 |
| `deploy/compose.lan.yaml` | 绑定指定可信局域网 IPv4，端口 8000 |
| `deploy/compose.local-mysql.yaml` | 本机并行验收，`127.0.0.1:18001` |
| `deploy/compose.training-agent.yaml` | 可选宿主训练执行器 Unix socket |

文件按从基础到覆盖的顺序叠加。同一部署后续的 `up`、`exec`、`logs` 和 `down`
应保持同一项目名、环境文件和 Compose 组合。

## 2. 部署前准备

```bash
cp .env.example .env
chmod 600 .env
```

至少配置：

```dotenv
HKC_STORAGE_PATH=/path/to/hkc-storage
HKC_MYSQL_DATA_PATH=/path/to/mysql-data
HKC_IMAGE=hkc-data-platform:VERSION_TAG
HKC_MYSQL_DATABASE=hkc_platform
HKC_MYSQL_USER=hkc_app
HKC_MYSQL_PASSWORD=CHANGE_ME_APP_PASSWORD
HKC_MYSQL_ROOT_PASSWORD=CHANGE_ME_ROOT_PASSWORD
APP_UID=1000
APP_GID=1000
```

要求：

- storage 与 MySQL 使用不同目录；
- MySQL 目录位于 Linux 原生文件系统；
- storage 可使用大容量数据盘，但必须允许应用 UID/GID 读写；
- `.env` 不提交 Git；
- 正式部署前确认镜像架构与服务器一致。

推荐把项目源码、storage、MySQL 私有目录和训练执行器目录相互分开。训练 socket
必须放在支持 Unix socket 的 Linux 文件系统。

## 3. 本机开发

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml up -d --build --force-recreate --wait
```

访问 `http://127.0.0.1:8000/login`。

开发覆盖只读挂载源码。静态文件刷新即可生效，Python 由 Uvicorn reload；依赖、
Dockerfile 或生产镜像内容变化仍需重建。

## 4. 可信局域网

```bash
read -r -p "请输入本机可信局域网 IPv4：" HKC_LAN_BIND_ADDRESS
export HKC_LAN_BIND_ADDRESS
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml -f deploy/compose.lan.yaml up -d --build --force-recreate --wait
```

`HKC_LAN_BIND_ADDRESS` 必须是本机实际网卡地址。切换网络后重新确认并创建容器。
防火墙只放行实际可信网段，不开放到公网。

## 5. 服务器部署

确保服务器已经具有 `HKC_IMAGE` 对应镜像，然后：

```bash
docker compose --env-file /path/to/server.env -p hkc-vla -f compose.yaml -f compose.server.yaml up -d --no-build --force-recreate --wait
```

访问 `http://SERVER_IP:18000/login`。

如果已部署训练执行器，额外叠加：

```text
-f deploy/compose.training-agent.yaml
```

环境文件还需设置：

```dotenv
HKC_TRAINING_AGENT_SOCKET_DIR=/path/to/private/socket-directory
```

该目录不能放在 NTFS/exFAT。执行器详情见 `deploy/TRAINING_EXECUTOR.md`。

## 6. 镜像离线迁移

源机器：

```bash
docker image inspect IMAGE_TAG --format 'OS={{.Os}} ARCH={{.Architecture}} SIZE={{.Size}}'
docker save IMAGE_TAG | gzip > hkc-platform.tar.gz
sha256sum hkc-platform.tar.gz > hkc-platform.tar.gz.sha256
```

目标服务器：

```bash
sha256sum -c hkc-platform.tar.gz.sha256
gzip -dc hkc-platform.tar.gz | docker load
docker image inspect IMAGE_TAG
```

MySQL 镜像也必须存在。离线镜像不包含宿主 `.env`、storage 或 MySQL 数据。

## 7. 源码挂载与镜像内源码

- 叠加 `compose.dev.yaml`：容器使用宿主 `app/` 和 `migrations/`；
- 不叠加：容器使用构建镜像时复制进去的代码；
- `restart` 不会重新读取挂载或环境变量；
- 配置变化使用 `up -d --force-recreate`；
- 不挂载源码的部署更新代码时必须重建或导入新镜像。

服务器临时开发可以叠加开发覆盖，稳定部署应移除源码挂载并使用已验证镜像。

## 8. 验证

```bash
docker compose --env-file /path/to/server.env -p hkc-vla -f compose.yaml -f compose.server.yaml ps
curl -fsS http://127.0.0.1:18000/healthz
```

还应确认：

- platform 与 mysql 均 healthy；
- 容器 `/data` 指向预期 storage；
- MySQL `/var/lib/mysql` 指向预期专用目录；
- 未登录页面跳转登录；
- 创建用户后能够首次登录改密；
- 上传小型测试 ZIP 后容器重启数据仍在；
- 训练部署时执行器在线。

## 9. 备份和更新

完整备份必须同时包含：

- MySQL 逻辑备份或停机后的数据目录冷备份；
- 完整 `HKC_STORAGE_PATH`；
- 私有环境文件；
- 当前 Git 提交和镜像标签。

推荐顺序：

1. 停止创建新的上传、质检、转换和训练任务；
2. 导出 MySQL；
3. 备份 storage；
4. 保存环境文件和镜像清单；
5. 更新镜像或源码；
6. 运行迁移并重建平台容器；
7. 检查健康、登录、DS 映射和任务状态。

不要单独恢复 MySQL 或 storage 后直接投入使用；二者必须来自一致的业务时间点。
