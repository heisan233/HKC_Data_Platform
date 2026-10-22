# HKC 数采平台 Codex 开发规则

本文件是本仓库内所有 Codex/ChatGPT 的强制入口。开始修改前，必须依次完整阅读：

1. `AGENTS.md`（本文件）；
2. `docs/CODEX_CURRENT_STATE.md`（当前集成状态与目录说明）；
3. `docs/COLLABORATION.md`（历史交接记录）；
4. 所负责模块的 README 和测试。

如果旧聊天、旧分支或旧文档与当前工作区冲突，以当前分支源码、测试、本文件和
`docs/CODEX_CURRENT_STATE.md` 为准。不要根据旧对话猜测项目状态。

## 1. 当前共同基线

- 当前双方共同可运行基线是 `integration/hs-lxp-v2`，它已经合并 Heisan 与 LXP
  两套实现。
- 当前下一阶段开发分支是 `integration/hs-v2.1`，从上述稳定基线创建；登录、
  机器人管理、数据定义和工作台的新开发在该分支逐步验收。
- 在使用者明确完成验收并将它合入 `main` 之前，`main` 不是当前功能基线。
- `hs_docker`、`lxp_docker_v2`、`feat/platform-v2` 和更早分支只用于查看历史，
  不得把其中的目录或文件整批覆盖回当前集成分支。
- 开始工作前必须执行 `git status --short --branch` 和 `git log -1 --oneline`；
  发现他人未提交修改时必须保留，不能重置、清理或覆盖。
- 未经使用者明确授权，不得删除分支、强制推送、改写历史、直接合并到 `main`，
  也不得提交或推送代码。

## 2. 当前产品与技术事实

- 技术栈：Python 3.11、FastAPI、Uvicorn、Jinja2、原生 JavaScript/CSS。
- 当前部署形态：一个应用镜像、一个应用容器。网页/API、SQLite 登记和基础质检
  暂时在同一容器中运行；没有 PostgreSQL、Redis 或独立 Worker。
- 大型 VLA 数据集始终保存在普通文件夹中。SQLite 只保存稳定 DS 编号、真实目录
  映射、来源关系、质检任务和人工审核等管理元数据。
- 当前真实数据格式以 LeRobot v3 为准。不要假定 LeRobot v2、ROS bag、MCAP、
  HDF5 或 RLDS 已经实现。
- 当前已有 SQLite 用户、scrypt 密码哈希和服务端 Cookie 会话保护，但没有 HTTPS；
  仍只能开放到可信内网，禁止宣称可安全暴露到公网。
- 当前应用镜像不安装 CUDA、ROS 或 VLA 训练环境。不同模型的训练环境继续使用
  独立镜像，不塞入平台容器。

## 3. 已完成与未完成边界

已连接真实文件或 SQLite 的功能：

- `auth`：无默认账号的用户表、密码哈希、服务端会话、登录、退出和全站访问保护；
- `robots`：独立机器人档案、稳定 RB 编号、新增/编辑、停用/恢复及同类型数据集统计；
- `ingestion`：单个 LeRobot v3 ZIP 上传、批量包装接口、安全解压、Raw 入库和
  自动分配 DS 编号；
- `datasets`：统一 DS 登记、Raw/Derived 列表、详情、显示信息、重新扫描、视频
  访问、质检状态和报告管理；
- `annotation`：真实 Episode/task/Subtask 读取、视频播放、标注以及完整派生数据集
  发布；
- `processing`：Episode 筛除、拆分、兼容数据集合并、重排、精确重复检测、去重和
  指定相机视频画面裁剪，所有结果生成新的 Derived DS；
- `quality`：版本化质检方案、Doctor、机器人规则、VLM 调用、任务历史、人工审核、
  隐藏/恢复和 JSON 报告；
- 报告回收站：报告只能移入 `storage/trash/reports/` 并恢复，不允许永久删除。

仍是占位或未完整实现的功能：

- `export` 独立页面仍使用演示常量，不是真实导出任务；
- `conversion` 尚未实现；
- 模型训练与评估、系统管理以及部分工作台页面仍属于框架或演示；
- 独立情景管理页面和 `/scenes/` 路由已按产品决定移除；未来场景分类归入数据
  定义，不重新建立独立情景管理模块；
- `semantics`：真实 LeRobot v3 结构参考，以及带稳定 SD 编号的场景、任务、动作、
  对象受控词表；当前不自动改写已有数据集或标注。

Codex 不得因为页面能够打开就声称功能已完成。判断标准是：是否读取真实数据、
是否有后端行为、是否有测试以及是否通过人工验收。

## 4. 目录与职责

```text
app/
├── main.py                 FastAPI 入口，只注册路由和静态目录
├── web.py                  共享 Jinja2 模板对象
├── core/                   路径、配置等跨模块基础能力
├── db/                     SQLite 访问代码，不存放数据库文件
├── modules/                按业务能力组织 Python 代码
├── templates/              Jinja2 HTML 模板
└── static/                 CSS、JavaScript 和网页图片

storage/                    宿主机运行数据；真实内容不提交 Git
├── raw/                    上传后不可原地修改的原始数据集
├── derived/                标注或处理生成的完整派生数据集
├── staging/                上传、标注、处理时的临时内容
├── reports/qc/             质检 JSON 报告
├── trash/reports/          可恢复的报告回收站
├── exports/                未来的真实导出产物
└── system/                 SQLite、上传记录、版本和任务记录

deploy/                     局域网 Compose 覆盖和部署说明
docs/                       架构、协作和当前状态文档
migrations/                 未来 SQLite 表结构升级脚本，不是旧数据仓库
tests/                      自动化测试，结构尽量对应业务模块
```

