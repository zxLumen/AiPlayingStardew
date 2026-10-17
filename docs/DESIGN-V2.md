# 架构 · V2（完全重做）

> 这份是**推翻重来**的设计，不是对现状的修补。现状与教训见 [`DESIGN.md`](DESIGN.md)（v1）与
> [`POST-MORTEM.md`](POST-MORTEM.md)；本文只引用它们的**结论**，不引用它们的代码形状。
>
> 三条地基（这一轮拿命换来的）：
> **工具必须自首 · 事实必须可测 · 裁判不可被修。**

---

## 0. 立宪：四条不变量（机器检查，且在可改范围之外）

| # | 不变量 | 怎么检查 |
|---|---|---|
| **I1** | 只能做玩家能做的事：不传送、不跳时间、不写结果 | 保真门（扫描 mod 源码里的作弊原语 + 每个动作的声明类别） |
| **I2** | 永不抢焦点、永不移动用户的鼠标 | ①静态：抢前台/移指针的 API 只允许出现在一个被隔离的文件里；②运行时：每个动作回执必须带 `focus_taken:false, cursor_moved:false`，为真即视为缺陷 |
| **I3** | 不许声称看不见的效果 | 判词词表里没有"没有证据的 OK"；同步动作只能报 `SENT`/`UNOBSERVABLE` |
| **I4** | 裁判不可被修改 | 裁判文件 hash 钉死；任何补丁触碰即拒绝并记台账 |

这四条**不是文档里的口号**：它们是运行时的检查，违反就拒绝执行 / 拒绝部署。

---

## 1. 内核（Kernel）

内核只管**时间、阶段、传输、日志**，不含任何游戏知识、不含任何策略。

### 1.1 阶段机（有界 + 有心跳）

```
BOOT → PLAN → ACT* → VERIFY → REVIEW → REST → (PLAN …)
```

每个阶段是一个 `Phase`，必须申报：

```python
Phase(
  name="BOOT",
  enter=lambda ctx: True,                 # 进入条件
  budget=Budget(steps=40, seconds=180),   # 上限：步数 + 秒数
  heartbeat="[启动] {what}",              # 每 N 秒说一次它在干什么
  exit=lambda ctx: ctx.game.has_save,     # 结束条件
  artifact="save_loaded",                 # 产出（写进日志，供后续阶段读）
  on_fail="retry|escalate|stop",          # 失败策略
)
```

**规矩**：没有 `budget` 的阶段不许注册；没有 `heartbeat` 的阶段不许注册。
——今天"控制台只有一句 `cycle 1`、几分钟没动静"就是这一条缺失的代价。

### 1.2 看门狗

- 任何阶段超过 `budget.seconds` → 打断、写一条 `[看门狗]` 事件、带上**它最后做的三件事**。
- 任何阶段超过 `heartbeat` 间隔没说话 → 打印"仍在 <阶段>，当前在做 X"，不打断（只是别静默）。

### 1.3 传输层

唯一的游戏客户端，`/state` 与 `/action` 各一个超时；返回**类型化**结果：

```python
ActionResult(
  ok: bool,
  verdict: Verdict,        # 见 §3.2
  changed: list[str],      # 观察到的世界差异（只报看见的）
  receipt: Receipt | None, # 点击类动作的回执
  detail: str,             # 游戏自己的话 / 拒绝理由
  reason_code: str,
)
```

**调用点必须消费 `verdict` 与 `receipt`**（今天三处调用点把返回值扔了，是最致命的一条 bug）。

### 1.4 日志（唯一事实源）

一行一件事，前缀固定，可 grep：

```
[阶段] BOOT enter budget=180s
[动作] step 12 menu_click id=buttons#1 → verdict=MENU_CALL focus_taken=false cursor_moved=false
[判词] step 12 → CHANGED(menu TitleMenu→LoadGameMenu)
[学习] game: "SeedShop 9:00 开门" evidence=3 source=game
[修复] ticket#7 patch compiled, probe PASSED, deployed
```

