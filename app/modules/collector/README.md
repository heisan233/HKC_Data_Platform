# 分布式采集服务端

本模块提供机器人采集端使用的 Bearer Token API，前缀为 `/api/v1/collector`。它不
使用浏览器 Cookie。

## 接口

| 接口 | 用途 |
| --- | --- |
| `GET /profile` | 验证 Token 并返回所属 RB 摘要 |
| `POST /uploads/preflight` | 上传前检查类型、摘要和重复情况 |
| `POST /uploads` | 接收一个完整 LeRobot v3 ZIP |
| `POST /uploads/rebind` | 确认跨机器人重复数据的改绑 |

## 规则

- 每台机器人最多一个有效 Token；
- 停用机器人立即不能上传；
- ZIP `robot_type` 必须与机器人档案一致；
- 同一 RB 的相同 SHA-256 返回已有 DS；
- 不同 RB 的相同 ZIP：类型不同拒绝，类型相同需要人工确认改绑；
- 成功上传后自动分配 DS、关联 RB，并保持不可用、待质检；
- 不自动启动质检；
- 摘要 claim 保存在 `storage/system/collector-claims/`。

服务端一次只接收一个 ZIP。文件夹打包、批量扫描、顺序上传、重试和本地标记由
`tools/auto_update/` 实现；旧人工单 ZIP 工具位于 `tools/collector_v1/`。

当前只适用于可信内网 HTTP，没有 HTTPS、断点续传或常驻客户端。
