# HKC_Data_Platform Docker 部署学习笔记

## 一、最终目标

我们要实现的结构是：

```text
Ubuntu 宿主机
│
├── Docker
│
├── HKC 项目源代码
│   └── /home/heisan/vscode_project/HKC/HKC_Data_Platform
│
└── HKC 容器
    ├── Python 3.11
    ├── FastAPI
    ├── Starlette
    ├── Uvicorn
    ├── Jinja2
    └── 挂载宿主机上的 HKC 项目代码
```

本次采用的是：

> 镜像负责保存 Python 运行环境和依赖，项目代码保存在宿主机，通过绑定挂载提供给容器。

因此：

- 镜像可以推送到 Docker Hub；
- 项目代码不会随镜像上传；
- 更换设备时需要同时获得镜像和项目代码；
- 不需要在容器中安装 Anaconda；
- 当前项目也不需要 ROS 2、CUDA、PyTorch 或 GPU。

---

## 二、Docker 的几个基本概念

### 1. Dockerfile

Dockerfile 是“镜像制作说明书”。

它描述：

- 使用什么基础系统；
- 使用什么 Python 版本；
- 安装哪些依赖；
- 容器启动时执行什么命令。

Dockerfile 本身不是镜像，也不是容器。

### 2. 镜像 Image

镜像是构建完成的只读运行环境。

当前镜像名：

```text
hkc-data-platform-runtime:1.0
```

镜像中包含：

```text
Debian Bookworm 用户空间
Python 3.11.16
FastAPI 0.141.1
Starlette 1.6.0
Uvicorn 0.53.0
Jinja2 3.1.6
```

镜像中不包含：

```text
main.py
routers/
templates/
static/
```

### 3. 容器 Container

容器是镜像运行起来后的实例。

当前 Compose 自动生成的容器名：

```text
hkc_data_platform-hkc-platform-1
```

可以理解为：

```text
镜像 = 模板
容器 = 模板运行出来的实例
```

### 4. Bind Mount 绑定挂载

绑定挂载把宿主机目录映射到容器中。

当前配置：

```yaml
volumes:
  - .:/app:ro
```

含义：

```text
.       当前宿主机项目目录
/app    容器内项目目录
ro      容器只能读取，不能修改
```

因此宿主机上的：

```text
~/vscode_project/HKC/HKC_Data_Platform/main.py
```

在容器内显示为：

```text
/app/main.py
```

两者实际上是同一个文件，不是复制出来的两份。

### 5. Docker Compose

`compose.yaml` 用于描述如何创建和运行容器，包括：

- 使用哪个镜像；
- 映射哪些端口；
- 挂载哪些目录；
- 如何进行健康检查。

---

## 三、为什么选择官方 APT 方式安装 Docker

Docker 官方提供两种常见方式：

```text
get.docker.com 自动脚本
Docker 官方 APT 软件仓库
```

我们选择了 APT 仓库方式，因为：

- 安装过程更透明；
- 可以看见安装的软件包；
- 后续升级更规范；
- 更适合长期维护的设备；
- 可以选择或锁定 Docker 版本。

实际安装的软件包包括：

```text
docker-ce
docker-ce-cli
containerd.io
docker-buildx-plugin
docker-compose-plugin
```

其中：

- `docker-ce`：Docker Engine；
- `docker-ce-cli`：Docker 命令行；
- `containerd.io`：容器底层运行服务；
- `docker-buildx-plugin`：新式镜像构建工具；
- `docker-compose-plugin`：Compose 工具。

安装完成后，Docker 服务状态为：

```text
active (running)
```

---

## 四、为什么最初 Docker 命令需要 sudo

Docker 后台服务使用 Unix Socket：

```text
/var/run/docker.sock
```

普通用户默认没有访问权限，因此开始时需要：

```bash
sudo docker ...
```

后来执行：

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

作用是把当前用户加入 `docker` 用户组，使以下命令不再需要 sudo：

```bash
docker ps
docker build
docker run
docker compose up
```

需要注意：

> `docker` 用户组拥有接近 root 的系统权限，只应添加可信用户。

