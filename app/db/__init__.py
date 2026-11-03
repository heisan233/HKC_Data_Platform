"""平台 SQLite 管理元数据的访问层。

``datasets.py`` 维护稳定 DS 编号与 Raw/Derived 真实目录的映射，``quality.py``
维护质检方案、任务和人工审核，``users.py`` 维护用户、密码哈希与登录会话，
``robots.py`` 维护独立机器人档案与稳定 RB 编号，``semantics.py`` 维护场景、任务、
动作与对象的受控词表。
真正的数据库文件位于
``storage/system/platform.sqlite``，大型数据集仍保存在 ``storage/raw`` 与
``storage/derived``，不能放进本 Python 包或 SQLite。
"""
