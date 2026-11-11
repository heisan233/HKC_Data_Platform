# HKC 数采平台日常使用手册

本文只保留当前 MySQL 版本的有效操作。项目原理和功能范围见 `readme.md`，服务器
部署细节见 `deploy/README.md`。

## 1. 第一次使用

进入包含 `compose.yaml` 的项目根目录：

```bash
cd /path/to/HKC_Data_Platform
cp .env.example .env
chmod 600 .env
```

编辑 `.env`：

- `HKC_STORAGE_PATH`：数据集、报告、导出和模型目录；
- `HKC_MYSQL_DATA_PATH`：MySQL 私有数据目录；
- `HKC_MYSQL_PASSWORD`：平台 MySQL 账号密码；
- `HKC_MYSQL_ROOT_PASSWORD`：MySQL root 密码；
- `APP_UID`、`APP_GID`：持久化目录所有者；
- 可选的 VLM、局域网和训练执行器配置。

两个 MySQL 密码必须非空且不同，不能写入 Git。storage 与 MySQL 目录也不能使用同一
路径。

检查目录：

```bash
mkdir -p "$(awk -F= '/^HKC_STORAGE_PATH=/{print $2}' .env)"
mkdir -p "$(awk -F= '/^HKC_MYSQL_DATA_PATH=/{print $2}' .env)"
```

如果路径含空格或复杂字符，请不要使用上面的便捷命令，改为人工创建准确目录。

## 2. 本机开发

启动或重新创建：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml up -d --build --force-recreate --wait
```

访问：

```text
http://127.0.0.1:8000/login
```

`compose.dev.yaml` 只读挂载 `app/` 和 `migrations/`，Python 改动通常会自动
重载。修改依赖、Dockerfile 或镜像内文件后必须重新构建。

查看状态和日志：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml ps

docker compose --env-file .env -f compose.yaml -f compose.dev.yaml logs -f --tail=200 platform
```

停止但保留数据：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml down
```

仅重启已有容器：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml restart platform
```

`restart` 不会重新读取新的 Compose 挂载、端口或环境变量。配置变化时使用
`up -d --force-recreate --wait`。

## 3. 本机 18001 并行验收

当 8000 端口被另一套平台占用时：

```bash
docker compose --env-file .env -p hkc-v31-mysql -f compose.yaml -f compose.dev.yaml -f deploy/compose.local-mysql.yaml up -d --build --wait
```

访问 `http://127.0.0.1:18001/login`。项目名 `hkc-v31-mysql` 只是容器命名隔离，
不改变数据库内容；必须仍使用独立 storage 和 MySQL 目录。

## 4. 可信局域网

查看本机 IPv4：

```bash
ip -br -4 address show
ip -4 route show
```

把可信局域网网卡的实际地址写入当前终端：

```bash
read -r -p "请输入本机可信局域网 IPv4：" HKC_LAN_BIND_ADDRESS
export HKC_LAN_BIND_ADDRESS
```

启动开发模式局域网：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml -f deploy/compose.lan.yaml up -d --build --force-recreate --wait
```

另一台同网机器访问：

```text
http://LAN_IP_ADDRESS:8000/login
```

切换网络后 IP 可能变化，需要重新设置变量并重新创建容器。不要把局域网端口转发到
公网；如启用防火墙，只允许实际可信网段访问。

## 5. 服务器运行

服务器基础组合发布 `0.0.0.0:18000`：

```bash
docker compose --env-file /path/to/server.env -p hkc-vla -f compose.yaml -f compose.server.yaml up -d --no-build --force-recreate --wait
```

`--no-build` 要求 `HKC_IMAGE` 指定的镜像已经存在。需要宿主训练执行器时增加：

```bash
-f deploy/compose.training-agent.yaml
```

并在服务器环境文件中设置 `HKC_TRAINING_AGENT_SOCKET_DIR`。完整服务器目录与备份
要求见 `deploy/README.md`。

服务器状态：

```bash
docker compose --env-file /path/to/server.env -p hkc-vla -f compose.yaml -f compose.server.yaml ps
```

如果运行时叠加了训练执行器文件，之后所有 `up`、`exec`、`logs` 和 `down`
命令都应保持同一组 `-f` 参数。

## 6. 用户管理

以下示例使用本机开发组合。服务器上替换为实际环境文件、项目名和 Compose 组合。

创建用户：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml exec platform python -m app.cli.create_user --username USERNAME
```

列出用户：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml exec platform python -m app.cli.list_users
```

重置密码：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml exec platform python -m app.cli.reset_user_password --username USERNAME
```

