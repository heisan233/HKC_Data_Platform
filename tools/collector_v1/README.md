# 分布式采集端工具（第一版，保留兼容）

服务器管理员使用的清空测试环境脚本位于 `tools/server/`，说明见
`tools/server/README.md`。该目录不需要复制到机器人采集设备。

这个目录可以单独复制到 Ubuntu/Linux 采集电脑。第一版一次上传一个完整的
LeRobot v3 `.zip`，采集端只需要 Python 3 和 `curl`，不需要安装平台 Python 依赖。

## 1. 获取机器人 Token

先在平台“机器人管理”登记机器人。平台会自动生成 Token，并在创建成功弹窗中显示
一次完整值；关闭后网页只展示状态和前缀。完整 Token 仍可由平台部署机终端查看。
将它保存到采集电脑的私有文件；不要写进脚本、Git 仓库或聊天记录。

采集电脑示例：

```bash
mkdir -p "$HOME/.config/hkc-collector"
chmod 700 "$HOME/.config/hkc-collector"
printf '%s\n' '这里粘贴完整Token' > "$HOME/.config/hkc-collector/RB000001.token"
chmod 600 "$HOME/.config/hkc-collector/RB000001.token"
```

## 2. 上传一个 ZIP
<!-- 本機127.0.0.1:18001 -->
```bash
./tools/collector_v1/hkc_upload.sh ./dataset.zip \
  --server http://192.168.0.163:18000 \
  --token-file "$HOME/.config/hkc-collector/RB000001.token"
```

可选参数：

- `--name "数据集名称"`：省略时使用 ZIP 文件名；
- `--description "备注"`：写入上传记录；
- 也可以不传 `--token-file`，改用临时环境变量 `HKC_ROBOT_TOKEN`。

工具会依次执行：验证 Token、读取 ZIP 的 `meta/info.json`、核对机器人类型、计算
SHA-256、查询平台是否已有相同 ZIP，最后才用 `curl` 流式上传。它不会一次把大文件
读入内存。

## 3. 确认规则

- ZIP 的 `robot_type` 与机器人档案一致：正常上传；
- ZIP 缺少 `robot_type`：终端明确显示档案类型并要求确认；
- 两边类型不一致：直接拒绝，并显示两个值；
- 同一机器人再次提交完全相同 ZIP：返回原 DS，不创建重复数据；
- 另一台同类型机器人提交相同 ZIP：询问数据真实归属，确认后只改绑原 DS；
- 另一台不同类型机器人提交相同 ZIP：拒绝；
- 停用机器人不能使用 Token 上传。

上传成功后平台自动分配 DS 编号、关联 Token 所属机器人，并标记为“不可用、待质检”；
不会自动启动质检。

## 4. 连通性检查

`curl_profile_example.sh` 是只读的最小 curl 示例。先设置：

```bash
export HKC_SERVER=http://192.168.0.163:18000
export HKC_TOKEN_FILE="$HOME/.config/hkc-collector/RB000001.token"
./tools/collector_v1/curl_profile_example.sh
```

`collector.env.example` 只是变量模板，不包含真实凭证。第一版使用可信内网 HTTP；
不要把端口直接暴露到互联网。文件夹上传、批量目录、断点续传、自动扫描和心跳不在
第一版范围内。

也可以复制并加载变量模板，这样每次上传不用重复写地址和 Token 文件位置：

```bash
cp ./tools/collector_v1/collector.env.example ./tools/collector_v1/collector.env
# 编辑 collector.env 后：
set -a
. ./tools/collector_v1/collector.env
set +a
./tools/collector_v1/hkc_upload.sh ./dataset.zip
```

`tools/collector_v1/collector.env` 含设备配置，不应提交 Git。第二版批量扫描工具见
`tools/auto_update/README.md`。
