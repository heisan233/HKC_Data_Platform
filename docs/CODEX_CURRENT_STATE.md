# HKC 数采平台当前状态

更新日期：2026-11-11

本文是当前代码事实的唯一状态说明，不记录旧版本开发流水账。开发规则见
`AGENTS.md`，实际操作见 `how_use.md`。

## 1. 当前基线

- 共同开发分支：`integration/hs-lxp-v3.2.0-cq`；
- Web 入口：`app.main:app`；
- 平台形态：FastAPI 应用容器 + MySQL 8.4 容器；
- 文件格式：当前真实业务以 LeRobot v3 为主；
- 文件存储：数据集、报告、导出和模型位于 `storage/`；
- 管理元数据：MySQL；旧 SQLite 不再读取或迁移；
- 训练：平台通过宿主执行器调用独立 X-VLA Docker；
- 网络：仅可信内网 HTTP，没有 HTTPS。

开始工作前：

```bash
git status --short --branch
git log -1 --oneline --decorate
```

## 2. 页面与模块

| 地址 | 模块 | 当前真实能力 |
| --- | --- | --- |
| `/login`、`/account/` | auth | 登录、首次改密、个人中心、退出和会话保护 |
| `/dashboard/` | dashboard | DS、机器人、质检、训练、待办和磁盘只读总览 |
| `/robots/` | robots | RB 档案、停用/恢复、Token 状态和 DS 关联 |
| `/semantics/` | semantics | LeRobot v3 Schema 扫描与派生字段扩展 |
| `/data/` | datasets | DS 总览、详情、关节曲线、报告和回收站 |
| `/ingestion/` | ingestion | 单个/批量 LeRobot v3 ZIP 网页上传 |
| `/api/v1/collector/*` | collector | 机器人 Token 预检、上传、防重复和改绑确认 |
| `/qc/` | quality | Doctor、规则、VLM、人工审核和报告 |
| `/annotation/` | annotation | Episode/task/Subtask 标注和完整派生发布 |
| `/preprocess/` | processing | 筛除、拆分、合并、重排、去重和视频裁剪 |
| `/export/` | conversion/export | HDF5 转换和 DS/CV 导出 |
| `/training/` | training | X-VLA 任务、离线评估、模型包和任务回收站 |
| `/system/` | system | 脱敏、持久化访问日志 |
| `/healthz` | app | 无需登录的存活检查 |

`/scenes/` 已删除。旧 `app/modules/logs/` 和 `app/modules/models/` 是未注册的
历史演示模块；当前系统日志由 `system` 提供，模型训练由 `training` 提供。

## 3. 数据集生命周期

```text
LeRobot v3 ZIP
  → staging 安全解压与结构检查
  → data/<DS 编号>
  → MySQL 登记为不可用、待质检
  → Doctor / 规则 / 可选 VLM
  → 人工审核
  → 可用
  → 标注、处理或数据定义产生新的不可用 DS
```

规则：

- 新 DS 使用 `DS000001` 形式的稳定编号；
- 新发布目录为 `storage/data/<DS 编号>/`；
- 旧 `raw/` 和 `derived/` 目录映射继续可用，但不自动迁移；
- 数据集显示名称唯一，忽略首尾空格和英文大小写；
- 回收站中的 DS 仍占用名称，永久清除后才可复用；
- 活跃质检方案名称唯一；
- 源 DS 永远不原地修改；
- 普通删除进入回收站，永久清除前检查派生、转换和训练引用；
- 机器人页保留回收站 DS 的历史关联并显示回收状态，永久清除后关系才消失。

## 4. 分布式采集

每台机器人最多有一个有效上传 Token。机器人停用后 Token 立即不能上传。

服务端：

- Bearer Token 认证，不使用网页 Cookie；
- 一次接收一个完整 LeRobot v3 ZIP；
- 检查机器人类型和 SHA-256；
- 同 RB 精确重复返回已有 DS；
- 跨 RB 重复按类型和人工确认规则处理；
- 成功后自动分配 DS、关联 RB，并保持不可用、待质检。

客户端：

