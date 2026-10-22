# 测试目录

测试按照 `app/modules/` 的业务结构组织。单元测试放在 `tests/unit/`，HTTP 和
文件存储集成测试放在 `tests/integration/`，可安全提交的小型 LeRobot 样本放在
`tests/fixtures/`。真实业务数据集不能放进测试目录。

`tests/integration/test_data_management.py` 使用临时 `storage/` 和 SQLite 文件测试
目录扫描、编号登记、路径安全及报告关联，不访问真实业务数据。

`tests/integration/test_auth.py` 使用独立临时 SQLite 验证无默认账号、密码哈希、
登录 Cookie、退出和访问保护。其他业务测试通过 `tests/conftest.py` 注入测试用户，
不会读取或修改正式 `storage/system/platform.sqlite` 中的账号与会话。

`tests/unit/test_user_cli.py` 验证管理员创建用户时必须交互输入并确认密码，以及用户
列表不会输出密码、密码哈希或会话令牌。测试同样只使用临时 SQLite。

`tests/integration/test_robots.py` 使用临时 SQLite 验证机器人档案为空时不再展示
演示数据，以及真实创建、列表、编辑、停用、恢复、唯一性与“无永久删除”边界。

`tests/integration/test_semantics.py` 验证数据定义页展示真实 LeRobot v3 路径，并
使用临时 SQLite 覆盖场景/任务/动作/对象词条的稳定 SD 编号、创建、编辑、筛选、
停用、恢复、唯一性、输入校验和“无永久删除”边界。
