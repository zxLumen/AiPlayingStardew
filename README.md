# AiPlayingStardew

让 AI 自己玩星露谷物语。**从打开游戏开始全权操控**：启动进程 → 过标题 → 捏角色 → 建/载自己的档 → 干活 → 睡觉。

不是截图 + OCR，也不是强化学习 —— 而是 **SMAPI mod 暴露结构化事实与动作回执，Python harness 驱动 LLM 做决策**。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | **当前系统实际怎么工作**：分层、契约、回执与判词、输入（躯体）层、运维硬规矩、文件地图 |
| [`docs/POST-MORTEM.md`](docs/POST-MORTEM.md) | 这一段踩过的坑：每条的现象 → 根因证据 → 现在的形态；负面清单；已验证/未验证；重做建议 |
| [`notes/reference-study.md`](notes/reference-study.md) | 立项时的调研（StarDojo / stardew-mcp 等） |

---

## 为什么是这个架构

调研 GitHub 上 AI 玩星露谷的项目后，我们与 **StarDojo**（学术基准，2025）和 **stardew-mcp** 独立收敛到同一套：

```
SMAPI mod (C#) ──结构化状态 + 动作回执──> Python harness ──> LLM
                                              │
                                        验证层（世界变了吗）· 三本账 · 修复层
```

StarDojo 论文数据：即使**同时给图像和内部状态**，GPT-4.1 任务成功率也只有 **12.7%**，瓶颈在**低层操作与长期规划**，不在感知。
→ 所以**不给视觉**：省 token、省截图开销，把力气花在动作层、验证层和记忆层。

---

## 铁律

1. **只能做玩家能做的事情** —— 不传送、不跳时间、不写结果；一切动作走游戏自己的处理路径，游戏自己决定收不收。
2. **不抢用户的焦点、不动用户的鼠标** —— 物理鼠标已**禁用**；点击靠"投递消息 / 调用游戏自己的菜单处理"。
3. **桥必须自首** —— 每个动作返回回执（要的是什么 / 实际派发到哪里 / 是不是唯一 / 世界变了什么）；名字对应多个控件就**拒绝**并给候选。
4. **测量优先，规则即疤痕** —— 提示词里只留"新玩家必须被告诉的操作"（手册）；其余靠数据 + 测量（如 `world.steps_since_change` 只报数，不设阈值）。
5. **裁判不可被修改** —— agent 可以修"手"（mod），不能修判定它的东西（`check_fidelity` / `authenticity` / `tools/bridge.py`）。

---

## 结构

```
mod/        SMAPI mod(C#)：HTTP JSON API · 动作+回执 · 输入层(投递/直调) · ASCII 视野 · 宏
agent/      主循环 · 决策(streamer/planner) · 验证(witness/watcher) · 三本账(world/bridgehealth/lessons)
tools/      bridge.py（改桥的闸门：read/ticket/patch/build/deploy/revert）· probes/ · watch_stack.py
memory/     跨局成长：world(游戏知识) · bridge(工具事实) · lessons/facts/defects(项目教训)
config/     密钥与配置（secrets.json 不入库）
docs/       设计文档与复盘
notes/      调研笔记
```

## 关键设计（详见 docs/DESIGN.md）

- **动作回执**：点击返回 `verdict`（`OK` / `POSTED` / `MENU_CALL` / `SILENT_NOOP` / `HANDLE_AMBIGUOUS` …）+ `changed` + `active`（窗口是否活动）。
- **唯一句柄**：菜单控件有唯一 `id`；名字允许模糊，句柄不允许。
- **输入层**：后台窗口不跑帧 → 按住 `pause_when_unfocused`；投递消息进窗口队列；队列不被读则调用游戏自己的菜单处理；**永不抢焦点、不动鼠标**。
- **验证层**：动作发出后用廉价 HTTP 轮询观察世界，绝不烧 LLM 轮询；"世界变了吗"由 `witness` 单独判定。
- **尺子**：`world.steps_since_change` / `last_change` —— 只报数。
- **宏**：`clear_area / water_all / …` 在游戏内按游戏速度跑，可被新指令打断，成果记在 `chore_last`。
- **修复层**：补丁必须编译、部署必须在游戏停止时、探针不过自动回滚；一次只允许一个未验证补丁。

---

## 运行

```powershell
# 1. 编译 + 部署 mod（部署需要游戏停止；游戏在跑时 build 会在拷贝那步失败，这是正常的）
python tools\bridge.py build
python tools\bridge.py deploy --probe-cmd "python tools\probes\menu_handles_unique.py"
python tools\bridge.py status

# 2. 配置密钥：config\secrets.json -> llm.api_key（或环境变量 OPENCODE_API_KEY）

# 3. 让 AI 自己玩
.venv\Scripts\python.exe agent\agent.py --forever --max-hours 8 --steps 200 --max-days 60
.venv\Scripts\python.exe agent\agent.py --new-game        # 强制捏新角色、建新档
```

首次运行 AI 会**自己捏角色、建新档**；之后只载入它自己的存档，**不碰玩家的档**。
`tools/watch_stack.py` 常驻：栈掉了自己拉起，agent 输出写进 `logs/agent-console.log`。

---

## 许可

本仓库代码为自有实现。`vendor/` 下的参考项目版权归各自作者（`stardew-mcp` **无许可证**，仅本地学习，不得再分发）。
