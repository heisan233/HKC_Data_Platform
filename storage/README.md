# 平台存储目录

这里保存数据集、处理结果和平台运行状态，容器内统一映射为 `/data`。除目录中的
`.gitkeep` 和本说明外，所有内容都被 Git 忽略。

## 当前存储方案

数据集和质检产物保存在普通文件夹。数据管理使用
`system/platform.sqlite` 保存登记编号、展示信息及 Raw 目录映射；大型数据文件
不进入数据库。hs 上传与解压独立进行，数据管理页面扫描 `raw/` 并由用户选择登记。

宿主机上的本目录会被 Docker 映射为容器内的 `/data`：

```text
项目中的 ./storage  ->  容器中的 /data
```

所以业务代码应读取配置提供的存储根目录，不能写死
`/home/heisan/.../storage` 这样的个人路径。

## 子目录用途

- `raw/`：hs 上传解压后的原始数据，包括待登记目录；登记时不移动或改名。
- `detected/`：质检后的数据集文件，按登记 ID 关联。
- `staging/`：上传、校验和安全解压期间的临时内容。
- `derived/`：裁剪、合并、拆分、去重或修改 task 后生成的新版本。
- `converted/`：格式转换输出。
- `exports/`：供用户或机器人下载的打包结果。
- `reports/`：结构检查、自定义规则、VLM 等质检报告。
- `models/`：模型定义、权重或发布包；首期暂不使用。
- `training-runs/`：训练日志、指标和 checkpoint；首期暂不使用。
- `system/`：平台运行状态及 `platform.sqlite`，不提交 Git。

不要把 Python、HTML、配置文件或 Dockerfile 放在此目录。测试用小样本应放在
`tests/fixtures/`，并确认其许可和体积适合进入 Git。
