# HKC 数采平台：环境配置与修改记录

## Platform v2 开发基线

本目录是通过 Git worktree 从 `origin/main` 创建的独立开发工作区，对应分支为
`feat/platform-v2`。Git worktree 不是项目内部的代码目录结构，而是同一个 Git
仓库的另一个检出目录：旧的 `HKC_Data_Platform` 可以继续保留在
`hs_docker`，本目录则用于 Platform v2 的重构开发。两个目录共享 Git 历史，
但工作区文件和当前分支相互独立。

Platform v2 已完成第一轮目录迁移。现有演示页面被保留，但入口、路由、模板和
静态资源已经移入 `app/`。后续功能应继续遵守下面的实际目录结构，避免重新在
项目根目录堆放业务文件：

数据集和运行结果保存在 `storage/`。数据管理使用 SQLite 登记编号及目录映射，
数据库文件位于 `storage/system/platform.sqlite`；大型数据文件不写入数据库。
`migrations/` 仍是后续数据库结构升级预留目录。

```text
HKC_Data_Platform_v2/
├── app/
│   ├── main.py                 # FastAPI 应用入口，只负责组装应用和注册路由
│   ├── web.py                  # 多个页面共享的 Jinja2 模板对象
│   ├── core/                   # 路径、环境配置等全项目共用的基础代码
│   ├── db/                     # 数据集登记 SQLite 访问代码
│   ├── modules/                # 按业务能力组织代码，不与 HTML 严格一一对应
│   │   ├── dashboard/          # 工作台
│   │   ├── datasets/           # 数据集目录、详情、版本和导出
│   │   ├── ingestion/          # 网页上传和机器人 HTTP 上传
│   │   ├── quality/            # LeRobot、自定义规则和 VLM 质检
│   │   ├── processing/         # 裁剪、合并、拆分、重排、去重
│   │   ├── annotation/         # task 提示词等标注信息修改
│   │   ├── conversion/         # 数据集格式转换
│   │   ├── training/           # 模型训练与评估接口，首期只保留框架
│   │   ├── robots/             # 机器人管理
│   │   ├── scenes/             # 情景管理
│   │   ├── system/             # 系统管理
│   │   ├── semantics/          # v1 数据定义兼容模块，后续从导航移除
│   │   ├── export/             # v1 导出兼容模块，后续并入 datasets
│   │   ├── models/             # v1 未注册页面，等待归并
│   │   └── logs/               # v1 未注册页面，等待归并
│   ├── templates/              # Jinja2 HTML 模板
│   └── static/                 # 浏览器直接加载的 CSS、JavaScript、图片
├── tests/                      # 单元、接口和集成测试
├── migrations/                 # 未来数据库表结构升级脚本预留
├── deploy/                     # 本地与服务器部署配置
├── storage/                    # 数据集、处理结果和平台运行数据，实际内容不提交 Git
├── Dockerfile                  # 应用镜像构建说明
├── compose.yaml                # 通用、单应用容器定义
├── compose.dev.yaml            # 本地热重载覆盖配置
├── pyproject.toml              # Python 项目和直接依赖声明
├── .env.example                # 可公开的配置示例，不包含真实密钥
└── readme.md                   # 开发、运行、部署和目录规范
```

### 先区分四类内容

1. `app/modules/` 保存“平台能够做什么”的 Python 代码，例如上传、质检和转换。
2. `storage/` 保存真正的数据集、处理结果、报告和平台产生的普通文件。
3. `app/db/` 保存登记库的数据库访问代码；SQLite 文件在 `storage/system/`。
4. `migrations/` 未来保存“如何升级数据库表结构”的脚本；它不是旧数据存档。

数据管理页面扫描 `storage/raw/` 的真实目录，并由用户选择目录登记为 DS 编号；
质检页面另提供单个或批量 LeRobot v3 ZIP 上传，先在 `staging/` 安全解压和校验，
再移入 `raw/` 并登记。其他页面仍可能使用演示常量。

### `modules` 与页面、按钮的关系

`templates/` 决定页面长什么样，`modules/` 负责平台能做什么。两者不是一一对应：
一个页面可以调用多个业务模块，一个业务模块也可以同时提供网页接口和机器人
HTTP API。

