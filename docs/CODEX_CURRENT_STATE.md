# HKC 数采平台当前状态

更新日期：2026-11-25

本文是当前代码事实的唯一状态说明，不记录旧版本开发流水账。开发规则见
`AGENTS.md`，实际操作见 `how_use.md`。

## 1. 当前基线

- 当前检出的开发分支：`integration/hs-lxp-v3.2.1-cq`；
- Web 入口：`app.main:app`；
- 平台形态：FastAPI 应用容器 + MySQL 8.4 容器；
- 文件格式：当前真实业务以 LeRobot v3 为主；
- 文件存储：数据集、报告、导出和模型位于 `storage/`；
- 管理元数据：MySQL；旧 SQLite 不再读取或迁移；
- 训练：平台通过宿主执行器调用模型专属 Docker；X-VLA、π₀ 与 SmolVLA 已接入；
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
| `/ingestion/` | ingestion | VLA/WAM/EGO LeRobot v3 ZIP 上传；EGO 支持分块续传，可清空上传记录 |
| `/api/v1/collector/*` | collector | 机器人 Token 预检、上传、防重复和改绑确认 |
| `/qc/` | quality | VLA/WAM/EGO 方案模板、Doctor、机器人专项规则、VLM、人工审核和报告 |
| `/annotation/` | annotation | Episode/task/Subtask 标注和完整派生发布 |
| `/preprocess/` | processing | 筛除、拆分、合并、重排、去重和视频裁剪 |
| `/export/` | conversion/export | HDF5 转换和 DS/CV 导出 |
| `/training/` | training | VLA 训练：X-VLA、π₀ 与 SmolVLA 任务、离线评估、模型包和任务回收站 |
| `/training/lawam` | training | 独立 WAM 训练：通过 LaWAM Docker 执行器提交任务、评测，查看状态、日志和训练指标曲线；新任务当前以 LaWAM 预训练权重为起点 |
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
  → 匹配数据集类别与 robot_type 的质检方案
  → Doctor / 机器人专项规则 / 可选 VLM
  → 人工审核
  → 可用
  → 标注、处理或数据定义产生新的不可用 DS
