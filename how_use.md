# HKC 数采平台拉取、部署、启动与网络切换

本文用于以下常见情况：

- 电脑切换了 Wi-Fi、有线网络或手机热点，本机 IPv4 地址发生变化；
- 原来的网页地址无法访问，需要用新地址重新启动平台；
- 回到公司网络后，需要让平台连接公司服务器上的 VLM；
- 查看容器状态、日志，或者停止平台。

> 当前平台已有基础登录鉴权，但没有 HTTPS。局域网模式仍只允许在可信内网使用，
> 不要映射到公网，也不要在路由器上配置端口转发。

## 一、同事第一次拉取并构建集成分支

本节适用于同事在另一台 Ubuntu 电脑或服务器上第一次部署
`integration/hs-lxp-v3.0`。Git 只传输代码和公开配置，不传输 `.env`、数据集、
SQLite、报告、模型或其他 `storage/` 运行数据。

### Git 会带来什么

克隆正确分支后，以下公开文件会自动存在，不需要手工重写：

- `Dockerfile`、`compose.yaml`、`compose.dev.yaml`；
- `deploy/compose.lan.yaml`；
- `.env.example`；
- 应用代码、网页资源、数据库表结构代码和 `storage/` 空目录骨架。

### Git 不会带来什么

以下内容被 `.gitignore` 排除或属于操作系统配置，每台设备都必须单独准备：

| 内容 | 新设备上的处理方式 |
| --- | --- |
| `.env`、`.env.*` | 从 `.env.example` 新建，重新填写本机路径、UID/GID、IP、VLM 地址和 Token |
| `storage/` 真实内容 | 新部署保持空目录；迁移时复制完整目录，不能只复制 SQLite |
| `HKC_LAN_BIND_ADDRESS` | 使用新设备当前网卡的私有 IPv4，换网络后重新确认 |
| 外接硬盘挂载 | 在操作系统中重新挂载并配置开机自动挂载，Git 不管理磁盘 |
| Docker 代理或镜像加速 | 在新设备重新配置 `/etc/docker/daemon.json` 或 systemd，项目不会上传它们 |
| UFW/服务器防火墙规则 | 按新网段和实际网卡重新配置，只开放可信内网 TCP 8000 |
| 已构建 Docker 镜像 | 重新构建、从镜像仓库拉取，或使用 `docker save/load` 单独迁移 |
| IDE、缓存、日志 | 不需要迁移，会由本机工具和程序重新生成 |

Compose 只会自动读取“项目根目录”中的 `.env`。从另一台设备复制旧 `.env` 很容易
带入错误 IP、路径或密钥，因此默认做法是从 `.env.example` 重新创建。

### `.env` 变量在什么时候生效

| 变量 | 用途 | 修改后需要什么操作 |
| --- | --- | --- |
| `HKC_STORAGE_PATH` | 宿主机持久化目录 | 重新创建容器，并确认目标磁盘已挂载 |
| `HKC_IMAGE` | Compose 使用的应用镜像标签 | 重新创建容器；离线部署保持 `hkc-data-platform:v3.0-server` |
| `APP_UID`、`APP_GID` | 构建镜像内的非 root 用户 | 必须重新构建镜像；对已加载的离线镜像无效 |
| `PIP_INDEX_URL`、`PIP_DEFAULT_TIMEOUT` | 构建时下载 Python 包 | 只在重新构建镜像时使用 |
| `HKC_LAN_BIND_ADDRESS` | 绑定当前设备的可信内网 IPv4 | 使用局域网覆盖配置重新创建容器 |
| `VLM_BASE_URL`、`VLM_API_TOKEN` | 容器运行时连接 VLM | 重新创建容器，普通 `restart` 不够 |
| `QC_DOCTOR_TIMEOUT_SECONDS`、上传限制 | 容器运行参数 | 重新创建容器 |

