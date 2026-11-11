# 平台工具

`tools/` 中的程序用于采集端或部署终端，不属于网页应用。

| 目录 | 用途 |
| --- | --- |
| `collector_v1/` | 人工上传一个完整 LeRobot v3 ZIP，支持 `--name` |
| `auto_update/` | 一次扫描多个文件夹/ZIP，顺序上传后退出 |
| `server/` | 测试环境永久重置工具 |

推荐新采集流程使用 `auto_update/`。旧单 ZIP 工具仅用于明确指定一个压缩包和显示
名称的场景。

自动上传的显示名称取文件夹名或 ZIP 去扩展名。上传成功会写本地标记；测试平台重置
后可用 `auto_update/clear_upload_markers.sh` 清除标记。

所有真实 Token 和 `config.env` 都必须留在采集机本地并限制权限，不能提交 Git。
当前采集协议是可信内网 HTTP，不适用于公网。
