# 部署配置目录

这里保存开发与服务器部署的 Compose 覆盖配置和反向代理配置。业务代码不放在
此目录，真实密码、Token、证书和 `.env` 文件也不得提交 Git。

## 配置分层

- `compose.yaml`：唯一应用容器、完整运行依赖、持久化存储及仅本机端口；适合
  正式运行，也是所有覆盖配置的基础。
- `compose.dev.yaml`：只把 `app/` 只读挂载进容器并启用 Uvicorn 热重载；只在
  本地开发时叠加。
- `deploy/compose.lan.yaml`：把默认 `127.0.0.1` 端口替换成指定可信内网 IPv4；
  本地开发和内网服务器都可以叠加。
- `compose.server.yaml`：通用服务器覆盖配置；监听 `0.0.0.0:18000` 并启用 Docker
  日志轮转，不包含任何用户专属绝对路径。
- `deploy/compose.training-agent.yaml`：可选训练覆盖；只读挂载宿主训练执行器通信
  目录并注入训练操作员。只有训练环境已经部署时才叠加。

主 `Dockerfile` 构建当前源码时会包含训练页面/API，但不会安装训练依赖。
`deploy/Dockerfile.training-overlay` 是服务器既有旧镜像的代码叠加方案，并非第二个
训练环境；新构建完整平台镜像时不必使用它。不要为数据管理、质检再启动重复平台容器。

服务器至少使用 `compose.yaml`、`compose.server.yaml` 和自己的 `.env`。开发阶段
需要源码热重载时再叠加 `compose.dev.yaml`。训练执行器是独立宿主机进程，不由
Compose 创建；它尚未部署时不要叠加训练覆盖。运行命令、Conda 环境、基座模型及
受控 VLM 权限见 `deploy/TRAINING_EXECUTOR.md`。不要把服务器的 `.env`、socket
令牌或模型权重复制进 Git。

服务器已导入匹配镜像时，在项目根目录执行：

```bash
docker compose --env-file .env -p hkc-vla \
  -f compose.yaml -f compose.server.yaml \
  up -d --no-build --force-recreate --wait
```

`--force-recreate` 会让新增挂载和环境变量生效；普通 `restart` 不会加载这些配置。
开发阶段希望服务器直接读取宿主源码并热重载时使用：

```bash
docker compose --env-file .env -p hkc-vla \
  -f compose.yaml -f compose.dev.yaml -f compose.server.yaml \
  up -d --no-build --force-recreate --wait
```

源码以只读方式挂载，容器不能修改宿主代码；宿主编辑器仍可正常修改。以上命令都
不会安装或修改宿主 LeRobot/Conda 环境。

宿主训练执行器准备完成后，才使用包含训练覆盖的完整命令：

```bash
docker compose --env-file .env -p hkc-vla \
  -f compose.yaml -f compose.server.yaml \
  -f deploy/compose.training-agent.yaml \
  up -d --no-build --force-recreate --wait
```

并在 `.env` 设置位于 Linux 文件系统的 `HKC_TRAINING_AGENT_SOCKET_DIR`。Unix
socket 目录不能放在 NTFS/exFAT 数据盘。

## 首次准备 `.env` 和持久化目录

```bash
cp .env.example .env
id -u
id -g
```

将输出写入 `.env` 的 `APP_UID`、`APP_GID`。默认数据目录和系统状态目录都位于
项目内；服务器可把大型文件与 SQLite/system 状态分开放置，例如：

```dotenv
HKC_STORAGE_PATH=/media/<用户名>/<数据盘>/vscode_project/HKC_Data_Platform/storage
HKC_SYSTEM_PATH=/home/<用户名>/vscode_project/SQLite_data
APP_UID=1000
APP_GID=1000
```

`HKC_STORAGE_PATH` 映射到 `/data`，`HKC_SYSTEM_PATH` 作为更具体的嵌套挂载映射
到 `/data/system`。后者包含 SQLite、日志、上传记录和任务状态，应放在 Linux
ext4；前者可以位于容量更大的数据盘。两个目标目录都必须由这组数字 UID/GID
写入。业务代码和数据集记录中不得出现宿主机绝对路径。已有 `.env` 时不要用示例
文件覆盖它。

如果 `HKC_STORAGE_PATH` 位于额外硬盘，必须先由系统管理员使用 UUID 配置稳定的
开机挂载，再启动 Compose。每次启动前可用 `findmnt -T "$HKC_STORAGE_PATH"`
确认路径确实落在目标磁盘；不能在磁盘尚未挂载时让 Docker 创建同名普通目录。

`.env.example` 还给出上传限制、Doctor 超时以及空的 VLM 配置。真实 VLM 地址与
Token 只写入部署机 `.env`，不要提交 Git。

## 默认模式与可信局域网模式

项目默认的 `compose.yaml` 将网页发布到 `127.0.0.1:8000`，只有本机能够访问。
不要修改这个安全默认值。需要临时允许同一可信局域网中的其他机器访问时，叠加
`deploy/compose.lan.yaml`。

当前平台已有基础登录与服务端会话，但没有 HTTPS。局域网模式仍不能用于公网，
不要在路由器上配置端口转发，也不要绑定公网地址。

全新数据库没有默认账号。容器首次健康启动后，由管理员交互创建首个用户：

```bash
docker compose exec platform \
  python -m app.cli.create_user --username <用户名>
```

密码输入不会显示在终端，命令也不接受密码参数；不要把密码写在命令、Compose、
`.env` 或文档中。继续创建其他账号时重复执行同一命令。使用下面的命令确认账号：

