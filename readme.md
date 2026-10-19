# HKC 数采平台：环境配置与修改记录

> 双人协作开发前，请先阅读根目录 `AGENTS.md` 和
> `docs/COLLABORATION.md`。其中记录了分支分工、目录边界、当前任务、验收标准
> 和 Codex 交接方式。

## Platform v2 开发基线

本目录是通过 Git worktree 从 `origin/main` 创建的独立开发工作区，对应分支为
`feat/platform-v2`。Git worktree 不是项目内部的代码目录结构，而是同一个 Git
仓库的另一个检出目录：旧的 `HKC_Data_Platform` 可以继续保留在
`hs_docker`，本目录则用于 Platform v2 的重构开发。两个目录共享 Git 历史，
但工作区文件和当前分支相互独立。

Platform v2 已完成第一轮目录迁移。现有演示页面被保留，但入口、路由、模板和
静态资源已经移入 `app/`。后续功能应继续遵守下面的实际目录结构，避免重新在
项目根目录堆放业务文件：

当前阶段采用**纯文件夹存储**：数据集和运行结果保存在 `storage/`，尚未启用
SQLite、PostgreSQL 或其他数据库。`app/db/` 和 `migrations/` 只是为以后接入
数据库预留，当前程序不依赖它们。

```text
HKC_Data_Platform_v2/
├── app/
│   ├── main.py                 # FastAPI 应用入口，只负责组装应用和注册路由
│   ├── web.py                  # 多个页面共享的 Jinja2 模板对象
│   ├── core/                   # 路径、环境配置等全项目共用的基础代码
│   ├── db/                     # 未来数据库访问代码预留；当前不参与运行
│   ├── modules/                # 按业务能力组织代码，不与 HTML 严格一一对应
│   │   ├── dashboard/          # 工作台
│   │   ├── datasets/           # 数据集目录、详情、版本和导出
│   │   ├── ingestion/          # 网页上传和机器人 HTTP 上传
│   │   ├── quality/            # LeRobot、自定义规则和 VLM 质检
│   │   ├── processing/         # 裁剪、合并、拆分、重排、去重
│   │   ├── annotation/         # Episode/task/Subtask 标注与派生发布
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
├── migrations/                 # 未来数据库表结构升级脚本预留；当前不参与运行
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
3. `app/db/` 未来保存“如何访问数据库”的 Python 代码；当前为空且不使用。
4. `migrations/` 未来保存“如何升级数据库表结构”的脚本；它不是旧数据存档。

当前除“原始数据集上传”和“数据标注”外，网页中看到的数据集、质检报告等内容
主要仍是各 `router.py` 中的静态演示常量。完成目录重构不等于全部业务功能已经
完成；接下来仍需逐项实现数据管理、质检、处理、转换等能力。

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
  -> quality/checkers/ 执行具体检查
  -> storage/reports/ 保存报告
```

所以“网页按钮代码和数据检查代码分开”是正确做法；它们可以归属同一个
`quality` 业务模块，但不应该堆在同一个路由函数中。更详细的说明见
`app/modules/README.md`。

### 当前文件存储方案

- 大型 VLA 数据集直接保存在 `storage/raw/`，不放入数据库。
- 正在上传、解压或校验的内容放在 `storage/staging/`。
- 处理生成的新版本放在 `storage/derived/`，不原地修改原始数据。
- 真实质检完成并通过验收后的完整数据集放在 `storage/detected/`；质检报告仍放在
  `storage/reports/`，两者不能混放。
- 质检报告和导出包分别放在 `storage/reports/` 与 `storage/exports/`。
- 当前通过目录结构、LeRobot 自身元数据以及必要的 JSON 文件管理状态。
- 如果以后文件式管理无法满足并发查询和状态一致性，再启用数据库；届时数据集
  仍保存在文件系统，数据库只保存名称、路径、状态等管理信息。

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
任务记录或质检报告丢失。当前没有数据库服务或数据库容器。

### 当前启动方式

直接使用本机 Python 时，从项目根目录运行：

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

