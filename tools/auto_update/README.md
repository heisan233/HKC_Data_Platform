# 一次性批量采集上传工具

该工具在采集完成后运行一次：扫描多个 LeRobot v3 文件夹或 ZIP，顺序上传，然后
退出。它不是后台服务，也不会持续监视目录。

## 1. 环境

要求 Ubuntu/Linux、Python 3、curl 和可访问的平台地址。

```bash
cd tools/auto_update
cp config.env.example config.env
chmod 600 config.env
```

配置：

```dotenv
HKC_SERVER=http://PLATFORM_HOST:PORT
HKC_TOKEN_FILE=$HOME/.config/hkc-collector/RB000001.token
HKC_AUTO_SCAN_DIR=/path/to/datasets
HKC_RETRY_ATTEMPTS=3
```

Token 文件建议：

```bash
install -d -m 700 "$HOME/.config/hkc-collector"
chmod 600 "$HOME/.config/hkc-collector/RB000001.token"
```

不要提交 `config.env` 或 Token。

## 2. 数据放置

默认扫描 `datasets/` 第一层：

```text
datasets/
├── dataset-a/       完整 LeRobot v3 文件夹
├── dataset-b.zip    完整 LeRobot v3 ZIP
└── dataset-c/
```

每个直接子文件夹或 `.zip` 视为一个独立数据集；不会递归把更深层目录当成新数据集。

显示名称规则：

- 文件夹：使用文件夹名；
- ZIP：使用去掉 `.zip` 的文件名；
- 第二版没有 `--name` 参数；
- 需要自定义名称时先重命名文件夹或 ZIP；
- 名称与平台现有 DS 重复时会被拒绝。

## 3. 执行

扫描配置目录：

```bash
./upload_once.sh
```

直接指定来源：

```bash
./upload_once.sh /path/to/dataset-a /path/to/dataset-b.zip
```

工具会依次：

1. 验证 Token 和机器人状态；
2. 检查 `meta/info.json` 与 `robot_type`；
3. 将文件夹临时打包到 `temporary/`；
4. 计算 SHA-256 并调用预检；
5. 上传 ZIP；
6. 成功或同 RB 已存在时写本地标记；
7. 输出汇总并退出。

网络错误和服务端 5xx 按 `HKC_RETRY_ATTEMPTS` 重试。普通单项失败后继续下一个；
Token 无效或机器人停用会终止剩余队列。

## 4. 上传标记

文件夹成功后写：

```text
dataset-a/.hkc-uploaded.json
```

ZIP 成功后写：

```text
dataset-b.zip.hkc-uploaded.json
```

标记包含平台地址、RB 和 DS。下次对同平台、同 RB 运行时，会在打包和 SHA-256 前
跳过。

测试平台被重置后，服务器 DS 已消失，但采集机标记仍存在。清除标记：

```bash
./clear_upload_markers.sh
```

指定目录：

```bash
./clear_upload_markers.sh /path/to/datasets
```

该命令只删除 `.hkc-uploaded.json`，不删除数据集文件、ZIP 或服务器 DS。

## 5. 重复与类型

- 同 RB、相同 ZIP：平台返回已有 DS；
- 不同 RB、相同 ZIP：工具报告冲突，不自动改绑；
- 机器人类型不一致：拒绝；
- ZIP 缺少类型：当前工具要求先补齐，避免无人值守确认；
- 上传成功：自动关联 RB，状态为不可用、待质检，不自动启动质检。

## 6. 当前限制

- 没有常驻监听或 systemd 服务；
- 没有断点续传、并发和本地 SQLite 队列；
- 文件夹每次实际上传前都临时打包；
- 工具依赖调用 curl 完成流式上传；
- 只支持可信内网 HTTP；
- 只支持完整 LeRobot v3 文件夹或 ZIP。
