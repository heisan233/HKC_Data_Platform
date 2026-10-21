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

数据管理的 `datasets` 模块已扫描 `storage/raw/` 并通过 `app/db/datasets.py`
保存登记编号。`ingestion` 实现了质检页的 LeRobot v3 ZIP 上传与自动登记；
`quality` 的 `service.py` 负责方案版本、任务快照和统一报告，
`robot_rules.py` 与 `vlm.py` 分别执行机器人专项与视频规则，Doctor 由服务层调用。
质检任务及人工复核历史保存在 `app/db/quality.py` 管理的 SQLite 表中。
`export` 从 `derived/` 创建 LeRobot v3 → HDF5 后台转换任务，结果写到
`converted/`，并提供两个区域的数据集压缩包下载。其他多数模块仍只有从旧原型
迁移来的演示路由；独立的 `conversion` 模块尚未启用。
