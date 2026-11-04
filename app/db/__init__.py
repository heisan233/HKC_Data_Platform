"""平台 MySQL 管理元数据的访问层。

``datasets.py`` 维护稳定 DS 编号与 Raw/Derived 真实目录的映射，``quality.py``
维护质检方案、任务和人工审核，``users.py`` 维护用户、密码哈希与登录会话，
``robots.py`` 维护独立机器人档案与稳定 RB 编号。``semantics.py`` 保留旧受控词表
接口，当前数据定义页不使用它。MySQL 数据目录由部署配置单独挂载；大型数据集
仍保存在 ``storage/raw`` 与 ``storage/derived``，不放进数据库。
"""
