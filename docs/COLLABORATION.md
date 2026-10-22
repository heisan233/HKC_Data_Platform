# HKC 数采平台协作与 Codex 交接文档

更新日期：2026-10-22

这是一份双方开发者和双方 Codex 共用的交接记录。合并后的当前结构、已完成与
未完成功能、数据生命周期和接手步骤已经集中到 `docs/CODEX_CURRENT_STATE.md`；
架构规则以根目录 `AGENTS.md` 为准。本文件后面的按人分工与交接内容包含历史
背景，不得用它覆盖当前集成源码。

> 当前集成分支是 `integration/hs-lxp-v3.0`，它从已验证的
> `integration/hs-v2.1` 创建并合入 `integration/hs-lxp-v2.5`。历史分支只供
> 查阅历史。新 Codex 必须先读取 `AGENTS.md` 和
> `docs/CODEX_CURRENT_STATE.md`。

## 一、项目当前状态

- 当前集成工作位于 `integration/hs-lxp-v3.0`，以 `integration/hs-v2.1` 为基线，
  合入同事的 `integration/hs-lxp-v2.5`；
  验收完成前不直接覆盖 `main`。
- Web 技术栈为 FastAPI、Uvicorn 和 Jinja2。
- 原始数据上传、数据管理、质检、Episode/task/Subtask 标注和 Episode 数据处理
  已使用真实 LeRobot v3 文件及统一 DS 编号；其他预留模块仍可能只有框架页面。
- 数据集与运行结果采用文件夹存储，宿主机目录为 `storage/`，容器内为 `/data`。
- SQLite 保存 DS 编号映射、展示信息、质检任务和人工审核记录；大型数据文件仍
  保存在普通目录，`migrations/` 保留给未来表结构升级。
- 当前为单应用容器，没有 PostgreSQL、Redis 或独立 Worker 容器。
- 当前已有 SQLite 用户、密码哈希和服务端会话保护，但尚无 HTTPS；任何网络开放
  仍只能面向可信内网。

## 二、分支与人员分工

| 分支 | 当前用途 | 规则 |
| --- | --- | --- |
| `integration/hs-lxp-v3.0` | 当前合并与验收分支 | 集成 v2.1 与 v2.5；验收前不覆盖 `main` |
| `integration/hs-v2.1` | Heisan 来源分支 | 登录、机器人、数据定义、视频裁剪等已冻结能力 |
| `integration/hs-lxp-v2.5` | LXP 来源分支 | 格式转换、导出和数据管理扩展 |
| `integration/hs-lxp-v2` | 双方已经合并且可运行的稳定基线 | 保留为上一阶段回退点；未授权不得覆盖 `main` |
| `main` | 未来验收后的稳定目标 | 当前仍落后于集成分支，不作为新功能起点 |
| `hs_docker` | Heisan 合并前历史 | 只读参考，不把旧目录覆盖回集成分支 |
| `lxp_docker_v2` | LXP 合并前历史 | 只读参考，不继续作为独立事实来源 |
| `feat/platform-v2` | 最初目录重构历史 | 只读参考 |

本阶段双方开始新任务前先获取最新 `integration/hs-lxp-v3.0`。如需并行开发，从这一
分支创建范围明确的功能分支；完成后先提交并验证，再由双方审查集成。不要让两个
Codex 同时修改同一个共享文件，也不要让它们直接修改 `main`。

## 三、合并前历史分工：Heisan

> 本节记录功能来源和验收背景，其中大部分已经进入当前集成分支；它不再表示
> Heisan 仍需重新实现这些功能。当前事实见 `docs/CODEX_CURRENT_STATE.md`。

### 目标

让同一可信局域网中的其他机器通过开发机 IP 访问平台，同时不破坏默认仅本机
访问的配置。

### 推荐实现边界

- 保留通用 `compose.yaml` 中 `127.0.0.1:8000:8000` 的本机默认绑定。
- 在 `deploy/` 中新增明确命名的局域网 Compose 覆盖配置，通过它绑定宿主机
  `0.0.0.0:8000` 或指定内网地址。
- Uvicorn 在容器内继续监听 `0.0.0.0:8000`；真正控制外部可访问范围的是 Docker
  发布端口与宿主机防火墙。
- 文档说明启动方式、本机内网 IP、另一台机器的验证方法和停止方式。
- 局域网配置本身不改数据管理业务；上传模块只负责接收入库，不接管 LXP 负责的
  数据集列表、详情和导出。

### 验收条件

1. 默认启动时仍只能由本机通过 `127.0.0.1:8000` 访问。
2. 使用局域网覆盖配置后，另一台同网段机器可以访问指定页面。
3. `docker compose config` 能正确解析组合后的配置。
4. 文档说明当前无鉴权、禁止映射到公网，并建议用防火墙限制可信网段。
5. 停止局域网模式后，不残留额外监听服务。

### 原始数据集上传边界

- 第一版只接收一个完整的 LeRobot v3.0 `.zip` 压缩包。
- 页面与 API 归 `hs_docker` 维护，路由前缀为 `/ingestion/`。
- 上传时分块写入 `storage/staging/`，不得把整个大型文件一次性载入内存。
- 入库前检查 ZIP 路径穿越、符号链接、加密文件、异常压缩率和最低 v3.0 结构。
- 通过检查后安全解压到 `storage/raw/<内部目录名>/`；失败内容不得进入原始区。
- 文件式上传记录放在 `storage/system/upload-records/`；入库成功后由 SQLite 统一
  登记层分配 DS 编号，真实目录名不改。
- 该结构检查不等于数据质检；Parquet 内容、时间戳和图像质量仍由质检模块处理。

### Episode/task/Subtask 标注边界

- 页面与 API 归 `hs_docker` 维护，路由前缀为 `/annotation/`。
- 当前只支持 LeRobot v3.0，从 `tasks.parquet`、Episode 元数据和 frame 数据读取
  真实 task，并根据 Episode 视频索引播放真实相机视频。
- `tasks.parquet` 按 LeRobot 官方 Pandas 索引语义读取和写回：自然语言 task 是
  名为 `task` 的 DataFrame 索引，`task_index` 是普通列；Episode 帧数使用官方
  `length` 字段。读取端保留对平台早期普通 `task` 列和 `num_frames` 的兼容。
- 浏览器中的修改在正式发布前只是内存草稿，不会写入数据集。
- 每次正式发布都把源数据集完整复制到 `storage/staging/annotation/`，在临时
  副本中同步任务表、Episode task 和 frame `task_index`；校验成功后原子移动到
  `storage/derived/<dataset_id>/`。
- Episode 执行结果为 `success`、`partial` 或 `failure`；部分成功和失败必须记录
  原因。Subtask 使用 Episode 相对秒数标注开始/结束，发布时同时换算为帧区间。
- 扩展标注写入派生副本的 `meta/annotations/episodes.parquet`、
  `subtasks.parquet` 与 `schema.json`，不加入 LeRobot v3 核心 feature，也不修改
  原始数据集的核心格式。
- `storage/raw/` 和既有 `storage/derived/` 均不可原地修改；当前没有覆盖发布和
  永久删除。
- 派生版本旁路记录写入 `storage/system/dataset-versions/`，并由当前 SQLite 统一
  登记层分配 DS 编号和保存来源关系。

### Episode 数据处理边界

- 页面与 API 归 `hs_docker` 维护，路由前缀为 `/preprocess/`。
- 支持 Episode 筛除、按比例或编号拆分、结构完全兼容的数据集合并、完整顺序
  重排、精确重复检测与去重，以及指定相机视频画面裁剪。
- 所有输出先写入 `storage/staging/processing/`，校验后进入
  `storage/derived/`；源数据集不可原地修改。
- 处理后重新映射 Episode、frame、全局 index、task index 和旁路标注；视频按
  Episode 边界重新编码为独立 H.264 MP4。画面裁剪对所选相机全部 Episode 使用
  同一像素裁剪框，并同步更新 LeRobot v3 feature 的 shape 和视频宽高元数据。
