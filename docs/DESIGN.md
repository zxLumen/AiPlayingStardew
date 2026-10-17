# 设计文档 · 现状（2026-10）

> 这份文档描述**当前系统实际是怎么工作的**（不是愿景）。凡是"我们后来才知道"的东西，都写在
> [`POST-MORTEM.md`](POST-MORTEM.md) 里，本文只保留结论。
> 重做设计之前请先读这两份，本文末尾 §7 列出重做必须解决的债。

---

## 0. 一句话

**SMAPI mod 只提供事实与回执，Python harness 驱动 LLM 玩。**
规则只保留"新玩家必须被告诉的操作"（手册）；其余一律靠**数据 + 测量**，不靠写死的规则。

底线（不可协商）：

1. **只能做玩家能做的事情** —— 不传送、不跳时间、不写结果。所有动作走游戏自己的处理路径，游戏自己决定收不收。
2. **不抢用户的焦点、不动用户的鼠标** —— 游戏跑在副屏后台，用户在主屏干活。
3. **桥不许替游戏下结论** —— 读数只说读到了什么；动作只说它做了什么、世界变了没有。

---

## 1. 分层

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 7 修复层      tools/bridge.py: read / ticket / patch / build / deploy /  │
│               revert + tools/probes/*（探针）                            │
│               裁判不可补丁：tools/check_fidelity.py · agent/authenticity │
│               · tools/bridge.py                                          │
├──────────────────────────────────────────────────────────────────────────┤
│ 6 知识层      三本账，不许混：                                           │
│               · 游戏知识（挣来的）  agent/world.py → memory/world.json    │
│               · 工具事实（量出来的）agent/bridgehealth.py → memory/bridge │
│               · 项目教训/缺陷      memory/{lessons,facts,defects}        │
├──────────────────────────────────────────────────────────────────────────┤
│ 5 验证层      witness.py（世界变了吗）· watcher.py（盲等→观察）          │
│               learning.py（实测成功率，advisory）· bridgehealth 判词     │
├──────────────────────────────────────────────────────────────────────────┤
│ 4 决策层      streamer.py（每段排几条动作）· planner（每天计划）         │
│               design_character / bootstrap（开局）                       │
├──────────────────────────────────────────────────────────────────────────┤
│ 3 感知/压缩   compact.py（state → 模型看到的，字符数被测量并打印）        │
│               ASCII 视野 · location.place_actions · ui.buttons[].id     │
├──────────────────────────────────────────────────────────────────────────┤
│ 2 躯体层      **输入如何真正到达游戏**（见 §3）                          │
│               物理鼠标：禁用 · 投递消息 POSTED · 菜单直调 MENU_CALL       │
│               按键：后台投递 / 前台真实 · 永不抢焦点                      │
├──────────────────────────────────────────────────────────────────────────┤
│ 1 游戏访问层  mod/（C#）: GET /state · POST /action · GET /health         │
│               StateSnapshotBuilder · ActionProcessor · UiStateBuilder ·   │
│               WorldMapper · ChoreController                              │
└──────────────────────────────────────────────────────────────────────────┘
```

分层规则：**只有第 1、2 层碰游戏；只有第 4 层做选择；第 5 层只测量不下命令。**

---

## 2. 契约

### 2.1 `/state`

- 每个字段必须"有主"：**谁需要它、为什么**。问不出来就删（今天删掉了 3.1k 字符的规则与字段）。
- **测量字段不带阈值、不附动作**。例：`world.steps_since_change`（多久没变化）+ `world.last_change`（上一次变化是什么）。
  只报数，不建议、不设限——数值怎么用是决策层的事。
- 需要"游戏自己的话"就直接给原话，例如 `location.shops[].note`（店的休息日）、动作失败时的 `detail`。

### 2.2 `/action`

返回 `{ok, action, detail, reason_code, data}`。

**点击类动作的 `data` 是回执（receipt）**，字段固定：

| 字段 | 含义 |
|---|---|
| `requested` | 你要的句柄/名字 |
| `resolved` | **实际派发到的**控件 id |
| `grid` | `bag` / `container` / `shop` / `control` |
| `unique` / `matches` / `candidates` | 这个名字对应几个控件；不唯一时把候选**连屏幕坐标**列出来 |
| `verdict` | 见下表 |
| `point` / `rect` | 点击的点与目标矩形（客户端坐标） |
| `changed` | 钱/背包/菜单手/光标/菜单名 的前后差异（**只报它看见的**） |
| `active` | 出这一击时**游戏窗口是不是活动窗口** |

判词表：

| verdict | 含义 |
|---|---|
| `OK` | 一击命中且世界变了 |
| `OK_OLD_PATH` | 旧路径完成且世界变了 |
| `POSTED` | 投递进窗口队列，**效果下一帧才知道** |
| `MENU_CALL` | 调用游戏自己的菜单处理；**没有 OS 输入参与**；效果下一帧才知道 |
| `SILENT_NOOP` | 命中了、送到了、世界没变（**游戏有权拒绝**，不是判词的错） |
| `NO_CURSOR` | 鼠标没到位（旧路径遗留） |
| `HANDLE_AMBIGUOUS` | 这个名字对应**多个**控件 → **拒绝**并给候选 |
| `HANDLE_UNKNOWN` | 这个名字对应**零个**控件 → 拒绝并列可用项 |

### 2.3 句柄（handle）

- 菜单里**每个控件都有唯一 `id`**，构造方式：路径 + 字段 + 序号（`ItemsToGrabMenu.inventory#12` / `buttons#1` / `slotButtons#0`）。
- **名字允许模糊，句柄不允许**。重名 → 拒绝（`HANDLE_AMBIGUOUS`），因为"一个名字对应两个控件"是**桥的缺陷**，不是 agent 的错。
- 路径必须带**嵌套层**：`ItemGrabMenu` 的两排格子都挂在字段 `inventory` 上，曾经因此同名（`inventory.12`），
  解析取第一个 → 点到了容器那排。现在 Source 是 `ItemsToGrabMenu.inventory` 与 `inventory`，天然不同。

### 2.4 动作类别（合法性审计用）

`authenticity.py` 给每个动作声明类别：`action` / `knowledge` / `read` / `recovery` / `illegal` / **`repair`**。
- `repair`（`bridge_report` / `bridge_patch` / `bridge_deploy`）**不经过游戏**，是 agent 找 harness 修工具；
  因此"桥里没有这个动作"不算 phantom。
- **裁判文件不可被补丁**（见 §7）。

---

## 3. 躯体层：输入怎么真正到达游戏

这是今天被反复打脸之后定下来的形态，**每一条都有实证**。

### 3.1 事实

| 事实 | 证据 |
|---|---|
| 物理鼠标会**移动用户的指针** | 用户："你这还是把我鼠标抢过去了" |
| `SetForegroundWindow` 抢焦点**还丢点击** | Windows 会把"激活窗口"的那一击吃掉 |
| `pause_when_unfocused = true` 时，**后台窗口不跑帧** | 实测：非活动时任何输入路径都无效 |
| 这个游戏窗口**不读自己的消息队列** | 连续 4 次 `POSTED` 后标题界面未动 |

### 3.2 现在的做法

```
点击：
  ① 投递消息 (WM_MOUSEMOVE/LBUTTONDOWN/UP，客户端坐标) → verdict=POSTED
     · 不动指针、不抢焦点
  ② 3 秒后读状态，若菜单未动 → 调用游戏自己的菜单处理（ClickRouter → receiveLeftClick）
     → verdict=MENU_CALL，无 OS 输入
  ③ 一旦量到"这个窗口不读队列"（memory/bridge.json: input.queue_read=false），
     之后 ① 直接跳过，省掉那 3 秒

按键：
  窗口非活动 → 投递 WM_KEYDOWN/WM_KEYUP（否则按键会落进用户正在用的窗口）
  窗口活动   → 真实按键

物理鼠标：**禁用**（NativeInput.ClickAt 直接返回 false 并说明原因）；
         旧实现在文件里改名 ClickAtDisabled，只作为"我们曾经这么干过"的记录。

焦点：永不主动获取（EnsureWindowActive 空实现；runner 的 focus_window 已删）。

失焦暂停：mod 在 state 构建时把 Game1.options.pauseWhenOutOfFocus 按住关掉，
         并在 /state 的 display.pause_when_unfocused 里报出来。
```

### 3.3 为什么允许"直接调用菜单处理"

它调用的就是**玩家点击时游戏自己跑的那段代码**（同样的坐标、同样的 `receiveLeftClick`），
游戏照样有权拒绝（背包满、确认框空着都仍旧拒绝）。区别只在**输入怎么送达**。
这不是替游戏下结论，而是**唯一能在不抢前台的前提下把点击送进去**的办法。

---

## 4. 一次点击的生命周期

```
LLM 产出 {"action":"menu_click","id":"buttons#1"}
   │
   ├─ agent：若 bridgehealth.queue_is_read()==false → 自动补 "via":"direct"
   ├─ POST /action → mod（游戏线程）
   │     ① 解析句柄：MatchHandles 精确匹配；0 个→HANDLE_UNKNOWN；>1 个→HANDLE_AMBIGUOUS（都拒绝）
   │     ② 选择工具：投递 / 直调
   │     ③ 组装回执（含 active / changed / candidates）
   ├─ agent 读回执：
   │     · HANDLE_*  → 记录进 memory/bridge.json（桥的账），**不写进游戏知识**
   │     · POSTED   → 3 秒后确认；没动 → 升级 via:"direct"
   │     · MENU_CALL→ 效果下一帧，由紧随其后的状态读取判定
   ├─ watcher：观察世界（不是盲等）
   ├─ witness：世界变了吗（worked: True/False/None）
   └─ world.observe(...)：只有"关于游戏"的教训才写进游戏知识
```

**点击不是"发送"，是"发送 + 回执 + 复核"。** 三处调用点（bootstrap / runner / play 循环）都必须读回执——
曾经它们把回执扔掉，于是"被拒绝的点击"和"游戏还在加载"长得一模一样，一循环就是几分钟。

---

## 5. 知识层的三本账

| 账 | 存放 | 写什么 | 谁读 |
|---|---|---|---|
| 游戏知识 | `memory/world.json`（`agent/world.py`） | "这种尝试在这类东西上不行"、店门上的营业时间等**挣来的**游戏规律 | 提示词（`world.brief()`） |
| 工具事实 | `memory/bridge.json`（`agent/bridgehealth.py`） | 桥自己的失败计数、`input.queue_read`、可用路线 | 提示词末尾"桥自己的记录"一行 |
| 项目教训 | `memory/{lessons,facts,defects}.json`、`memory/observations.jsonl` | 缺陷台账、判定、复盘 | 审计/医生/复盘 |

**铁律：桥的缺陷不许被学成游戏知识。** 判词属于 `BRIDGE_FAULTS` 时：
`worked=None`（"这什么都没说明"），跳过 `world.observe`，只记进桥的账。
——今天 agent 自己手写"ItemGrabMenu 里两排同名"当游戏知识，就是这条铁律要防的事。

---

## 6. 运行与运维

```powershell
# mod 编译 + 部署（部署在游戏停止时进行；游戏在跑时 build 会在拷贝那步失败，这是正常的）
python tools\bridge.py status
python tools\bridge.py build
python tools\bridge.py deploy --probe-cmd "python tools/probes/menu_handles_unique.py"
python tools\bridge.py revert          # 换回上一版 DLL + git checkout mod/

# 玩
.venv\Scripts\python.exe agent\agent.py --forever --max-hours 8 --steps 200 --max-days 60
.venv\Scripts\python.exe agent\agent.py --new-game        # 强制新档
```

**部署的硬规矩**（`tools/bridge.py` 强制，agent 绕不过）：

1. 补丁必须先**编译**；编不过自动 `git checkout` 回滚并把编译器原话交回。
2. 部署只能在**游戏停止**时；旧 DLL 备份，**探针不过就换回旧 DLL 并重启游戏**（栈永远回来）。
3. **一次只允许一个未验证的补丁**（树必须干净）。
4. 只能改**手**，不能改裁判：`tools/check_fidelity.py`、`agent/authenticity.py`、`tools/bridge.py` 拒绝写入。
5. 每一次补丁 / 探针 / 回滚都写进 `logs/bridge-patches.jsonl`。

**常驻监视**：`tools/watch_stack.py`（栈掉了自己拉起，agent 输出重定向到 `logs/agent-console.log`）、
`logs/click-verdict.txt`（点击证据）、`logs/agents/streamer.jsonl`（每步的结构化记录，含回执）。

---

## 7. 文件地图

| 文件 | 职责 |
|---|---|
| `mod/ModEntry.cs` | HTTP 端点、游戏线程任务、窗口/分辨率 |
| `mod/Utils/StateSnapshotBuilder.cs` | `/state` 组装；`CategoryWords`；失焦暂停的按住 |
| `mod/Utils/ActionProcessor.cs` | `/action` 分发；`MenuClick` + 回执；`NativeInput`（PostClick/PostKey/禁用物理鼠标）；`CheckTileAction` |
| `mod/Utils/UiStateBuilder.cs` | 菜单反射：`CollectFrom`（带路径）、`DescribeMenu`（唯一名）、`MatchHandles`（严格）、`BuildButtons`（份额含内容/grid） |
| `mod/Utils/WorldMapper.cs` | ASCII 视野、`BuildWarps`、`BuildLockedDoors`、`BuildPlaceActions`（每个地点的动作瓦片 + 邮箱） |
| `mod/Utils/ChoreController.cs` | 宏（clear_area/water_all/…）：游戏速度跑，可被新指令打断 |
| `agent/agent.py` | 主循环：boot、每步、回执处理、宏等待、尺子、找人重瞄 |
| `agent/runner.py` | 进档流水线（`_click_heard`：投递→复核→直调） |
| `agent/streamer.py` | 决策（模型调用 + `last_prompt_chars` 测量） |
| `agent/compact.py` | state → 模型所见（含 `ui.buttons[].id/grid`、`place_actions`、`mail_waiting`） |
| `agent/prompts.py` | 手册 + ACTION_SCHEMA + 每日计划提示 |
| `agent/witness.py` · `watcher.py` | 世界变了吗 / 盲等改观察 |
| `agent/world.py` · `bridgehealth.py` | 游戏知识账 / 工具事实账 |
| `tools/bridge.py` · `tools/probes/` | 修复层与探针 |

---

## 8. 重做设计必须先解决的债（详情见 POST-MORTEM §6）

1. **提示词的"体积预算"**：现在 17.3k 字符（压到过 13.6k）。规则=疤痕，手册=必须告知；要有测量与上限。
2. **`press_key` 的后台路径未在实战验证**（投递按键是否被读，同 `queue_read` 需实测并记忆）。
3. **`direct` 依赖菜单类型**：`receiveLeftClick` 之外还有 `update()` 里做事的菜单，需要"哪些菜单必须走哪条路"的实测清单。
4. **boot 流水线缺自动化测试**：今天三条 bug 全在这条路上（扔回执、按名字点、抢焦点）。
5. **`queue_read` 是窗口级事实**：换机器/换窗口模式需重新量。
6. **宏与决策的边界**：宏可被打断，但"打断后旧活记在 chore_last"这条链路还缺长跑验证。
7. **记忆没有版本**：存档重建后旧知识仍在（今天手动作了归档）；重做时要设计"哪本账跟着存档走"。