每个模块中的 `router.py` 只应接收 HTTP 请求和返回结果；实际业务以后写在同一
模块的 `service.py` 或更具体的子目录中。例如质检按钮的调用链应为：

```text
qc.html 中的按钮
  -> quality/router.py 接收请求
  -> quality/service.py 组织流程
  -> quality/robot_rules.py、vlm.py 执行自定义检查，Doctor 由服务层调用
  -> storage/reports/ 保存报告
```

所以“网页按钮代码和数据检查代码分开”是正确做法；它们可以归属同一个
`quality` 业务模块，但不应该堆在同一个路由函数中。更详细的说明见
`app/modules/README.md`。

### 当前文件存储方案

- 其他采集流程放入 `storage/raw/` 的 VLA 数据集，登记时不移动原始目录。
- 数据管理在 SQLite 中分配 DS 编号，保存编号到 Raw 目录的映射。
- 质检仅读取 Raw 文件；统一报告保存在 `storage/reports/qc/<DS 编号>/`。
- 数据管理首页显示待人工审核数量；Raw 列表按最新质检任务显示未质检、待审核、已审核通过、打回重采等状态。质检完成本身不等于人工通过。
- `storage/detected/` 保留为未来实际数据清洗产物目录；通过质检不会复制 Raw。
- 正在上传、解压或校验的内容放在 `storage/staging/`。
- 处理生成的新版本放在 `storage/derived/`，不原地修改原始数据。
- 质检报告和导出包分别放在 `storage/reports/` 与 `storage/exports/`。
- 数据集内容由目录结构和 LeRobot 自身元数据提供；登记与展示信息保存在 SQLite。

### 文件放置规则

- 新业务代码必须放进 `app/modules/<业务领域>/`，不要为每个按钮在项目根目录
  新建一个 Python 文件。
- 路由只处理 HTTP 输入输出；具体业务放在同领域的服务代码中，文件读写与业务
  判断分开。未来启用数据库后，数据库访问也应保持独立。
- 页面模板放进 `app/templates/`，公共布局和组件集中维护；CSS、JavaScript、
  图标和图片放进 `app/static/`。
- 测试代码统一放进 `tests/`，目录尽量与 `app/` 的业务结构对应。
- Docker、Compose 和依赖声明属于项目源代码，必须提交 Git。
- 原始数据集、处理结果、模型权重、运行状态文件、日志、缓存和 `.env` 不提交
  Git，应通过宿主机目录或 Docker volume 挂载。
- 程序内部统一使用容器路径 `/data`。开发机和服务器只通过 Compose 配置决定
  `/data` 实际对应哪个宿主机目录，业务代码不得写死个人目录。
- 密码、Token、API Key 只允许通过环境变量或密钥管理方式提供，仓库中只保存
  `.env.example`。

### 首期容器边界

Platform v2 首期暂时把网页/API 和数据集基础自检放在同一个应用镜像、同一个
应用容器中，以降低本地开发复杂度。代码仍需保持模块边界，耗时操作通过统一
任务接口调用，避免直接写进页面路由。后续如果 LeRobot Doctor、视频扫描、VLM
质检或数据处理影响网页稳定性，再将任务执行模块拆为独立 Worker 容器，而不必
重写业务逻辑。

所有 `storage/` 内容必须持久化到容器外部；删除或重建应用容器不应导致数据集、
任务记录或质检报告丢失。登记库是同一存储卷中的 SQLite 文件，不需要独立数据库容器。

### 当前启动方式

直接使用本机 Python 时，从项目根目录运行：

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

使用首期单应用容器运行：

```bash
cp .env.example .env
docker compose -f compose.yaml -f compose.dev.yaml up --build
```

容器内代码路径为 `/opt/hkc-platform`，数据路径固定为 `/data`；开发机上的
`./storage` 会挂载到 `/data`。修改 Python 或模板后，开发覆盖配置中的
Uvicorn `--reload` 会重新加载应用。正式部署时只使用 `compose.yaml`，代码来自
镜像而不是宿主机源码挂载。

### 数据质检