不要在宿主机 `.env` 中设置 `HKC_DATA_ROOT`。它是容器内部固定的 `/data`；宿主机
目录只能通过 `HKC_STORAGE_PATH` 配置。

### 1. 分支负责人先发布最新代码

同事开始拉取前，分支负责人必须先在当前开发机完成代码审查、提交和推送：

```bash
cd ~/vscode_project/HKC/HKC_Data_Platform_v3.0
git status --short --branch
git log -3 --oneline --decorate
```

只有工作区修改已经提交，并且执行以下命令成功后，另一台机器才能取得最新版本：

```bash
git push origin integration/hs-lxp-v3.0
```

不要为了推送而执行 `git reset`、`git clean` 或强制覆盖远程分支。需要提交哪些
文件应先由双方审查决定。

### 2. 同事检查 Docker

```bash
docker --version
docker compose version
docker info --format 'Docker Server {{.ServerVersion}}'
```

三条命令都应正常输出。若最后一条出现访问 `/var/run/docker.sock` 权限不足，应先
完成 Docker 用户组配置并重新登录，不要长期依赖 `sudo docker`。

### 3. 第一次克隆指定分支

```bash
mkdir -p ~/vscode_project/HKC
cd ~/vscode_project/HKC

git clone \
  --branch integration/hs-lxp-v3.0 \
  https://github.com/heisan233/HKC_Data_Platform.git \
  HKC_Data_Platform_v3.0

cd HKC_Data_Platform_v3.0
git status --short --branch
git log -1 --oneline --decorate
```

如果同事电脑上已经有这个仓库，不要再次克隆。在工作区没有未提交修改的前提下：

```bash
cd ~/vscode_project/HKC/HKC_Data_Platform_v3.0
git fetch origin
git switch integration/hs-lxp-v3.0
git pull --ff-only origin integration/hs-lxp-v3.0
```

如果本地还没有同名分支，则使用：

```bash
git switch --track origin/integration/hs-lxp-v3.0
```

`--ff-only` 会在本地与远程已经分叉时停止，避免自动制造意外合并。遇到这种情况先
查看 `git status` 和本地提交，不要使用强制拉取。

### 4. 创建这台设备自己的配置

```bash
cp .env.example .env
id -u
id -g
nano .env
```

将 `APP_UID`、`APP_GID` 改为两条 `id` 命令的实际输出。开发机可保留：

```dotenv
HKC_STORAGE_PATH=./storage
```

服务器建议设置为已经备份、权限明确的绝对目录。真实 VLM 地址和 Token 只写入
本机 `.env`，不得提交 Git，也不要直接复制其他人的 `.env`。

外部存储目录必须在启动前存在，并由 `APP_UID:APP_GID` 对应的用户写入。例如，
假设额外硬盘已经可靠挂载到 `/mnt/hkc-data`：

```bash
sudo install -d -o "$(id -u)" -g "$(id -g)" /mnt/hkc-data/storage
```

然后在 `.env` 中设置：

```dotenv
HKC_STORAGE_PATH=/mnt/hkc-data/storage
```

不要把尚未挂载的目录写进 `.env`，否则系统可能在根分区创建同名普通目录，导致
数据写到错误磁盘。服务器还应由管理员配置 `/etc/fstab`，并在启动平台前用
`findmnt` 确认额外硬盘确实已挂载。

新的 Git 克隆不会包含原设备的数据。需要复用数据时，必须单独复制原设备
`HKC_STORAGE_PATH` 指向的完整目录，不能只复制 SQLite，也不能通过 Git 上传。

### 5. 准备基础镜像和 Python 下载源

优先尝试官方 Docker Hub：

```bash
docker pull python:3.11-slim-bookworm
```

如果当前网络无法访问 Docker Hub，应优先使用公司内部镜像仓库。仅作临时开发
验证时，也可以从已经审核的加速地址拉取，再标记成 Dockerfile 使用的标准名称：

```bash
docker pull docker.m.daocloud.io/library/python:3.11-slim-bookworm
docker tag \
  docker.m.daocloud.io/library/python:3.11-slim-bookworm \
  python:3.11-slim-bookworm
```

