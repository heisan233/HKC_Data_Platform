# 服务器维护工具

本目录只放部署机/服务器管理员使用的维护脚本，不应复制到机器人采集设备。

## 清空平台测试数据

`reset_platform_data.sh` 会永久删除 `HKC_STORAGE_PATH` 中的数据集、报告、导出、
训练结果和日志，同时删除 `HKC_MYSQL_DATA_PATH` 中的整个 MySQL 数据目录，然后用
指定的 Compose 组合重新创建空白平台。源码、Git 历史和 Docker 镜像不会删除。

本机 MySQL 测试环境：

```bash
tools/server/reset_platform_data.sh \
  --env-file .env \
  --project-name hkc-v31-mysql \
  --compose-file compose.yaml \
  --compose-file compose.dev.yaml \
  --compose-file deploy/compose.local-mysql.yaml \
  --confirm DELETE-ALL-HKC-DATA
```

重庆服务器应将 `--env-file` 换成服务器专用 `server.env`，并传入服务器实际使用的
Compose 文件。不要照抄本机项目名或覆盖文件。脚本会显示最终解析的两个删除路径，
并拒绝根目录、用户家目录、项目源码目录、相同目录及不在允许根路径下的目标。

这是不可恢复操作。执行前应确认不需要 MySQL 逻辑备份和 storage 文件备份。重置后
数据库只有表结构，没有任何用户；必须先通过终端重新创建第一个平台用户。
