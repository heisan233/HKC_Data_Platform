# 数据定义模块

`semantics` 同时提供两类能力：

1. 只读展示平台当前真实支持的 LeRobot v3 文件层级与关键字段；
2. 在 SQLite 中维护场景、任务、动作和对象四类受控词表。

每条词表记录使用 `SD000001` 形式的稳定编号，并包含类别、机器编码、显示名称、
说明、示例、排序值和启用状态。机器编码创建后不可修改，记录不能永久删除，只能
停用和恢复。

## 当前边界

- 词表属于平台管理元数据，不写入 `storage/raw/` 或 `storage/derived/`；
- 当前不会自动替换 LeRobot `task` 原文，也不会修改已有 Subtask 标注；
- 场景分类已经纳入本模块，不恢复独立情景管理页面；
- 当前没有把机器人档案、数据集和词表建立外键；
- 后续若要让上传或标注强制选择词条，应先设计兼容旧数据的映射与迁移方案。

## 文件职责

- `router.py`：页面、筛选与 JSON API；
- `schemas.py`：新增和编辑请求校验；
- `service.py`：类别说明、LeRobot v3 结构参考和业务调用；
- `app/db/semantics.py`：SQLite 表与稳定 SD 编号；
- `app/templates/semantics.html`：页面结构；
- `app/static/js/semantics.js`：新增、编辑、停用和恢复交互；
- `app/static/css/semantics.css`：页面样式。