默认通过官方 PyPI 构建。如果官方 PyPI 下载过慢，可只在本机 `.env` 中临时改为：

```dotenv
PIP_INDEX_URL=https://mirrors.ustc.edu.cn/pypi/simple
PIP_DEFAULT_TIMEOUT=120
```

不要把个人代理、公司地址或临时镜像地址写进 `.env.example`。

### 6. 网络不稳定服务器使用离线镜像

离线镜像包不包含 Compose 文件、`.env` 或 `storage/`，服务器仍需要同一提交版本
的项目目录。可以通过 Git 获取项目目录，也可以由管理员通过内网或移动硬盘传输
代码包。

把镜像压缩包和 `.sha256` 文件放在服务器额外硬盘后，先进入文件所在目录校验：

```bash
sha256sum --check hkc-data-platform-v3.0-server-amd64.tar.gz.sha256
```

只有结果显示“成功”后才导入：

```bash
docker load --input hkc-data-platform-v3.0-server-amd64.tar.gz
```

当前离线包中的正式标签是：

```text
hkc-data-platform:v3.0-server
```

该离线镜像已经固定为 `linux/amd64` 和 UID/GID `1000:1000`。服务器的持久化目录
必须允许 `1000:1000` 写入；单独修改 `.env` 中的 `APP_UID/APP_GID` 不会改变已经
构建完成的镜像。UID/GID 不匹配时应重新构建镜像，不要让应用容器改成 root。

项目 `compose.yaml` 默认使用同一标签，因此导入后不需要再次 `docker tag`。确认镜像存在：

```bash
docker image inspect hkc-data-platform:v3.0-server \
  --format 'ID={{.Id}} OS={{.Os}} ARCH={{.Architecture}} USER={{.Config.User}}'
```

离线服务器启动时必须加 `--no-build`，避免 Compose 再次联网下载基础镜像或 Python
依赖。服务器只运行镜像内代码，不叠加 `compose.dev.yaml`：

```bash
docker compose \
  -f compose.yaml \
  -f deploy/compose.lan.yaml \
  up -d --no-build --force-recreate --wait
```

注意：把约 378 MB 压缩包放到额外硬盘，并不表示 Docker 解压后的镜像也在额外
硬盘。执行 `docker load` 前必须确认 Docker/Containerd 的实际数据目录有足够空间；
不要手工移动 `/var/lib/docker` 或 `/var/lib/containerd`。

### 7. 第一次构建并以本机开发模式启动

建议先只允许本机访问，确认平台正常后再开放可信局域网：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  config --quiet

docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  up -d --build --wait
```

浏览器访问：

```text
http://127.0.0.1:8000/
```

#### 创建和检查平台用户

全新 `storage/` 没有默认账号。容器健康后必须由管理员交互创建首个用户。推荐
明确写出用户名，密码仍由终端隐藏输入：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  exec platform \
  python -m app.cli.create_user --username <用户名>
```

将 `<用户名>` 换成实际用户名，不要输入尖括号。也可以省略 `--username`，此时
用户名同样由终端询问。随后根据提示输入两次密码；密码不会显示。命令故意不提供
`--password` 参数，禁止把密码写入 Git、`.env`、Compose、文档或命令参数。

同一命令既能创建首个用户，也能继续创建其他用户。用户名不区分英文字母大小写，
重复创建会明确失败且不会覆盖旧密码。创建后可安全查看账号摘要：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  exec platform \
  python -m app.cli.list_users
```

列表只显示 ID、用户名、启用状态、创建时间和最近登录时间，不显示密码哈希、密码或
会话令牌。用户保存在 `HKC_STORAGE_PATH/system/platform.sqlite`：重启或重建容器
不会丢失；重新克隆 Git 或使用一个全新存储目录时不会自动带来旧用户，必须重新
创建。若把完整 `HKC_STORAGE_PATH` 迁移到新设备，用户记录会与其他平台元数据一同
迁移。

正式部署或局域网部署时命令相同，只需在启动平台所用的项目目录中执行：

```bash
docker compose exec platform \
  python -m app.cli.create_user --username <用户名>
