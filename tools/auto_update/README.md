# 一次性扫描上传工具（第二版）

该目录可整体复制到 Ubuntu 22.04 采集电脑。工具只依赖 Python 3.10+ 和 `curl`：
每次启动后扫描一次、按顺序上传、输出汇总并退出，不安装后台服务，也不会持续监听。

## 1. 初次配置

把机器人完整 Token 保存到私有文件（权限建议为 `600`），然后建立本地配置：
采集电脑示例：

```bash
mkdir -p "$HOME/.config/hkc-collector"
chmod 700 "$HOME/.config/hkc-collector"
printf '%s\n' '这里粘贴完整Token' > "$HOME/.config/hkc-collector/RB000001.token"
chmod 600 "$HOME/.config/hkc-collector/RB000001.token"


```bash
cd tools/auto_update
cp config.env.example config.env
chmod 600 config.env
```

编辑 `config.env`：
<!-- 本機127.0.0.1:18001 -->
```bash
HKC_SERVER=http://192.168.0.163:18000
HKC_TOKEN_FILE=$HOME/.config/hkc-collector/RB000001.token
HKC_AUTO_SCAN_DIR=/实际路径/tools/auto_update/datasets
HKC_RETRY_ATTEMPTS=3
```

`config.env`、Token、待上传数据和上传标记都不会提交 Git。

## 2. 推荐工作方式：扫描父目录一次

把每个完整 LeRobot v3 文件夹或 ZIP 放到 `datasets/` 的第一层，例如：

```text
datasets/
├── collection-001/          一个完整数据集，内含 meta/info.json
├── collection-002.zip       另一个完整数据集
└── collection-003/          第三个完整数据集
```

采集和文件复制全部结束后执行：

```bash
./upload_once.sh
```

工具不会递归把一个数据集的内部目录误当成多个数据集。它会逐个核对 LeRobot v3、
`robot_type` 和平台 Token 对应机器人，再顺序上传。网络错误或平台 5xx 最多尝试三次；
一个数据集格式错误不会阻止后面的数据集，但 Token 无效或机器人停用会终止整批。

## 3. 直接指定文件夹或 ZIP

无需移动到 `datasets/`：

```bash
./upload_once.sh /data/run-001 /data/run-002.zip
```

文件夹会先打包到 `temporary/`，上传结束后删除临时 ZIP。打包内容保留数据集根目录，
不会包含 `.hkc-uploaded.json`。原数据集不会被移动或删除。

## 4. 已上传标记

上传成功或平台确认完全相同的数据已存在后：

- 文件夹：写入 `<数据集>/.hkc-uploaded.json`；
- ZIP：写入 `<文件名>.zip.hkc-uploaded.json`。

标记保存 DS 编号、RB 编号、平台地址和上传时间。下次运行时，只有平台地址和 RB
编号均与当前配置相同时才直接跳过，且跳过发生在本地打包和 SHA-256 之前。删除标记
会允许再次检查；平台的 SHA-256 防重复机制仍会避免创建重复 DS。

不同机器人遇到完全相同数据且需要人工确认归属时，本工具会把该项记为失败，不自动
改绑。请使用第一版人工工具或网页核对后再处理。

## 5. 退出结果

末尾会显示：发现、新上传、平台已存在、标记跳过、失败数量。

- 退出码 `0`：全部成功/跳过，或没有数据；
- 退出码 `1`：至少一个数据集失败；
- 退出码 `2`：配置、Token 验证或扫描目录无法使用。

失败项不写标记，修复后再次运行即可。第二版不包含常驻扫描、systemd、断点续传、
并发上传或本地 SQLite 队列。