打开 `/qc/` 上传或选择已登记的 LeRobot v3 Raw 数据集。先从数据集创建质检方案，
在方案页配置机器人字段与阈值、Doctor 检查项、机器人专项规则及 VLM 判断要求。
每次保存方案都会生成新版本。任务页选择方案后只调整本次启用的规则；任务保存
方案版本、启用项和 Raw 文件指纹快照。同一方案可供多个同类型数据集复用：
一次勾选多个 Raw 数据集或上传多个 ZIP，会为每个数据集各创建一个任务，提交后
留在任务列表查看进度。方案可从列表删除；删除后不再用于新任务，已有任务、
版本快照和报告仍可查询。

任务依次执行 `lerobot-doctor` 0.2.0、机器人专项规则、VLM 视频规则。Doctor 使用
该版本实际提供的 11 项原生检查；机器人专项读取元数据、Parquet 与 MP4；VLM
依据 Episode 时间边界截取视频片段，通过 `VLM_BASE_URL` 指向的 OpenAI 兼容接口
请求模型，默认模型为 `qwen3-vl-4b`。VLM 连接所需的 `VLM_BASE_URL` 和
`VLM_API_TOKEN` 通过 `.env`/Compose 环境变量设置。项目的 `.env.example`
已填入 `vlm_api_quickstart.md` 指定的服务地址和占位 key；部署到其他网络时可覆盖。
运行前会快速检查 VLM 服务连通性，连接失败会在报告中说明原因。
未配置接口时，可在方案或任务中关闭 VLM 规则。视频片段只放在 `staging/` 的
任务临时目录，执行后删除。

每个任务只生成一份 JSON 报告，包含 Doctor、机器人专项和 VLM 三部分，位于
`storage/reports/qc/<DS 编号>/<任务 ID>.json`。页面支持人工通过、待复核、
打回重采、按原配置重跑和下载报告。任务列表可将已结束的记录移出当前显示，并在“已移出记录”
中恢复；任务、报告、人工判定和 Raw 数据集均保留。列表分页显示，每页 20 条。
人工复核保留历史；覆盖系统结果
为通过时须填写理由。报告首页显示三部分结论，点击后进入各自的双列详细结果；
报告仅记录检查结果，不自动修改原始数据。

### 当前尚未完成

- `ingestion` 已实现质检页面的 ZIP 上传；机器人 HTTP 上传与 `conversion` 仍待实现。
- `app/db/` 已供数据集登记使用；`migrations/` 仍为后续结构升级预留。
- 数据管理已读取真实目录并使用 SQLite 登记；其他页面仍可能使用 v1 演示数据。
- PostgreSQL、Redis 和独立 Worker 暂不加入首期 Compose。
- `semantics` 和独立 `export` 路由为迁移兼容而保留，后续按产品方案调整导航。

## 附录：v1 原型历史记录

> 以下内容记录 2026-09-22 的 v1 Windows/Conda 原型，仅供追溯。其中
> `main:app`、根目录 `routers/`、`templates/` 等路径已经失效；Platform v2
> 请以上面的目录规范和 `app.main:app` 启动方式为准。

记录日期：2026-09-22

## 1. 项目概况

本项目是基于 FastAPI + Jinja2 的机器人数据采集平台演示原型，采用后端渲染 HTML 页面。

- 项目目录：`D:\APP\PyCharm\Project\HKC_Data_Platform`
- Conda 环境：`hkc-data-platform`
- 当前入口：`main.py` 中的 `app`
- 访问方式：直接进入工作台，无需登录。
- 当前业务数据主要保存在 Python 常量中，尚未连接数据库。

```text
HKC_Data_Platform/
├── main.py               # 应用入口、静态资源挂载、路由注册
├── routers/              # 13 个业务路由模块
├── templates/            # 16 个 Jinja2 页面模板
│   ├── base.html         # 公共布局、导航和样式
│   ├── dashboard.html    # 工作台
│   ├── login.html        # 保留但未使用的登录页面
│   └── settings.html     # 保留但未使用的设置页面
├── static/
│   ├── css/              # 当前为空
│   └── js/               # 当前为空
└── readme.md
```