```

如果没有使用 Docker，也可以在项目 Python 环境中执行：

```bash
python -m app.cli.create_user --username <用户名>
python -m app.cli.list_users
```

此时命令写入 `HKC_DATA_ROOT` 指向的 SQLite；未设置时使用项目内 `storage/`。执行前
务必确认数据根目录，避免误在另一个空目录中创建一套新数据库。

检查容器、依赖和工作台：

```bash
docker compose -f compose.yaml -f compose.dev.yaml ps

docker compose -f compose.yaml -f compose.dev.yaml \
  exec platform python -m pip check

curl -fsS http://127.0.0.1:8000/healthz
```

正常结果应为容器 `healthy`、pip 没有依赖冲突并返回 `{"status":"ok"}`。浏览器
打开平台后应先进入 `/login`；需要让其他设备访问时，再按照本文“三、选择访问
方式”的局域网步骤重新创建容器。

### 8. 开发前运行自动化测试

正式运行镜像不包含 pytest/Ruff。可以使用一次性容器检查当前代码，测试数据写入
`/tmp`，不会读取真实 `storage/`：

```bash
docker run --rm \
  --volume "$PWD:/workspace:ro" \
  --workdir /workspace \
  --env HKC_DATA_ROOT=/tmp/hkc-test-data \
  --env PIP_INDEX_URL=https://pypi.org/simple \
  hkc-data-platform:v3.0-server \
  sh -lc '
    python -m pip install --user --no-cache-dir pytest==9.1.1 ruff==0.16.8 &&
    /home/appuser/.local/bin/ruff check --no-cache app tests &&
    python -m pytest -q -p no:cacheprovider
  '
```

当前 `integration/hs-lxp-v3.0` 基线的预期结果是 Ruff 全部通过、pytest `76 passed`。一条来自
Starlette/AnyIO 的弃用警告不影响功能。`docker run` 不会自动读取项目 `.env`；
如果官方 PyPI 较慢，可将测试命令中的地址明确替换成前述中科大地址。

### 9. 以后获取同事提交的更新

先停止正在修改文件，确认自己的工作已经提交或妥善保存，然后执行：

```bash
git status --short --branch
git fetch origin
git pull --ff-only origin integration/hs-lxp-v3.0
```

普通 Python/HTML/CSS 修改在开发模式下通常会热重载；如果更新包含 Dockerfile、
`pyproject.toml` 或 Compose 配置，则重新执行：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  up -d --build --force-recreate --wait
```

## 二、进入项目目录

打开终端，进入包含 `compose.yaml` 的项目根目录：

```bash
cd ~/vscode_project/HKC/HKC_Data_Platform_v3.0
```

确认位置正确：

```bash
pwd
ls compose.yaml compose.dev.yaml deploy/compose.lan.yaml
```

下文所有 Docker 命令都在这个目录执行。

首次运行时创建本机配置；如果 `.env` 已存在则不要覆盖：

```bash
cp .env.example .env
id -u
id -g
```

确认 `.env` 中的 `APP_UID`、`APP_GID` 与两条命令输出一致。默认
`HKC_STORAGE_PATH=./storage`；如果服务器把数据放在其他磁盘，应改成对应绝对
路径，并保证该目录归相同 UID/GID 所有。容器内路径始终是 `/data`。

## 三、选择访问方式

### 方式 A：只有本机访问

