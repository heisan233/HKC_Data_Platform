# HKC 数采平台日常启动与网络切换

本文用于以下常见情况：

- 电脑切换了 Wi-Fi、有线网络或手机热点，本机 IPv4 地址发生变化；
- 原来的网页地址无法访问，需要用新地址重新启动平台；
- 回到公司网络后，需要让平台连接公司服务器上的 VLM；
- 查看容器状态、日志，或者停止平台。

> 当前平台没有登录鉴权和 HTTPS。局域网模式只允许在可信内网使用，不要映射到
> 公网，也不要在路由器上配置端口转发。

## 一、进入项目目录

打开终端，进入包含 `compose.yaml` 的项目根目录：

```bash
cd ~/vscode_project/HKC/HKC_Data_Platform_merge
```

确认位置正确：

```bash
pwd
ls compose.yaml compose.dev.yaml deploy/compose.lan.yaml
```

下文所有 Docker 命令都在这个目录执行。

## 二、选择访问方式

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

## 三、配置公司网络中的 VLM

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
HKC_DATA_ROOT=/data
VLM_BASE_URL=http://192.168.123.105:实际端口/v1
VLM_API_TOKEN=实际令牌
QC_DOCTOR_TIMEOUT_SECONDS=1800
```

注意：

- 地址、端口和 Token 必须以公司实际部署为准；
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

## 四、确认平台是否启动成功

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

## 五、停止平台

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
- 宿主机 `storage/` 中的数据集、SQLite、报告和导出文件；
- 项目代码。

不要增加 `-v`，以免误删以后可能使用的 Docker 卷。

## 六、切换网络后的最短操作清单

如果只是 IP 改变，按下面执行即可：

```bash
cd ~/vscode_project/HKC/HKC_Data_Platform_merge

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

## 七、仍然打不开时的排查顺序

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

## 八、需要记住的区别

- `docker compose restart`：只重启现有容器，不更新 IP 绑定和 `.env` 环境变量；
- `docker compose up -d --force-recreate`：按当前配置重新创建容器，切换网络时使用；
- `--build`：重新构建镜像，仅代码依赖或 Dockerfile 改变时需要；
- `logs -f`：查看日志，不会启动或停止容器；
- `down`：停止并移除容器，不会删除 `storage/` 中的真实数据。
