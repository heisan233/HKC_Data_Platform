# 合并后项目现状（供同事 Codex 接手）

更新日期：2026-10-22

这份文档只描述当前集成代码，不描述旧分支设想。每次新会话先读取根目录
`AGENTS.md`，再读本文件。若本文件与源码或测试不一致，先停止修改并把差异告诉
开发者，确认后同步文档。

## 1. 从哪个版本开始工作

当前下一阶段开发分支：

```text
分支：integration/hs-v2.1
来源：integration/hs-lxp-v2（已验证的上一阶段稳定基线）
目录：HKC_Data_Platform_merge
```

开始任务时先确认：

```bash
git status --short --branch
git log -1 --oneline --decorate
```

`hs_docker`、`lxp_docker_v2` 和 `feat/platform-v2` 是合并前历史。可以用于理解旧
需求，但不能将旧文件整批复制回当前目录。直到开发者明确通知集成分支已经合入
`main`，不要从旧 `main` 开始新功能。

如果工作区已有未提交文件，它们属于当前开发者，必须保留。不要执行
`git reset --hard`、`git clean` 或 `git checkout -- <文件>`。

## 2. 当前请求链路

```text
浏览器 HTML/JavaScript
        ↓ Cookie 会话访问保护
        ↓ HTTP
app/modules/<业务>/router.py
        ↓
app/modules/<业务>/service.py
        ↓
app/db/*.py（管理元数据） + storage/（真实文件）
```

`app/main.py` 只创建 FastAPI 应用、挂载 `/static` 并注册模块路由。Uvicorn 启动
入口是 `app.main:app`。

主要网址与代码位置：

| 网址 | 模块 | 当前状态 |
| --- | --- | --- |
| `/login`、`/logout` | `auth` | SQLite 用户、密码哈希、登录会话和退出 |
| `/healthz` | 应用入口 | 无需登录的 Docker 存活检查，不返回业务数据 |
| `/dashboard/` | `dashboard` | 工作台页面，部分展示内容仍偏框架 |
| `/data/` | `datasets` | 真实 DS 数据集总览和质检状态 |
| `/data/manage/` | `datasets` | 真实登记、信息修改与重新扫描 |
| `/data/raw/` | `datasets` | Raw/Derived 数据集列表与详情入口 |
| `/data/reports/` | `datasets` | 真实质检报告和回收站 |
| `/ingestion/` | `ingestion` | 真实 LeRobot v3 ZIP 单个/批量上传 |
| `/annotation/` | `annotation` | 真实 Episode/task/Subtask 标注 |
| `/preprocess/` | `processing` | 真实数据处理、视频画面裁剪和派生版本生成 |
| `/qc/` | `quality` | 真实质检方案、任务、报告和人工审核 |
| `/export/` | `export` | 目前只有演示常量，不能视为真实导出 |
| `/training/` | `training` | 框架页面，未接入训练任务 |
| `/robots/` | `robots` | 独立 SQLite 机器人档案、新增/编辑、停用/恢复和筛选 |
| `/semantics/` | `semantics` | 真实 LeRobot v3 结构参考和 SQLite 受控词表 |
| `/system/` | `system` | 框架页面 |

独立情景管理页面已经按产品决定删除；`/scenes/` 当前应返回 HTTP 404。未来如果
需要维护场景分类，统一纳入数据定义，不恢复独立情景管理模块。

## 3. 数据集生命周期

### 上传

```text
LeRobot v3 ZIP
  → storage/staging/uploads/
  → ZIP 安全检查和最低结构检查
  → storage/raw/<内部目录名>/
  → SQLite 登记
  → 返回 DS000001 形式的编号
```

上传支持单文件接口和最多 20 个文件的批量包装接口。上传检查不是完整质检。

### 统一登记

```text
DS000001
  → storage/system/platform.sqlite
  → storage_area + storage_relpath
  → storage/raw/<目录> 或 storage/derived/<目录>
```

真实目录名不需要改成 DS 编号。页面、标注、处理和质检使用 DS 编号，通过 SQLite
解析真实目录。

