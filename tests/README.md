# 测试目录

测试按照 `app/modules/` 的业务结构组织。单元测试放在 `tests/unit/`，HTTP 和
文件存储集成测试放在 `tests/integration/`，可安全提交的小型 LeRobot 样本放在
`tests/fixtures/`。真实业务数据集不能放进测试目录。

所有测试使用临时 `HKC_DATA_ROOT` 与独立的 MySQL 测试库。运行测试前须提供
`HKC_TEST_MYSQL_HOST` 和 `HKC_TEST_MYSQL_ROOT_PASSWORD`；可选端口为
`HKC_TEST_MYSQL_PORT`。测试夹具逐个创建和删除 `hkc_test_*` 数据库，不连接正式库。

截至 2026-11-10，分布式采集第二版实现后的全量结果为 `154 passed`，另有一条
Starlette/AnyIO 上游弃用警告；Ruff 0.16.8 全仓库通过。

`tests/integration/test_data_management.py` 使用临时 `storage/` 和测试 MySQL 库测试
旧 Raw 编号登记兼容接口、路径安全、报告关联、统一回收站的移入/恢复/永久清除、
依赖拦截，以及真实 Parquet Episode 关节曲线读取；
不会访问真实业务数据。

`tests/integration/test_auth.py` 使用独立 MySQL 测试库验证无默认账号、密码哈希、
登录 Cookie、退出、首次强制改密、个人资料、全部会话失效、停用/启用和多用户并发
会话。`test_mysql_backend.py` 还验证旧版用户升级后密码不变且不会被强制改密。其他
业务测试通过 `tests/conftest.py` 注入测试用户，不会读取或修改旧
`storage/system/platform.sqlite` 中的账号与会话。

`tests/integration/test_system.py` 验证真实访问日志落盘、4xx/5xx 分级、页面筛选，
以及查询参数不会写入日志。认证中间件的日志根目录在每个测试中指向临时目录，
不会污染正式 `storage/system/logs/`。

`tests/unit/test_user_cli.py` 验证终端创建、重置、停用、启用用户及交互式密码确认，
并验证用户列表不会输出密码、密码哈希或会话令牌。测试同样只使用独立 MySQL 测试库。

`tests/integration/test_robots.py` 使用独立 MySQL 测试库验证机器人档案为空时不再展示
演示数据，以及真实创建、自动 Token、列表、编辑、停用、恢复、唯一性与“无永久
删除”边界。

`tests/integration/test_collector.py` 验证机器 Token 认证、停用拦截、缺少或冲突的
`robot_type`、成功入库和自动关联、同机器人精确去重、同类型跨机器人确认改绑、
不同类型拒绝及 SHA-256 内容校验。测试 ZIP 和去重记录只写入临时存储。

`tests/unit/test_collector_tools.py` 验证第二版一次性扫描工具的候选发现、上传标记按
平台/RB 匹配、文件夹打包排除标记、平台已存在结果写标记，以及临时 ZIP 清理。

`tests/integration/test_semantics.py` 使用临时 Raw/Derived 和 MySQL 验证真实 Schema
扫描、Episode 统一字段、逐帧全空向量、CSV 索引导入、训练可用状态，以及核心字段
禁止删除和普通官方 Feature 二次确认；测试不会修改真实 `storage/`。