`newgrp docker` 会临时把当前终端的主用户组改为 `docker`，所以当时创建的一些文件组显示为数字 `999`。这不影响 Git 或 Docker。注销并重新登录后，主用户组会恢复正常，同时仍保留 Docker 使用权限。

---

## 五、项目检查与旧版本问题

最初的旧版项目存在两个问题：

### 1. 导入不存在的认证模块

旧版 `main.py` 包含：

```python
from routers import auth
```

但项目中没有：

```text
routers/auth.py
```

因此启动会失败。

### 2. TemplateResponse 参数不兼容

旧代码使用旧版参数格式：

```python
templates.TemplateResponse(
    "dashboard.html",
    {"request": request}
)
```

新版本改为：

```python
templates.TemplateResponse(
    request=request,
    name="dashboard.html",
    context={...},
)
```

后来重新提供的 ZIP 已经修复这些问题，因此我们只使用：

```text
/home/heisan/vscode_project/HKC/HKC_Data_Platform
```

不再使用旧目录：

```text
/home/heisan/vscode_project/HKC_Data_Platform
```

---

## 六、requirements.txt 的作用

我们创建了：

```text
requirements.txt
```

内容：

```text
fastapi==0.141.1
starlette==1.6.0
uvicorn[standard]==0.53.0
jinja2==3.1.6
```

作用是固定项目的 Python 直接依赖版本。

Docker 构建镜像时，会读取这个文件并执行安装。

它应该提交到 Git，不应该加入 `.gitignore`。

---

## 七、Dockerfile 逐行解释

当前 Dockerfile：

```dockerfile
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.txt

RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `FROM`

```dockerfile
FROM python:3.11-slim-bookworm
```

使用官方 Python 3.11 轻量镜像。

`bookworm` 是 Debian 12 的代号。

最终检查得到：

```text
Python 3.11.16
```

### `ENV`

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
```

含义：

- 不生成 `.pyc` 缓存文件；
- Python 日志立即输出到终端；
- pip 不反复显示版本升级提示。

### `WORKDIR`

```dockerfile
WORKDIR /app
```

设置容器内默认工作目录。

后续命令会在 `/app` 中执行。

### `COPY requirements.txt`

```dockerfile
COPY requirements.txt /tmp/requirements.txt
```

只把依赖清单复制到镜像中。

它没有复制项目代码。

### `RUN pip install`

```dockerfile
RUN python -m pip install \
    --no-cache-dir \
    --requirement /tmp/requirements.txt
```

这就是实际的 `pip install`。

构建日志中的：

```text
[4/5] RUN python -m pip install ... 30.3s
```

表示依赖已经安装到镜像。

`--no-cache-dir` 表示不保留 pip 下载缓存，减少镜像体积。

### 创建普通用户

```dockerfile
RUN useradd --create-home --uid 1000 appuser
USER appuser
```

容器中的应用不以 root 用户运行，降低安全风险。

### `EXPOSE`

```dockerfile
EXPOSE 8000
```

表示应用预计监听容器的 8000 端口。

需要注意：

> `EXPOSE` 只是声明，并不会自动把端口开放到宿主机。

真正的端口映射由 Compose 的 `ports` 完成。

### `CMD`

```dockerfile
CMD [
  "python",
  "-m",
  "uvicorn",
  "main:app",
  "--host",
  "0.0.0.0",
  "--port",
  "8000"
]
```

这是容器默认启动命令。

`main:app` 的含义：

```text
main  → main.py
app   → main.py 中的 app = FastAPI(...)
```

因此它不是直接运行：

```bash
python main.py
```

而是使用 Uvicorn 导入 `main.py` 中的 FastAPI 应用。

---

## 八、.dockerignore 的作用

当前内容：

```text
*
!Dockerfile
!requirements.txt
```

含义：

```text
*                    默认排除全部文件
!Dockerfile          允许 Dockerfile
!requirements.txt    允许 requirements.txt
```

因此构建镜像时，Docker 只能获得：

```text
Dockerfile
requirements.txt
```

无法获得：

```text
main.py
routers/
templates/
static/
.git/
```

构建日志显示上下文只有：

```text
117B
```

这证明项目代码没有进入镜像。

### `.dockerignore` 与 `.gitignore` 的区别