- `tools/collector_v1/`：人工上传单个 ZIP，支持 `--name`；
- `tools/auto_update/`：一次扫描多个文件夹/ZIP，顺序上传后退出；
- 自动上传显示名称取来源文件夹名或 ZIP 去扩展名；
- 成功后写本地 `.hkc-uploaded.json`，重跑前可跳过打包和摘要计算；
- `clear_upload_markers.sh` 只清理本地标记，不删除数据或服务器记录。

当前没有常驻监听、断点续传、并发、本地 SQLite 队列或 HTTPS。

## 5. MySQL 与文件存储

MySQL 保存：

- 用户、密码摘要和会话；
- DS/RB/CV/TR 编号及关系；
- 可用性、状态标签、质检任务和人工审核；
- 转换、训练和回收站管理元数据。

MySQL 不保存数据集视频、Parquet、报告正文、导出包或模型包。这些内容在 storage。

```text
HKC_STORAGE_PATH     → 容器 /data
HKC_MYSQL_DATA_PATH  → MySQL /var/lib/mysql
```

两个路径必须分开。容器内业务代码只通过 `settings.data_root` 访问 `/data`。

主要 storage 目录：

- `data/`：新 DS；
- `raw/`、`derived/`：历史目录映射；
- `staging/`：临时构建；
- `reports/qc/`：质检报告；
- `converted/`：CV 转换结果；
- `exports/`：导出包；
- `training-runs/`、`models/`：训练输出与模型；
- `trash/`：数据集和报告回收站；
- `system/`：日志、上传记录和 collector claim。

## 6. 数据定义边界

数据定义页是 Schema 编辑器，不是旧受控词表：

- 扫描 `info.json`、Episode Parquet 和 frame Parquet；
- Episode 扩展字段定义写入 `hkc_extensions.episode_features`；
- 支持 string、int64、float64、bool 的 Episode 统一值或空值；
- 支持数值标量/定长向量逐帧 Feature；
- 可从 CSV/Parquet 按 `episode_index + frame_index` 导入；
- 空值字段会明确标记“不满足训练要求”，不会静默填 0；
- 图片、视频和深度媒体字段目前只读，不能新建媒体内容；
- 所有变更发布新 DS。

## 7. 训练边界

平台容器只负责任务管理、数据预检、状态展示和执行器通信。宿主执行器负责：

- 固定数据快照；
- 调用独立 X-VLA 训练容器；
- 保存日志、checkpoint、离线损失和动作 MAE；
- 校验并登记模型包。

当前离线指标不等于真实机器人任务成功率。执行器离线或训练环境未部署时，平台不能
提交真实训练任务。部署说明见 `deploy/TRAINING_EXECUTOR.md`。

## 8. 当前未完成或受限

- 没有 HTTPS、角色权限、公开注册和公网防护；
- 没有真实机器人在线控制；
- 没有真实机器人任务成功率评估；
- 质检、转换和部分处理仍受单应用进程能力限制；
- 不支持 LeRobot v2、ROS bag、MCAP、RLDS 等完整导入流程；
- 数据定义不能创建新的图像、视频或深度媒体内容；
- 分布式采集没有断点续传和常驻自动监听。

## 9. 本地尚未提交的当前改动

截至 2026-11-11，本工作区已实现但尚未提交、推送或部署：

- 回收站 DS 在机器人管理页保留并标记，永久清除后关联才消失；
- 自动上传标记一键清理工具；
- 数据集显示名称和活跃质检方案名称唯一校验；
- 对应集成与工具测试；
- 全部 Markdown 文档精简重写。

已有重复名称不会被自动改名或删除；测试数据可按需要重置。

## 10. 最近验证

- 名称唯一性和相关共享逻辑完整测试：`161 passed`；
- 本次涉及文件 Ruff：通过；
- 全仓 Ruff 仍有 `app/modules/logs/router.py` 中 5 个既有 E741 命名问题；
- 既有 Starlette/AnyIO 上游弃用警告不影响测试通过。

这些结果表示本机自动化测试通过，不等同于浏览器人工验收或重庆服务器验收。
