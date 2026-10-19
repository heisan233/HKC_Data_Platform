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
Python 文件里的演示常量，只用于显示现有页面，并不表示上传、扫描、质检、处理、
标注、转换或训练功能已经完成。`ingestion` 和 `conversion` 等目录目前只有边界
占位，后续实现功能时再增加 `router.py`、`service.py` 等文件。