- 任务记录写入 `storage/system/processing-jobs/`，派生版本登记到
  `storage/system/dataset-versions/`，同时由 SQLite 统一登记层分配 DS 编号。
- 精确重复判定只比较标准化 frame Parquet 指纹，不代表视频视觉相似度检测。

## 四、合并前历史分工：LXP

> 数据管理实现已经进入当前集成分支；独立 `export` 页面目前仍是演示常量，真实
> 导出是这一范围内尚未完成的主要功能。

### 开始前的迁移原则

旧 `lxp_docker` 可作为界面和行为参考，但不能整包复制。旧版含有与 Platform v2
决策冲突的内容：

- 根目录 `data_catalog.py`、`data_management.py`、`dataset_store.py`；
- SQLite 数据库存储；
- 永久清理数据和登记记录；
- `routers/`、根目录 `templates/`、根目录 `static/` 的旧目录结构；
- 对旧质检 Worker 的直接耦合。

需要逐项判断旧功能是否仍符合现有需求，再迁移到新模块。任何重置或强制推送都
必须先得到开发者明确确认。

### 目标文件范围

```text
app/modules/datasets/       数据集扫描、列表、详情和管理服务
app/modules/export/         导出任务和下载接口
app/templates/data*.html    数据管理页面
app/static/css/data*.css    数据管理样式
app/static/js/data*.js      数据管理交互
tests/                      对应测试
```

### 功能目标

1. 将当前简单的数据管理页调整为旧 `lxp_docker` 的数据资产管理样貌。
2. 从 `settings.data_root` 指向的文件夹读取真实内容，不写死 `/home/...`。
3. 保留仍符合需求的数据集列表、详情、搜索、目录浏览和安全下载能力。
4. 增加数据导出：生成导出文件或压缩包到 `storage/exports/`，允许用户下载。
5. 导出过程不得修改 `storage/raw/` 中的原始数据。
6. 当前不实现不可恢复的永久删除；旧版 purge 功能不得迁移。
7. 所有用户提供的路径都必须防止 `../` 等路径穿越。

### 建议模块结构

```text
app/modules/datasets/
├── router.py       # 页面/API 请求
├── service.py      # 扫描、查询、整理数据集信息
└── schemas.py      # 必要时定义请求和响应结构

app/modules/export/
├── router.py       # 发起导出、查询状态、下载
└── service.py      # 安全打包并写入 storage/exports
```

### 验收条件

1. 页面展示来自测试存储目录的真实文件夹，而不是写死的演示常量。
2. 数据管理页面的主要链接和按钮有对应后端行为，不只改变外观。
3. 能导出一个小型测试数据集并下载，且原始数据不被改变。
4. 空目录、损坏数据集、重名导出和非法路径有明确错误处理。
5. 不引入数据库依赖，不在项目根目录新增业务 Python 文件。
6. 对扫描和导出至少提供自动化测试。

## 五、共享接口约定

Python 代码只能通过以下入口取得存储根目录：

```python
from app.core.config import settings

data_root = settings.data_root
```

- 目录含义以 `storage/README.md` 为准；容器内统一为 `/data`。
- 质检不复制完整数据集；结果由 SQLite 中的任务/人工审核状态和
  `storage/reports/qc/` 报告表达。页面移除的报告进入
  `storage/trash/reports/`，不得永久删除，且不影响任务或审核记录。
- 页面路由继续位于 `/data/` 等现有前缀下。
- 路由负责参数和响应，文件操作放入 service。
- 上传文件名、压缩包内部路径和浏览器传来的路径都不可信。
- 不提交真实数据、账号、密码、Token、`.env` 或其他运行状态。
- 当前已有登录会话保护，但没有 HTTPS；局域网访问不等于可以部署到公网。

## 六、每次交接的固定格式

每位开发者或 Codex 完成一段工作后，在“当前交接记录”顶部增加一条：

```text
日期：
开发者/分支：
完成内容：
主要修改文件：
执行的验证：
未完成或已知问题：
是否修改共享文件：
下一步建议：
```

### 当前交接记录

日期：2026-10-22

开发者/分支：Heisan + LXP / `integration/hs-lxp-v3.0`

完成内容：从已冻结验收的 `integration/hs-v2.1` 创建独立集成 worktree，合入
`origin/integration/hs-lxp-v2.5`。人工解决数据库说明、模块说明、全局导航、协作
记录和项目 README 冲突；保留登录/退出、机器人管理、数据定义、情景管理移除、
“数据处理”命名和视频裁剪，接入 LeRobot v3 Derived DS → HDF5、CV 任务、转换
结果展示以及 Derived/CV 打包导出。转换结果使用 CV 编号，不进入 DS 登记表；
Raw 与 Derived 均继续支持质检。

主要修改文件：`app/db/conversions.py`、`app/modules/conversion/`、
`app/modules/export/`、`app/modules/datasets/`、转换/数据管理模板与静态资源、
`pyproject.toml`、`AGENTS.md`、当前状态与协作文档。

执行的验证：冲突标记已清除，`git diff --cached --check` 与 Python 语法编译通过；
旧验证镜像不含本次新增的 h5py，完整 Ruff/pytest 和页面人工验收留到依赖镜像重建
阶段执行。

未完成或已知问题：尚未构建包含 h5py/Pillow 的 v3.0 验证镜像，尚未用真实派生
数据执行转换和导出。首次大数据导出为同步打包，`exports/` 需要后续磁盘清理策略。

是否修改共享文件：是，修改 SQLite 转换任务表、数据管理路由、全局导航、直接
依赖和共同文档；没有改写现有 DS 编号或真实 storage 数据。

下一步建议：重建验证镜像并运行全套检查；随后用小型已备份 Derived DS 人工完成
转换、HDF5 文件核对、打包下载和容器重启验收。

日期：2026-10-22

开发者/分支：Heisan / `integration/hs-v2.1`

完成内容：完成第二阶段本地功能冻结与验收。冻结范围覆盖登录、上传、数据管理、
标注、Episode 数据处理与可视化视频裁剪、质检、报告回收站、机器人档案和数据
定义；工作台真实统计、导出、转换、训练和系统管理继续明确标记为未完成，不因
页面可渲染而视为真实功能。新增独立验收记录，修正 README 中对数据定义和当前
真实能力的过时描述。

主要修改文件：`docs/LOCAL_ACCEPTANCE_2026-10-22.md`、
`docs/CODEX_CURRENT_STATE.md`、`docs/COLLABORATION.md`、`readme.md`。

执行的验证：使用一次性容器及隔离的 `/tmp/hkc-freeze-test-data` 运行全仓库 Ruff
0.16.8 和 pytest 9.1.1，结果为 Ruff 全部通过、`70 passed`，仅有 1 条既有
Starlette/AnyIO 上游弃用警告；8 个 JavaScript 文件语法检查通过；默认、开发和
局域网三种 Compose 配置通过解析，分别确认默认绑定 `127.0.0.1:8000`、局域网
覆盖绑定指定 IPv4、宿主机存储映射到 `/data`。运行中开发容器的健康接口、未登录
页面跳转、API 401、静态资源和 staging 写权限通过；受控重启后恢复 healthy，
SQLite 文件与用户数量保持不变。使用者已人工完成一次可视化视频裁剪。

未完成或已知问题：内置浏览器无法路由到开发机私有网段，因此页面视觉和操作手感
以使用者浏览器人工验收为准；真实公司 VLM、跨设备局域网、正式服务器和不挂载
源码的生产镜像不属于本轮已通过项。标注/处理后台任务仍不能跨容器重启续跑。

是否修改共享文件：只修改验收与状态文档，没有修改 Python、JavaScript、SQLite
表结构、DS 编号、数据集内容、依赖或 Compose 行为。

