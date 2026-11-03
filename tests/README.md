# 测试目录

测试按照 `app/modules/` 的业务结构组织。单元测试放在 `tests/unit/`，HTTP 和
文件存储集成测试放在 `tests/integration/`，可安全提交的小型 LeRobot 样本放在
`tests/fixtures/`。真实业务数据集不能放进测试目录。

`tests/integration/test_data_management.py` 使用临时 `storage/` 和 SQLite 文件测试
目录扫描、编号登记、路径安全、报告关联，以及真实 Parquet Episode 关节曲线读取；
不会访问真实业务数据。

`tests/integration/test_auth.py` 使用独立临时 SQLite 验证无默认账号、密码哈希、
登录 Cookie、退出和访问保护。其他业务测试通过 `tests/conftest.py` 注入测试用户，
不会读取或修改正式 `storage/system/platform.sqlite` 中的账号与会话。

`tests/integration/test_system.py` 验证真实访问日志落盘、4xx/5xx 分级、页面筛选，
以及查询参数不会写入日志。认证中间件的日志根目录在每个测试中指向临时目录，
不会污染正式 `storage/system/logs/`。

`tests/unit/test_user_cli.py` 验证管理员创建用户时必须交互输入并确认密码，以及用户
列表不会输出密码、密码哈希或会话令牌。测试同样只使用临时 SQLite。

`tests/integration/test_robots.py` 使用临时 SQLite 验证机器人档案为空时不再展示
演示数据，以及真实创建、列表、编辑、停用、恢复、唯一性与“无永久删除”边界。

`tests/integration/test_semantics.py` 使用临时 Raw/Derived 和 SQLite 验证真实 Schema
扫描、Episode 统一字段、逐帧全空向量、CSV 索引导入、训练可用状态，以及核心字段
禁止删除和普通官方 Feature 二次确认；测试不会修改真实 `storage/`。