### 标注与处理

标注和处理永远不修改源数据集：

```text
Raw/Derived 源 DS
  → storage/staging/annotation/ 或 processing/
  → 构建完整 LeRobot v3 副本
  → 校验
  → storage/derived/<新目录>/
  → 登记新的 DS 编号和来源关系
```

标注可修改 Episode 总体 task、执行结果、失败原因和 Subtask 时间段/描述/动作/
对象/结果。额外标注保存在派生数据集 `meta/annotations/`，不会破坏 LeRobot v3
核心字段。

处理已支持筛除、拆分、合并、重排、精确去重和指定相机视频画面裁剪。合并/重排
必须同步所有 Episode、frame、task、index、旁路标注和视频映射；画面裁剪页面
提供真实 Episode 视频预览、八向拖拽裁剪框和精确像素坐标，对该相机的全部
Episode 重新编码 H.264，并同步更新 LeRobot v3 feature 宽高。

### 质检与报告

```text
已登记 DS
  → 选择版本化质检方案
  → Doctor / 机器人规则 / 可选 VLM
  → SQLite 保存任务和人工审核
  → storage/reports/qc/<DS>/<task>.json
```

完成自动检查后进入待人工审核，再决定通过、待复核或打回重采。页面“移除报告”
只会将 JSON 移到 `storage/trash/reports/`；不会删除质检任务、审核历史或数据集。

## 4. 存储与数据库

宿主机默认 `./storage`，容器内固定 `/data`：

```text
HKC_STORAGE_PATH（宿主机） → /data（容器）
```

程序只能使用：

```python
from app.core.config import settings

root = settings.data_root
```

禁止在业务代码写死个人路径。

SQLite 访问代码：

- `app/db/datasets.py`：DS 编号、Raw/Derived 位置、名称、机器人类型、标签和来源；
- `app/db/quality.py`：质检方案版本、任务、状态和人工审核；
- `app/db/users.py`：用户、scrypt 密码哈希和服务端登录会话；
- `app/db/robots.py`：稳定 RB 编号、机器人档案和使用状态；
- `app/db/semantics.py`：稳定 SD 编号和场景/任务/动作/对象受控词表；
- 真实数据库：`storage/system/platform.sqlite`，被 Git 忽略。

平台不提供默认账号。每台全新部署设备启动后，由管理员在容器中交互执行
`python -m app.cli.create_user` 创建用户，并可用 `python -m app.cli.list_users`
查看不含认证秘密的账号摘要；密码不得写入 Git、命令历史或文档。
除 `/login`、`/healthz` 和静态资源外，页面与 API 都需要有效会话。当前仍没有
HTTPS，因此只能在可信内网使用。

`migrations/` 未来用于数据库表结构升级，不存放旧数据库、旧数据集或模型。

### 机器人档案边界

`RB000001` 是平台内的机器人实例编号。机器人页面维护名称、类型、型号、序列号、
使用状态、网络地址、传感器和备注，没有永久删除接口。网络地址只是管理字段，当前
不会探测在线状态、控制机器人或调用机器人 API。

已有 LeRobot 数据集继续保存自身 `robot_type`。机器人页面展示的“同类型数据集”
只是字符串匹配统计，不代表数据集已经绑定该机器人。当前没有修改 datasets 表，
也不会回写 Raw/Derived 文件；未来若要追踪具体采集设备，需要单独设计
`robot_id` 关联和历史数据迁移。

### 数据定义边界

`SD000001` 是平台受控词条编号。数据定义页维护场景、任务、动作和对象四类词表，
机器编码创建后不可修改，词条没有永久删除接口，只能停用或恢复。页面同时展示
平台实际读取的 LeRobot v3 数据集、任务、Episode、帧、视频和旁路标注结构。

词表当前只保存平台管理元数据，不写入 Raw/Derived，不自动替换 Episode task、
Subtask 动作或对象，也不与机器人档案建立外键。后续接入上传或标注前，必须先
设计兼容旧自由文本数据的映射方案。