下一步建议：保持当前分支功能冻结；合并另一位开发者的最新分支后重新执行完整
验收，再统一重建正式镜像并部署到服务器。训练模块应通过独立任务接口连接服务器
训练环境，不把训练依赖装入平台容器。

日期：2026-10-22

开发者/分支：Heisan / `integration/hs-v2.1`

完成内容：按产品命名将导航和接口文档中的“数据预处理”统一改为“数据处理”，
保留已有 `/preprocess/` 地址以避免破坏链接；在现有不可变派生处理链路中新增
视频画面裁剪。使用者可选择相机和预览 Episode，直接在真实视频上移动裁剪框，
或拖动四角、四边调整范围；切换 Episode 时保留同一裁剪框，精确像素坐标仍可在
折叠设置中校准。裁剪框应用到该相机全部 Episode，其他相机保持原尺寸。输出重新
编码为 H.264，并同步更新 LeRobot v3 feature 的 `shape`、`video.width` 与
`video.height`。

主要修改文件：`app/modules/processing/`、`app/templates/preprocess.html`、
`app/static/css/processing.css`、`app/static/js/processing.js`、公共导航、
`tests/integration/test_processing.py` 及当前状态与项目说明。

执行的验证：数据处理专项测试使用真实合成 MP4，确认预览接口支持 HTTP Range、
8×6 视频裁剪为 4×4、帧数保持、元数据同步、源视频不变，并确认越界裁剪安全失败
且不留下 Derived 半成品；页面测试确认八个拖拽手柄完整渲染。全仓库 Ruff 检查
通过，全部 `70 passed`，仅保留 1 条既有 Starlette/AnyIO 上游弃用警告。当前运行
容器 `/healthz` 返回 200。

未完成或已知问题：第一版一次任务只裁剪一个相机，且同一裁剪框应用于该相机的
全部 Episode；不提供逐帧变化裁剪框、画面缩放或旋转。视频重编码耗时和磁盘占用
随数据规模增长，任务仍在 Web 进程后台执行，容器重启不能续跑。内置验收浏览器
无法路由到开发机私有网段，最终拖拽手感仍需使用者在本机浏览器人工确认。

是否修改共享文件：修改公共导航、应用路由标签和文档；未修改 SQLite 表、DS 编号
规则、上传、标注、质检或机器人模块，也没有新增 Python 依赖。

下一步建议：使用真实小型数据集切换多个 Episode、拖动四角和整体移动裁剪框，
再发布派生 DS 并播放结果；大规模使用前评估独立 Worker 和任务续跑。

日期：2026-10-22

开发者/分支：Heisan / `integration/hs-v2.1`

完成内容：将 `/semantics/` 从虚构 Episode 文件结构的占位页改为真实数据定义功能。
页面只读说明平台实际使用的 LeRobot v3 数据集、任务、Episode、帧、视频和旁路
标注结构；新增 `semantic_definitions` 与 `semantic_definition_numbers` SQLite
表，为场景、任务、动作和对象四类词条分配 `SD000001` 形式的稳定编号。支持新增、
编辑、搜索、筛选、排序、停用和恢复；机器编码创建后不可修改，没有永久删除接口。

主要修改文件：`app/db/semantics.py`、`app/modules/semantics/`、
`app/templates/semantics.html`、`app/static/css/semantics.css`、
`app/static/js/semantics.js`、`tests/integration/test_semantics.py`，以及数据库、模块、
当前状态、使用与项目说明。

执行的验证：数据定义专项 Ruff 与 5 项测试通过；随后以
`HKC_DATA_ROOT=/tmp/hkc-v21-semantics-full-test-data` 隔离真实数据运行全仓库
Ruff 0.16.8 和 pytest 9.1.1，结果为 Ruff 全部通过、`66 passed`，仅有 1 条既有
Starlette/AnyIO 上游弃用警告。测试覆盖真实结构页面、稳定 SD 编号、创建、编辑、
筛选、唯一性、输入校验、停用、恢复和无 DELETE 接口。

未完成或已知问题：词表当前只保存平台管理元数据，不自动修改 Raw/Derived、
Episode task 或既有 Subtask；也没有与机器人档案或数据集建立外键。自动化测试
已经通过，但仍需使用者登录后完成人工页面验收。正式生产镜像按使用者决定留到
更多功能完成后统一重建。

是否修改共享文件：在现有 `platform.sqlite` 新增独立词表，不修改 datasets、
quality、users 或 robots 既有表；没有增加依赖，没有修改 Compose、DS 接口或
LeRobot 数据文件。

下一步建议：在页面建立少量真实场景、动作和对象词条，确认团队命名约定；以后
若要让标注或上传强制引用 SD 编号，先设计自由文本兼容和历史数据迁移方案。

日期：2026-10-22

开发者/分支：Heisan / `integration/hs-v2.1`

完成内容：将 `/robots/` 从 3 条硬编码演示数据改为独立机器人档案管理。新增
`robots` 与 `robot_numbers` SQLite 表，稳定编号格式为 `RB000001`；支持名称、
类型、型号、序列号、使用状态、网络地址、传感器和备注的新增、编辑、搜索、筛选、
停用与恢复。名称和非空序列号唯一，没有永久删除接口。页面只按 `robot_type`
统计同类型数据集，不修改 datasets 表、不回写数据集文件，也不建立未经设计的
机器人实例外键。

主要修改文件：`app/db/robots.py`、`app/modules/robots/`、
`app/templates/robots.html`、`app/static/css/robots.css`、
`app/static/js/robots.js`、`tests/integration/test_robots.py`，以及当前状态、数据库、
模块和项目说明。

执行的验证：以 `HKC_DATA_ROOT=/tmp/hkc-v21-test-data` 隔离真实数据运行完整检查；
Ruff 0.16.8 全部通过，pytest 9.1.1 共 `61 passed`，仅有 1 条既有的
Starlette/AnyIO 上游弃用警告。机器人测试覆盖空页面、创建、稳定 RB 编号、编辑、
停用、恢复和无 DELETE 接口。默认、开发和局域网三种 Compose 配置均通过解析。
本机网络切换后已按当前地址重新创建开发容器，容器保持 healthy；`/healthz` 返回
200，未登录访问 `/robots/` 返回 303 到登录页，机器人页面 CSS 与 JavaScript
均返回 200。真实 SQLite 中机器人表为空，没有写入测试机器人。

未完成或已知问题：当前网络地址只是档案，不探测在线状态、不控制机器人；数据集
仅按 `robot_type` 统计，没有具体 `robot_id` 关联。自动化和未登录静态资源验证已
完成，但尚未进行登录后的浏览器人工表单验收。正式生产镜像仍未重新构建；当前
验收对象是挂载最新源码的开发容器和一次性测试容器。

是否修改共享文件：新增同一 `platform.sqlite` 中的机器人表，但没有修改已有用户、
数据集或质检表；没有增加依赖，没有修改 Compose、DS 接口或 LeRobot 文件。

下一步建议：使用者先登录当前开发页面，人工注册一条可删除前的测试机器人，验证
编辑、停用、恢复和筛选；确认业务字段后，再决定是否设计数据集到具体机器人的
稳定关联，不要直接复用可变名称。

日期：2026-10-22

开发者/分支：Heisan / `integration/hs-v2.1`

完成内容：新增无默认账号的基础登录系统。`app/db/users.py` 在既有
`storage/system/platform.sqlite` 中创建 `users` 与 `user_sessions`；密码使用
scrypt 独立盐哈希，浏览器只持有随机 Cookie 令牌，数据库只保存其 SHA-256 摘要。
新增登录、退出、会话失效和全站中间件保护；未登录网页跳转 `/login`，未登录 API
返回 401。新增公开且不返回业务数据的 `/healthz`，Docker 健康检查改用该地址。
提供交互式 `python -m app.cli.create_user`，支持可选的 `--username`，但故意不
提供密码参数；新增 `python -m app.cli.list_users`，只显示不含认证秘密的账号
摘要。没有在源码、配置或文档中提供默认密码。已在本机被 Git 忽略的 SQLite 中
创建首个用户 `hei_san`。

