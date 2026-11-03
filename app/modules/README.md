# `app/modules` 目录说明

`modules` 按平台的业务能力组织代码，不是按照 HTML 文件数量组织，也不要求与
左侧导航或页面一一对应。

`auth` 是全平台共享的登录模块：路由提供登录与退出，中间件保护其他页面/API，
用户和服务端会话元数据由 `app/db/users.py` 写入同一个平台 SQLite。

`robots` 是独立机器人档案模块：每条档案有稳定 RB 编号，支持新增、编辑、停用
和恢复。它还负责把已登记 DS 关联、改绑或解除到具体机器人；数据管理页只读展示
结果。关系只写入 SQLite，不回写 LeRobot 文件，类型不一致时允许保存但必须告警。

`semantics` 是数据定义模块：读取已登记 DS 的 `info.json`、Episode Parquet 和
frame Parquet，区分官方字段、核心字段及平台扩展字段。修改始终生成完整 Derived；
Episode 自定义定义写入 `hkc_extensions.episode_features`，逐帧数值 Feature 同步
写入 `info.json.features` 和所有 frame 分片，并显示空值完整率与训练可用状态。
当前不强制修改已有数据集或标注。

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
上传表单可声明 `robot_type`：填写时在进入 Raw 前覆盖 `meta/info.json`，留空时沿用
包内非空值；包内缺少且表单留空时拒绝入库。
单文件与质检页批量上传复用同一服务。这只是“上传入库”而不是完整数据质检。

`datasets` 通过 `app/db/datasets.py` 管理统一 DS 编号到 Raw/Derived 真实目录的
映射；只在数据管理页展示尚未登记的 Raw 目录，不会自动迁移旧文件。数据集详情
还会只读解析真实 frame Parquet，按 Episode 绘制 `observation.state` 和 `action`
关节曲线；缺失关节名称时按“维度 N”显示，显示抽样不会改变源数据。`quality` 的 `service.py` 负责方案版本、任务快照和统一
报告，`robot_rules.py` 与 `vlm.py` 分别执行机器人专项与视频规则，
Doctor 由服务层调用。数据集登记、质检任务和人工复核历史保存在
`storage/system/platform.sqlite`。

`system` 提供只读系统管理日志页。认证中间件将已登录访问的用户、模块、HTTP
方法、纯路径、状态码和耗时写入 `storage/system/logs/platform.jsonl`；不会记录
查询参数、正文、Cookie 或认证秘密。该日志用于平台操作查看，不替代 Docker 或
Uvicorn 的进程诊断日志。

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

`semantics` 已替换旧受控词表页面，成为真实 Schema 编辑器。第一版 Episode 字段
支持 string/int64/float64/bool 统一值或统一空值；逐帧字段支持数值标量/向量全空
占位，或从 CSV/Parquet 按 `episode_index + frame_index` 导入。核心索引禁止删除，
其他官方 Feature 删除前需要二次确认。

`conversion` 目前提供 LeRobot v3.0 → HDF5 方案，页面按方案选择组织，便于后续增加
其他转换器。服务通过统一 DS 编号读取已登记的 Derived 来源、校验文件清单并记录
指纹，单线程后台生成最多每文件 10 个 Episode 的 HDF5；完成后将临时目录原子
发布到 `converted/<CV 编号>/`，转换结果在数据管理页单独展示，不分配 DS 编号。转换任务使用独立 CV 编号，状态
写入 SQLite；已有 `system/conversion-jobs/` JSON 记录在首次访问时复制入库，原文件
保留。`/export/` 页面提供进度和数据集导出按钮，不提供转换结果详情入口。`export`
服务可将已登记的 Derived 数据集或按 CV 编号定位的转换结果打包到 `storage/exports/`，再供浏览器
下载。视频帧会转换成 JPEG，旁路标注元数据保存在 HDF5 元数据区。