日志同时是：复盘的原料、修复工单的证据、审计的输入。**没有第二份真相。**

---

## 2. 器（Instruments）：输入是一组可替换的工具

替换掉"点击就是这个 mod 的一个函数"的旧形状。

### 2.1 工具注册表

每种原语（click / key / drag）注册若干**工具**，每个工具声明：

```python
Instrument(
  name="posted_click",
  primitive="click",
  needs=("window_handle",),                 # 前置事实
  works_when="any",                          # any | focused
  cost_ms=20,
  receipt=("verdict","focus_taken","cursor_moved"),
)
Instrument("direct_menu_call", primitive="click", works_when="any", cost_ms=1)
```

**物理鼠标不在注册表里。** 它在隔离文件里，只能被诊断命令显式调用，**play 路径不可达**（I2）。

### 2.2 会话事实表（每次会话量一次，然后记住）

```json
{ "window": {"handle":..., "geometry":..., "ui_scale":...},
  "input":  {"queue_read": false, "keys_queue_read": null, "pause_when_unfocused": false},
  "measured_at": "...", "session": "..." }
```

- 事实为 `null` = **没量过**，第一次用到时必须量（用一个便宜的探针：发一次动作，看下一帧世界有没有变）。
- 事实为 `false` = 量过且不成立 → 之后**直接选另一条工具**，不再重付学费。
- 换窗口模式/换机器 → 会话变了，事实作废，重新量（记一起 `session` 指纹）。

### 2.3 选择策略（策略属于 mind，不属于器）

```
click(policy):
  1. 器按"能工作 + 代价"排序：queue_read=false ⇒ direct_menu_call 优先
  2. 发第一击 → 回执
  3. 回执 verdict=SENT ⇒ 立刻读一次状态：变了 → CHANGED；没变 → 下一件工具
  4. 全部工具失败 ⇒ UNOBSERVABLE/REFUSED，交给 policy 决策（绝不静默退回鼠标）
```

**没有隐式回退。** 每一级升级都在日志里留一行。

---

## 3. 事实（Facts）：游戏接口是一套"视图"，不是一坨 JSON

### 3.1 声明的观察

```python
Observation(
  name="bag.slots",
  kind="bag",                      # 视图分组
  unit="items",
  source="Farmer.Items",           # 游戏里的来源（人可去查）
  owner="actor|planner|verifier",  # **谁需要它**——答不出来就不许加
  cadence="every_step|on_open|daily",
  cost_chars=180,
  rules=("不解释", "不设阈值", "不给建议"),   # 事实的三不
)
```

- `/state` 汇报**视图**：`vitals / whereabouts / bag / menu / place / quests / mail / work / map`。
- 每个视图有**字符预算**；装配时按预算裁剪并**报出实际大小**（今天提示词悄悄涨到 17.3k 就是缺这条）。
- 事实**永远不是结论**：`steps_since_change=37` 可以；`你卡住了` 不可以。

### 3.2 判词词表（Verdict）

| 判词 | 含义 | 谁产生 |
|---|---|---|
| `CHANGED` | 世界按预期变了 | 验证层 |
| `NO_CHANGE_ALLOWED` | 没变，但游戏有权拒绝（背包满、框是空的） | 验证层 |
| `REFUSED` | 游戏明确拒绝并给了理由（门锁着、玩家不可控） | 验证层 |
| `SENT` | 动作已送出，**效果下一帧才知道** | 传输层 |
| `UNOBSERVABLE` | 无法观察（无状态、无见证窗口） | 验证层 |
| `MISDISPATCH` | 实际派发到的**不是**要的那个控件 | 器 |
| `HANDLE_AMBIGUOUS` / `HANDLE_UNKNOWN` | 句柄对应多个/零个控件 | 器（**拒绝**，绝不猜） |
| `INSTRUMENT_FAILED` | 工具本身没能工作（无窗口句柄等） | 器 |