主要修改文件：`app/db/users.py`、`app/modules/auth/`、`app/cli/create_user.py`、
`app/cli/list_users.py`、`app/main.py`、`app/templates/login.html`、
`app/templates/base.html`、`Dockerfile`、`tests/conftest.py`、
`tests/integration/test_auth.py`、`tests/unit/test_user_cli.py`、`pyproject.toml` 及相关
说明文档。

执行的验证：新增 5 项真实 SQLite 认证测试；全仓库 Ruff 0.16.8 检查通过，pytest
9.1.1 共 53 项全部通过，仅保留既有 Starlette/AnyIO 上游弃用警告。默认、开发和
局域网 Compose 均通过配置解析。本机真实库确认存在两张新表，`hei_san` 的哈希
方案为 scrypt，交互密码校验成功；`/healthz` 返回 200，未登录访问 `/dashboard/`
返回 303 并跳转登录页。当前开发容器健康且已挂载最新源码。

用户命令补充部分已用现有 Python 3.11 运行镜像验证：创建测试用户、密码认证以及
不泄露密码/哈希的账号列表均通过；真实开发容器的列表命令成功读取持久化库并显示
`hei_san`。新增的 3 项 CLI 自动化测试已纳入 2026-10-22 的完整测试，当前全套
结果为 Ruff 通过、pytest `61 passed`。

未完成或已知问题：平台仍未部署 HTTPS、角色权限、找回密码、登录限速和机器人
API Token，只能用于可信内网。完整源码测试与 Compose 配置验证已经通过，但旧
正式镜像尚未包含本次登录和机器人管理代码；后续仍需重新构建正式镜像，并进行
不挂载源码的生产式验收。

是否修改共享文件：是，修改 `app/main.py`、全局 `base.html`、Docker 健康检查和
同一个 SQLite 文件中的表结构；没有修改 datasets/quality 既有表、DS 编号、数据
文件或同事负责的数据管理接口。

下一步建议：先由使用者在当前开发容器浏览器中人工登录、退出；网络稳定后使用
可信 PyPI/公司仓库重新构建镜像并执行生产式验收，再继续真实机器人管理。

日期：2026-10-22

开发者/分支：Heisan / `integration/hs-v2.1`

完成内容：从已验证的 `integration/hs-lxp-v2` 创建下一阶段开发分支
`integration/hs-v2.1`；按产品决定移除独立情景管理，包括 `/scenes/` 路由、全局
导航入口、演示模块和模板。机器人管理继续保留；未来场景分类归入数据定义，不再
建立独立情景管理模块。新增回归测试，明确 `/scenes/` 应返回 HTTP 404，并同步
当前状态和项目目录说明。

主要修改文件：`app/main.py`、`app/templates/base.html`、
`tests/integration/test_pages.py`、`AGENTS.md`、`readme.md`、
`docs/CODEX_CURRENT_STATE.md`、`docs/COLLABORATION.md`；删除
`app/modules/scenes/` 和 `app/templates/scenes.html`。

执行的验证：在最终集成镜像中以只读源码挂载运行 Ruff 0.16.8，全仓库
`app tests` 检查通过；pytest 9.1.1 完整运行 48 项全部通过。保留一条来自
Starlette/AnyIO 的既有上游弃用警告。

未完成或已知问题：本步骤没有实现登录、真实机器人管理、数据定义或工作台；
这些功能将在 `integration/hs-v2.1` 后续分步完成。代码尚未提交或推送。

是否修改共享文件：是，删除路由注册并修改全局导航；没有修改 SQLite 表结构、
数据集接口、Docker/Compose、真实数据或运行容器。

下一步建议：审查本次删除差异后，再实现最小登录功能；登录接入时为 Docker 增加
不受鉴权保护的 `/healthz`，并避免同时修改同事正在维护的数据管理页面。

日期：2026-10-22

开发者/分支：Heisan / `integration/hs-lxp-v2`

完成内容：为合并后的双方 Codex 建立新的单一事实来源。重写根目录 `AGENTS.md`，
明确当前集成分支、已完成/未完成边界、目录职责、DS 数据生命周期、配置、默认
维护范围与最低验证要求；新增 `docs/CODEX_CURRENT_STATE.md`，为 LXP 侧 Codex
提供接手入口、真实路由状态、存储/SQLite 关系和任务工作流。将旧分支与按人任务
明确标记为历史参考，并修正 `app/db/__init__.py` 中“尚未启用数据库”的过时说明。

主要修改文件：`AGENTS.md`、`docs/CODEX_CURRENT_STATE.md`、
`docs/COLLABORATION.md`、`readme.md`、`app/db/__init__.py`。

执行的验证：对照当前路由、业务模块、SQLite 代码、存储目录、Compose、依赖和
测试文件人工核对文档；执行 Markdown/代码差异检查与 Python 语法检查。

未完成或已知问题：真实独立数据导出、格式转换、模型训练与评估仍未实现；旧
README 后半部分包含早期本机/Windows 运行笔记，不能优先于新的 Codex 规范。

是否修改共享文件：只修改协作说明与 `app/db` 包说明，不改变数据库表结构、API、
数据集、镜像或运行容器。

下一步建议：同事拉取该提交后让 Codex 先读取 `AGENTS.md` 和
`docs/CODEX_CURRENT_STATE.md`，再围绕约定模块开始具体任务。

日期：2026-10-22

开发者/分支：Codex / `integration/hs-lxp-v2.5`（来源分支记录）

完成内容：来源分支曾增加服务器训练环境只读盘点。本轮集成没有把包含特定服务器
地址、目录和环境快照的 `服务器配置.md` 纳入共享分支；训练架构将在后续阶段根据
当时服务器状态重新核实。

日期：2026-10-22

开发者/分支：LXP / `integration/hs-lxp-v2.5`

完成内容：按最新要求，转换结果不再进入数据集登记流程，也不分配新的 DS 编号。Raw/Derived 的既有 DS 登记方式保持不变；已完成的转换结果以 CV 任务编号关联 `converted/<CV 编号>/`，在 `/data/` 的独立“格式转换结果”区域展示。导出接口接受 Derived DS 或转换 CV 编号，压缩包仍写入 `storage/exports/`。已有的旧版 `converted_datasets` 表及文件保留，但不再用于新结果的登记、查询或展示。

主要修改文件：`app/db/datasets.py`、`app/db/conversions.py`、`app/modules/conversion/service.py`、`app/modules/export/service.py`、`app/modules/datasets/`、`app/templates/data*.html`、`app/templates/export.html`、`app/static/js/export.js`、`tests/integration/test_conversion.py` 及相关说明。

执行的验证：Python 语法编译、应用导入和 `/data/`、`/export/` 页面 HTTP 200；在临时存储目录直接运行现有的转换并导出、旧 JSON 转换任务展示两条集成用例，均通过；`git diff --check` 通过。运行镜像未安装 pytest，临时安装测试依赖因下载超时失败，因此未运行 pytest 命令。

未完成或已知问题：旧版 `converted_datasets` 表作为历史元数据保留，不再显示为已登记数据集；该表清理需要单独制定数据迁移方案。未用真实业务数据运行转换或导出。

是否修改共享文件：本轮未修改 `app/main.py`、`app/templates/base.html`、部署配置或依赖声明。

下一步建议：在数据管理页确认“已登记”与“格式转换结果”两个区域的实际排版，并用一份小型派生数据集确认转换后的 CV 导出文件。

日期：2026-10-22

开发者/分支：LXP / `integration/hs-lxp-v2.5`