## 2. 环境与依赖

使用 Windows PowerShell 或 PyCharm Terminal。需预先安装并配置 Anaconda 或 Miniconda。

以下版本来自当前 `hkc-data-platform` 环境的实际检查，并非项目的最低版本要求。

| 组件 | 当前版本 | 用途 |
| --- | --- | --- |
| Python | 3.11.16 | 运行 Python 代码 |
| FastAPI | 0.141.1 | Web 应用与路由 |
| Starlette | 1.6.0 | FastAPI 底层 Web 组件与模板响应 |
| Uvicorn | 0.53.0 | ASGI 服务启动 |
| Jinja2 | 3.1.6 | HTML 模板渲染 |
| python-multipart | 0.0.32 | 表单和文件上传解析；当前无认证版本暂不需要 |
| pytest | 9.1.1 | 可选测试工具 |
| httpx | 0.28.1 | 可选 HTTP 客户端与测试依赖 |
| Ruff | 0.16.8 | 可选代码检查和格式化工具 |

`datetime`、`random` 是 Python 标准库，不需要额外安装。当前页面演示不需要 CUDA、PyTorch、YOLO 模型权重、Node.js 或数据库服务。

### 2.1 首次创建环境

```powershell
cd D:\APP\PyCharm\Project\HKC_Data_Platform
conda create -n hkc-data-platform python=3.11 -y
conda activate hkc-data-platform
python -m pip install --upgrade pip
```

如果环境已经存在，直接激活即可，不需要重复创建。

### 2.2 安装运行依赖

以下命令固定了本次验证涉及的主要运行依赖版本：

```powershell
python -m pip install "fastapi==0.141.1" "starlette==1.6.0" "uvicorn[standard]==0.53.0" "jinja2==3.1.6"
```

后续需要表单或文件上传时安装：

```powershell
python -m pip install "python-multipart==0.0.32"
```

可选开发依赖：

```powershell
python -m pip install "pytest==9.1.1" "httpx==0.28.1" "ruff==0.16.8"
```

本次验证使用的 `TestClient` 能运行，但 Starlette 对使用 `httpx` 给出了弃用提示；这不影响页面启动。上述清单未锁定全部间接依赖，不是完整环境锁文件。

### 2.3 检查环境

```powershell
python --version
python -c "import sys; print(sys.executable)"
python -c "import fastapi, starlette, uvicorn, jinja2; print('依赖导入成功')"
python -m pip check
```

本机对应解释器：

```text
D:\APP\Anaconda\envs\hkc-data-platform\python.exe
```

PyCharm 中应将项目解释器选为以上 Conda 环境。安装到其他目录的电脑，需要选择其实际解释器路径。

## 3. 日常启动与停止

```powershell
cd D:\APP\PyCharm\Project\HKC_Data_Platform
conda activate hkc-data-platform
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

出现 `Application startup complete.` 表示服务启动成功。

- 平台首页：[http://127.0.0.1:8000](http://127.0.0.1:8000)
- 工作台：[http://127.0.0.1:8000/dashboard/](http://127.0.0.1:8000/dashboard/)
- 自动生成的接口文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

访问首页会跳转到工作台。运行时保持终端打开，停止服务按 `Ctrl+C`。`--reload` 用于开发，Python 文件修改后会自动重载；模板修改后刷新浏览器查看。

应在项目根目录启动，因为代码使用相对路径 `templates` 和 `static`。绑定 `127.0.0.1` 时仅本机可访问。

如果服务已经运行，只需刷新网页。若 8000 端口被占用，可停止原服务，或改用 `--port 8001` 并访问对应端口。

## 4. 已完成的修改

### 4.1 移除认证模块依赖

原入口导入了 `routers.auth`，但文件夹里没有 `routers/auth.py`，导致应用无法正常加载。当前按开发模式去掉认证入口。

`main.py` 的调整：

- 从导入列表中移除 `auth`。
- 删除 `app.include_router(auth.router, prefix="/auth", tags=["认证"])`。
- 删除入口中不再需要的 `Request`、`Jinja2Templates` 和模板实例。
- 首页改为直接跳转到工作台：

```python
from fastapi.responses import RedirectResponse


