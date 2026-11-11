# 测试平台重置工具

`reset_platform_data.sh` 会永久清空指定 storage 和 MySQL 私有目录，然后重新创建
空白平台。只允许用于可丢弃的测试环境。

## 会删除

- 所有数据集、报告、导出、训练结果和日志；
- 全部 MySQL 用户、机器人、DS、质检、转换和训练记录。

不会删除：

- 项目源码和 Git 历史；
- Docker 镜像；
- 采集机上的 `.hkc-uploaded.json` 标记。

## 使用

先核对环境文件中的两个目录，再执行：

```bash
tools/server/reset_platform_data.sh --env-file .env --project-name hkc-v31-mysql --compose-file compose.yaml --compose-file compose.dev.yaml --compose-file deploy/compose.local-mysql.yaml --confirm DELETE-ALL-HKC-DATA
```

脚本要求：

- 环境文件、项目名和至少一个 Compose 文件；
- 确认文本必须完全一致；
- storage 与 MySQL 路径不能相同；
- 拒绝根目录、家目录、项目根目录和不在允许范围内的路径；
- 停止 Compose、清空两个绑定目录，再按原组合启动。

执行前建议人工运行：

```bash
docker compose --env-file .env -p hkc-v31-mysql -f compose.yaml -f compose.dev.yaml -f deploy/compose.local-mysql.yaml config
```

确认输出中的宿主挂载路径与准备删除的测试目录一致。

重置完成后：

1. 重新创建平台用户；
2. 重新创建机器人并保存新 Token；
3. 在采集端运行 `tools/auto_update/clear_upload_markers.sh`；
4. 再上传测试数据。

该脚本不可用于需要保留数据的服务器。正式环境应使用备份、迁移和受控恢复流程。
