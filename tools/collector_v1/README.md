# 旧版单 ZIP 上传工具

该工具由人工上传一个完整 LeRobot v3 ZIP。新批量流程优先使用
`tools/auto_update/`。

## 准备

```bash
cd tools/collector_v1
cp collector.env.example collector.env
chmod 600 collector.env
```

配置：

```dotenv
HKC_SERVER=http://PLATFORM_HOST:PORT
HKC_TOKEN_FILE=$HOME/.config/hkc-collector/RB000001.token
```

Token 文件权限应为 0600，不得提交 Git。

## 使用

```bash
./hkc_upload.sh /path/to/dataset.zip
```

指定唯一显示名称和备注：

```bash
./hkc_upload.sh /path/to/dataset.zip --name "数据集名称" --description "备注"
```

`--name` 省略时使用 ZIP 文件名。平台要求显示名称唯一，比较时忽略首尾空格和英文
大小写。

也可直接传参数：

```bash
./hkc_upload.sh /path/to/dataset.zip --server http://PLATFORM_HOST:PORT --token-file /path/to/token
```

## 流程

1. 验证 Token 并读取机器人档案；
2. 读取 ZIP 中 `meta/info.json`；
3. 检查机器人类型；
4. 计算 SHA-256；
5. 调用预检接口；
6. 使用 curl 流式上传；
7. 输出 DS 编号和默认状态。

ZIP 缺少 `robot_type` 时会要求终端确认；类型明确不一致时拒绝。跨机器人重复需要
人工确认改绑。

当前仅支持可信内网 HTTP 和单个 ZIP，没有断点续传或批量目录。
