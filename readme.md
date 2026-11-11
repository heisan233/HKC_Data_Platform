# HKC VLA 数据采集平台

HKC 数采平台用于管理机器人采集的 LeRobot v3 数据集，并提供上传、质检、标注、
处理、格式转换、导出和 X-VLA 训练任务入口。

当前共同开发分支为 `integration/hs-lxp-v3.2.0-cq`。平台面向可信内网使用，
尚未配置 HTTPS，不应直接暴露到公网。

## 当前能力

- 多用户登录、首次改密、个人中心和服务端会话；
- 机器人档案、稳定 RB 编号、停用/恢复和采集 Token；
- 网页 ZIP 上传与机器人命令行分布式上传；
- MySQL 统一 DS 登记、机器人关联、数据集详情和回收站；
- LeRobot v3 Episode/task/Subtask 标注；
- Episode 筛除、拆分、合并、重排、去重和视频画面裁剪；
- Doctor、机器人规则、可选 VLM、人工审核和质检报告；
- LeRobot v3 → HDF5 转换及数据集/转换结果导出；
- Schema 扫描与 Episode/数值逐帧字段扩展；
- X-VLA 训练任务、离线评估和模型包管理；
- 工作台真实统计与脱敏访问日志。

功能的准确边界和未完成项见
[`docs/CODEX_CURRENT_STATE.md`](docs/CODEX_CURRENT_STATE.md)。

## 架构

```text
浏览器或采集端
        │
        ▼
FastAPI 应用容器
        ├── MySQL 8.4：用户、编号、目录映射、任务和审核元数据
        ├── storage/：数据集、报告、导出包和模型文件
        ├── 可选 VLM 服务
        └── 可选宿主训练执行器 → 独立 X-VLA Docker
```

平台镜像不包含 CUDA、ROS 或模型训练依赖。训练环境必须与平台应用容器隔离。

## 目录

```text
app/
├── core/          配置和公共路径
├── db/            MySQL 仓储层
├── modules/       业务模块
├── templates/     Jinja2 页面
└── static/        CSS、JavaScript 和图片

migrations/mysql/  MySQL 迁移
deploy/            Compose 覆盖与训练执行器
tools/             采集端和服务器维护工具
tests/             自动化测试
storage/           运行数据，不提交 Git
```

主要运行目录：

```text
storage/
├── data/                 新发布的 DS 数据集
├── raw/                  历史原始目录映射
├── derived/              历史派生目录映射
├── staging/              临时构建区
├── reports/qc/           质检报告
├── converted/            HDF5 转换结果
├── exports/              导出包
├── training-runs/        训练任务输出
├── models/               模型包
├── trash/                数据集和报告回收站
└── system/               日志、上传记录和采集摘要
```

## 五分钟本机启动

### 1. 准备配置

```bash
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，至少为以下两项设置不同的非空强密码：

```dotenv
HKC_MYSQL_PASSWORD=CHANGE_ME_APP_PASSWORD
HKC_MYSQL_ROOT_PASSWORD=CHANGE_ME_ROOT_PASSWORD
```

真实密码不得提交 Git。

### 2. 启动开发环境

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml up -d --build --wait
```

`compose.dev.yaml` 会只读挂载 `app/` 和 `migrations/`，并启用 Uvicorn 热重载。

### 3. 创建首个用户

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml exec platform python -m app.cli.create_user --username USERNAME
```

密码在终端中交互输入，不会出现在命令历史。新用户首次登录必须修改初始密码。

### 4. 打开平台

访问 [http://127.0.0.1:8000/login](http://127.0.0.1:8000/login)。

状态与日志：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml ps

docker compose --env-file .env -f compose.yaml -f compose.dev.yaml logs -f platform
```

停止：

```bash
docker compose --env-file .env -f compose.yaml -f compose.dev.yaml down
```

`down` 不会删除绑定挂载的 `storage/` 和 MySQL 数据目录。

## 数据集流程

```text
上传 LeRobot v3 ZIP
    ↓ 安全解压、最低结构校验、分配 DS 编号
不可用 / 待质检
    ↓ Doctor、规则、可选 VLM、人工审核
可用
    ├── 标注 → 新 DS → 重新质检
    ├── 数据处理 → 新 DS → 重新质检
    ├── 数据定义 → 新 DS → 重新质检
    ├── HDF5 转换与导出
    └── X-VLA 训练
```

任何派生操作都不会原地修改源 DS。数据集显示名称必须唯一；质检方案的活跃名称也
必须唯一。名称比较会忽略首尾空格和英文大小写。

## 分布式采集

- `tools/collector_v1/`：人工上传单个 ZIP，可用 `--name` 指定唯一显示名称；
- `tools/auto_update/`：采集结束后一次扫描多个文件夹或 ZIP，顺序上传后退出；
- 自动上传的显示名称取文件夹名或 ZIP 去扩展名；需要自定义时先重命名来源；
- 上传成功后自动分配 DS、关联 Token 所属机器人，并保持“不可用、待质检”。

详见 [`tools/README.md`](tools/README.md)。

## Compose 文件

| 文件 | 用途 |
| --- | --- |
| `compose.yaml` | 基础服务，默认 `127.0.0.1:8000` |
| `compose.dev.yaml` | 开发源码只读挂载和热重载 |
| `deploy/compose.lan.yaml` | 指定可信局域网 IPv4，端口 8000 |
| `deploy/compose.local-mysql.yaml` | 本机并行验收，`127.0.0.1:18001` |
| `compose.server.yaml` | 服务器发布，默认 `0.0.0.0:18000` |
| `deploy/compose.training-agent.yaml` | 可选宿主训练执行器 socket |

具体命令见 [`how_use.md`](how_use.md) 和
[`deploy/README.md`](deploy/README.md)。

## 开发与测试

```bash
python -m pip install -e '.[dev]'
python -m pytest -q -p no:cacheprovider
python -m ruff check app tests tools
git diff --check
```

测试必须连接隔离 MySQL 并使用临时 storage，不能指向正式数据。协作和代码修改规则
见 [`AGENTS.md`](AGENTS.md)。

## 安全边界

- 不提交 `.env`、密码、Token、真实 IP、公司 VLM 地址或数据集；
- 默认只监听本机；局域网模式也只用于可信网络；
- 上传包和所有用户路径都按不可信输入处理；
- 平台没有 HTTPS、公开注册、角色权限或公网防护；
- 备份必须同时覆盖 MySQL 和 `storage/`，只备份其中之一不能完整恢复平台。
