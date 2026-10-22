# 机器人管理模块

本模块维护独立机器人档案，不连接、控制或远程执行真实机器人。

## 当前真实能力

- SQLite 为每台机器人分配 `RB000001` 形式的稳定编号；
- 登记名称、机器人类型、型号、序列号、状态、网络地址、传感器和备注；
- 支持搜索、状态筛选、新增、编辑、停用与恢复；
- 名称和非空序列号不允许重复；
- 没有永久删除接口，停用记录仍保存在 `platform.sqlite`；
- 页面按相同 `robot_type` 统计数据集数量，但尚未建立数据集到机器人实例的外键。

## 文件职责

- `router.py`：页面、HTTP 参数和 API 状态码；
- `schemas.py`：新增/编辑请求校验；
- `service.py`：机器人列表与同类型数据集统计；
- `app/db/robots.py`：SQLite 表、稳定编号和读写操作；
- `app/templates/robots.html`：页面结构；
- `app/static/js/robots.js`：注册、编辑、停用和恢复交互；
- `app/static/css/robots.css`：机器人页面样式。

## 当前边界

`network_address` 只是档案字段，不代表在线探测结果；`active` 表示“在用”，不是
实时“在线”。修改机器人类型不会回写 LeRobot 数据集的 `meta/info.json`，也不会
改变 SQLite 中已有数据集的 `robot_type`。如果以后需要追踪“哪一台实体机器人采集
了某个数据集”，应另行设计稳定 `robot_id` 关联与迁移方案，而不是根据名称猜测。