完成内容：按本轮确认，完整导出包必须写入 `storage/exports/`。`/export/` 新增独立“数据集导出”区和转换成功任务的导出按钮，可按统一 DS 编号打包已登记的 Derived 或 Converted 数据集为 `tar.gz`；先写入 `staging/export/`，校验来源快照后原子发布到 `exports/`，相同快照复用已生成的包。转换页不再提供“查看数据集”入口，之前新增的 Converted 专用详情页和路由已移除；转换结果仍在数据管理已登记列表显示。

主要修改文件：`app/modules/export/service.py`、`app/modules/export/router.py`、`app/templates/export.html`、`app/static/js/export.js`、`app/static/css/export.css`、`app/modules/datasets/router.py`、`app/templates/data.html`、`app/templates/data_manage.html`、`tests/integration/test_conversion.py` 及相关说明。

执行的验证：仅运行转换、导出和数据管理相关的 15 个集成测试，覆盖 Derived/Converted 压缩包内容、`exports/` 落盘、非法路径和符号链接拒绝；全部通过。相关 Python 文件 Ruff 检查通过，页面与 API 在开发容器返回 HTTP 200，并检查了页面排版。

未完成或已知问题：目前只提供 LeRobot v3.0 → HDF5 转换方案；生成大型压缩包时，首次下载需等待打包完成，且 `exports/` 会占用相应磁盘空间。未使用真实业务数据触发导出。

是否修改共享文件：已在本条记录中提前说明 `app/templates/base.html` 导航文案调整；未改动 Compose、Dockerfile、`main` 或对方业务模块。

下一步建议：在需要导出真实数据时确认 `exports/` 的可用空间；后续按业务需求添加其他转换方案。

日期：2026-10-22

开发者/分支：LXP / `integration/hs-lxp-v2.5`

完成内容：重做 `/export/` 为按转换方案选择的页面，目前只开放 LeRobot v3.0 → HDF5。成功任务在 SQLite 中登记独立 DS 编号，保存来源 DS、格式、目录、转换 CV 任务、规模与展示信息；结果可在数据管理总览和已登记列表中看到。CV 任务状态迁入 SQLite，首次访问会幂等复制旧 JSON 任务并保留原文件；有效的历史成功结果也登记 DS。失败和运行中的任务不登记结果。Converted 专用详情页在下一轮按使用者要求移除。

主要修改文件：`app/db/datasets.py`、`app/db/conversions.py`、`app/modules/conversion/service.py`、`app/modules/datasets/`、`app/modules/export/router.py`、`app/templates/export.html`、`app/templates/data*.html`、`app/static/css/export.css`、`app/static/js/export.js`、相关说明与 `tests/integration/test_conversion.py`。

执行的验证：仅运行转换相关的 6 个集成测试（含 DS 登记、数据管理可见、展示信息修改、历史 JSON 迁移），全部通过；相关 Python 文件 Ruff 检查与 `git diff --check` 通过；当前开发容器中的 `/export/`、`/data/`、`/data/manage/?view=registered`、`/export/api/profiles` 返回 HTTP 200，并在浏览器检查了页面排版。

未完成或已知问题：只实现 HDF5 方案；其他格式需新增转换器和方案定义。历史任务若输出文件已经缺失，不会生成 DS 记录。CV 任务由当前应用进程后台执行，重启时运行中的任务标记为失败。

是否修改共享文件：已在本记录先行说明 `app/templates/base.html` 的导航文案和窄屏主区宽度调整；未改动 Compose、Dockerfile、`main` 或对方业务模块。

下一步建议：用真实已登记的派生数据集从页面发起一次转换，检查生成文件；之后再按业务需求添加第二个转换方案。

日期：2026-10-22

开发者/分支：LXP / `integration/hs-lxp-v2.5`

完成内容：修复开发环境页面无法访问。开发 Compose 热加载了新增转换代码，但正在运行的
旧 `v2.5` 镜像没有 `h5py`，导致 Uvicorn 子进程导入失败。使用当前依赖重建镜像并
重新创建开发容器，保留原有 `storage/` 挂载。

主要修改文件：未修改应用代码或部署配置；只补充本交接记录。

执行的验证：运行容器 `pip check` 通过，`h5py` 与 Pillow 可导入，容器状态为
`healthy`；本机 `http://127.0.0.1:8000/dashboard/`、`/export/` 和
`/export/api/datasets` 均返回 HTTP 200。

未完成或已知问题：当前 Compose 仅绑定 `127.0.0.1:8000`；其他机器访问仍需按
`deploy/README.md` 使用可信局域网覆盖配置。

是否修改共享文件：仅更新协作文档；未修改 Compose、Dockerfile 或正式数据。

下一步建议：本机刷新 `/export/`；如需其他机器访问，先确认可信内网地址。

日期：2026-10-22

开发者/分支：LXP / `integration/hs-lxp-v2.5`

完成内容：实现已登记 Derived 数据集的 LeRobot v3.0 → HDF5 格式转换。转换任务
使用统一 DS 编号定位来源并保存文件指纹，读取官方 task 索引及早期全局 Parquet
行索引；视频/图像转为逐帧 JPEG，保留平台旁路标注元数据。结果先写入
`storage/staging/conversion/`，校验后原子发布至 `storage/converted/`，任务元数据
写入 `storage/system/conversion-jobs/`。页面支持进度查询与 HDF5 分块、清单下载；
Raw/Derived 来源不原地修改。

主要修改文件：`pyproject.toml`（新增固定版本 `h5py`、`pillow`）、
`app/modules/conversion/`、`app/modules/export/router.py`、`app/templates/export.html`、
`app/static/css/export.css`、`app/static/js/export.js`、
`tests/integration/test_conversion.py`、模块/存储/项目说明。

执行的验证：默认、开发、局域网三种 Compose 组合通过 `config --quiet`；使用临时
镜像标签完成全新构建，`pip check` 无冲突，容器健康；`/dashboard/`、`/export/`、
`/export/api/datasets` 均返回 HTTP 200。全仓库 pytest 53 项通过，转换模块 Ruff
通过，`git diff --check` 通过。测试只用临时合成 LeRobot v3 数据。

未完成或已知问题：尚未用实际业务数据集核对 HDF5 内容和体积；视频会有损重编码
为 JPEG，可能显著增大存储占用。Web 进程单线程执行，运行中的任务重启后标记失败，
需重新创建；打包导出不在本次范围内。

是否修改共享文件：修改 `pyproject.toml` 增加 HDF5/JPEG 运行依赖；请另一位开发者
合并依赖时保留。未修改 Compose、Dockerfile、`app/main.py` 或正式数据集。

下一步建议：双方审阅本分支差异后，在已备份的小型真实 Derived DS 上人工核对
Episode、动作、图像、标注和生成文件体积，再决定是否集成打包导出。

日期：2026-10-21

开发者/分支：Heisan / `integration/hs-lxp-v2`

完成内容：完成集成计划第十、十一步的依赖、部署和协作规范合并。确认对方分支的
FastAPI/Jinja2/Uvicorn、上传、Doctor、HTTPX、NumPy 依赖均已进入当前基线，并
保留本分支处理官方 LeRobot v3 所需的 AV、Pandas、PyArrow；补充代码直接使用的
Pydantic 固定版本。`pyproject.toml` 成为 Python 直接依赖唯一来源。Dockerfile
继续使用 Python 3.11 slim 和 FFmpeg，支持可配置 APP_UID/APP_GID 并保持非 root。
Compose 保留 127.0.0.1:8000 安全默认和可信局域网覆盖，拒绝对方临时 8001；新增
HKC_STORAGE_PATH、三项上传限制、30 秒优雅停止，并保持单平台容器。同步更新部署、
换机、备份、依赖边界和双方 Codex 协作规则，示例中不包含公司地址或 Token。
Dockerfile 与 Compose 支持按部署网络临时覆盖 PIP_INDEX_URL/PIP_DEFAULT_TIMEOUT，
默认仍使用官方 PyPI。修复全仓库 Ruff 报告的 9 条既有规则问题。

