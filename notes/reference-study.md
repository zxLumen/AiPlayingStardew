# 参考项目学习笔记

> 调研时间：2026-10-16
> 所有源码已下载到 `vendor/`（仅供学习，不发布）

---

## 一、仓库清单

| 目录 | 来源 | ⭐ | 许可证 | 性质 |
|---|---|---|---|---|
| `vendor/stardew-mcp-default` | Hunter-Thompson/stardew-mcp（默认分支 `stardew-mcp-bridge`） | 167 | **无** ⚠️ | 早期版，11 工具，无 ASCII 地图 |
| `vendor/stardew-mcp-main` | 同仓库 `main` 分支 | 167 | **无** ⚠️ | **强化版**：`CommandExecutor.cs` 190KB、`copilot_agent.go` 62KB、**有 ASCII 地图** |
| `vendor/stardojo` | StarDojo2025/stardojo | 73 | ✅ MIT | 学术评测基准，1000 任务 |
| `vendor/Stardew-Sage` | Zhang-986/Stardew-Sage | 27 | ✅ MIT | 问答/RAG，**不控游戏** |
| `vendor/StardewValley-MCP` | amarisaster/StardewValley-MCP | 30 | ✅ Apache-2.0 | AI 当第二玩家陪玩 |

**两个版本差异极大**，看 README 会被误导 —— 必须看 `main` 分支。

---

## 二、他们的架构 vs 我们

| | stardew-mcp | StarDojo | **我们 (StateBridge)** |
|---|---|---|---|
| 通信 | WebSocket (+ `WebSocketSharp` 依赖) | socket | HTTP (原生 TcpListener) |
| 状态 | 每秒**广播** | 按需 | 按需拉取 |
| 寻路 | **手写 A\*** | 内置 | **游戏自带 `PathFindController`** ✅ |
| 思考时暂停 | `cheat_time_freeze` | pause-and-resume | `pause_time` ✅ |
| 菜单/标题/捏人 | ❌ (`Context.IsWorldReady` 门禁) | 统一接口跳过 | ✅ **唯一有** |
| 失败原因码 | ❌ 只有 `success:true/false` | ❌ | ✅ `reason_code` |
| 记忆/学习 | ❌（靠手写攻略文档） | ❌ | ✅ facts/lessons/journal |
| 监工(第二模型) | ❌ | ❌ | ✅ |
| ASCII 视野 | ✅ 61×61 | ❌ | ❌ **要补** |
| 评测任务集 | ❌ | ✅ 1000 个 | ❌ **要补** |

---

## 三、他们自述踩过的坑（`CLAUDE.md`）—— 和我们完全一致

| 挑战 | 他们的结论 | 我们的状态 |
|---|---|---|
| **延迟** | "AI 发高层命令，mod 自主执行" + "考虑 **pause and plan**" | ✅ `pause_time` 已做 |
| **寻路** | 「Stardew 没有玩家可用的 A*」→ 自己写 | ⚠️ **他们这句错了**，`PathFindController` 就是；我们用游戏的，更稳 |
| **状态序列化** | 「只发附近 + **cache/diff**」 | ✅ 只发附近；❌ 没做 diff |

---

## 四、⭐ 核心发现：他们对「假成功」的解法

`mcp-server/main.go` 里嵌了一份**手写攻略**当资源，开头第一条：

> **⚠️ TRUST NOTHING. VERIFY EVERYTHING.**
> 一个 `success: true` **只代表命令发出去了，不代表它成功了**。必须在每个动作后重读状态确认。

**这正是我们第一天踩的坑**（动作全 OK、世界一动不动、时间冻结）。
**两种解法：**

| | 他们 | 我们 |
|---|---|---|
| 机制 | **提示词要求模型自觉复核** | **代码强制**：`reason_code` + 真实寻路校验 + 自动 settle |
| 可靠性 | 靠模型遵守 | 结构保证 ✅ |

**结论：我们的更硬。但「知识文档」这个形式值得吸收** —— 而且我们要做的是**让 AI 自己从实战生成**，不是人手写。

---

## 五、⭐ 移植清单（按价值排序）

### P0 — 立刻做（都能大幅减少 AI 犯错）

