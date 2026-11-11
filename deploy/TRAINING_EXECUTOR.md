# X-VLA 宿主训练执行器

平台容器不包含 CUDA、LeRobot 或 X-VLA。宿主执行器通过私有 Unix socket 接收受限
任务，并调用独立训练 Docker；也保留旧 Conda 运行方式。

## 1. 安全边界

- 平台只能提交预定义训练参数，不能传任意命令或路径；
- 数据集、基座、tokenizer 和执行器脚本只读挂载；
- 任务输出和模型包目录可写；
- Docker 训练容器默认禁网并以执行器 UID/GID 运行；
- socket 目录权限为 0700，token 文件为 0600；
- socket 和 work 位于 ext4/XFS 等 Linux 文件系统；
- GPU、镜像、权重和训练缓存不进入平台应用镜像。

## 2. 环境文件

用户服务读取 `~/.config/hkc-training-agent.env`：

```dotenv
HKC_TRAINING_PYTHON=/usr/bin/python3
HKC_TRAINING_SCRIPT=/path/to/project/deploy/training_agent.py
HKC_TRAINING_STORAGE=/path/to/platform-storage
HKC_TRAINING_MODEL=/path/to/xvla-base
HKC_TRAINING_TOKENIZER=/path/to/tokenizer
HKC_TRAINING_SOCKET_DIR=/path/to/private/control
HKC_TRAINING_WORK_DIR=/path/to/private/work
HKC_TRAINING_DOCKER_IMAGE=hkc-xvla-train:VERSION_TAG
HKC_TRAINING_DOCKER_GPU_ID=0
HKC_TRAINING_MIN_FREE_MB=18000
HKC_TRAINING_MIN_WORK_GB=12
HKC_TRAINING_VLM_SERVICE=
```

`HKC_TRAINING_PYTHON` 用于 systemd 启动执行器；旧 Conda 模式还用它执行训练。
Docker 模式仍需提供可运行 `training_agent.py` 的宿主 Python。

`HKC_TRAINING_DOCKER_IMAGE` 为空时使用 Conda/宿主 Python；当前服务器推荐 Docker。

## 3. 目录与依赖检查

```bash
install -d -m 700 /path/to/socket-dir /path/to/work-dir
test -r /path/to/model-dir/config.json
test -r /path/to/model-dir/model.safetensors
test -r /path/to/tokenizer-dir/tokenizer_config.json
docker image inspect TRAINING_IMAGE_TAG
nvidia-smi
```

执行器用户必须能读取 storage、基座和 tokenizer，能写 training-runs、models 和
work，并有权使用 Docker 与指定 GPU。

## 4. 安装用户服务

```bash
mkdir -p ~/.config/systemd/user
cp deploy/hkc-training-agent.service ~/.config/systemd/user/hkc-training-agent.service
chmod 600 ~/.config/hkc-training-agent.env
systemctl --user daemon-reload
systemctl --user enable --now hkc-training-agent.service
```

查看状态：

```bash
systemctl --user status hkc-training-agent.service
journalctl --user -u hkc-training-agent.service -f
```

如需用户退出后仍运行，由服务器管理员按公司规范启用 linger。

第一次启动会在 socket 目录创建 `agent.sock` 和 `token`。不要人工修改或提交
token。

## 5. 平台连接

服务器环境文件设置：

```dotenv
HKC_TRAINING_AGENT_SOCKET_DIR=/path/to/socket-dir
```

平台启动时叠加 `deploy/compose.training-agent.yaml`。该文件把目录只读挂载到容器
`/run/hkc-training-agent`。

验证：

```bash
systemctl --user is-active hkc-training-agent.service
docker compose --env-file /path/to/server.env -p hkc-vla -f compose.yaml -f compose.server.yaml -f deploy/compose.training-agent.yaml exec platform ls -l /run/hkc-training-agent
```

随后在训练页确认执行器、GPU、基座和运行时状态。

## 6. VLM 启停

可选的 `HKC_TRAINING_VLM_SERVICE` 只能填写合法 systemd service 名称。若需要网页
启停 VLM，管理员应仅授予执行器用户对该单一服务 `start`、`stop` 的免密权限，
不能授予通配符或完整 root 权限。

训练占用 GPU 时不能恢复 VLM。平台当前没有管理员角色，因此所有有效登录用户都能
操作该开关。

## 7. 任务与重启

- 任务输出：`storage/training-runs/TR.../`；
- 模型包：`storage/models/TR...-xvla.tar.gz`；
- 执行器重启时运行中任务标记为 `INTERRUPTED`；
- 平台重启不删除执行器任务文件；
- 取消运行任务时停止对应容器或进程；
- 永久清理只允许已结束且无活动进程的任务。

## 8. 故障排查

执行器离线：

1. 检查用户服务；
2. 检查 socket/token 权限；
3. 检查平台是否叠加训练覆盖；
4. 检查两侧是否指向同一宿主目录。

训练不可用：

1. 检查镜像、GPU 和 Docker 权限；
2. 检查模型与 tokenizer 必需文件；
3. 检查显存和工作盘剩余空间；
4. 检查 storage、work 和模型目录权限；
5. 查看执行器 journal 与任务 `train.log`。

模型环境变化后先验收小任务，再开放正式训练。