主要修改文件：`pyproject.toml`、`Dockerfile`、`compose.yaml`、`.env.example`、
`app/core/config.py`、`app/core/paths.py`、`app/modules/logs/router.py`、`app/web.py`、
`tests/integration/test_pages.py`、`deploy/README.md`、`how_use.md`、`readme.md`、
`storage/README.md`、`AGENTS.md`、`docs/COLLABORATION.md`。

执行的验证：默认、开发、本机局域网和服务器局域网四种 Compose 组合均通过
`config --quiet`；另用无密钥环境验证自定义绝对存储路径、UID/GID 与默认端口解析。
从 DaoCloud 加速地址取得 `linux/amd64` Python 基础镜像后，使用中科大 PyPI
临时构建源完成最终验证镜像；容器以 uid/gid 1000 非 root 运行，`pip check` 无
冲突，关键依赖导入、Doctor 0.2.0 与 FFmpeg 通过。全仓库 `ruff check app tests`
通过，pytest 48 项全部通过，仅有一条来自 Starlette/AnyIO 的上游弃用警告。最终
镜像在不挂载宿主机源码和真实数据的条件下为 healthy，工作台、上传、数据管理、
标注、处理、质检和报告回收站页面均返回 HTTP 200。新旧镜像显示大小均约
1.55 GB，没有因本次合并异常膨胀。

未完成或已知问题：当前网络仍不能直连 Docker Hub，最终验收通过经确认的临时
加速地址取得基础镜像；生产环境应优先使用公司仓库或受控镜像缓存。全仓库
`ruff format --check` 会建议机械格式化 28 个历史文件，本次为避免产生大面积无关
差异，只格式化实际修复文件；规则检查已经完全通过。当前正式运行容器未被替换。

是否修改共享文件：是，修改依赖、Docker/Compose、公开环境变量示例及共同文档；
未修改 SQLite 表结构、数据集内容、现有 DS 编号、质检任务或真实 `.env`。

下一步建议：双方审阅当前差异后提交并推送集成分支；确认备份和 `.env` 后，再由
负责人决定是否将正式 Compose 服务替换为本次验收镜像。合并 `main` 前在目标
服务器使用实际 UID/GID 与 HKC_STORAGE_PATH 再执行一次部署验收。

日期：2026-10-21

开发者/分支：Heisan / `integration/hs-lxp-v2`

完成内容：废止并移除空的 `storage/detected/`。质检不再复制完整数据集，结论由
SQLite 任务/人工审核状态和 `storage/reports/qc/` 报告共同表达。将原来的报告
永久删除改为可恢复回收站：页面移除报告时原子移动到
`storage/trash/reports/<DS 编号>/`，隐藏元数据记录原路径和 UTC 移入时间；新增
回收站列表、查看、下载和恢复功能，不提供永久清空接口。质检任务和审核历史在
移入及恢复过程中均保持不变，同名文件不会被覆盖。

主要修改文件：`app/modules/datasets/service.py`、
`app/modules/datasets/router.py`、`app/templates/data_reports.html`、
`app/templates/qc_report.html`、`app/static/js/data_reports.js`、
`app/static/css/data.css`、`tests/integration/test_data_management.py`、
`storage/trash/reports/.gitkeep`、`storage/README.md`、`AGENTS.md`、
`readme.md`、`docs/COLLABORATION.md`。

执行的验证：移除前确认 `storage/detected/` 为空；相关 Ruff 检查通过，数据管理与
质检回归测试共 21 项全部通过。测试覆盖移入、回收站列表/读取、元数据、恢复、
同名防覆盖、路径安全及任务/审核记录保留。运行中容器保持健康，当前报告页、
回收站页和质检页均返回 HTTP 200。

未完成或已知问题：首期不提供回收站永久清空功能，也不自动按时间清理。正式存储
目前没有报告被移动；需要使用者在页面用一份可恢复的测试报告做一次人工验收。

是否修改共享文件：修改了数据管理报告接口和页面、存储约定及协作规则；未修改
SQLite 表结构、现有 DS 编号、数据集内容、质检任务或人工审核记录。

下一步建议：打开 `/data/reports/` 将一份测试报告移入回收站，再到“回收站”页
确认查看、下载和恢复均正常；之后再继续真实 VLM 端到端质检验收。

日期：2026-10-21

开发者/分支：Heisan / `integration/hs-lxp-v2`

完成内容：完成集成计划第七步的本地质检适配。确认质检方案版本、Doctor、机器人
规则、VLM、任务历史、隐藏与恢复、人工审核和数据管理状态回显均已使用统一 DS
编号；Raw 与 Derived 数据集都可创建质检任务。新增公共 LeRobot v3 task 读取器，
使标注和 VLM 共用官方 `tasks.parquet` Pandas 索引语义，并让 VLM 正确读取 Episode
的 `tasks` 列；缺少 Episode task 时可根据 frame `task_index` 回查任务文本。

主要修改文件：`app/modules/datasets/lerobot_v3.py`、
`app/modules/annotation/service.py`、`app/modules/quality/vlm.py`、
`tests/integration/test_quality.py`、`readme.md`、`docs/COLLABORATION.md`。

执行的验证：相关 Ruff 检查通过；质检、数据管理和标注集成测试共 26 项全部通过。
测试覆盖官方索引格式、多 task Episode 传入 VLM、Derived DS 创建质检任务及报告
保存真实存储区域。VLM 使用模拟函数，没有调用公司服务或上传真实视频。

未完成或已知问题：尚未在公司网络中使用真实 VLM 地址执行端到端视频质检；正式
测试前仍需确认 `.env` 中地址、Token、模型名及服务器接口兼容性。报告移入回收站
属于后续计划，不在本步骤中修改。

是否修改共享文件：新增数据集公共元数据读取模块，并让标注、质检共享该逻辑；
未修改 SQLite 表结构、现有数据集、DS 编号或正式质检记录。

下一步建议：先用一个小型已登记 Raw 数据集关闭 VLM 完成 Doctor/机器人规则人工
验收，再在公司网络中启用一条 VLM 规则进行端到端验证，最后用 Derived DS 重复
一次并完成人工审核。

日期：2026-10-21

开发者/分支：Heisan / `integration/hs-lxp-v2`

完成内容：使用 `git worktree move` 将集成 worktree 从 Codex 项目镜像目录安全
移动到 `~/vscode_project/HKC/HKC_Data_Platform_merge`，保留未提交改动、`.env`、
SQLite、Raw 数据和上传记录；从新目录重建局域网容器并验证 `/data/staging` 可写。
同时定位上传误报权限问题：LeRobot v3 ZIP 的 `robot_type` 为 `null` 时，上传预检
允许继续但统一登记层拒绝，异常最终被错误包装成 500。现统一按“未声明”登记，
质检方案及重新扫描使用相同规范；其他可预期登记错误会返回真实校验原因。

主要修改文件：`app/modules/datasets/service.py`、
`app/modules/datasets/router.py`、`app/modules/ingestion/service.py`、
`app/modules/quality/catalog.py`、`app/modules/quality/service.py`、
`tests/integration/test_ingestion.py`、`how_use.md`、`readme.md`、
`docs/COLLABORATION.md`。

执行的验证：新容器健康检查通过，bind mount 已指向新目录，容器临时写入测试
通过；相关 Ruff 检查通过，上传、质检和数据管理共 23 项测试通过；实际
`my_sim_dataset.zip` 在一次性临时目录中成功完成解压与登记模拟，识别 47 个
Episodes、4700 帧并以“未声明”机器人类型登记，未写入正式 storage。

未完成或已知问题：此前 15 条失败上传记录作为历史事实继续保留；实际 ZIP 尚未
替使用者再次正式上传。未声明机器人类型的数据可以进入平台，但团队仍应在数据
采集规范中决定是否要求采集端填写明确类型。

是否修改共享文件：修改了统一登记、上传和质检对缺失机器人类型的共同解释；
未修改 SQLite 表结构、数据集内容或现有 DS 编号。

