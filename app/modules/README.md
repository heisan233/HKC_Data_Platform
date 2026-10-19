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

目前多数模块只有从旧原型迁移来的 `router.py`。其中的数据列表主要是写在
Python 文件里的演示常量，只用于显示现有页面，并不表示扫描、质检、处理、转换
或训练功能已经完成。

`ingestion` 已实现第一版：接收一个完整的 LeRobot v3.0 ZIP，分块保存，执行
压缩包安全检查和最低目录结构检查，然后写入 `storage/raw/`。这只是“上传入库”
而不是完整数据质检。`conversion` 等其他目录仍是边界占位。

`annotation` 已实现真实 Episode/task/Subtask 标注：从 LeRobot v3 Parquet 读取
任务表、Episode 和视频索引，网页播放真实相机视频，可修改总体 task、Episode
执行结果以及 Subtask 时间段、描述、动作类型、对象和结果。正式发布时复制完整
源数据集；task 修改同步到 LeRobot 核心文件，扩展标注写入副本的
`meta/annotations/` 旁路 Parquet，校验后进入 `storage/derived/`。它不会原地修改
`storage/raw/` 或既有派生版本。任务表遵循 LeRobot 官方约定：自然语言 task 是
Pandas DataFrame 的 `task` 索引，`task_index` 是普通列；Episode 帧数读取官方
`length` 字段。读取端同时兼容平台早期使用的普通 `task` 列和 `num_frames`。
