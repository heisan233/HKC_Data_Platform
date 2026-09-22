# HKC 数采平台：环境配置与修改记录

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