**句柄规则**：控件必须有唯一 `id`（路径+字段+序号）；名字允许模糊，句柄不允许。

---

## 4. 手册（Manual）：提示词是一份**有版本、有预算、有测试**的文档

拆成三段，分别装配、**分别测量**：

| 段 | 内容 | 规矩 |
|---|---|---|
| **MANUAL** | 新玩家必须被告诉的操作：动作表、句柄怎么用、地图图例、菜单/容器/商店怎么读 | 有版本号；有字符预算；有 lint（禁 `{}` 花括号陷阱——今天 f-string 吃掉目标就是这个）；**有内容测试**（装配后必须含目标/计划/世界摘要） |
| **SITUATION** | 当前视图 + 今日计划 + 尺子 + **工具自己的记录**（桥的账） | 按视图预算裁剪；字符数是每步打印的指标 |
| **LEARNED** | 挣来的游戏知识（带证据数） | 上限条数；无证据不写入；与 SITUATION 分开保存，便于"清空重学" |

**规则=疤痕，手册=必须告知。** 任何"上次坏了所以写一句"的冲动，先问：数据里有没有？有就删规则。

---

## 5. 心（Mind）：六个角色，职责不重叠

| 角色 | 频率 | 输入 | 输出 | 不许做 |
|---|---|---|---|---|
| **Actor** 演员 | 每"段" | SITUATION | 一串动作 + **每条的预期效果** | 不许读长历史；不许下结论 |
| **Planner** 规划 | 每天 | 目标/任务 + 世界 + 昨天复盘 | 今日计划 + **每个目标的失败退路** | 不许写坐标（坐标只能来自事实） |
| **Verifier** 验证 | 每步（**尽量不用模型**） | 动作 + 前后状态 | 判词（§3.2） | 不许改世界；不许学习 |
| **Reviewer** 复盘 | 每天 | 当天日志 | 学到的知识 + 矛盾 + 工单 | 不许把工具缺陷写成游戏知识 |
| **Designer** 造人 | 每档一次 | 无 | 角色/农场/人设 | — |
| **Referee** 裁判 | 每循环（带外） | 声明 + 源码 + 日志 | 违规、说谎、幻影声明 | **不可被 agent 修改**；不参与决策 |
| **Repairer** 修理工 | 有工单时 | 工单 + 探针库 | 补丁 → 编译 → 探针 → 上线/回滚 | 不许碰裁判文件；不许一次多补丁 |

**"演员"是唯一能决定做什么的角色。** 其他角色只能观察、验证、记账、修工具——这消除了现状里"监工/处方/医生/守望者互相指挥"的纠缠。

---

## 6. 帐（Beliefs）：三本账，**来源即归类**

| 账 | 文件 | 能写什么 | 写作条件 |
|---|---|---|---|
| **game** | `beliefs/game.json` | 关于游戏的规律（店的门、作物的季节、NPC 的作息） | **只能由被验证的世界变化写入**（`CHANGED` 证据） |
| **instrument** | `beliefs/instrument.json` | 关于"手"的事实（`queue_read`、窗口几何、UI 缩放、pause 设置） | 只能由测量写入，带会话指纹 |
| **self** | `beliefs/self.json` | 关于自己的表现（这类动作在这类目标上实测失败多少次） | 由验证层统计写入，**advisory**（只提醒，不封路） |

- 每条信念字段：`text / source / evidence_ids / confirmed_count / contradicted_count / last_seen`。
- **审计规则**：任何走进 `game.json` 的条目，其 `source` 必须是 `game`；工具缺陷进游戏账 = 硬错误。
- 重建存档时：`game` 可保留（对游戏成立），`instrument` 保留（对机器成立），`self` 清空（对这一次表现）。

---

## 7. 工坊（Workshop）：自我修复的闸门