停用或恢复：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml exec platform python -m app.cli.disable_user --username USERNAME

docker compose --env-file .env -f compose.yaml -f compose.dev.yaml exec platform python -m app.cli.enable_user --username USERNAME
```

新建或重置密码后，用户下次登录必须修改密码。停用用户会立即注销其全部会话。

## 7. 机器人 Token

机器人通常在网页创建时自动生成 Token，并弹窗显示一次。网页列表只显示前缀。

终端查看完整 Token：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml exec platform python -m app.cli.show_robot_token --robot-id RB000001
```

为旧机器人补充 Token：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml exec platform python -m app.cli.create_robot_token --robot-id RB000001
```

重新生成：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml exec platform python -m app.cli.regenerate_robot_token --robot-id RB000001
```

删除：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml exec platform python -m app.cli.delete_robot_token --robot-id RB000001
```

完整 Token 只能保存到采集机权限受限的文件或临时环境变量，不能提交仓库。

## 8. 采集端一次性上传

第二版工具位于 `tools/auto_update/`：

```bash
cd tools/auto_update
cp config.env.example config.env
chmod 600 config.env
```

编辑服务器地址和 Token 文件路径后，将多个 LeRobot v3 文件夹或 ZIP 放入
`datasets/`，运行：

```bash
./upload_once.sh
```

也可显式指定：

```bash
./upload_once.sh /path/to/dataset-a /path/to/dataset-b.zip
```

数据集显示名称自动使用文件夹名或 ZIP 去掉扩展名后的名称。平台不允许重复显示
名称；需要自定义时先重命名来源。

成功上传会写 `.hkc-uploaded.json`。测试平台重置后如需从头上传，只清理本地标记：

```bash
./clear_upload_markers.sh
```

该命令不会删除本地数据集，也不会删除服务器 DS。详见
`tools/auto_update/README.md`。

## 9. 更新代码与镜像

先确认工作区：

```bash
git status --short --branch
git pull --ff-only
```

开发挂载模式中，`app/` 修改通常直接生效。以下变化需要重建镜像：

- `Dockerfile`；
- `pyproject.toml`；
- 镜像中未挂载的文件；
- 正式服务器不挂载源码时的任何代码变化。

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml build platform

docker compose --env-file .env -f compose.yaml -f compose.dev.yaml up -d --force-recreate --wait
```

遇到镜像仓库或 Python 包下载超时时，应先处理网络、代理或受控镜像源，再继续构建；
不要在通用 Compose 中写死临时镜像站。

## 10. 备份

完整恢复需要同时备份：

1. `HKC_STORAGE_PATH`；
2. MySQL 逻辑备份或完整 `HKC_MYSQL_DATA_PATH` 冷备份；
3. 服务器私有环境文件；
4. 与当前提交匹配的应用镜像或可重建源码。

在线导出 MySQL 的示例：

```bash
docker compose --env-file .env -f compose.yaml exec -T mysql sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' > hkc-platform.sql
```

复制 MySQL 私有目录前必须先停 MySQL 容器，不能在其写入过程中直接复制。

## 11. 常见故障

### 网页打不开

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml ps
curl -I http://127.0.0.1:8000/healthz
ss -lnt | grep -E ':8000|:18000|:18001'
```

- `PORTS` 为空：启动时缺少正确覆盖文件，重新 `up --force-recreate`；
- 容器 unhealthy：查看 `logs platform` 和 `logs mysql`；
- 切换网络后局域网失效：重新确认 IP 并重新创建容器；
- 本机能开、其他电脑不能开：检查 Docker 绑定地址、同网段和防火墙。

### `docker compose restart` 报找不到配置

必须先进入项目根目录，或者显式提供所有 `-f` 和 `--env-file`。

### 修改代码后页面没有变化

- 开发模式确认容器挂载的是当前工作区；
- 查看 Uvicorn reload 日志；
- 强制刷新浏览器缓存；
- 正式模式重新构建镜像并创建容器。

### 上传名称冲突

数据集显示名称和活跃质检方案名称不能重复。比较时忽略首尾空格和英文大小写。
回收站中的 DS 仍占用名称，永久清除后才可复用。

### 自动上传显示“标记跳过”

这是采集端本地标记，不代表当前平台一定仍保留该 DS。测试环境重置后运行
`tools/auto_update/clear_upload_markers.sh`，再重新上传。

### 训练页面显示执行器离线

检查服务器启动时是否叠加 `deploy/compose.training-agent.yaml`，以及 socket 目录、
宿主执行器和模型专属容器。详见 `deploy/TRAINING_EXECUTOR.md`。