下一步建议：由使用者在上传页重新提交同一个 ZIP，确认正式入库后获得新 DS 编号；
再继续第七步质检适配。

日期：2026-10-21

开发者/分支：Heisan / `integration/hs-lxp-v2`

完成内容：验收并补齐集成计划第六步。确认标注和数据处理均使用统一 DS 编号解析
Raw/Derived 真实目录；发布结果进入 `storage/derived/` 后自动登记新 DS 并保存
来源关系。数据处理任务输出改为可点击结果，可直接把新派生 DS 载入处理工作台；
标注发布原有的自动切换新 DS 行为保持不变。

主要修改文件：`app/static/js/processing.js`、`app/static/css/processing.css`、
`app/templates/preprocess.html`、`tests/integration/test_annotation.py`、
`tests/integration/test_processing.py`、`readme.md`、`docs/COLLABORATION.md`。

执行的验证：一次性项目容器内运行相关 Ruff 检查通过，标注和处理集成测试共
10 项全部通过；新增
断言确认派生结果获得 `DS000002`、登记区域为 `derived`、真实目录映射正确且来源
为 `DS000001`。`git diff --check` 通过。

未完成或已知问题：数据处理是后台任务，拆分操作可能一次生成多个 DS，因此页面
在任务完成后提供逐个点击载入，而不是擅自自动选择其中一个。当前容器内任务仍不
支持重启续跑，该限制不属于本次 DS 适配范围。

是否修改共享文件：修改了数据处理前端及项目说明，没有修改 SQLite 表结构、
Compose、Dockerfile、上传或质检实现。

下一步建议：人工用小型已备份数据集各发布一次标注派生版本和处理派生版本，确认
页面能载入新 DS 后，再进入集成计划第七步的质检适配验收。

日期：2026-10-21

开发者/分支：Heisan / `integration/hs-lxp-v2`

完成内容：新增平台日常使用文档，说明切换网络后重新确认局域网 IPv4、使用 LAN
Compose 覆盖配置强制重建容器、配置和验证公司 VLM、查看状态与日志、停止平台及
常见网络故障排查方法。

主要修改文件：`how_use.md`、`docs/COLLABORATION.md`。

执行的验证：使用占位局域网地址完成 Compose 组合配置解析；`git diff --check`
通过。

未完成或已知问题：VLM 的真实地址、端口、Token、公司网段和防火墙规则仍需由
实际部署环境提供，文档没有写入任何凭据。

是否修改共享文件：只新增使用文档并更新协作交接记录，没有修改应用、镜像或
Compose 行为。

下一步建议：回到公司网络后按 `how_use.md` 的最短操作清单重新创建容器，并在
容器内部验证 `/models` 连通性。

日期：2026-10-20

开发者/分支：Heisan / `hs_docker`

完成内容：为数据处理模块增加早期 LeRobot v3 Episode 元数据兼容。源数据缺少
`data/chunk_index`、`data/file_index` 时，根据 `info.json.data_path` 和 frame
Parquet 的 `episode_index` 自动反查所在分片；同时把 `num_frames/task` 映射为
`length/tasks`。兼容仅发生在内存读取层，派生输出补齐标准字段，raw 不被改写。

主要修改文件：`app/modules/processing/service.py`、
`tests/integration/test_processing.py`、数据处理相关说明。

执行的验证：处理模块 Ruff 通过，全平台 26 项自动化测试通过。真实“测试”数据集
成功读取 7 个 Episodes、2,647 帧、20 FPS、zhiyan 和 2 路视频；每个 Episode 的
长度、时长和 task 均成功返回。未向真实数据生成派生版本。

未完成或已知问题：自动反查要求一个 Episode 的 frame 数据位于单个 Parquet；
如果未来出现一个 Episode 跨多个 Parquet 的非标准数据，需要先增加重组策略。

是否修改共享文件：否；没有增加依赖或修改 Compose。

下一步建议：由使用者继续提出数据处理交互要求；正式执行前先用已备份的小数据
集验证视频重编码和派生版本的训练读取。

日期：2026-10-20

开发者/分支：Heisan / `hs_docker`

完成内容：将 `/preprocess/` 从静态说明页改为真实 LeRobot v3 数据处理工作台。
支持 Episode 筛除并生成派生数据集、按比例或显式编号拆分、完全兼容数据集合并、
Episode 完整顺序重排，以及 frame Parquet 精确重复检测和去重。所有操作保持源数据
只读，重新映射 Episode/frame/index/task，迁移已有旁路标注，并将视频按新 Episode
边界重新编码后发布为完整派生数据集。

主要修改文件：`app/modules/processing/`、`app/templates/preprocess.html`、
`app/static/css/processing.css`、`app/static/js/processing.js`、
`tests/integration/test_processing.py`、`pyproject.toml` 及项目/模块/存储说明。

执行的验证：处理模块 Ruff 通过；全平台 25 项自动化测试通过；PyAV 人工生成的
10 帧视频成功裁出 3 帧 H.264 MP4。只读浏览器验证真实 SmolVLA v3 数据成功显示
50 个 Episodes、19,631 帧、so100、30 FPS 和 2 路视频，五种操作面板可切换，
Episode 选择联动正常；精确重复扫描完成且未发现重复，控制台无错误。没有对真实
数据创建派生版本。

未完成或已知问题：第一版每个输出写入单个 Parquet shard；重复检测只比较去除
全局 index 后的 frame 数据，不解码视频判断视觉相似；任务运行在 Web 进程后台，
容器重启后不能续跑，也未实现任务取消和并发队列。大数据量或多人并发后应拆分
独立 Worker。

是否修改共享文件：是，在 `pyproject.toml` 增加 `av==15.0.0`，用于派生数据集的
视频切片和 H.264 重编码；LXP 合并或重建镜像时必须保留。同步更新了 `AGENTS.md`
中的 Heisan 负责范围。

下一步建议：先用已备份的小型真实数据集分别人工生成一次筛除和拆分版本，核对
视频、Parquet 和训练读取；确认后再评估多 shard 输出、持久任务队列和视觉近似
重复检测。

日期：2026-10-19

开发者/分支：Heisan / `hs_docker`

完成内容：按 LeRobot 官方 v3 实现修正标注模块兼容性。`tasks.parquet` 不再强制
要求物理 `task` 列，而是使用 Pandas DataFrame 的 task 索引；写回派生版本也采用
官方索引语义。Episode 帧数改读官方 `length` 字段，并兼容平台早期格式。

主要修改文件：`app/modules/annotation/service.py`、
`tests/integration/test_annotation.py`、`pyproject.toml` 及标注相关说明。

执行的验证：标注模块 Ruff、5 项标注回归测试以及全平台 21 项自动化测试通过；
真实 SmolVLA v3 数据成功读取 50 个 Episodes、19,631 帧、1 个 task 和 2 路视频。
浏览器中 Episode 0000 显示
454 帧、15.133 秒，两路视频均加载到 readyState 4，控制台无错误。未向真实数据
发布派生版本。

未完成或已知问题：只适配当前使用到的 LeRobot v3 元数据；尚未运行完整 Doctor
级别的一致性检查。平台扩展标注仍使用 `meta/annotations/` 旁路文件。

是否修改共享文件：是，在 `pyproject.toml` 增加 `pandas==2.3.3`；LXP 重建镜像
或调整依赖时应保留 pandas 与 pyarrow。

下一步建议：由使用者在可备份数据集上人工发布一个派生标注版本；随后实现数据
质检流程并接入 LeRobot Doctor。

日期：2026-10-19

开发者/分支：Heisan / `hs_docker`

完成内容：扩展 `/annotation/`，新增 Episode 执行结果（成功、部分成功、失败及
原因）和真实 Subtask 时间段标注；支持从视频播放位置取开始/结束时间，记录描述、
动作类型、对象及该段结果，并在时间轴中查看、定位、编辑和删除。正式发布仍创建
完整派生数据集；新增标注写入 `meta/annotations/` 旁路 Parquet，同时保存秒数和
换算后的帧区间，不修改 LeRobot v3 核心 feature。

