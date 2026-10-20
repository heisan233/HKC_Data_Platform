# `app/modules` 目录说明

`modules` 按平台的业务能力组织代码，不是按照 HTML 文件数量组织，也不要求与
左侧导航或页面一一对应。

例如“数据管理”页面可能同时调用：

- `datasets`：列出和查看数据集；
- `ingestion`：接收网页上传或机器人 HTTP 上传；
- `quality`：显示或启动质检；
- `processing`：删除 episode、拆分、合并或去重；
- `export`：打包供用户下载。

## 模块内部怎样分工

一个模块以后可以按需要逐步增加以下文件，不必一开始全部创建：

```text
quality/
├── __init__.py       # 表明这是 Python 包，通常不写具体业务
├── router.py         # 接收 HTTP 请求，检查参数并返回 HTTP 响应
├── service.py        # 组织质检流程等真正的业务逻辑
├── schemas.py        # 定义请求和返回的数据结构
└── checkers/         # LeRobot Doctor、自定义规则、VLM 等具体检查器
```

浏览器按钮的调用过程应该是：

```text
HTML/JavaScript 按钮
        -> router.py 接收 HTTP 请求
        -> service.py 执行具体功能
        -> storage/ 读取或写入真实文件
        -> router.py 把结果返回浏览器
```

因此，网页按钮和数据检查应该分开实现；“放在同一个业务模块”不等于“写在同一
个 Python 文件”。

## 当前实际状态

`ingestion` 已实现第一版：接收一个完整的 LeRobot v3.0 ZIP，分块保存，执行
压缩包安全检查和最低目录结构检查，然后写入 `storage/raw/` 并自动登记 DS 编号。
单文件与质检页批量上传复用同一服务。这只是“上传入库”而不是完整数据质检。

`datasets` 通过 `app/db/datasets.py` 管理统一 DS 编号到 Raw/Derived 真实目录的
映射；只在数据管理页展示尚未登记的 Raw 目录，不会自动迁移旧文件。`quality` 的 `service.py` 负责方案版本、任务快照和统一
报告，`robot_rules.py` 与 `vlm.py` 分别执行机器人专项与视频规则，
Doctor 由服务层调用。数据集登记、质检任务和人工复核历史保存在
`storage/system/platform.sqlite`。

`annotation` 已实现真实 Episode/task/Subtask 标注：从 LeRobot v3 Parquet 读取
任务表、Episode 和视频索引，网页播放真实相机视频，可修改总体 task、Episode
执行结果以及 Subtask 时间段、描述、动作类型、对象和结果。正式发布时复制完整
源数据集；task 修改同步到 LeRobot 核心文件，扩展标注写入副本的
`meta/annotations/` 旁路 Parquet，校验后进入 `storage/derived/`。它不会原地修改
`storage/raw/` 或既有派生版本，发布成功后自动登记新的 DS 编号。任务表遵循 LeRobot 官方约定：自然语言 task 是
Pandas DataFrame 的 `task` 索引，`task_index` 是普通列；Episode 帧数读取官方
`length` 字段。读取端同时兼容平台早期使用的普通 `task` 列和 `num_frames`。

`processing` 已实现真实 LeRobot v3 Episode 处理：筛除、按比例或编号拆分、兼容
数据集合并、完整顺序重排，以及基于 frame Parquet 指纹的精确重复检测和去重。
路由只负责请求与任务状态，实际数据构建位于 `service.py`；所有结果先写入
`storage/staging/processing/`，校验后进入 `storage/derived/` 并登记新的 DS 编号，源数据不原地修改。
视频使用 PyAV 按 Episode 边界重新编码。第一版每个输出使用单个 Parquet shard，
重复检测不解码视频做视觉相似度判断，任务由 Web 进程后台执行且不能跨重启续跑。
读取端兼容早期 v3 Episode 元数据中的 `num_frames/task`，并可在缺少
`data/chunk_index`、`data/file_index` 时从 frame Parquet 的 `episode_index`
自动反查文件位置；派生输出会补齐当前标准字段，不修改原始数据。

`conversion` 尚未实现；其他仍使用演示常量的模块不应视为已完成业务功能。
