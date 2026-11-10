# 分布式采集模块

`collector` 提供给已登记机器人调用的机器上传 API。它不使用浏览器 Cookie，而是读取
`Authorization: Bearer <Token>`；Token 由 `app/db/robot_tokens.py` 管理。登录中间件只
放行该 URL 前缀，模块路由仍会逐个请求验证 Token 和机器人状态。

## 第一版接口

- `GET /api/v1/collector/profile`：检查 Token 并返回 RB 编号、名称、类型和状态；
- `POST /api/v1/collector/uploads/preflight`：检查 SHA-256、大小、机器人类型和重复；
- `POST /api/v1/collector/uploads`：接收一个完整 LeRobot v3 ZIP；
- `POST /api/v1/collector/uploads/rebind`：同类型另一台机器人确认真实归属后改绑原 DS。

上传成功后复用 `ingestion.service.receive_dataset_upload` 完成分块保存、ZIP 安全检查、
结构校验、解压和 DS 发布，再自动关联 Token 所属 RB。新 DS 保持不可用、待质检，
不会自动启动质检。

## 防重复和存储

精确重复以完整 ZIP 的 SHA-256 判断。运行记录写入
`storage/system/collector-claims/<SHA-256>.json`，只包含摘要、DS、RB 和处理状态；
不保存 Token 或 ZIP 内容，也不新增 MySQL 上传任务表。文件锁和原子替换用于避免同一
摘要并发提交创建两个 DS。

客户端实现和操作说明位于根目录 `tools/README.md`。第一版只支持可信内网 HTTP 的
单 ZIP 上传；文件夹、批量目录、断点续传、自动扫描、心跳和公网 HTTPS 尚未实现。
