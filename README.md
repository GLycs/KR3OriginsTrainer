# Kingdom Rush Origins 修改器 (KR3OriginsTrainer)

Kingdom Rush Origins（王国保卫战：起源）的单机离线路人向修改器，Windows 平台，图形界面 + 热键。

**不注入进程、不修改 exe/DLL、不 patch 内存、不写注册表**，仅通过游戏自带的 `-custom_script` 调试参数加载桥接 Lua，在游戏自身的 Lua 运行时里读写状态。

---

## 一、原理

游戏本体基于 LÖVE 引擎（kr3-desktop 6.4.46），自带启动参数 `-custom_script <模块名>`：

```
Kingdom Rush Origins.exe -custom_script kr3_bridge
```

游戏启动时会在 `love.filesystem` 中 `require` 该模块并调用其 `init / game_init / update / keypressed` 钩子。修改器把 `kr3_bridge.lua` 放进游戏存档目录（`%APPDATA%\kingdom_rush_origins\`），以该参数启动游戏，桥接脚本：

1. 每帧读取存档目录下的 `kr3t_state.lua`（由 GUI 写入）；
2. 在游戏自己的运行时里修改 `game.store` 字段、实体字段与 `DBG_TIME_MULT`；
3. 把 `kr3t_status.txt` 作为心跳与遥测回写给 GUI（金币、生命、实体数、场景、关卡名等）；
4. 所有写操作记录原值，总开关关闭或退出修改器时按原值还原。

因为改的是游戏正在使用的原生对象，并走游戏自己的信号（如 `got-enemy-gold`）与结算流程，数值与 HUD、成就、结算逻辑保持一致。

## 二、下载

可执行文件与打包压缩包统一放在 **Release** 里，仓库只保留源码与文档：

| 文件 | 适用场景 |
| --- | --- |
| [`KR3OriginsTrainer_exe_v1.0.0.zip`](https://github.com/GLycs/KR3OriginsTrainer/releases/download/v1.0.0/KR3OriginsTrainer_exe_v1.0.0.zip) | **推荐**，解压后双击 exe，无需 Python |
| [`KR3OriginsTrainer.exe`](https://github.com/GLycs/KR3OriginsTrainer/releases/download/v1.0.0/KR3OriginsTrainer.exe) | 单文件裸 exe |
| [`KR3OriginsTrainer_v1.0.0.zip`](https://github.com/GLycs/KR3OriginsTrainer/releases/download/v1.0.0/KR3OriginsTrainer_v1.0.0.zip) | 源码包，需本机 Python 3 |

全部版本见 [Releases](https://github.com/GLycs/KR3OriginsTrainer/releases)。

## 三、仓库文件清单

| 文件 | 说明 |
| --- | --- |
| `KR3OriginsTrainer.pyw` | 主程序源码（Python 3 + tkinter） |
| `kr3_bridge.lua` | 注入游戏的桥接脚本（exe 版已内置，也可单独部署到存档目录） |
| `使用说明.md` | 详细中文说明 |
| `README.md` | 项目说明 |

> 打包后的 `KR3OriginsTrainer.exe` 已把 `kr3_bridge.lua` 打进程序内部，启动时解压到临时目录并自动定位；若杀毒软件对单文件打包产物误报，添加信任即可。

## 四、快速开始

1. 双击 `KR3OriginsTrainer.exe`（首次启动解压需 2–3 秒）；源码方式运行 `KR3OriginsTrainer.pyw`；
2. 顶部路径框确认游戏 exe 路径，不确定点「浏览」；
3. 点「启动游戏」：程序自动把桥接脚本部署到存档目录，并以 `-custom_script` 参数拉起游戏；
4. 进入关卡后勾选「启用修改」，状态栏显示「已连接 / 关卡内 / 金币 xx / 生命 xx」即生效；
5. 关掉「启用修改」或退出修改器，游戏内数值按原值还原。

> 游戏已在运行时桥接无法热加载，需关闭游戏后重新用「启动游戏」启动。

## 五、功能

### 资源与速度
- 游戏速度 1x–16x（走游戏自带 `DBG_TIME_MULT` 时间通道，动画与计时同步）
- 暂停 / 继续
- 锁定金币、锁定基地生命（任意数值）
- 宝石写入存档（写 `slot_N.lua` 的 `gems`）
- 杀敌金币倍率（挂 `got-enemy-gold` 信号补差额，不动基础数值）

### 战斗强化
- 友军无敌（英雄 / 士兵 / 塔血量锁定）
- 英雄技能无冷却
- 友军伤害倍率（`damage_min / damage_max / damage_factor`）

### 防御塔与兵营
- 塔攻击速度倍率（递归缩放冷却字段，最大深度 4）
- 塔攻击射程倍率
- 兵营增强：单座士兵数、补员时间倍率

### 关卡进度
- 临时解锁全部关卡（关闭即还原）
- 临时全关三星（关闭即还原）
- 只读模式：完全不写存档，宝石 / 解锁 / 三星三项强制失效，运行时功能不受影响

### 战局控制
- 提前下一波、强制推进波次、立即通关（清空场上敌人并驱动游戏自己的胜利结算）

### 游戏内热键

| 热键 | 作用 |
| --- | --- |
| Ctrl+Alt+1 | 金币锁定开关 |
| Ctrl+Alt+2 | 生命锁定开关 |
| Ctrl+Alt+3 | 友军无敌开关 |
| Ctrl+Alt+4 | 速度 1x / 2x / 3x 循环 |
| Ctrl+Alt+5 | 暂停开关 |

## 六、实测记录（Windows 11 / kr3-desktop 6.4.46）

| 项目 | 结果 |
| --- | --- |
| 桥接加载（`-custom_script`） | 通过，状态文件持续心跳 |
| 场景识别 | 通过（menu / map / comics / game） |
| 关卡内金币写入 777777 | 通过，HUD 同步显示 |
| 关卡内生命写入 99 | 通过，HUD 同步显示 |
| 速度 6x | 通过，游戏时间倍率生效 |
| 全功能同时开启运行 10s | 通过，无 Lua 报错、无崩溃 |
| 存档写入（宝石 / 三星） | 通过 |

## 七、风险与还原

1. **存档级改动**（宝石、解锁、三星）会写进 `slot_N.lua`，游戏随时可能再写盘。想保持存档干净请勾「只读模式」，或只用运行时功能。
2. 不改 exe、不改 DLL、不写注册表。**卸载**：删除存档目录下的 `kr3_bridge.lua`、`kr3t_state.lua`、`kr3t_status.txt`，之后正常启动游戏即恢复原版行为。
3. 建议首次使用前备份 `%APPDATA%\kingdom_rush_origins` 目录。
4. 仅供单人离线游戏使用，请勿用于联机或排行榜。

## 八、参考与致谢

本项目的接口认知与设计思路完全建立在以下两份公开资料之上，特此致谢：

1. **[MIO-456/Lumi_Nox](https://github.com/MIO-456/Lumi_Nox)** — 《Kingdom Rush AI Bot 逆向工程笔记》
   <https://github.com/MIO-456/Lumi_Nox/blob/main/docs/games/kingdom-rush-reverse-engineering.md>
   提供了最重要的基础情报：游戏基于 LÖVE 引擎、exe 内嵌 ZIP 可用 Python `zipfile` 直接解包、`game.store` 为状态容器、`game.simulation` 为 ECS 实体管理等关键结论，本项目的字段定位全部由这份笔记起步。

2. **[Nolanse/KingdomRushFrontiersTrainer](https://github.com/Nolanse/KingdomRushFrontiersTrainer)**
   <https://github.com/Nolanse/KingdomRushFrontiersTrainer>
   提供了「外部 GUI + 游戏内桥接脚本」这一架构范本与功能划分思路，本项目的修改器形态与功能覆盖面参考其设计。

感谢两位作者公开分享逆向成果与实现思路。若这些资料对你有帮助，请同样给原项目点 Star。

## 免责声明

本项目仅供学习与单机离线研究使用。使用本工具产生的存档变化与任何后果由使用者自行承担，请自行备份存档。
