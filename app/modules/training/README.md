# 模型训练模块

平台端负责已登记 DS 选择、最终 DS 的轻量预检、任务和模型版本元数据、页面/API。
首期仅开放 X-VLA 基座微调；不提供从零训练或 checkpoint 续训。

平台容器不安装 CUDA/LeRobot，也不执行训练命令。`service.py` 通过管理员配置的
Unix socket 与宿主机执行器通信；缺少执行器时模型显示不可用，
不会创建假的“成功”任务。执行器只接受登记的 X-VLA 基座与预设，并允许在执行器
公布的范围内调整 `steps`、`batch_size`、`log_freq`；`num_workers`、模型路径、
命令及其余参数仍固定。训练输出在共享存储的
`training-runs/`，通过校验的模型包在 `models/`。SQLite 只保存任务快照与状态。

新建向导第三步选择执行器已登记的 Conda 训练环境。当前仅有 `lerobot` 一项，
其 Python 路径只保存在执行器配置中，不由网页输入。平台提交时核对环境 ID、
可用状态和模型能力，执行器再次拒绝未登记环境；任务 SQLite 保存 `runtime_id`，
既有任务迁移为当前唯一的 `lerobot` 环境。GPU/VLM 属于执行器所在服务器的共享
资源，不是每个 Conda 环境独占一份。未来新增环境需要单独登记与测试。

任务列表与详情在页面可见时每 5 秒自动请求最新状态；执行器训练期间约每 5 秒读取
实际 LeRobot 日志并记录损失、学习率、梯度范数、Epoch 及 GPU 整卡使用率。
旧任务的日志若包含结构化指标行，执行器启动时会回填真实指标，不推算缺失数据。
图表中的 GPU 趋势是整卡状态，不代表该任务独占。当前离线评估仅对最终 checkpoint
做加载/推理冒烟测试与留出 Episode 平均前向损失，没有训练中验证曲线、动作 MAE 或
真实机器人成功率。

训练任务列表中所有状态都有删除入口。排队或运行中的任务会先请求执行器取消，
在确认任务进入终态后自动移入回收站；取消失败时不标记为待删除。待删除意图持久化在
SQLite，平台下次同步任务状态时继续完成归档。删除仅设置 SQLite 的 `deleted_at`/`deleted_by`，
不会清理执行器日志、训练文件或通过验证的模型版本。默认任务列表不显示回收站记录，
`GET /training/api/tasks?deleted=true` 可查询回收站，`DELETE /training/api/tasks/{id}`
及 `POST /training/api/tasks/{id}/restore` 分别用于移入和恢复。
回收站还可永久删除终态任务：点击“永久删除”立即执行，不再弹出输入任务编号的
确认框。页面请求仍自动附带同编号请求头，接口
`POST /training/api/tasks/{id}/purge` 会核对请求头。平台先请求执行器清理该任务
在 `training-runs/`、`models/` 和私有工作目录中的专属文件，再删除 SQLite 任务记录；
执行器离线或清理失败时保留回收站记录以便重试。永久删除后相关模型版本及下载入口
消失、无法恢复；来源 DS、X-VLA 基座和其他任务不受影响。TR 编号不会复用。

`deploy/training_agent.py` 是独立宿主机执行器，须由现有 LeRobot Python 运行。
具体安装/启动方式与宿主机权限边界见 `deploy/TRAINING_EXECUTOR.md`。

X-VLA 接入会把最终 DS 的相机名称映射到基座模型的视觉槽位；缺少的视角由模型自身
掩码处理。旧数据集若把任务文本保存在 `tasks.parquet` 普通 `task` 列，执行器仅在
临时训练视图中转换为 LeRobot 所需的索引；Raw/Derived 原目录始终不改动。
