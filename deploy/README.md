# 部署配置目录

这里保存开发与服务器部署的 Compose 覆盖配置和反向代理配置。业务代码不放在
此目录，真实密码、Token、证书和 `.env` 文件也不得提交 Git。

## 默认模式与可信局域网模式

项目默认的 `compose.yaml` 将网页发布到 `127.0.0.1:8000`，只有本机能够访问。
不要修改这个安全默认值。需要临时允许同一可信局域网中的其他机器访问时，叠加
`deploy/compose.lan.yaml`。

当前平台没有登录、身份验证或 HTTPS。局域网模式不能用于公网，不要在路由器上
配置端口转发，也不要绑定公网地址。

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
  config
```

输出中的端口应为指定内网 IP 的 `8000`，不应同时保留 `127.0.0.1:8000`。

### 3. 启动局域网开发模式

```bash
docker compose \
  -f compose.yaml \
  -f compose.dev.yaml \
  -f deploy/compose.lan.yaml \
  up -d --build --wait
```

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