如果只需要在运行 Docker 的这台电脑上打开网页，不需要其他电脑访问，执行：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  up -d --force-recreate --wait
```

浏览器访问：

```text
http://127.0.0.1:8000/
```

这种模式绑定 `127.0.0.1`，切换网络后一般不需要修改 IP。

### 方式 B：允许可信局域网中的其他设备访问

这种模式需要使用电脑在当前网络中的 IPv4 地址。切换网络后，地址可能改变，
必须用新地址重新创建容器；只执行 `docker compose restart` 不会更新端口绑定。

#### 1. 查找当前 IPv4 地址

```bash
ip -br -4 address show
ip -4 route show
```

选择当前联网网卡上的私有 IPv4，例如：

```text
192.168.123.45
```

不要选择以下地址：

- `127.0.0.1`；
- `docker0` 上的地址；
- `br-` 开头的 Docker 网桥地址；
- 已经断开的旧网卡地址。

也可以让系统根据默认路由获取候选地址：

```bash
ip route get 1.1.1.1 | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}'
```

如果电脑同时开启 VPN、多个网卡或虚拟网络，自动结果可能不是其他设备能够访问的
地址，仍需结合 `ip -br -4 address show` 人工确认。

#### 2. 设置本次使用的新地址

把示例 IP 替换为刚才查到的实际地址：

```bash
export HKC_LAN_BIND_ADDRESS=192.168.123.45
```

确认变量：

```bash
echo "$HKC_LAN_BIND_ADDRESS"
```

这个 `export` 只对当前终端有效。以后新开终端或再次切换网络，需要重新执行。

固定 IP 的服务器也可以把地址写入本机 `.env`，这样新终端无需再次 `export`：

```dotenv
HKC_LAN_BIND_ADDRESS=192.168.123.45
```

Shell 中已经 `export` 的值优先于 `.env`。更换设备或网络后必须修改其中一处，并
执行带 `--force-recreate` 的启动命令。如果继续使用旧设备 IP，Docker 可能报告
`cannot assign requested address`，其他机器也会访问失败。

#### 3. 检查 Compose 配置

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  config --quiet
```

命令没有输出并正常返回，表示配置能够解析。

#### 4. 用新 IP 重新创建容器

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  up -d --force-recreate --wait
```

网络切换本身通常不需要重新构建镜像。只有修改了 `Dockerfile`、Python 依赖或镜像
内容时，才增加 `--build`：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  up -d --build --force-recreate --wait
```

#### 5. 打开网页

本机和同一可信局域网中的其他设备都使用新地址：

```text
http://192.168.123.45:8000/
```

局域网模式只绑定指定的内网 IP，因此 `http://127.0.0.1:8000/` 不一定可用。

## 四、配置公司网络中的 VLM

VLM 配置保存在项目根目录的 `.env` 中。`.env` 已被 Git 忽略，不要提交，也不要
把真实 Token 写进 `.env.example`。

如果第一次配置，可先创建本地文件：

```bash
cp .env.example .env
```

如果 `.env` 已存在，不要再次复制覆盖，直接编辑：

```bash
nano .env
```

填写公司 VLM 服务的实际地址和 Token，例如：

```dotenv
HKC_STORAGE_PATH=./storage
APP_UID=1000
APP_GID=1000
VLM_BASE_URL=http://192.168.123.105:实际端口/v1
VLM_API_TOKEN=实际令牌
QC_DOCTOR_TIMEOUT_SECONDS=1800
```

上面的内容只展示关键字段，不是一份完整 `.env`。请保留从 `.env.example` 复制的
PIP 下载源、上传大小限制等其他行，只修改当前设备确实需要调整的值。

注意：

- 地址、端口和 Token 必须以公司实际部署为准；
- `HKC_STORAGE_PATH` 是宿主机目录，不要把它改成容器路径 `/data`；
- `VLM_BASE_URL` 通常写到 `/v1`，平台会继续请求 `/models` 和
  `/chat/completions`；
- 不要把 Markdown 链接写法或方括号复制进 `.env`；
- 修改 `.env` 后，必须执行 `up -d --force-recreate --wait`，普通 `restart`
  不会把新的环境变量重新注入容器。