```bash
docker compose exec platform python -m app.cli.list_users
```

列表不会显示密码哈希或会话令牌。账号位于
`HKC_SYSTEM_PATH/platform.sqlite`；重新创建容器不会删除账号，但换用全新系统
状态目录时必须重新创建。详细说明见项目根目录 `how_use.md`。

### 1. 查找本机的可信内网地址

```bash
ip -br -4 address show
ip -4 route show
```

选择当前联网网卡上的私有 IPv4 地址，例如 `192.168.43.7`。不要选择
`127.0.0.1`、`docker0` 或名称以 `br-` 开头的 Docker 网桥地址。

### 2. 检查最终配置

以下示例中的 IP 必须替换为本机当前实际地址：

```bash
export HKC_LAN_BIND_ADDRESS=192.168.43.7

docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  config --quiet
```

输出中的端口应为指定内网 IP 的 `8000`，不应同时保留 `127.0.0.1:8000`。

### 3. 启动局域网开发模式或服务器模式

本地开发需要源码热重载：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  up -d --build --wait
```

内网服务器不挂载源码。服务器可联网构建时运行：

```bash
docker compose \
  -f compose.yaml \
  -f deploy/compose.lan.yaml \
  up -d --build --wait
```

如果服务器已经通过 `docker load` 导入 `hkc-data-platform:v3.0-server`，则不要重新
联网构建，使用：

```bash
docker compose \
  -f compose.yaml \
  -f deploy/compose.lan.yaml \
  up -d --no-build --force-recreate --wait
```

`compose.yaml` 默认使用 `hkc-data-platform:v3.0-server`；如需使用其他受控标签，只在
部署机 `.env` 中设置 `HKC_IMAGE`。

在另一台同网段机器的浏览器中访问：

```text
http://192.168.43.7:8000/
```

如果开发机 IP 发生变化，重新设置 `HKC_LAN_BIND_ADDRESS` 并再次执行启动命令。

### 4. 验证监听与页面

开发机执行：

```bash
ss -lnt '( sport = :8000 )'
curl -fsS -o /dev/null -w '%{http_code}\n' \
  "http://${HKC_LAN_BIND_ADDRESS}:8000/dashboard/"
```

第一条应显示指定内网 IP，第二条应输出 `200`。最终验收仍需要另一台真实局域网
机器访问，因为开发机访问自己的内网 IP 不能证明中间网络和防火墙均已放行。

### 5. UFW 防火墙

先查看状态：

```bash
sudo ufw status verbose
```

如果 UFW 已启用且另一台机器无法访问，只允许实际可信网段从实际联网网卡进入。
以下是网卡 `wlp4s0`、可信网段 `192.168.43.0/24` 的示例，使用前必须按实际情况
替换：

```bash
sudo ufw allow in on wlp4s0 \
  from 192.168.43.0/24 \
  to any port 8000 proto tcp \
  comment 'HKC platform trusted LAN'
```

不要执行不限制来源网段的 `sudo ufw allow 8000`。

不再需要该规则时，使用相同条件删除：

```bash
sudo ufw delete allow in on wlp4s0 \
  from 192.168.43.0/24 \
  to any port 8000 proto tcp
```

### 6. 停止局域网模式

在仍保留同一个 `HKC_LAN_BIND_ADDRESS` 的终端中执行：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  down
```

需要恢复默认本机模式时执行：

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d --wait
```

恢复后，`ss` 应重新显示 `127.0.0.1:8000`。

## 依赖与镜像验收

Python 直接依赖统一来自 `pyproject.toml`；Dockerfile 另外安装 FFmpeg，基础镜像
提供 HTTPS CA 证书。修改依赖或 Dockerfile 后必须重新构建，不能只 `restart`：

```bash
docker compose build --pull platform
docker compose run --rm platform python -m pip check
docker compose up -d --force-recreate --wait
docker compose ps
```

正常结果应为 `pip check` 输出 `No broken requirements found`，`platform` 显示
`healthy`。运行镜像不包含 pytest/Ruff、CUDA、ROS 2 或 VLA 训练依赖。

默认通过官方 `https://pypi.org/simple` 构建。仅当部署网络无法稳定访问官方 PyPI
时，可以在本机 `.env` 中临时设置 `PIP_INDEX_URL` 和 `PIP_DEFAULT_TIMEOUT`；不要把
个人代理或未经审核的镜像地址提交到仓库。恢复正常网络后应切回官方源。

## 更新、备份与迁移设备

代码、镜像、数据目录和系统状态目录是不同内容。更换服务器时需要迁移 Git 项目、
`HKC_STORAGE_PATH` 与 `HKC_SYSTEM_PATH`；只推送或拉取镜像不会包含数据集、
SQLite、报告或回收站。

备份前先停止写入：

```bash
docker compose stop platform
```

然后由管理员同时备份 `HKC_STORAGE_PATH` 和 `HKC_SYSTEM_PATH` 的完整目录，完成
后再启动：

```bash
docker compose up -d --wait
```

禁止只复制 `platform.sqlite` 而遗漏其他 system 状态或 Raw/Derived/报告文件，也
不要在运行上传、标注发布、数据处理或质检任务时制作不一致的文件级备份。

更新代码后按顺序执行：拉取/切换到已验收版本、检查 `.env`、执行
`docker compose config --quiet`、重新构建、再 `up -d --force-recreate --wait`。
Compose 重建容器不会删除 bind mount 中的持久化目录；不要使用带数据删除含义的
命令或手工清空 `storage/`。
