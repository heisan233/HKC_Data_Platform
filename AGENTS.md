# HKC 数采平台开发规则

本文件是仓库内 Codex/ChatGPT 的强制入口。开始工作前依次阅读：

1. 本文件；
2. `docs/CODEX_CURRENT_STATE.md`；
3. 与任务相关的模块 README 和测试；
4. 需要协作时再读 `docs/COLLABORATION.md`。

旧聊天、旧分支和旧部署记录只作参考。发生冲突时，以当前分支源码、自动化测试、
本文件和当前状态文档为准。

## 1. 当前基线

- 当前共同开发分支：`integration/hs-lxp-v3.2.0-cq`。
- `main` 是一期基线，不直接承接当前二阶段开发。
- `integration/hs-lxp-v3.1*`、`v3.0*`、`hs_docker`、`lxp_docker*` 等均为历史参考，
  不得整批覆盖回当前分支。
- 每次工作前必须执行：

```bash
git status --short --branch
git log -1 --oneline --decorate
```

- 工作区已有改动属于当前开发者。不得重置、清理、覆盖或顺手格式化无关文件。
- 未经明确授权，不得提交、推送、合并、改写历史、强制推送或删除分支。

## 2. 架构事实

- Python 3.11、FastAPI、Uvicorn、Jinja2、原生 JavaScript/CSS。
- 平台由一个应用容器和一个 MySQL 8.4 容器组成。
- 启动入口是 `app.main:app`。
- MySQL 保存用户、DS/RB 编号、目录映射、质检、转换和训练等管理元数据。
- 大型数据集、报告、导出包和模型仍保存在 `storage/` 文件目录中。
- 平台应用镜像不安装 CUDA、ROS 或 VLA 训练环境。X-VLA 训练通过独立宿主执行器
  调用模型专属容器。
- 当前没有 HTTPS，只能面向可信内网，不能宣称可安全暴露到公网。

## 3. 目录职责

```text
app/
├── main.py          FastAPI 入口与路由注册
├── web.py           共享 Jinja2 模板对象
├── core/            配置、路径等公共能力
├── db/              MySQL 访问代码
├── modules/         按业务能力组织的后端模块
├── templates/       页面结构
└── static/          CSS、JavaScript 和图片

storage/             运行数据，不提交 Git
migrations/mysql/    MySQL 迁移脚本
deploy/              部署覆盖、训练执行器和部署说明
tools/               采集端与服务器维护工具
tests/               自动化测试
```

模块内部遵循：

- `router.py`：HTTP 参数、状态码、页面和 API 响应；
- `service.py`：业务流程、文件处理、校验和外部调用；
- `schemas.py`：Pydantic 请求/响应结构；
- `app/db/*.py`：MySQL 查询和事务；
- 浏览器交互放在 `app/static/js/`，不得只在前端伪造业务数据。

共享 LeRobot v3 读取能力优先复用 `app/modules/datasets/lerobot_v3.py`。禁止在项目
根目录新增零散业务 Python 文件。

## 4. 数据与编号规则

- 浏览器和 API 使用稳定的 `DS000001`、`RB000001`、`CV-...`、`TR...` 编号。
- 新数据集存放到 `storage/data/<DS 编号>/`；旧 `raw/`、`derived/` 映射继续保留。
- `storage_area` 只表示物理位置；业务资格以 `availability` 和实际校验结果为准。
- 新 DS 默认“不可用、待质检”，人工审核通过后才可进入标注、训练、转换和导出。
- 数据集显示名称必须唯一；比较时忽略首尾空格和英文大小写。回收站中的记录仍占用
  名称，永久清除后才可重新使用。
- 活跃质检方案名称必须唯一。
- 上传、标注、处理和数据定义必须先在 `staging/` 构建完整副本，校验后发布新 DS；
  不得原地修改已登记源数据集。
- 普通删除只进入回收站。永久清除只能从回收站执行，并须检查派生、转换和训练引用。
- `detected/` 已废止；质检状态由 MySQL 和 `reports/qc/` 表达。
- 所有业务代码通过 `settings.data_root` 取得数据根目录，禁止写死个人绝对路径。

## 5. 数据库与迁移

- 当前平台只使用 MySQL 8.4；旧 SQLite 文件不导入、不读写，也不是当前事实来源。
- `HKC_MYSQL_DATA_PATH` 与 `HKC_STORAGE_PATH` 必须分开配置。
- 表结构变化通过新的 `migrations/mysql/NNN_*.sql` 实现；已执行迁移不得修改内容。
- 修改共享仓库层、迁移或编号逻辑前，先检查所有调用方和现有测试。
- 测试必须使用隔离 MySQL 和临时 storage，绝不能指向正式数据。

## 6. 安全与配置

- 不得提交 `.env`、密码、Token、公司 VLM 地址、真实数据、MySQL 目录、日志、报告、
  导出包、模型或缓存。
- `.env.example` 只保存公开占位值；每台设备单独创建 `.env`。
- 网页用户通过容器终端命令创建、重置、启用和停用；密码必须交互输入，不能写在
  命令、源码或文档中。
- 机器人 Token 与网页会话不同。网页只有创建机器人或重新生成 Token 时返回一次
  完整值；其他接口只返回状态和前缀。部署终端可查看、生成、删除或重新生成。
- 上传文件名、ZIP 内路径、浏览器路径和客户端声明均不可信，必须进行路径和结构校验。
- 默认 Compose 只绑定 `127.0.0.1:8000`；局域网访问必须显式叠加对应覆盖文件。
- `compose.dev.yaml` 只读挂载 `app/`、`migrations/` 并启用热重载，不代表生产部署。

## 7. 当前维护边界

- Heisan：上传、采集工具、标注、数据处理、Docker/Compose 和部署。
- LXP：数据管理、详情、报告、格式转换、导出与训练接入。
- 共同评审：`app/db/`、统一 DS 接口、质检状态、`app/main.py`、`base.html`、依赖、
  Compose 和存储规范。

跨边界修改不是禁止，但必须说明原因、接口影响和验证方式，避免两个开发者同时修改
同一个共享文件。

## 8. 验证要求

- Python 改动：运行相关 pytest 和 Ruff；共享 DB/数据集逻辑改动运行完整测试。
- JavaScript 改动：至少运行语法检查和对应页面测试。
- Compose 改动：解析实际使用的基础、开发、局域网和服务器组合。
- 文件处理改动：覆盖非法路径、缺失文件、重复、失败清理和源数据不变。
- 完成后执行 `git diff --check`。
- 汇报必须区分：代码已写、自动化测试通过、本机人工验收、服务器验收。

当前完整测试基线和已知问题以 `docs/CODEX_CURRENT_STATE.md` 为准，不在本文件维护
易过期的测试数量和服务器快照。

## 9. 文档职责

- `readme.md`：项目介绍、目录和快速启动；
- `how_use.md`：日常启动、停止、账号、Token 和故障排查；
- `docs/CODEX_CURRENT_STATE.md`：当前功能边界和未完成项；
- `docs/COLLABORATION.md`：当前分工、共享接口和最近交接；
- 模块 README：该模块的真实职责、接口和限制。

文档只描述当前有效行为，不再追加旧版本开发流水账。