按照“方式 A”或“方式 B”的启动命令重新创建容器后，可从容器内部检查 VLM：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  exec platform \
  python -c 'import os,httpx; u=os.environ["VLM_BASE_URL"].rstrip("/"); t=os.environ.get("VLM_API_TOKEN","EMPTY"); r=httpx.get(u+"/models",headers={"Authorization":"Bearer "+t},timeout=8,trust_env=False); print(r.status_code); print(r.text[:300])'
```

以上命令是局域网模式的写法。如果使用仅本机模式，应去掉
`-f deploy/compose.lan.yaml`。

常见结果：

- HTTP `200`：通常表示容器可以访问 VLM；
- HTTP `401` 或 `403`：地址可达，但 Token 或权限不正确；
- `Connection refused`：目标地址可达，但端口没有服务监听；
- `ConnectTimeout`、`No route to host`：网络、路由或防火墙不通；
- `/models` 返回 `404`：当前 VLM 服务可能没有提供平台所需的 OpenAI 兼容接口。

## 五、确认平台是否启动成功

局域网模式使用：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  ps
```

正常情况下，`platform` 服务应显示为运行状态。查看实际端口绑定：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  port platform 8000
```

输出应类似：

```text
192.168.123.45:8000
```

本机测试工作台：

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' \
  "http://${HKC_LAN_BIND_ADDRESS}:8000/dashboard/"
```

正常应输出 `200`。

查看实时日志：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  logs -f --tail=100 platform
```

按 `Ctrl+C` 只会退出日志查看，不会关闭容器。

## 六、停止平台

局域网模式：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  down
```

仅本机模式：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  down
```

`down` 会删除应用容器和 Compose 网络，但不会删除：

- 已构建的镜像；
- 宿主机 `HKC_STORAGE_PATH` 目录中的数据集、SQLite、报告和导出文件；
- 项目代码。

不要增加 `-v`，以免误删以后可能使用的 Docker 卷。

## 七、切换网络后的最短操作清单

如果只是 IP 改变，按下面执行即可：

```bash
cd ~/vscode_project/HKC/HKC_Data_Platform_v3.0

ip -br -4 address show

export HKC_LAN_BIND_ADDRESS=新的实际IPv4地址

docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  up -d --force-recreate --wait

docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  ps
```

然后访问：

```text
http://新的实际IPv4地址:8000/
```

## 八、仍然打不开时的排查顺序

### 1. 检查容器

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  ps
```

如果未运行，查看日志：

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  logs --tail=200 platform
```

### 2. 检查是否仍绑定旧 IP

```bash
ss -lnt '( sport = :8000 )'
```

如果显示旧 IP，重新设置 `HKC_LAN_BIND_ADDRESS`，再运行带
`--force-recreate` 的启动命令。

### 3. 检查访问设备是否处于同一网络

手机热点、公司访客 Wi-Fi 和公司内部网络可能互相隔离。两台设备即使都能上网，
也不一定能够彼此访问。先确认它们位于允许互访的可信网段。

### 4. 检查 UFW

```bash
sudo ufw status verbose
```

如果 UFW 已启用，原来只允许手机热点网段的规则不会自动适配公司网段。应由网络
管理员确认当前网卡和可信网段，再只允许该网段访问 TCP 8000。不要直接执行不限制
来源的 `sudo ufw allow 8000`。

### 5. 检查是否启动了另一个旧项目容器

```bash
docker compose ls
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
```

确认正在访问的是当前项目的 `platform` 服务，而不是以前目录启动的旧容器。

## 九、需要记住的区别

- `docker compose restart`：只重启现有容器，不更新 IP 绑定和 `.env` 环境变量；
- `docker compose up -d --force-recreate`：按当前配置重新创建容器，切换网络时使用；
- `--build`：重新构建镜像，仅代码依赖或 Dockerfile 改变时需要；
- `logs -f`：查看日志，不会启动或停止容器；
- `down`：停止并移除容器，不会删除 `storage/` 中的真实数据。