```text
.dockerignore
决定哪些文件发送给 Docker 构建。

.gitignore
决定哪些文件不提交到 Git。
```

Dockerfile、Compose、requirements 和 `.dockerignore` 都属于项目部署配置，通常应该提交到 Git，不应忽略。

---

## 九、compose.yaml 逐项解释

当前核心配置：

```yaml
services:
  hkc-platform:
    build:
      context: .
      dockerfile: Dockerfile

    image: hkc-data-platform-runtime:1.0

    ports:
      - "127.0.0.1:8000:8000"

    volumes:
      - .:/app:ro

    init: true

    healthcheck:
      test:
        - CMD
        - python
        - -c
        - import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/dashboard/', timeout=3)
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 5s
```

### `services`

定义 Compose 中运行的服务。

服务名：

```text
hkc-platform
```

### `build`

```yaml
build:
  context: .
  dockerfile: Dockerfile
```

告诉 Compose：

- 使用当前目录作为构建上下文；
- 使用当前目录中的 Dockerfile。

### `image`

```yaml
image: hkc-data-platform-runtime:1.0
```

指定构建后的镜像名称和版本标签。

### `ports`

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

格式：

```text
宿主机地址:宿主机端口:容器端口
```

当前表示：

```text
宿主机 127.0.0.1:8000
        ↓
容器 8000
```

因此只能从本机访问：

```text
http://127.0.0.1:8000
```

局域网其他设备暂时不能访问。

如果以后需要局域网访问，可以改为：

```yaml
ports:
  - "8000:8000"
```

但当前应用没有登录和权限保护，不建议现在对局域网开放。

### `volumes`

```yaml
volumes:
  - .:/app:ro
```

把宿主机当前项目目录只读挂载到容器 `/app`。

### `init: true`

为容器添加一个轻量 init 进程，负责：

- 正确传递停止信号；
- 回收子进程；
- 让容器停止更干净。

### `healthcheck`

每隔10秒访问：

```text
http://127.0.0.1:8000/dashboard/
```

如果页面能正常返回，容器状态显示：

```text
healthy
```

---

## 十、构建镜像

执行：

```bash
docker compose build
```

流程：

```text
读取 Dockerfile
        ↓
下载 python:3.11-slim-bookworm
        ↓
复制 requirements.txt
        ↓
执行 pip install
        ↓
创建 appuser
        ↓
生成镜像
```

最终得到：

```text
hkc-data-platform-runtime:1.0
```

检查镜像：

```bash
docker image ls hkc-data-platform-runtime
```

验证 Python：

```bash
docker run --rm \
  hkc-data-platform-runtime:1.0 \
  python --version
```

验证依赖导入：

```bash
docker run --rm \
  hkc-data-platform-runtime:1.0 \
  python -c "import fastapi, starlette, uvicorn, jinja2; print('依赖导入成功')"
```

检查依赖冲突：

```bash
docker run --rm \
  hkc-data-platform-runtime:1.0 \
  python -m pip check
```

验证结果：

```text
Python 3.11.16
依赖导入成功
No broken requirements found.
```

---

## 十一、创建并启动容器

执行：

```bash
docker compose up -d --wait
```

其中：

- `up`：创建并启动；
- `-d`：后台运行；
- `--wait`：等待健康检查完成。

该命令完成：

```text
创建 Compose 网络
        ↓
使用镜像创建容器
        ↓
挂载当前项目到 /app
        ↓
映射 8000 端口
        ↓
执行 Uvicorn 启动命令
        ↓
等待状态变成 healthy
```

最终容器名：

```text
hkc_data_platform-hkc-platform-1
```

查看状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs --tail=50
```

实时跟踪日志：

```bash
docker compose logs -f hkc-platform
```

按 `Ctrl+C` 只会退出日志查看，不会停止容器。

查看运行进程：

```bash
docker compose top
```

查看挂载后的目录：

```bash
docker compose exec hkc-platform ls -la /app
```

---

## 十二、容器中的项目是如何启动的

完整过程：

```text
宿主机项目目录
        ↓ 只读挂载
容器 /app
        ↓
Dockerfile 中 WORKDIR /app
        ↓
执行 CMD
        ↓
python -m uvicorn main:app
        ↓
导入 /app/main.py
        ↓
