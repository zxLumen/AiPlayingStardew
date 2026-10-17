# 主播AI 的学习笔记

每条都是它在实战里验证过的结论。可以手动删改——它下次开局会照读。

## 2026-10-16
- 出门后先 scan 半径 8 再动手，能提前知道哪片地能清、能锄。
- go_to 报 NO_PATH 时不要重复撞同一个点，改指向相邻的空格（如 (65,22) 换成旁边可走格）。
- clear_area 有『无路径放弃』的格子，说明那片区域被障碍隔断，锄地优先选 till_area 一次全成的区域如 (67,21)。
- 长距离移动优先用 warp（Farm -> BusStop/Town 一步到位），省掉 go_to 的 20 步。

## 2026-10-16
- [医生开的约束] 在执行 interact / interact_at / use_tool 等需要玩家自由的动作前，必须先确认没有过场或事件正在播放（uistate.dialogue 不为 null 或 uistate.menu 非空都算占用）；若返回 NOT_FREE，不要重试同一动作，而是先调用 clear_event（或 dialogue_advance / dialogue_choose 结束对话）把玩家交还控制权，再重新执行该动作。

## 2026-10-16
- [处方] 写成计划约束：在执行 interact / interact_at / use_tool 等需要玩家自由的动作前，必须先确认没有过场或事件正在播放（uistate.dialogue 不为 null 或 uistate.menu 非空都算占用）；若返回 NOT_FREE，不要重试同一动作，而是先调用 clear_event（或 dialogue_advance / dialogue_choose 结束对话）把玩家交还控制权，再重新执行该动作。

## 2026-10-16
- [处方] 给 till_area 加了前置条件：{'fact': 'work.tillable', 'op': '>', 'value': 0}，不足时先 scan。原因：till_area 在 work.tillable 为 0（人还在 FarmHouse 里、附近根本没有可耕地）时被反复下发，bridge 只要把任务排进队列就回 OK，世界却一点没变，所以 22 次「OK」全是空头支票，偶尔才回一句「nothing nearby needs that work」。

## 2026-10-16
- 去 Town 别直接 go_to (30,60)（必 NO_PATH），改成 go_to (43,23) 再 enter Town。
- till_area 的完成格数取决于当天剩余可耕地，别把「20 格」当固定预期，先 scan 看清 work.tillable。
- 锄地优先挑一次全成、无放弃的区域：今天 (67,25) 半径 2 一次 10/10 完成。
- 出门先 exit_building 落在 (64,15)，再 scan 半径 8 确认当天可清/可锄的地块。

## 2026-10-16
- [处方] 给 use_tool_at 加了前置条件：{'fact': 'work.tillable', 'op': '>', 'value': 0}，不足时先 scan。原因：两次被记成 LYING 的挥动都发生在没有可耕作物的坐标上（work.tillable=0、empty_soil=0、debris=0，且角色仍在 FarmHouse 内），锄头确实挥出了，但目标格本就无物可作用，世界不变；桥接只按“工具挥出”就回 OK，于是被判成说谎。

## 2026-10-16
- till_area 返回 ok=False/FAILED 时先看 done 数，别当成完全失败就重试。
- 同一天不要反复换点硬锄，先把 scan 到的可锄格锄完，再转清地（clear_area）。
- clear_area (67,23) 半径 3 一次全成，是可优先选用的清地目标。
- till_area 的可锄格数按天变化，执行前先 scan 看 work.tillable，不要沿用昨天的数字。

## 2026-10-16
- 要从 Farm 去 Town 只能用 warp 到 Town，别再用 go_to (43,23)+enter Town（今天实测 FAILED，Farm 无 Town 出口）。
- 锄地前先 scan 看当天 work.tillable，别把『20 格』当固定值：今天 (67,21) 只有 4 格、(67,25) 只有 3 格。
- 锄地优先做 (67,21) 和 (67,25) 半径 2 这两片，今天两次都是 0 given up 全成。
- 长距离移动先用 warp/一次到位，go_to 单程动辄 30~50 步，等于白烧一天的时间。

## 2026-10-16
- 锄地只挑 (67,21) 和 (67,25) 半径 2 这两片 0 given up 的点；(69,27) 全放弃、(69,28) 放弃 5 格，别再碰。
- till_area 结果里出现大量 given up 并带 no path 时立刻换点，不要在同一片障碍区反复试。
- 每天动手前先 scan 看 work.tillable，今天 (67,21) 只剩 1 格、(67,25) 只剩 2 格，别按昨天或更早的格数做计划。
- Farm 内部长距离走位可参考 (64,15) -> (80,16) 约 23 步，跨区仍然优先用 warp。