业务模块固定放置：

- `app/modules/datasets/`：统一数据集登记、列表、详情与报告管理；
- `app/modules/auth/`：登录/退出路由、Cookie 会话与全站访问保护；
- `app/modules/robots/`：独立机器人档案，不连接或控制真实机器人；
- `app/modules/semantics/`：LeRobot v3 结构参考和平台受控词表；
- `app/modules/ingestion/`：ZIP 上传、校验和 Raw 入库；
- `app/modules/annotation/`：Episode/task/Subtask 标注与发布；
- `app/modules/processing/`：Episode 处理及 Derived 构建；
- `app/modules/quality/`：质检方案、规则、VLM、任务和报告；
- `app/modules/export/`：未来真实导出功能；
- `app/modules/conversion/`：未来格式转换。

禁止在项目根目录重新创建 `data_catalog.py`、`data_management.py`、
`dataset_store.py` 或其他业务 Python 文件。

## 5. 模块内部规则

- `router.py`：HTTP 参数、状态码、页面/API 响应；不得堆放复杂文件处理。
- `service.py`：业务流程、文件扫描、构建、校验和外部调用。
- `schemas.py`：Pydantic 请求/响应结构。
- `app/db/*.py`：SQLite 表初始化和查询。
- `templates/` 决定页面结构，`static/js/` 负责浏览器交互；业务数据必须通过模块
  API 获得，不能只在 JavaScript 中伪造。
- 共享 LeRobot v3 元数据读取逻辑优先放在
  `app/modules/datasets/lerobot_v3.py`，不要在标注、处理、质检中各写一套。

## 6. 数据与 DS 编号规则

- 浏览器/API 对数据集使用 `DS000001` 形式的稳定编号，不直接暴露真实目录作为
  主标识。
- SQLite 文件位于 `storage/system/platform.sqlite`，映射 DS 编号到
  `raw/<目录>` 或 `derived/<目录>`；数据集字节不写入 SQLite。
- 业务代码通过 `app.core.config.settings.data_root` 取得数据根目录，禁止写死
  `/home/...`、项目绝对路径或宿主机的 `storage/`。
- 容器内数据根目录始终是 `/data`；宿主机由 `HKC_STORAGE_PATH` 映射。
- `raw/` 不得原地修改。标注、筛除、拆分、合并、重排和去重必须在
  `staging/` 生成完整副本，校验后移入 `derived/` 并登记新 DS 编号。
- 合并或重排时必须同步 Episode 元数据、frame Parquet、全局 `index`、
  `episode_index`、task 索引、旁路标注以及必要的视频映射。
- `detected/` 已废止。质检状态由 SQLite 与 `reports/qc/` 表达，不复制完整数据集。
- 当前不允许永久删除数据集或报告。

## 7. Git、密钥与配置

- 不得提交 `.env`、Token、密码、公司 VLM 地址、真实数据集、SQLite、报告、模型、
  导出包、缓存或日志。
- 用户必须通过 `python -m app.cli.create_user` 在部署机交互创建；不得在源码、
  Compose、文档或测试中加入默认账号及真实密码。
- `.env.example` 只保存公开占位值；换设备时从它新建 `.env`。
- `compose.yaml` 默认只绑定 `127.0.0.1:8000`；
  `deploy/compose.lan.yaml` 才负责可信局域网绑定；`compose.dev.yaml` 只增加源码热
  重载。
- 个人 IP、代理和临时镜像源不能写入通用 Compose 文件。
- `pyproject.toml` 是 Python 直接依赖的唯一来源，不再维护第二份
  `requirements.txt`。
- 应用容器保持非 root。`APP_UID`、`APP_GID` 应与持久化目录所有者匹配。

## 8. 当前维护边界

合并后双方都基于同一套代码工作，但默认维护边界仍用于减少冲突：

- Heisan：上传、标注、Episode 处理、Docker/Compose、局域网与服务器部署；
- LXP：数据管理页面、数据集详情、报告展示以及后续真实导出；
- 共同评审：`app/db/`、统一 DS 接口、质检状态、`app/main.py`、`base.html`、
  `pyproject.toml`、Docker/Compose 和存储规范。

跨边界修改不是禁止，但必须先说明原因、接口影响和验证方式。不要为了格式化或
整理顺手修改对方模块。

## 9. 容易冲突的共享文件

修改下列文件前先检查另一位开发者的工作，并在交接记录中说明：

- `app/main.py`、`app/web.py`；
- `app/templates/base.html`；
- `app/core/config.py`、`app/core/paths.py`；
- `app/db/`；
- `compose.yaml`、`compose.dev.yaml`、`deploy/compose.lan.yaml`、`Dockerfile`；
- `pyproject.toml`、`.gitignore`、`.dockerignore`；
- `storage/README.md` 和统一 DS/LeRobot 读取代码。

## 10. 最低验证与交接

- Python 改动至少运行相关测试和 Ruff 检查；共享逻辑改动运行完整测试。
- Docker 配置改动必须验证默认、开发和局域网三种 Compose 组合。
- 测试必须使用隔离的临时 `HKC_DATA_ROOT`，不得修改真实 `storage/`。
- 不得用真实业务数据作为 Git 测试样本。
- 完成后更新 `docs/COLLABORATION.md` 顶部交接记录，写清改动、验证、已知问题和
  是否修改共享接口。
- 汇报时区分“代码已写”“自动化测试通过”“本机人工验证通过”“服务器验收通过”，
  不得把其中一种说成全部完成。
