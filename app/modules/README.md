# 业务模块

`app/modules/` 按业务能力组织后端代码。

## 代码约定

- `router.py`：请求参数、状态码、页面和 API 响应；
- `service.py`：业务流程、文件处理和外部调用；
- `schemas.py`：Pydantic 请求/响应结构；
- MySQL 查询统一放在 `app/db/`；
- HTML 放在 `app/templates/`，浏览器交互放在 `app/static/`。

复杂业务不得堆入路由或只在 JavaScript 中实现。

## 活跃模块

| 模块 | 职责 |
| --- | --- |
| `auth` | 登录、首次改密、个人中心、退出和访问保护 |
| `dashboard` | 平台真实统计与待办 |
| `robots` | 机器人档案、Token 状态和 DS 关联 |
| `collector` | 机器人 Bearer Token 上传 API |
| `ingestion` | 网页 ZIP 上传和安全入库 |
| `datasets` | DS 登记、详情、报告和回收站 |
| `semantics` | LeRobot v3 Schema 扫描与派生字段 |
| `quality` | Doctor、规则、VLM、人工审核和报告 |
| `annotation` | Episode/task/Subtask 标注和派生发布 |
| `processing` | Episode 处理、去重和视频裁剪 |
| `conversion` | LeRobot v3 → HDF5 |
| `export` | DS/CV 打包与下载 |
| `training` | X-VLA 任务、评估和模型包 |
| `system` | 脱敏访问日志 |

`logs` 和 `models` 是未注册的历史演示模块，不是当前功能入口。

## 通用数据流

```text
浏览器或采集端
  → router
  → service
  → app/db + storage
  → API 或 Jinja2 页面
```

发布数据集的模块必须使用统一 DS 发布服务：

1. 在 `staging/` 构建完整副本；
2. 校验结构和安全边界；
3. 发布到 `data/<DS 编号>/`；
4. MySQL 登记来源、类型和默认质检状态；
5. 必要时继承机器人关联。

源 DS 不允许原地修改。数据集显示名称和活跃质检方案名称不能重复。

## 共享能力

- LeRobot v3 元数据：`datasets/lerobot_v3.py`；
- DS 目录解析与发布：`datasets/service.py`；
- MySQL 仓储：`app/db/`；
- 配置：`app/core/config.py`；
- 模板对象：`app/web.py`。

修改共享能力后必须运行所有相关模块测试；修改 DB 或 DS 发布逻辑时运行完整测试。