| # | 东西 | 出处 | 说明 |
|---|---|---|---|
| 1 | **ASCII 局部地图** | `GameStateSerializer.cs:237 GenerateAsciiMap` | **61×61**（`ScanRadius=30`）。图例：`@`玩家 `.`地 `#`墙/建筑 `~`水 `T`树 `O`物体/石头 `C`作物 `H`翻过的土 `"`草 `>`门/传送点 `;`artifact `!`NPC `M`怪物。**治我们「空间盲」** |
| 2 | **寻路进度** `path_progress` / `path_length` / `path_target` | `PlayerState` | 我们只有 `is_auto_moving` 布尔。**进度不涨=卡住**，这是判定的关键 |
| 3 | **`tile_in_front`** | `GetTileInFront()` | 直接给「面前那格是什么、能否互动」 |
| 4 | **`minutes_until_morning`** | `GetMinutesUntilMorning()` | 剩余时间 → 决定何时睡觉 |
| 5 | **`warp_points` 列表** | `GetWarpPoints()` | 所有门/传送点+目标坐标，`exit_building` 才能选对门 |

### P1 — 补动作（我们缺的）

| 动作 | 作用 | 我们有吗 |
|---|---|---|
| **`place_item`** | **播种 / 放置** | ❌ **我们至今不能播种** |
| **`hold_tool {ticks}`** | **蓄力**（水壶/锄头范围效果，一次 3×3） | ❌ |
| `use_tool_repeat {count}` | 连击（砍树 10 刀一次发完） | ❌ |
| `eat_item {slot}` | 回体力 | ❌ |
| `select_item {name}` | **按名字**选物品（现在只有 slot index） | ❌ |

### P2 — 架构借鉴

| 东西 | 出处 | 说明 |
|---|---|---|
| **状态切分** | main.go 的 MCP resources | 把一坨 `/state` 拆成 `player` / `inventory` / `time` / `map` / `surroundings` / `tile_in_front` / `npcs` / `objects` / `warps` → **按需只读一块，省 token** |
| **任务集 + 成功率** | StarDojo `env/` + 1000 任务 | 「有没有变强」的唯一硬标准 |
| **游戏数据库** | Stardew-Sage `database.sql` | MIT，20+ 表 SDV 数据 → 喂知识层 |
| **知识文档** | main.go `gameKnowledge` | 我们改成**自动生成**（复盘产出），不手写 |

### P3 — 只用于压测（不上直播）

stardew-mcp 的 30 个作弊命令（`cheat_clear_debris` / `cheat_hoe_all` / `cheat_grow_crops`…）→ 快速验证框架，跳过枯燥步骤。

---

## 六、他们的坑（别学）

| 坑 | 后果 | 我们 |
|---|---|---|
| `obj.Name.Contains("Stone")` 判石头/树枝/杂草 | **中文环境直接失效**（本机游戏是 zh） | ✅ 用 `IsWeeds()`/`IsTwig()` API |
| 作物只给 ID（`indexOfHarvest.Value.ToString()`） | LLM 看不懂 `"24"` | ✅ 我们给显示名 |
| 手写 A* 而非游戏自带 | 和游戏判定不一致的风险 | ✅ 用 `PathFindController` |
| 满屏 `catch {}` | 防御性强但**掩盖问题** | ✅ 有 `reason_code` |
| 依赖 `WebSocketSharp` | 多一个第三方库 | ✅ 原生 TcpListener |
| **无许可证** | 不能发布/直接搬代码 | ✅ 自己写，只抄**思路** |

---

## 七、已确认的关键事实（源码级）

- stardew-mcp 的 mod **全程卡在 `Context.IsWorldReady`** → 无菜单/标题/捏人能力
- `move_to` 是**非阻塞**的，靠轮询 `isPathfinding` + `pathProgress` → **和我们独立做的 `wait_until_settled` 同构**
- README 的「61×61 ASCII 地图」在**默认分支不存在**，只在 `main` 分支
- `main` 分支 `ScanRadius = 30`（61×61）；默认分支 `ScanRadius = 10`（21×21）
- StarDojo 用 `setup.ps1`/`setup.sh` 安装，含 `StardojoMod`（SMAPI mod）+ Python `env/` + `agent/`

---

## 八、结论

> **我们不是在走弯路。** 两个独立团队（stardew-mcp 作者、StarDojo 论文组）都收敛到了同一套架构：
> **SMAPI mod + 高层动作 + 结构化状态 + 思考时暂停**。
>
> **我们的独有优势**：全流程自主（启动→捏人）、`reason_code`、记忆层、监工。
> **我们缺的**：ASCII 视野、寻路进度、5 个动作、评测任务集。
>
> **行动**：只抄「思路」，代码自己写（规避无许可证）；优先 P0 五项 + P1 五个动作。