```

规则：

- 新 DS 使用 `DS000001` 形式的稳定编号；
- 新发布目录为 `storage/data/<DS 编号>/`；
- 上传时选择 VLA/WAM 类别；两类数据集共用编号、存储和质检状态，训练入口分别筛选；
- 新 EGO 上传只接受完整 LeRobot v3 ZIP，每个 `part-*` 单独登记为一个 DS；
  `meta/`、`data/`、`videos/` 保存在 `data/<DS 编号>/`，`provenance/` 原样保留；
  入库标记为“不可用、待质检”，可选择匹配 `human_egocentric` 的 LeRobot 质检方案；
- EGO LeRobot 详情页按 Episode 浏览任务、时长和对应视频，所选片段按需读取
  `observation.state` / `action` 数值曲线并比较两列；曲线表示原始向量，不标为关节角度；
- EGO 没有固定单包大小上限，仍受服务器剩余磁盘空间和压缩包安全检查约束；
  浏览器采用分块续传，VLA/WAM 继续使用现有单包上限；
- 旧苹果格式 EgoDex DS 保留历史详情读取，但不再接受新上传，也不进入 LeRobot 处理流程；
- 清空最近上传记录只删除 `system/upload-records/` 的上传记录，不删除已登记 DS；
- 旧 `raw/` 和 `derived/` 目录映射继续可用，但不自动迁移；
- 数据集显示名称唯一，忽略首尾空格和英文大小写；
- 回收站中的 DS 仍占用名称，永久清除后才可复用；
- 活跃质检方案名称唯一；
- 质检方案固定绑定 VLA、WAM 或 EGO 类别以及 robot_type；
- 源 DS 永远不原地修改；
- 普通删除进入回收站，永久清除前检查派生、转换和训练引用；
- WAM 数据集的删除还会检查 LaWAM 训练任务引用；
- 机器人页保留回收站 DS 的历史关联并显示回收状态，永久清除后关系才消失。
- 数据集、VLA 训练和 WAM 训练的普通删除只进入各自回收站；永久删除只有在服务器
  文件、执行器状态和 MySQL 记录均清除后才返回成功，引用未清除或任一清理失败时
  保留回收站记录以便处理依赖或重试。

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

## 7. 质检规则边界

创建质检方案时先选择 VLA、WAM 或 EGO 模板。模板同时初始化 Doctor、机器人专项规则
和 VLM；可以选择同类别参考数据集读取字段、维度、FPS、视频字段和分辨率建议值。
参考数据只提供初始值，用户仍需按采集协议、机器人接口、模型输入和正常样本标定结果
确认标准。

“机器人专项规则”首版提供六类可重复添加的规则：

- 字段、类型与实际维度；
- 时间戳完整性与实际 FPS；
- 多模态实际时长对齐；
- 数值有效性、范围与突变；
- 数值字段冻结；
- 视频规格、曝光与冻结。

规则同时读取 `info.json` 声明和实际 Parquet/MP4 内容。字段维度逐帧检查；FPS 根据
实际时间戳和视频 PTS 测量；数值范围、突变、冻结按实际行检查；视频规则实际解码并
按设定间隔检查分辨率、曝光和冻结。报告分别展示方案标准、数据集声明、实际测量、
检查覆盖率和异常证据。

EGO 模板包含 `observation.state` 和 `action` 的维度、数值有效性及冻结规则。阈值不在
代码中按数据集类别强制统一：同一类别下不同采集设备和任务可以创建不同方案版本。
当前只接受配置结构 v2；上线前删除的旧方案、旧任务和旧报告不提供兼容解释器。

## 8. 训练边界

平台容器只负责任务管理、数据预检、状态展示和执行器通信。宿主执行器负责：

- 固定数据快照；
- 调用独立 X-VLA 训练容器；
- 保存日志、checkpoint、离线损失和动作 MAE；
- 校验并登记模型包。

当前离线指标不等于真实机器人任务成功率。执行器离线或训练环境未部署时，平台不能
提交真实训练任务。部署说明见 `deploy/TRAINING_EXECUTOR.md`。

## 9. 当前未完成或受限

- 没有 HTTPS、角色权限、公开注册和公网防护；
- 没有真实机器人在线控制；
- 没有真实机器人任务成功率评估；
- 质检、转换和部分处理仍受单应用进程能力限制；
- 不支持 LeRobot v2、ROS bag、MCAP、RLDS 等完整导入流程；
- 数据定义不能创建新的图像、视频或深度媒体内容；
- 分布式采集没有断点续传和常驻自动监听。

## 10. 当前尚未提交的改动

截至 2026-11-25，本工作区已实现但尚未提交或推送，并已部署到重庆服务器：

- VLA/WAM/EGO 整体质检方案模板和可选参考数据集；
- 六类可编辑、可重复添加的机器人专项规则；
- 对实际 Parquet 行、时间戳和 MP4 内容的检查；
- 标准、声明、测量、覆盖率和异常证据的统一报告；
- 质检方案数据集类别、模板和模板版本的 MySQL 结构；
- 对应集成测试和本状态说明。

已有重复名称不会被自动改名或删除；测试数据可按需要重置。

## 11. 最近验证

- 隔离 MySQL 下完整测试：`197 passed`；
- 全仓 Ruff：通过；
- `app/static/js/quality.js` Node 语法检查：通过；
- 开发镜像 `hkc-data-platform:v3.1` 构建：通过；
- 重庆服务器镜像 `hkc-data-platform:v3.2.1-cq-purge-safe` 构建并部署：通过；
- 重庆服务器 MySQL 迁移版本为 9，平台和 MySQL 容器健康检查：通过；
- 重庆服务器旧质检方案、版本、任务和审核记录已在备份后清空；
- 重庆服务器 `DS000017` 及其两个 WAM 回收站任务已永久删除，目录和关联记录残留检查：通过；
- 浏览器登录后的人工验收尚未执行。

这些结果包含本机自动化验证和重庆服务器部署验证，不等同于登录后的浏览器人工验收。