找到 app = FastAPI(...)
        ↓
监听容器 0.0.0.0:8000
        ↓
Compose 映射到宿主机 127.0.0.1:8000
```

成功日志：

```text
Application startup complete.
Uvicorn running on http://0.0.0.0:8000
```

---

## 十三、页面验证结果

访问地址：

```text
http://127.0.0.1:8000
```

接口文档：

```text
http://127.0.0.1:8000/docs
```

已经验证：

- `/` 能跳转到 `/dashboard/`；
- 工作台正常渲染；
- 机器人管理页面正常；
- 所有已注册业务页面均可加载；
- Swagger UI 正常；
- 容器健康检查正常。

---

## 十四、网络代理为什么要单独配置 Docker

终端中设置：

```bash
export HTTP_PROXY=...
export HTTPS_PROXY=...
```

只影响当前终端启动的程序。

Docker 拉取镜像是由后台的 `dockerd` 服务执行，因此终端代理不会自动传给它。

临时代理文件：

```text
/run/systemd/system/docker.service.d/proxy.conf
```

配置内容：

```ini
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7897"
Environment="HTTPS_PROXY=http://127.0.0.1:7897"
Environment="NO_PROXY=localhost,127.0.0.1"
```

重新加载：

```bash
sudo systemctl daemon-reload
sudo systemctl restart docker
```

验证：

```bash
docker info | grep -i -A 2 proxy
```

因为配置放在 `/run` 下：

> 电脑重启后临时代理会消失，需要重新配置。

---

## 十五、修改代码后应该做什么

### 只修改 HTML 模板

因为项目目录已经挂载，刷新浏览器通常即可看到变化。

### 修改 Python 文件

当前 Uvicorn 没有启用 `--reload`，因此需要：

```bash
docker compose restart hkc-platform
```

不需要重新构建镜像，因为代码不在镜像里。

### 修改 requirements.txt

依赖发生变化，需要重新构建：

```bash
docker compose build
docker compose up -d --force-recreate
```

### 修改 Dockerfile

同样需要重新构建：

```bash
docker compose build
docker compose up -d --force-recreate
```

### 修改 compose.yaml

通常执行：

```bash
docker compose up -d
```

Compose 会根据新配置更新容器。

---

## 十六、停止、启动和删除容器

停止但保留容器：

```bash
docker compose stop
```

重新启动：

```bash
docker compose start
```

停止并删除容器和 Compose 网络：

```bash
docker compose down
```

`docker compose down` 不会删除：

- 项目源代码；
- Dockerfile；
- Compose 文件；
- 镜像 `hkc-data-platform-runtime:1.0`。

重新创建：

```bash
docker compose up -d --wait
```

Ubuntu 正常关机时会停止 Docker 服务，因此不强制要求先手动停止容器。

当前没有设置自动重启策略，电脑重启后需要：

```bash
cd ~/vscode_project/HKC/HKC_Data_Platform
docker compose up -d
```

---

## 十七、当前尚未完成的部分

以下工作还没有执行：

```text
Docker Hub 登录
远程仓库创建
镜像重新打标签
docker push
在新设备 docker pull
复制项目到新设备
新设备挂载运行验证
```

未来推送大致流程：

```bash
docker login
```

```bash
docker tag \
  hkc-data-platform-runtime:1.0 \
  DockerHub用户名/hkc-data-platform-runtime:1.0
```

```bash
docker push \
  DockerHub用户名/hkc-data-platform-runtime:1.0
```

由于当前镜像不包含项目代码，新设备还需要获得：

```text
main.py
routers/
templates/
static/
Dockerfile
requirements.txt
compose.yaml
.dockerignore
```

然后再挂载运行。

---

## 十八、最重要的理解

当前方案可以概括为：

```text
Docker 镜像
负责保存：
Python + FastAPI + 依赖

宿主机项目目录
负责保存：
main.py + routers + templates + static

Docker Compose
负责：
创建容器 + 挂载代码 + 映射端口 + 健康检查
```

因此：

```text
镜像不是容器
容器不是项目源代码
挂载不是复制
Dockerfile 不是运行中的 Docker
docker build 负责创建镜像
docker compose up 负责创建并运行容器
```