## 2026-10-16
- 进城去 SeedShop 用连续 enter：Farm enter BusStop → BusStop enter Town → Town enter SeedShop，三步都成功，不必先 warp。
- 出门后先 scan 半径 8 确认当天可清/可锄地块，再决定进城还是干活。
- enter 系列比 go_to 到门口更可靠：enter 会自己走到门口并开门，少一步手动寻路。

## 2026-10-16
- 同一坐标连续 interact_at 第二次必回 FAILED，一次成功就当交互完成，别重试。
- clear_area 目标被清空后会返回 nothing to do，别对同一片反复下发，每天先 scan 再重新选清地点。
- 进了 SeedShop 办完事直接 exit_building 就能回 Farm，省掉 Town/BusStop 两步。
- Farm->BusStop->Town->SeedShop 的连续 enter 链依旧三步全通，进城买种优先走这条。

## 2026-10-16
- [处方] 禁用 chat：游戏没有「聊天」这个独立动词：对话是 interact 命中 NPC 时游戏自己弹出的（uistate.dialogue）。没有任何前置条件能让 chat 生效，它只能永远返回成功却不改变世界，因此必须从桥接层移除，改由 interact / interact_at 承担。

## 2026-10-16
- [处方] 禁用 narrate：游戏和桥接层都不存在“解说/旁白”这一动作，没有任何前置条件能让它生效，它只会白占一个行动回合且不改变世界；所有行动必须取自允许动作清单（go_to、move、work、use_tool、interact、plant_all、refill、scan 等）。

## 2026-10-16
- scan 之后必须接一个真正改变世界的动作（go_to/interact_at/work），今天同一圆心连扫 25 次全是零信息。
- SeedShop 里的交互点不止一个：(11,20) 之后还能 go_to (4,19) 再 interact_at (4,18)，一个点用过就换下一个。
- 进城办事固定走 Farm enter BusStop → enter Town → enter SeedShop，办完 exit_building 直接回 Farm，不要再手动寻路。
- Farm 出门点附近已无清地目标，clear_area (67,23) 半径 3 会返回 nothing to do，别再对同一片反复下发。

## 2026-10-16
- [处方] 禁用 chat：游戏不存在「聊天」这一独立玩家动词：与 NPC 交谈是 interact / interact_at 命中 NPC 后由游戏自己触发对话（uistate.dialogue）。chat 没有任何可满足的前置条件，任何状态下都只会空转返回成功、不改变世界，因此必须从桥接动作表中移除，相关需求一律改由 interact / interact_at 承担。

## 2026-10-16
- [处方] 禁用 narrate：游戏与桥接层都没有“解说/旁白”这一动作，允许动作清单（go_to、move、work、use_tool、interact、plant_all、refill、scan 等）里也没有它，任何前置条件都无法让它生效；它只会消耗一个行动回合并让日志失真。

## 2026-10-16
- 永久禁用 noop：和 chat、narrate 一样只回 ok=True 不改变世界，今天 17 次 noop 直接把一天烧光。
- scan 圆心一直是 (9,9) 而不是 (64,15) 时，先 go_to/exit 把位置校正到已知点，再重新 scan，别连扫。
- 每个 scan 后面必须紧跟一个真正改世界的动作（go_to / interact_at / work），否则当天等于空转。

## 2026-10-16
- [处方] 禁用 progress：游戏本身不存在与“progress”对应的玩家行为（没有工具、没有按键、没有 NPC 交互会推进它），它是桥接层把内部/调试通道当成正常动作暴露出来；任何前置条件都无法让玩家“做出”这个动作，只会污染规划器的可选动作集。

## 2026-10-16
- 今天锄地优先 (64,18) 半径 2（10 格 0 放弃）和 (67,20) 半径 2（6 格 0 放弃），别再按 (67,21)/(67,25) 的老格数做计划。
- 清地改用 (63,25) 半径 3（13 done / 7 given up），(67,23) 半径 3 已返回 nothing to do。
- 不要对 Forest 门口下发 enter/go_to：从 Farm 到 (40,65) 全 NO_PATH，enter Forest 直接 FAILED。
- 长距离移动仍走 go_to 一次到位（今天 (80,15) 只需 17 步），切忌连发 noop/重复 scan 烧回合。

