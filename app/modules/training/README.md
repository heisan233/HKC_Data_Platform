# 模型训练

训练模块负责平台侧任务管理，不把 CUDA 或模型训练依赖装入平台容器。

## 架构

```text
平台应用
  → Unix socket（推荐）或受控 HTTP runner
  → 宿主训练执行器
  → 模型专属 Docker（X-VLA、π₀ 或 SmolVLA）
  → storage/training-runs 与 storage/models
```

生产环境推荐 Unix socket，并通过 `deploy/compose.training-agent.yaml` 只读挂入平台。

## 任务流程

1. 选择已审核可用的 LeRobot v3 DS；
2. 训前检查数据快照、任务、动作维度和视频；
3. 创建固定来源快照的 TR 任务；
4. 执行器启动模型专属训练容器；
5. 持久化日志、checkpoint 和状态；
6. 对留出 Episode 计算前向损失；X-VLA 另计算动作 MAE；
7. 校验并登记模型包。

离线损失和动作 MAE 仅用于链路与模型对比，不代表真实机器人任务成功率。
π₀ 使用独立的 LeRobot v3 容器和基座权重，当前固定为 expert-only 微调、
batch size 1。若抽样发现 `action` 与同帧 `observation.state` 完全相同，
训前检查提示核对采集时序；这项检查本身不能确定动作语义。

SmolVLA 使用独立 Docker、只读基座和本地 SmolVLM2 配置与 tokenizer。执行器为每个
任务生成私有配置副本，将公开仓库引用改为本地路径，并保持基座文件不变。当前采用
expert-only 微调；训练、评估和打包期间容器均禁网。

## 页面能力

- 查看执行器、数据集和 VLM 状态；
- 创建、取消和重试任务；
- 查看日志、进度、评估指标和 Episode 动作曲线；
- 下载已验证模型包；
- 任务移入回收站、恢复和永久清理；
- 已登录用户可通过受限执行器启停配置的 VLM 服务。

## 边界

- 执行器离线时不能提交真实训练；
- 运行中的任务不能直接删除；
- 数据集和基座权重只读挂载；
- 训练容器默认禁网并限制可写目录；
- 模型环境、GPU、缓存和权重不进入平台镜像；
- 平台不会控制真实机器人进行在线评测。

部署见 `deploy/TRAINING_EXECUTOR.md`。
