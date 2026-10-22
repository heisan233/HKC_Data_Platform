"""平台 SQLite 管理元数据的访问层。

``datasets.py`` 维护稳定 DS 编号与 Raw/Derived 真实目录的映射，``quality.py``
维护质检方案、任务和人工审核。真正的数据库文件位于
``storage/system/platform.sqlite``，大型数据集仍保存在 ``storage/raw`` 与
``storage/derived``，不能放进本 Python 包或 SQLite。
"""