## 2026-10-16
- 回农舍别用 enter FarmHouse（返回 FAILED），直接在农舍门口 interact_at (64,14) 就能进门。
- scan 圆心还是 (9,9) 时立刻 exit_building / go_to 校正回 (64,15)，今天开头又白烧了 6 次 noop。
- 清地候选加上 (65,29) 半径 2（7 done / 4 given up），(63,25) 半径 3 可连做两轮不要只做一次。
- 别对 Forest 门口反复下发 go_to/enter：今天 (41,64)/(40,64)/(42,64) 全 NO_PATH，enter Forest 直接 FAILED。

## 2026-10-16
- enter 返回 NOT_FREE（cutscene/event playing）不是永久失败，隔一回合重发同一 enter 即可，别换目标也别放弃。
- SeedShop 内落点在 (6,29) 时，继续用已知可行的 (4,19) 作为 go_to 目标，(4,18)/(5,18)/(4,16) 今天全 NO_PATH。
- (64,18) 半径 2 和 (67,20) 半径 2 今天都只剩 1 格，已基本锄完，明天先 scan 再重新挑新锄地点，别在老点空锄。
- 每天开场 exit_building 后立刻 scan 一次刷新可锄/可清目标，今天的锄地格数比昨天又缩水，按老坐标做计划会空跑。

## 2026-10-16
- SeedShop 打开商店菜单后，别连点 menu_click '0'（今天至少点了 8 次全是空转），一次点击后用 menu_click 'upperRightCloseButton' 关掉即可。
- SeedShop 落在 (6,29) 时先 go_to (6,26)（4 步）再 go_to (4,19)（10 步），不要直接对 (4,18)/(5,18)/(4,16) 下发 go_to。
- ShopMenu 打开时任何 go_to/interact_at 都返回 NOT_FREE，先把菜单用 upperRightCloseButton 关掉再走下一步。
- 买种子流程固定为 go_to (4,19) → interact_at (4,18) → 用菜单 → upperRightCloseButton 关闭，不要在同一菜单里反复点 '0'。

## 2026-10-16
- 在 SeedShop 里一旦打开 ShopMenu，第一条命令必须是 upperRightCloseButton，并且之后不要再点 '0'，否则菜单会立刻重开、把 exit_building 卡成 NOT_FREE。
- 已经在 SeedShop 内部时不要再 enter SeedShop，重复 enter 只回 ok=True 空转；要换区域直接 go_to。
- SeedShop 内的长距离移动 go_to 一次到位即可（今天 go_to (19,12) 用了 78 步），不要在店内拆成多个 go_to 试探。
- 看到 exit_building 返回 NOT_FREE『menu open: ShopMenu』时，先补一发 upperRightCloseButton 再重发 exit_building，别改目标也别放弃。

## 2026-10-16
- 别在 SeedShop 内部重复 enter SeedShop（今天连发多次都只回 'entered via the door at (43,56)' 空转），换位置直接用 go_to。
- ShopMenu 打开后夹点 menu_click '0' 只会让菜单重开、把 exit_building 卡成 NOT_FREE；先点一次 upperRightCloseButton 再立刻 exit_building，中间不要插 '0'。
- 彻底禁掉 menu_click '0'（今天被守门器拦下仍是空转），需要翻商品列表用 downArrow (1638,816)。
- 当天结尾又连发 8 次 noop 烧回合，一看到 noop 被接受就立刻改发 go_to/exit_building 这类真正改变世界的动作。

## 2026-10-16
- [处方] 桥缺能力：菜单点击缺少坐标换算

## 2026-10-16
- ShopMenu 里要翻列表用 downArrow (1638,816)，彻底禁掉 menu_click '0'（今天点了几十次全是空转）。
- 被守门器拦下（本日已重复 4 次）时不要重发同一动作，立刻换动作类型（如 go_to / scan / downArrow）。
- 关 ShopMenu 只用一次 upperRightCloseButton 然后立刻 exit_building，中间不要插 '0'，否则菜单重开并把 exit_building 卡成 NOT_FREE。
- 别再连发 noop：今天多次连续 noop 把回合烧光，一看到 noop 被接受就改发 go_to/exit_building 这类真正改变世界的动作。

## 2026-10-16
- 进 SeedShop 后绝不点 menu_click '0'：今天点了十几次，一点就重开菜单，导致整天关不掉 ShopMenu；翻列表只用 downArrow (1638,816)。
- ShopMenu 卡成 NOT_FREE 时别反复 exit_building（第 4 次会被守门器拦），立刻改用 scan 或 downArrow 换动作类型，避免循环。
- 买种子的固定连招：Farm → enter BusStop → enter Town → enter SeedShop → go_to (4,19) → interact_at (4,18) → 菜单里只用 downArrow，最后一把关掉。
- 今天两处锄地 (64,18)/(67,20) 半径 2 各只锄到 1 格，明天先 scan 刷新再挑新点，别在老坐标空锄。

