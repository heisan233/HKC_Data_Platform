# LaWAM 平台执行器

平台通过私有 Unix socket 连接 `deploy/lawam_agent.py`。该宿主进程只接受固定的
LaWAM 训练、LIBERO 评测、状态、日志和取消操作，按次调用
`/srv/hkc-ai/models/LaWAM/docker/compose.yaml` 中的独立容器。

服务器的私有环境文件 `~/.config/hkc-lawam-agent.env` 至少配置：

```dotenv
HKC_LAWAM_PYTHON=/usr/bin/python3
HKC_LAWAM_SCRIPT=/path/to/platform/deploy/lawam_agent.py
HKC_LAWAM_STORAGE=/path/to/platform/storage
HKC_LAWAM_COMPOSE=/srv/hkc-ai/models/LaWAM/docker/compose.yaml
HKC_LAWAM_SOCKET_DIR=/path/to/private/lawam-control
HKC_LAWAM_GOAL_EPISODES=/path/to/goal_episodes.json
HKC_LAWAM_GPU_ID=0
HKC_LAWAM_VLM_CONTAINER=
```

`HKC_LAWAM_VLM_CONTAINER` 仅填写管理员固定配置的容器名。只有用户在表单勾选
“训练前停止 vLLM”时，执行器才尝试停止该容器。目标目录必须在支持 Unix socket
的 Linux 文件系统上，权限为 0700；执行器生成的 token 为 0600。

在部署环境文件中设置 `HKC_LAWAM_AGENT_SOCKET_DIR` 指向同一私有目录，并在现有
Compose 组合后叠加 `deploy/compose.lawam-agent.yaml`。平台只读挂载 socket 与 token。

安装用户服务：

```bash
install -d -m 700 ~/.config/systemd/user
cp deploy/hkc-lawam-agent.service ~/.config/systemd/user/
chmod 600 ~/.config/hkc-lawam-agent.env
systemctl --user daemon-reload
systemctl --user enable --now hkc-lawam-agent.service
```

训练任务和评测记录位于 `storage/training-runs/lawam/WAM*/`。训练容器的输出目录为
`storage/training-runs/lawam/<任务编号>/out/`；评测录像位于对应任务的 `eval/`
目录。训练时执行器把所选平台 WAM 数据集设置为 `LAWAM_DATASET_ROOT`，Compose 将其
只读挂载到 `/datasets/selected`；LaWAM 预训练基座及所选任务的评测检查点只读挂载，容器禁用网络。
执行器按 GPU 空闲显存排队，并与平台 VLA 执行器使用同一物理 GPU；正式并发调度
仍需统一资源锁，当前运行前以显存门槛防止明显冲突。