主要修改文件：`app/modules/annotation/`、`app/templates/annotation.html`、
`app/static/css/annotation.css`、`app/static/js/annotation.js`、
`tests/integration/test_annotation.py`、项目和存储说明。

执行的验证：Ruff 通过；全部自动化测试 20 项通过；浏览器使用真实 7 个 Episodes
和两路视频完成执行结果、失败原因、Subtask 添加、时间轴和暂存流程验证，控制台
无错误。发布写入测试只使用临时合成数据，没有向真实数据集发布测试版本。

未完成或已知问题：当前是单人浏览器草稿，没有多人审核/锁定、自动保存、后台
发布队列和断点恢复；Subtask 动作类型为自由输入加常用建议，尚未建立团队统一词表。

是否修改共享文件：是，只更新文档中的标注能力和存储约定；未修改 LXP 的数据
管理或导出业务。

下一步建议：由使用者用一个可备份数据集人工发布首个标注版本，确认团队希望采用
的动作类型词表；随后再实现质检或标注审核流程。

日期：2026-10-19

开发者/分支：Heisan / `hs_docker`

完成内容（历史记录，已于 2026-10-21 废止）：曾新增 `storage/detected/`，用于
保存质检通过后的完整数据集；现已改为 SQLite 状态与 `reports/qc/` 报告表达结论。

主要修改文件：`storage/detected/.gitkeep`、`storage/README.md`、`AGENTS.md`、
`readme.md`、`docs/COLLABORATION.md`。

执行的验证：确认目录骨架可被 Git 跟踪，且真实运行内容仍受 `.gitignore` 保护；
`git diff --check` 通过。

未完成或已知问题：当前只建立目录与使用规则，真实质检业务尚未实现，因此不会
自动向 `detected/` 写入数据集。

是否修改共享文件：是，只补充存储目录约定，没有修改 LXP 的数据管理代码。

下一步建议（已废止）：不再向 `detected/` 写入或复制数据集。

日期：2026-10-19

开发者/分支：Heisan / `hs_docker`

完成内容：实现 `/annotation/` 第一版真实 LeRobot v3 Episode/task 标注工作台；
读取真实任务表和 Episode 元数据，按元数据定位并播放多相机视频；支持单选、批量
选择和 task 草稿修改；每次正式发布创建完整派生数据集，并同步更新任务表、
Episode 元数据、frame `task_index` 和 `info.json.total_tasks`。

主要修改文件：`app/modules/annotation/`、`app/templates/annotation.html`、
`app/static/css/annotation.css`、`app/static/js/annotation.js`、`pyproject.toml`、
`tests/integration/test_annotation.py`、存储与协作文档。

执行的验证：Ruff 对本次标注模块检查通过；全部自动化测试 18 项通过；真实上传
数据成功读取 7 个 Episodes、1 个 task 和 2 路视频索引；真实 MP4 Range 请求返回
206；浏览器实测两路视频均进入 readyState 4，并同步播放到相同时间点，无控制台
错误。发布写入测试只使用临时合成数据，确认完整复制且原始数据不变。

未完成或已知问题：当前只修改每个 Episode 的单一 task 提示词；尚未支持帧级
分段标注、多人审核/锁定、发布任务后台队列和发布进度恢复。完整副本会占用与源
数据集近似的额外空间，发布期间 HTTP 请求需要保持连接。

是否修改共享文件：是，在 `pyproject.toml` 增加 `pyarrow==26.0.0`，用于直接
读取和写入 LeRobot v3 Parquet；没有加入 PyTorch、CUDA 或训练框架。LXP 更新
依赖或镜像时应保留该运行依赖。

下一步建议：由使用者人工修改一个可备份的小数据集并正式发布，确认业务 task
文案；后续再决定是否增加帧级时间段标注、审核流与后台任务。

日期：2026-10-19

开发者/分支：Heisan / `hs_docker`

完成内容：实现 `/ingestion/` 原始数据集上传页面和真实 API；上传内容分块进入临时
区，经 ZIP 安全检查和 LeRobot v3.0 最低结构检查后，安全解压到原始区；增加文件
式上传记录、页面进度反馈和失败原因展示。

主要修改文件：`app/modules/ingestion/`、`app/templates/ingestion.html`、
`app/static/css/ingestion.css`、`app/static/js/ingestion.js`、`app/main.py`、
`app/templates/base.html`、`app/templates/data.html`、`tests/integration/`。

执行的验证：Python 语法检查和 `git diff --check` 通过；上传页面、CSS、JavaScript
实际返回 HTTP 200；自动化测试 16 项通过，覆盖有效 v3 入库、错误版本拒绝和 ZIP
路径穿越拒绝。

未完成或已知问题：当前只做最低结构验证，不解析 Parquet 语义，也不执行
LeRobot Doctor；上传在单应用容器内处理，后续超大数据和并发任务需要再评估独立
Worker；数据管理列表尚需由 `lxp_docker` 读取新入库目录。

是否修改共享文件：是，注册了上传路由、增加公共导航入口，并把数据管理页原有
无行为的“上传数据”按钮链接到 `/ingestion/`。LXP 合并数据管理页面时应保留该
链接，不要复制上传业务代码。

下一步建议：先用一个真实但可备份的小型 LeRobot v3.0 ZIP 做人工验收；随后由
`lxp_docker` 在数据管理列表中扫描 `storage/raw/` 并关联文件式上传记录。

日期：2026-10-19

开发者/分支：Heisan / `hs_docker`

完成内容：新增 `deploy/compose.lan.yaml`，在不改变默认本机绑定的前提下，支持
显式绑定可信局域网 IPv4 地址；补充启动、验证、停止和 UFW 最小放行说明。

主要修改文件：`deploy/compose.lan.yaml`、`deploy/README.md`、`readme.md`。

执行的验证：默认模式实际监听 `127.0.0.1:8000` 且内网地址拒绝连接；局域网模式
实际监听 `192.168.43.7:8000`，容器健康，`/dashboard/` 返回 HTTP 200；两种
Compose 组合均通过 `docker compose config`。

未完成或已知问题：UFW 已启用，但规则详情需要本机 sudo 密码；仍需使用另一台
位于 `192.168.43.0/24` 的真实机器完成最终访问测试，必要时按部署文档添加仅限
该网段的防火墙规则。

是否修改共享文件：修改了 `readme.md`，只增加局域网部署入口说明；未修改应用
页面和数据管理业务。

下一步建议：另一台机器访问 `http://192.168.43.7:8000/`；如果失败，先检查
`sudo ufw status verbose`，不要开放不限制来源的 8000 端口。

日期：2026-10-19
开发者/分支：共同基线 / `main`  
完成内容：Platform v2 目录重构、单容器开发环境、`storage/` 文件存储边界以及
协作规范。  
主要修改文件：`app/`、`storage/`、`Dockerfile`、Compose 文件、项目 README。  
执行的验证：容器健康；`/dashboard/`、`/data/`、`/qc/` 返回 HTTP 200。  
未完成或已知问题：页面仍以静态演示数据为主；上传、真实扫描、导出、质检等
业务尚未完整迁移。  
是否修改共享文件：是，已形成当前 `main` 基线。  
下一步建议：双方分别在 `hs_docker` 与 `lxp_docker` 完成上述任务，避免交叉修改。

## 七、给双方 Codex 的启动提示

每次开启新的 Codex 对话，可先发送：

```text
请先完整阅读仓库根目录 AGENTS.md、docs/COLLABORATION.md、readme.md，以及我所
负责模块中的 README。先报告当前 Git 分支、工作区状态、我的负责范围和可能冲突
的共享文件，再开始修改。不要修改另一位开发者的负责范围，不要直接推送 main。
```