## 2026-10-16
- ShopMenu 里 menu_click '0' 会真买第 1 个商品并扣钱（今天 500->420），要买才点它，不买绝不要碰。
- upperRightCloseButton 今天全部超时无变化，关不掉 ShopMenu；菜单卡住时别再连点同一按钮，立刻换动作类型（如 downArrow / 离开店内）。
- Farm 新锄地点用 (61,18) 半径 2（5 done, 0 放弃）；(60,21)/(64,21) 有 (62,23) 的树挡路会放弃，别选它们。
- 已经在 SeedShop 内部时不要反复 enter SeedShop，只会回 'entered via the door at (43,56)' 空转。

## 2026-10-16
- 买种子流程：go_to (4,19) → interact_at (4,17)/(4,18) 打开 ShopMenu → 点 forSaleButtons.0 (1060,235) 买够数量 → 立刻一次 upperRightCloseButton 再 exit_building，绝不要连点 4 次以上。
- 别在 ShopMenu 里乱点 inventory.* 与数字按钮（'4' '10' '12' 等今天全是 WATCHER_TIMEOUT 空转），只有 forSaleButtons.0 和 upperRightCloseButton 有实际效果。
- press_key 'escape' 关不掉 ShopMenu（今天 3 次全 timeout），不要浪费回合。
- Farm 锄地换新点用 (61,18)：先 clear_area 半径 3（11 格）再 till_area 半径 2（11 格）；(67,23) 已清空，(77,18) 半径 2 只有 2 done 2 given up 不划算。

## 2026-10-16
- ShopMenu 里绝不要碰 inventory.* 前缀名，除 inventory.0-3 外全报 NO_SUCH_BUTTON；需要点格子用纯数字名 '4'-'24'，但今天这些数字点击也全是 WATCHER_TIMEOUT，实际上别点。
- 买种子一招到底：interact_at (4,18) 开菜单 → 连点 forSaleButtons.0 到第 4 次（守门器上限）→ 立刻 exit_building，一次买 4 个别贪。
- 看到 rc=NOT_FREE『dialogue is open』时先发 press_key 'escape'（今天有效），不要连发 dialogue_advance（4 次就被拦）。
- 在 Town (43,57) 用 interact_at (43,56) 能直接重开 SeedShop 门，比 enter SeedShop 少一步且不会被重复守门器拦。
- Farm (61,18) 是可每天复用的锄地/清地点，出了 FarmHouse 先 scan 再直奔 (61,18)，不要浪费回合在 (77,18)/(67,23)。

## 2026-10-16
- [处方] 给 refill 加了前置条件：{'fact': 'held.name', 'op': '==', 'value': '喷壶'}，不足时先 select_slot。原因：refill 从来不是孤立的动作：它要求浇水壶正拿在手上、且农夫面向/紧邻一格水面，而每次调用时这两个前置条件一个都没满足，所以桥只能如实回报失败。

## 2026-10-16
- 进屋流程：go_to (64,15) → enter FarmHouse 走门 (64,14)；从别处直接 enter FarmHouse 只会 FAILED。
- plant_all 排队后不要再发 refill/water_all，会 FAILED『already busy』；等它走完再 select_slot 2 + refill + water_all。
- (61,18) 已锄完，换 (70,17)-(71,20) 这一带：先 clear_area 半径 3，再 till_area 半径 0 逐格锄，最后 plant_all。
- Town 里被 dialogue/cutscene 卡住时，用 press_key 'space'（必要时 'enter'）推进；只剩一次 escape 推进机会，别连点同一动作被守门器拦。

## 2026-10-17
- 每天早上的固定开场：dialogue_advance → dialogue_choose 0 → doneNamingButton → dialogue_advance 到 no dialogue box open，然后才 harvest_all/water_all，别在对话开着时派活。
- 9 点前别去 SeedShop（LOCKED），先浇水/锄地/清杂，9 点后再去买种；游戏会报剩余分钟，按它排计划。
- ShopMenu 手上有物品时：先点空数字格（'10' 等）把物品放进背包，再一次 upperRightCloseButton + exit_building；别反复 exit_building（第 4 次被守门器拦）。
- plant_all 排队期间 refill/water_all/till_area 全部 already busy，用 noop 等它跑完再浇水；plant_all 大量 given up 说明目标土没锄好，先 till 再 plant。
- CommunityCenter 内 exit_building 走 (32,24) 会 WATCHER_TIMEOUT，两次都卡住，明天换 go_to 门口格再出去或先 scan 看门的位置。