使用首期单应用容器运行：

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build
```

默认端口只绑定 `127.0.0.1`。需要让可信局域网中的其他机器临时访问时，不要直接
修改 `compose.yaml`，请按 `deploy/README.md` 使用
`deploy/compose.lan.yaml` 覆盖配置。当前没有登录鉴权，禁止开放到公网。

容器内代码路径为 `/opt/hkc-platform`，数据路径固定为 `/data`；开发机上的
`./storage` 会挂载到 `/data`。修改 Python 或模板后，开发覆盖配置中的
Uvicorn `--reload` 会重新加载应用。正式部署时只使用 `compose.yaml`，代码来自
镜像而不是宿主机源码挂载。

### 原始数据集上传（第一版）

启动平台后访问：

```text
http://127.0.0.1:8000/ingestion/
```

局域网模式则把 `127.0.0.1` 换成开发机的可信内网 IPv4 地址。页面接受一个完整
的 LeRobot v3.0 `.zip`，需要填写平台显示名称；备注可选。服务端不会把完整 ZIP
载入内存，而是分块写入 `storage/staging/`。只有同时通过以下检查才会移动到
`storage/raw/<dataset_id>/`：

- 有效且未加密的 ZIP，不包含绝对路径、`../` 路径穿越或符号链接；
- 一个压缩包中只有一个数据集（允许外层有一个同名总目录）；
- `meta/info.json` 中 `codebase_version` 为 `v3.0`；
- 存在 `meta/stats.json`、`meta/tasks.parquet`、`meta/episodes/**/*.parquet`；
- 存在 `data/**/*.parquet`；`videos/` 为可选目录；
- 文件数量、压缩大小、解压大小、压缩率和服务器剩余空间均在安全范围内。

默认单个 ZIP 上限为 10 GiB，解压总量上限为 50 GiB，文件数上限为 200,000。
部署时可通过 `HKC_MAX_UPLOAD_BYTES`、`HKC_MAX_EXPANDED_BYTES` 和
`HKC_MAX_ARCHIVE_FILES` 环境变量覆盖（大小单位均为字节）。上传记录保存在
`storage/system/upload-records/`，失败的临时文件会清理，不会进入原始区。

这一步只检查安全性和 LeRobot v3.0 的最低结构，并不解析 Parquet 的实际字段，
也不等于数据质检。内容级检查仍由后续数据质检模块完成。

### Episode/task/Subtask 数据标注

先通过“原始数据集上传”入库 LeRobot v3.0 数据，然后访问：

```text
http://127.0.0.1:8000/annotation/
```

工作流程如下：

1. 页面从 `storage/raw/` 和 `storage/derived/` 读取真实数据集，而不是演示常量。
2. 选择数据集后，平台读取 `meta/tasks.parquet`、Episode 元数据和视频索引。
   其中 task 按 LeRobot 官方结构作为 Pandas DataFrame 索引读取，Episode 帧数
   使用官方 `length` 字段；同时兼容本平台早期的普通 `task` 列和 `num_frames`。
3. 左侧选择一个或多个 Episode，中间播放该 Episode 的真实多相机视频。
4. 右侧可修改总体 task，选择 Episode 执行结果（成功、部分成功、失败），并记录
   失败或未完全成功的原因。
5. 通过视频播放头获取相对 Episode 的开始/结束时间，添加 Subtask 描述、动作
   类型、对象和该段执行结果；时间段允许留空档但不能重叠。
6. 点击“暂存当前 Episode”后仍只是浏览器草稿，刷新页面会丢弃，源文件不会改变。
7. 填写派生版本名称并正式发布。平台将源数据集完整复制到临时区，在副本中同步
   更新任务表、Episode task、frame `task_index` 和 `info.json.total_tasks`；扩展
   标注写入 `meta/annotations/`。
8. 校验通过后，新数据集进入 `storage/derived/<dataset_id>/`，版本记录进入
   `storage/system/dataset-versions/`；原始数据集和既有派生版本始终保持不变。

因此一次正式发布需要近似一个完整源数据集的额外磁盘空间。平台使用
`pandas==2.3.3` 与 `pyarrow==26.0.0` 按 LeRobot 官方索引语义处理 Parquet，
没有把 PyTorch、CUDA 或 VLA 训练依赖加入 Web 容器。Subtask 的秒数相对 Episode
开始位置，并自动保存对应的
`start_frame` 与排他结束帧 `end_frame_exclusive`。Episode 结果和 Subtask 使用
平台旁路 Parquet 保存，不加入 LeRobot v3 核心 feature，因此不会破坏标准训练
字段。当前不包含多人审核或后台发布队列。

标注模块提供以下真实 API：

```text
GET  /annotation/api/datasets
GET  /annotation/api/datasets/{dataset_id}
GET  /annotation/api/datasets/{dataset_id}/episodes/{episode_index}/video?camera=...
POST /annotation/api/publish
```

### 当前尚未完成

- `conversion` 现在只有边界定义，业务代码尚未实现。
- `app/db/` 与 `migrations/` 是未来数据库预留，当前不参与程序运行。
- 除上传和 Episode/task 标注外，页面仍以 v1 静态演示数据为主；目录迁移不代表
  其他按钮功能已经完成。
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
