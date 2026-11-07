# astrbot_plugin_mifit

把小米运动健康（Mi Fitness）云端的手环数据接进 AstrBot：睡眠、步数、心率、血氧、压力、元气值等等，能查文字，也能画图。

底层用 [mifit](https://github.com/Jenwein/mifit)（非官方逆向的小米账号接口）。小米随时可能改接口，改了这个插件就可能失效，所以在设计上所有失败都老实报出来，不假装有数据。

## 能力

| 类型 | metric | 说明 |
|---|---|---|
| 睡眠 | `sleep` | 每晚总时长 + 深睡/浅睡/REM/清醒分段 |
| 步数 | `steps` | 步数、距离、消耗 |
| 心率 | `heart` | 日均/静息/最高/最低 |
| 血氧 | `spo2` | 均值与最低值 |
| 压力 | `stress` | 日均、区间分布 |
| 活动 | `activity` | 活动次数与活动分钟 |
| 热量 | `consumption` | 静息 + 活动 + 摄入 + 总计 |
| 活动卡路里 | `calories` | 纯活动消耗 |
| 有效站立 | `stand` | 每小时站立次数 |
| 中高强度 | `intensity` | 中高强度分钟数 |
| 目标达成 | `goal` | 步数/卡路里/站立/中高强度的实际 vs 目标 |
| 睡眠评分 | `score` | 睡眠评分、长睡评估、入睡与醒来钟点 |
| 元气值 | `vitality` | 累计元气值 + 当日低/中/高强度贡献 |
| 呼吸质量 | `breath` | 睡眠呼吸质量分 |
| 睡眠分期 | `stage` | 完整分期时间轴 |
| 心率采样 | `hrsample` | 逐分钟心率点 |
| 站立时段 | `standwin` | 每次有效站立的时间窗 |

中文别名都能直接用：睡眠、步数、心率、血氧、压力、元气、评分、分期、呼吸……

## 工具

- `mifit_login` — 发二维码给用户扫，登录小米账号（会话落盘，之后自动续）
- `mifit_login_status` — 查扫码进度
- `mifit_health` — 查文字数据
- `mifit_chart` — 画图并发送

## 指令

```
/mifit_login                 发登录二维码
/mifit 睡眠 7                 查最近 7 天睡眠
/mifit 元气 7                 查最近 7 天元气值
/mifit_chart 睡眠 7           画最近 7 天睡眠结构图
/mifit_chart 分期 3           画最近 3 晚的睡眠分期时间轴
/mifit_chart 心率采样 1       画单日心率散点
/mifit_chart 站立时段 3       画最近 3 天的站立时间窗
```

可画的类型：睡眠、步数、心率、血氧、活动、卡路里、中高强度、站立、评分、元气、呼吸、分期、心率采样、站立时段。

## 安装

1. 把目录放进 `data/plugins/`
2. 装依赖：`pip install mifit matplotlib`
3. 重载插件，发 `/mifit_login`，用小米运动健康 App 的扫一扫确认

二维码用小米 App 扫，微信扫只会打开一个链接——那一步是小米账号的网页登录流程，不是套娃出错。

### mcp 版本冲突

mifit 0.4.0 声明依赖 `mcp>=2.3`，而 AstrBot 要求 `mcp<2`。装完 mifit 后如果 AstrBot 报错，把它压回 1.x：

```
pip install "mcp>=1.8.0,<2"
```

只要不 import `mifit.mcp_server`（本插件不 import），两边就能共存。

## 配置

| 项 | 说明 |
|---|---|
| `session_path` | 会话文件位置，留空用 `data/plugin_data/astrbot_plugin_mifit/session.json`，也支持环境变量 `MIFIT_SESSION` |
| `default_days` | 默认回看天数 |
| `timezone` | 日期用的时区，默认 `Asia/Shanghai` |
| `max_rows` | 单次最多返回天数 |

## 图表

用 matplotlib 画，系统中文字体（Noto CJK 之类）能直接显示。matplotlib 是可选依赖：没装就只给文字，插件不会挂。

- 睡眠：横向堆叠条，深睡/浅睡/REM/清醒分色
- 元气值：堆叠柱（当日低/中/高强度）+ 双轴折线（累计值）
- 心率：最高/平均/静息三条线
- 睡眠分期：每行一晚的时间轴，横轴从入睡算起
- 站立时段：横轴 24 小时，色块是活动区间
- 心率采样：单日散点 + 均值线

## 扫 key

`scan_keys.py` 可以扫一遍接口的 key 空间，看还有哪些数据活着：

```
export MIFIT_SESSION=/path/to/session.json
python scan_keys.py
```

会话路径也支持环境变量，不会把路径写死在代码里。

## 免责

非官方项目，与小米没有任何关系。只读取你自己有权访问的账号数据。会话文件含 token，别提交到仓库，`.gitignore` 里已经排除了。
