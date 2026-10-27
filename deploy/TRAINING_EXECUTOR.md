# X-VLA 宿主机训练执行器

执行器与平台容器分离，使用已经存在且通过 GPU 检查的 LeRobot Python；启动脚本
**不会安装、升级或修改 Conda 包**。它只接受 X-VLA 基座微调和内置预设，不接受
网页传入任意路径、命令或 shell 参数。平台容器只通过单独挂载的 Unix socket
调用执行器。本服务器数据盘是 exFAT，不能保存 socket/0600 令牌；通信目录必须
位于支持 Unix 权限和 socket 的 ext4/XFS 文件系统。

当前服务器已只读核对过：LeRobot 环境 `/home/z123/miniconda3/envs/lerobot` 可识别
RTX 4090 D；完整基座位于 `/externalDisk/data/model/xvla-base`；tokenizer 副本位于
`/externalDisk/data/hkc_storage/pretrain/bart-large`。这些是**部署机的配置值**，
不是平台通用代码中的固定路径。其他服务器须重新提供各自的三个路径。

在部署机设置以下环境变量，再以存储目录所有者运行
`<lerobot-python> deploy/training_agent.py`：

```text
HKC_TRAINING_STORAGE=<宿主机平台持久化目录>
HKC_TRAINING_MODEL=<X-VLA 官方基座目录>
HKC_TRAINING_TOKENIZER=<可离线加载的 BART tokenizer 目录>
HKC_TRAINING_PYTHON=<已验证的 LeRobot Python 路径>
HKC_TRAINING_MIN_FREE_MB=18000
HKC_TRAINING_SCRIPT=<本项目 deploy/training_agent.py 绝对路径>
HKC_TRAINING_SOCKET_DIR=<宿主机 ext4/XFS 上的私有通信目录>
HKC_TRAINING_WORK_DIR=<宿主机 ext4/XFS 上的临时训练工作目录>
HKC_TRAINING_MIN_WORK_GB=12
HKC_TRAINING_VLM_SERVICE=<管理员允许受控启停的系统级 VLM 服务名>
```

首次启动会在 `<通信目录>/token` 自动生成随机令牌（0600），
**不得放入 Git、通用 Compose 或命令历史**。socket 由执行器创建（0660）。
平台服务发现 socket 和令牌后才显示在线。更换权重
后须重启执行器；启动时对权重计算 SHA-256，基座版本展示为
`xvla-base@<摘要前16位>`。

执行器接收任务后再次确认 DS 目录在授权的 raw/derived 下、`info.json` 摘要未变、
训练/验证 Episode 不重叠。只有 GPU 空闲显存达到阈值时才启动训练，否则保持排队
并说明原因。训练、离线验证、打包分别由执行器跟踪；失败和重启中断记录会保留。
验证指标是留出 Episode 的平均前向损失，不是机器人任务成功率。打包包含 checkpoint
完整模型目录、BART tokenizer、可用的数据集统计、评估报告、来源/参数清单和单文件
SHA-256；平台还验证整个包的哈希。
LeRobot 在保存 checkpoint 后会创建 `last` 符号链接，因此训练工作目录不能位于
exFAT。任务成功且模型包校验完成后，执行器会删除该任务的 ext4 临时工作副本，
保留持久化盘中的模型包、评估、任务状态与日志；失败或中断的工作副本暂时保留供排障。

执行器在每个任务的 ext4 临时目录中建立只读数据视图和基座配置副本：数据/视频
仍引用已登记的原始 DS，旧格式 `tasks.parquet` 的任务文本索引只在视图中修正；
基座权重通过符号链接读取，预处理器配置改用管理员提供的本地 BART tokenizer。
这不会修改原始 DS、基座模型目录或 Conda 环境。相机名称按模型视觉槽位映射，
`head` 优先作为主视角，多余于基座槽位的相机会被明确拒绝。训练 checkpoint 的
预处理器保存该映射，离线评估会使用相同映射。

本服务器的 VLM 是系统级 `qwen3vl.service`。管理员可仅授权运行执行器的账户
执行 `/usr/bin/systemctl start qwen3vl.service` 和
`/usr/bin/systemctl stop qwen3vl.service`；执行器会先以 `sudo -n -l` 核对这两条
权限，未授权时页面按钮仍禁用。不得把 `sudo` 密码写入应用或执行器。
平台在关闭 VLM 前设置持久化的质检队列闸门；若已有 VLM 质检正在运行，就拒绝
关闭并恢复闸门。关闭期间新 VLM 质检保留排队；恢复服务后自动重新提交这些任务。
普通无 VLM 的质检不受此闸门影响。执行器不接受网页提交的服务名或任意命令。

服务器当前使用 `deploy/hkc-training-agent.service` 用户级 systemd 服务，变量在
仅属于该用户的 `~/.config/hkc-training-agent.env`。要保证用户注销后和服务器开机、
用户尚未登录时仍运行，管理员还须为该用户启用 linger。

2026-10-27 已用已登记的 DS000004 完成 `TR000008` 的 100-step 真实 GPU 训练、
固定留出 Episode 全帧前向损失与逐维动作 MAE、曲线 API 和模型包校验。
X-VLA 的内部 20 维输出比该数据集的 14 维动作多出补齐尾部；评估按 checkpoint
声明的 14 维截取后再执行后处理，不把补齐维度计入 MAE。该短任务只验证链路，
不代表模型质量；登录后下载端点的单字节 Range 请求已返回 206，浏览器视觉交互
和完整 3 GB 文件下载仍待人工确认。