## 5. 配置与部署

- `compose.yaml`：生产式基础配置，默认只允许本机 `127.0.0.1:8000`；
- `compose.dev.yaml`：挂载 `app/` 并启用 Uvicorn `--reload`；
- `deploy/compose.lan.yaml`：把 8000 端口绑定到
  `HKC_LAN_BIND_ADDRESS`，供可信内网访问；
- `.env.example`：可提交的公开模板；
- `.env`：每台设备单独创建，包含本地目录、IP 和 VLM 配置，不提交 Git；
- `how_use.md`：换设备、构建、离线镜像、局域网和 VLM 使用说明。

真实数据、SQLite 与 `.env` 不会随 Git 克隆。换设备时必须另行迁移完整
`HKC_STORAGE_PATH`，或接受一个全新空平台。

## 6. 当前依赖边界

Python 直接依赖只在 `pyproject.toml` 声明。主要包括 FastAPI/Jinja2/Uvicorn、
Pandas/PyArrow/NumPy、PyAV、HTTPX 和 `lerobot-doctor`。FFmpeg 由 Dockerfile 安装。

平台镜像不包含：

- CUDA/cuDNN；
- ROS/ROS 2；
- SmolVLA、X-VLA 等训练依赖；
- PostgreSQL、Redis 或任务队列。

若功能需要上述环境，先提出架构方案，不要直接把它们装进现有镜像。

## 7. 同事 Codex 的默认工作范围

LXP 侧默认继续负责：

- `app/modules/datasets/` 的数据管理页面与详情；
- 数据集搜索、展示、状态和报告交互；
- 将 `app/modules/export/` 从演示页面实现为真实、可恢复、可测试的导出功能；
- 对应模板、JavaScript、CSS 和集成测试。

以下接口已被其他功能共同依赖，修改前必须先沟通：

- `DatasetRepository` 的表结构、DS 编号和目录解析；
- `datasets/lerobot_v3.py`；
- 上传成功后的自动登记；
- 标注/处理发布后的 Derived 登记；
- 质检状态、报告路径与人工审核；
- `base.html` 的全局导航和共享静态资源。

数据导出的正确方向是：使用 DS 编号解析真实目录，在 `storage/staging/` 或安全
临时目录构建，最终产物进入 `storage/exports/`；不得修改 Raw/Derived 源数据，
不得提供不可恢复的删除。

## 8. 开始和结束任务的固定动作

开始：

```bash
git status --short --branch
git log -1 --oneline --decorate
```

先读相关 router、service、模板、JavaScript 和测试，再提出实现方案。不要只凭页面
截图改 HTML。

最低验证：

```bash
python -m ruff check app tests
python -m pytest -q -p no:cacheprovider
docker compose --env-file .env.example -f compose.yaml config --quiet
docker compose --env-file .env.example -f compose.yaml -f compose.dev.yaml config --quiet
HKC_LAN_BIND_ADDRESS=192.168.123.45 \
  docker compose --env-file .env.example \
  -f compose.yaml -f deploy/compose.lan.yaml config --quiet
```

如果只改一个业务模块，可先运行其相关测试；修改 DB、共享 LeRobot 读取、配置或
Compose 时必须运行完整测试。测试使用临时目录，绝不能指向真实 `storage/`。

2026-10-22 的 `integration/hs-v2.1` 工作树基线已完成上述验证：Ruff 0.16.8
全部通过，pytest 9.1.1 为 `66 passed`（另有 1 条 Starlette/AnyIO 上游弃用
警告）；默认、开发和局域网三种 Compose 配置均通过解析。当前开发容器健康，
但正式生产镜像仍需在提交前重新构建并执行不挂载源码的验收。

结束时报告：

```text
完成内容：
主要修改文件：
执行的验证及结果：
未完成或已知问题：
是否修改共享接口：
需要另一位开发者注意什么：
```

除非开发者明确要求，不要自行 commit、push、合并或部署到正式服务器。