```
判词/审计 → Ticket（带证据 id）
   → Patch（worktree，只能改"手"，裁判文件 hash 校验）
   → Compile（不过 → 回滚 + 编译器原话）
   → Unit probe（离线：形状/契约）
   → Deploy at break（游戏停止；旧 DLL 备份）
   → Behaviour probe（真机行为：必须由失败翻转为通过）
   → Keep / Revert（不翻转就回滚，连游戏一起回到旧桥）
```

- **探针库**（`probes/`）：每个能力上线必须带一个探针，探针就是验收测试。今天的
  `menu_handles_unique.py` 是第一个：id 唯一、未知句柄被拒、整排句柄被拒并给候选。
- **不可改集合**：`invariants/`（I1–I4 的检查）、`authenticity` 声明、`workshop` 工具自身、裁判的探针。
- **人可见**：`status` 一条命令给出：会话事实 / 当前阶段 / 最近 10 条判词 / 未结工单 / 上次补丁与探针结果。

---

## 8. 目录（建议）

```
mod/           游戏接口：视图 + 回执 + 器（薄，零策略）
kernel/        阶段机 · 预算与看门狗 · 传输 · 日志
instruments/   工具注册表 + 会话事实表 + 选择策略的**接口**
facts/         视图声明 + 装配 + 字符预算
manual/        手册（版本化）+ 装配 + lint + 内容测试
mind/          actor · planner · verifier · reviewer · designer · referee · repairer
beliefs/       三本账 + 来源校验
workshop/      工单 · 补丁流水线 · 探针库
invariants/    I1–I4 的检查（不可改集合）
docs/          本设计 · 复盘 · 运维
```

---

## 9. 迁移：怎么从 v1 走到 v2

**保留**（它们被证明是对的）：
- mod 的**回执 + 唯一句柄**设计、`menu_probe`、动作瓦片扫描（`place_actions`）、地图 ASCII、宏与可打断语义、保真门、探针思路、日志格式、以及"测量不是规则"的纪律。

**重写**（现状是团在一起的）：
- `agent.py` 的万象主循环 → `kernel/` 阶段机 + `mind/` 七角色。
- `compact.py` + `prompts.py` → `facts/` 视图声明 + `manual/` 分段手册（带预算与测试）。
- `world.py` + 各种 store → `beliefs/` 三本账 + 来源校验。
- 杂散的审计/医生/处方守望 → 收进 `referee/`（带外、不可改）与 `verifier/`（每步）。

**删除**：
- 一切"抢前台 / 移动鼠标 / 静默回退 / 按名字点控件 / 把测量变阈值 / 把工具缺陷写成游戏知识"的代码路径。
- 没有预算与心跳的静默阶段。

**顺序**（每步都能跑、都能验收）：
1. `kernel/`（阶段机 + 看门狗 + 日志 + 传输）——先用它驱动**现有的** mod，行为不变。
2. `facts/` 视图声明 + 字符预算（含提示词装配测试）。
3. `instruments/` 会话事实表 + 工具选择（把"queue_read 记住"规范化）。
4. `mind/verifier` 判词化（把 witness 升级为 §3.2 词表）。
5. `beliefs/` 三本账 + 来源校验。
6. `workshop/` 工单化（把 `tools/bridge.py` 收进去，加 hash 校验与探针库）。
7. 只有在 1–6 都绿之后，才允许 agent **自己**修桥（Repairer 开权限）。

---

## 10. 反目标（明确不做）

- 不做视觉/OCR（StarDojo：给了图像+状态，GPT-4.1 仍 12.7%，瓶颈在动作层与规划）。
- 不把测量变成阈值（尺子只报数）。
- 不做"某次踩坑写成规则"（规则=疤痕）。
- 不让 mod 含策略（mod 只出事实与回执）。
- 不让事实含结论。
- 不让 agent 修裁判、抢前台、动鼠标、静默回退。
- 不注册没有预算/心跳的阶段。
