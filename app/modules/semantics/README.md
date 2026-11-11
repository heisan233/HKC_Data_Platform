# 数据定义（Schema 编辑器）

`/semantics/` 用于检查和扩展已登记 LeRobot v3 数据集结构。它不是旧受控词表页面。

## 扫描

页面读取：

- `meta/info.json`；
- 全部 Episode 元数据 Parquet；
- 全部 frame Parquet；
- 字段 dtype、shape、names、来源和空值完整率。

## Episode 字段

平台扩展定义写入：

```text
info.json["hkc_extensions"]["episode_features"]
```

实际值写入所有 Episode 元数据。支持：

- `string`；
- `int64`；
- `float64`；
- `bool`；
- 全部 Episode 统一值或统一空值。

## 逐帧字段

支持数值标量和定长向量：

- 同时更新 `info.json.features` 和全部 frame Parquet；
- 可建立全空占位；
- 可从 CSV/Parquet 按 `episode_index + frame_index` 导入；
- shape 大于 1 时必须明确元素名称或接受“维度 N”展示；
- 全空或部分为空会标记为“不满足训练要求”，不会自动填 0。

图片、视频和深度媒体字段目前只读；第一版不能创建新媒体文件。

## 发布与删除

所有修改都在 `staging/schema/` 构建完整副本并发布新 DS，源数据集不变。核心索引
和定位字段禁止删除；其他官方字段需要醒目警告和二次确认；平台新增字段可以删除。

发布结果默认不可用、待质检，必须重新审核。