@app.get("/")
async def root():
    return RedirectResponse("/dashboard/")
```

`templates/base.html` 的调整：

将原来的 `admin`、设置和注销入口替换为：

```html
<div class="user">
    <span>🛠️ 开发模式</span>
</div>
```

`templates/login.html` 和 `templates/settings.html` 仍保留，目前没有注册使用它们的认证路由。当前应用没有登录状态、用户身份校验或权限控制。

### 4.2 修复模板响应参数不兼容

原始报错：

```text
GET /dashboard/ → 500 Internal Server Error
TypeError: unhashable type: 'dict'
```

原因是当前 Starlette 的 `TemplateResponse` 参数顺序为 `request, name, context`，原项目使用旧版位置参数写法，导致上下文字典被当作模板名称。

修改前：

```python
return templates.TemplateResponse("dashboard.html", {
    "request": request,
    "stats": stats,
    "recent": recent,
})
```

修改后使用明确的关键字参数：

```python
return templates.TemplateResponse(
    request=request,
    name="dashboard.html",
    context={"stats": stats, "recent": recent},
)
```

Starlette 会把 `request` 放入模板上下文，因此不必再在 `context` 中重复传入。

已更新以下全部 13 个路由文件：

```text
routers/annotation.py
routers/dashboard.py
routers/data.py
routers/export.py
routers/log.py
routers/models.py
routers/preprocess.py
routers/qc.py
routers/robots.py
routers/scenes.py
routers/semantics.py
routers/system.py
routers/training.py
```

## 5. 验证结果

模板调用修改完成后，使用当前 Conda 环境和 FastAPI `TestClient` 对主应用逐页请求。验证结果如下：

| 地址 | 页面 | 结果 |
| --- | --- | --- |
| `/` | 首页 | 307，跳转 `/dashboard/` |
| `/dashboard/` | 工作台 | 200 |
| `/semantics/` | 数据定义 | 200 |
| `/robots/` | 机器人管理 | 200 |
| `/scenes/` | 情景管理 | 200 |
| `/data/` | 数据管理 | 200 |
| `/preprocess/` | 数据预处理 | 200 |
| `/qc/` | 数据质检 | 200 |
| `/annotation/` | 数据标注 | 200 |
| `/export/` | 数据集导出 | 200 |
| `/training/` | 模型训练 | 200 |
| `/system/` | 系统管理 | 200 |

该验证确认已注册页面能返回并渲染 HTML，不代表页面按钮对应的业务功能已经实现。

## 6. 当前限制与待补充内容

- 数据集、机器人、任务和质检结果主要是静态演示数据；尚未接入真实采集、处理、标注、导出或训练流程。
- 大多数操作按钮尚未绑定后端功能。
- `routers/models.py` 和 `routers/log.py` 的模板调用已更新，但路由尚未在 `main.py` 中注册，不包含在上述页面验证范围内。
- `routers/log.py` 引用的 `templates/log.html` 当前缺失；仅注册路由仍不能使该日志页面正常渲染。
- `templates/models.html` 中的图片推理通过前端随机检测框模拟，没有调用真实模型。
- `templates/api.html` 是保留的接口管理模板，其中的接口示例不代表真实接口已经实现。
- 暂无项目级依赖清单、完整依赖锁文件、数据库配置或自动化测试套件；建议后续补齐。

## 7. 常见问题

### `No module named 'fastapi'`

通常是未激活正确环境，或没有在该环境安装依赖。先执行 `conda activate hkc-data-platform`，再用 `python -c "import sys; print(sys.executable)"` 检查解释器。

### 找不到 `routers.auth`

确认 `main.py` 中已经去掉 `auth` 导入和认证路由注册。当前版本不需要该文件。

### `TypeError: unhashable type: 'dict'`

确认相应路由使用 `TemplateResponse(request=request, name="页面.html", context={...})`。本次已统一修复。

### `GET /` 返回 307

这是首页跳转到工作台的正常响应，不是报错。

### `/favicon.ico` 返回 404

表示浏览器请求的网站图标尚未配置，不影响业务页面使用。
