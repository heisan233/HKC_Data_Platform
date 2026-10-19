# HKC 数采平台 Codex 协作规则

本文件供参与本仓库开发的 Codex/ChatGPT 阅读。开始修改前，必须完整阅读本文件、
`docs/COLLABORATION.md`、`readme.md` 和所负责模块内的 README。

## 当前共同基线

- `main` 是双方共同的结构基线和最终集成分支。
- `hs_docker` 由 Heisan 使用，当前负责局域网访问、部署配置、原始数据集上传和
  Episode/task/Subtask 数据标注。
- `lxp_docker` 由 LXP 使用，当前负责数据管理页面与数据导出。
- 两个开发分支开始任务前都必须基于最新 `main`；不要把旧分支目录整体复制回
  新结构。
- 未经使用者明确同意，Codex 不得删除分支、强制推送、重置历史或直接修改
  `main`。

## 已确定的架构约束

1. 当前只用普通文件夹保存数据集和运行结果，不启用 SQLite、PostgreSQL、Redis
   或其他数据库。`app/db/` 与 `migrations/` 仅为未来预留。
2. 宿主机 `storage/` 挂载到容器 `/data`。业务代码通过
   `app.core.config.settings.data_root` 获取根目录，禁止写死个人绝对路径。
3. 原始数据集放在 `storage/raw/`，不得原地修改；派生结果、质检通过的数据集、
   报告、导出包分别放入 `derived/`、`detected/`、`reports/`、`exports/`。
4. 当前不允许永久删除数据集。可以设计软删除或移入隔离区，但不得实现不可恢复
   的清除操作。
5. 首期仍是一个应用镜像、一个应用容器，网页/API 与基础自检暂时同容器运行。
6. `router.py` 只处理 HTTP 输入输出；扫描、导出、检查等实际功能放入同一业务
   模块的 `service.py` 或明确的子模块。
7. 真实数据集、模型、报告、密钥、Token、`.env` 和运行状态不得提交 Git。

## 文件放置规则

- FastAPI 入口和路由注册：`app/main.py`
- 数据集管理业务：`app/modules/datasets/`
- 上传业务：`app/modules/ingestion/`
- 数据导出业务：`app/modules/export/`
- Episode/task/Subtask 标注业务：`app/modules/annotation/`
- 质检业务：`app/modules/quality/`
- HTML：`app/templates/`
- CSS、JavaScript、图片：`app/static/`
- 部署和网络覆盖配置：`deploy/`
- 测试：`tests/`，尽量与 `app/modules/` 的结构对应

禁止重新在项目根目录创建 `data_catalog.py`、`data_management.py`、
`dataset_store.py` 等业务文件。旧 `lxp_docker` 中的实现只能作为行为和界面参考，
必须迁移进上述新结构。

## 分工边界

### Heisan / `hs_docker`

- 让本机平台能够被可信局域网内其他机器访问。
- 实现完整 LeRobot v3 ZIP 的网页/API 上传、安全检查和原始区入库。
- 默认的本机安全访问方式应继续保留；局域网开放优先通过 `deploy/` 下的独立
  Compose 覆盖配置实现。
- 当前没有登录鉴权，因此不得配置或宣称为公网可安全访问。
- 负责相应 Docker、Compose、端口、防火墙和部署说明。
- 上传临时内容放入 `storage/staging/`；通过校验后才能进入 `storage/raw/`，
  不得修改已经入库的原始数据。
- 维护 `/annotation/` 的 LeRobot v3 Episode/task/Subtask 标注功能。正式发布必须创建
  `storage/derived/` 下的完整派生副本，并同步任务表、Episode 元数据和 frame
  数据中的 task 索引；Episode 执行结果和 Subtask 时间段保存在
  `meta/annotations/` 平台旁路文件中，不得原地修改 `raw/` 或既有派生版本。

### LXP / `lxp_docker`

- 以最新 `main` 的目录结构为基础开发。
- 将数据管理界面调整为旧 `lxp_docker` 的样貌，并迁移仍符合当前需求的功能。
- 增加数据导出；导出结果写入 `storage/exports/`，不得修改原始数据。
- 数据管理页可以链接到 `/ingestion/`，但上传校验和入库代码由 `hs_docker`
  维护；如需调整上传接口，先在协作文档中同步。
- 不得把旧版 SQLite、永久清除、根目录业务文件或旧 Worker 耦合原样迁回。
- 数据来源必须适配当前文件夹存储方案。

## 容易冲突的共享文件

修改下列文件前，应先在 `docs/COLLABORATION.md` 的交接记录中说明并通知另一方：

- `app/main.py`
- `app/templates/base.html`
- `app/core/config.py` 与 `app/core/paths.py`
- `compose.yaml`、`compose.dev.yaml`、`Dockerfile`
- `pyproject.toml`
- `.gitignore`、`.dockerignore`

不要为了整理代码顺手修改对方负责范围内的文件。

## 最低验证要求

- 修改前确认当前分支和工作区状态。
- Python 改动至少完成语法/导入检查和相关页面或 API 验证。
- Docker 配置改动必须运行 `docker compose config`。
- 不得用真实业务数据作为提交到 Git 的测试样本。
- 完成任务后更新 `docs/COLLABORATION.md` 的交接记录，列出改动、验证和未完成项。
