# AiPlayingStardew

让 AI 自己玩星露谷物语。**从打开游戏开始全权操控**：启动进程 → 过标题 → 载入它自己的存档 → 干活 → 睡觉结束一天。

不是截图 + OCR，也不是强化学习 —— 而是 **SMAPI mod 暴露结构化状态，LLM 每步决策一个动作**。

---

## 为什么是这个架构

调研了 GitHub 上所有 AI 玩星露谷的项目后，我们和 **StarDojo**（学术评测基准，2025）以及 **stardew-mcp**（167⭐）**独立收敛到了同一套架构**：

```
SMAPI mod (C#) ──高层动作+结构化状态──> 智能体 (Python) ──> LLM
                     ↑
              思考时冻结游戏时钟
```

StarDojo 的论文数据（`docs` 里引用）：即使**同时给图像和内部状态**，GPT-4.1 的任务成功率也只有 **12.7%**，瓶颈是**低层操作和长期规划**，不是感知。
→ 所以**我们不给视觉**：省下 token、省下截图开销，把力气花在动作层和记忆层。

详见 [`notes/reference-study.md`](notes/reference-study.md)。

---

## 结构

```
mod/        SMAPI mod（C#）: HTTP JSON API + 高层动作 + ASCII 视野
agent/      主播AI(玩) + 监工AI(监督/复盘) + 记忆层
memory/     经验库: facts / lessons / journal / skills  ← 让 AI 跨局成长
config/     密钥与配置（secrets.json 不入库）
tools/      反射 API 探查工具（apidump）
vendor/     参考项目源码（仅供学习，不入库）
notes/      调研与设计笔记
```

## 两个智能体

| | 角色 |
|---|---|
| **主播AI** | 每步读状态 → 输出一个动作 JSON。角色是它自己设计的（名字、农场、外观、人设） |
| **监工AI** | 监督进度、判定卡死、给具体指令；**每天结束后复盘**，把经验写进 `memory/` |

## 关键设计

- **失败原因码**：每个动作返回 `reason_code`（`NO_PATH` / `WRONG_TOOL` / `NOT_FREE` …），
  让 AI 能区分「工具不对」和「这里没东西」——这是能学习的前提
- **自动等待**：动作发出后用**廉价的 HTTP 状态轮询**等它结束，绝不烧 LLM 去轮询
- **冻结时钟**：SDV 的时钟走真实时间，思考慢就烧掉白天 → `pause_time`
- **ASCII 视野**：61×61 区域渲染成字符网格（`@`你 `#`墙 `T`树 `C`作物 `~`水 `>`门）
- **记忆层**：facts（带置信度+证据数）/ lessons / journal，跨会话累积

## 运行

```powershell
# 1. 编译并部署 mod（会自动复制到游戏的 Mods 目录）
$env:LOCALAPPDATA\dotnet-sdk\dotnet.exe build mod\StateBridge.csproj -c Release `
  "-p:GamePath=E:\Program Files (x86)\Steam\steamapps\common\Stardew Valley"

# 2. 配置密钥
#    config\secrets.json -> llm.api_key  （或环境变量 OPENCODE_API_KEY）

# 3. 让 AI 自己玩
.venv\Scripts\python.exe agent\agent.py --steps 60
```

首次运行 AI 会**自己捏一个角色、建一个新档**；之后每次都只载入它自己的存档，**不碰玩家的档**。

## 许可

本仓库代码为自有实现。`vendor/` 下的参考项目版权归各自作者（注意 `stardew-mcp` **无许可证**，仅作本地学习，不得再分发）。
