# 主播AI 的学习笔记

每条都是它在实战里验证过的结论。可以手动删改——它下次开局会照读。

## 2026-10-16
- 出 FarmHouse 后不要再调 exit_building，直接 go_to 即可。
- go_to 返回 NO_PATH 时，改瞄准相邻空格重试，通常能成功。
- (67,19)-(69,23) 一带没有杂草，别再用镰刀空挥，换区域或先 observe。
- 出门起点是 (64,15)，向 (69,20) 方向走可绕过 (66,19)、(68,19) 的阻挡。

## 2026-10-16
- go_to 返回 NO_PATH 时先 move 一格再 go_to 可绕开（今天 (64,15) 下移后成功到 (66,20)）。
- 镰刀在 (67,19)-(69,23)、(68,25) 全部 NOTHING_DONE，这片确实没有杂草，别再空挥。
- 出门起点 (64,15)，去 (69,20) 可走 (65,19)->(66,20)->(69,20)。
- (80,16)、(40,55) 一带 NO_PATH，不要作为导航目标。

## 2026-10-16
- blocked 的格子先用斧头（槽 0）清障，清完再 go_to 就能走过去耕地。
- 镰刀在 (67,19)-(69,23)、(68,25) 仍全部 NOTHING_DONE，别再空挥，那没用。
- (69,28) 往南 (69,29)/(68,31)/(70,31)/(64,28) 全 NO_PATH，别把南侧当导航目标。
- 锄头（槽 1）是主力耕地工具，走到目标格后 use_tool_at 即 ok。

## 2026-10-16
- blocked 格先用斧头（槽 0）清、石头用十字镐（槽 3）清，清完再 go_to 过去用锄头耕地。
- (67,19)-(69,23)、(68,25) 反复 NOTHING_DONE，这片没杂草，别再拿镰刀空挥，直接换锄头耕地。
- go_to 返回 NOT_FREE 表示角色还在忙，等一小下重试，不要当成 NO_PATH 去改目标。
- Farm 西侧 (8,21)-(10,23) 一带锄头全 ok，是可开辟的第二片耕地。

## 2026-10-16
- 混合种子（槽 5）不能直接 use_tool_at/interact_at 播种，会返回 WRONG_TOOL/FAILED，别在耕地上反复试。
- 在 Farm 户外 sleep 会返回 NO_BED，睡觉前要先回到床边，别在外面空试。
- till_area {x,y,radius} 能一次排队多格耕地，比逐格 use_tool_at 省步数。
- (69,25)、(68,20) 是十字镐+锄头组合格：先槽 3 清，再槽 1 耕，顺序不能反。

## 2026-10-16
- blocked 格先用斧头（槽 0）清，清完立刻用锄头（槽 1）在同一格耕地，(67,19)-(67,21)、(68,21)-(68,22) 今天这样一次成功。
- (68,24)/(69,24)/(70,24) 是可耕地新收获，可用 till_area 或逐格 use_tool_at 继续扩展。
- (80,16) 其实可达（从 (79,16) 2 步），别被旧 NO_PATH 结论吓退；真正 NO_PATH 的是 (40,55)、(40,65)、(43,52)。
- 在户外 sleep 一定 NO_BED，收工前先 go_to 回 FarmHouse 门口附近再睡。

## 2026-10-16
- 同一目标 go_to 先 ok 后 NO_PATH 时是站位问题：靠近后重试，别下永久 NO_PATH 结论。
- (44,40)/(45,40)、(44,55)/(30,55) 全 NO_PATH，别把 Farm 南侧远格当导航目标。
- till_area 返回 'nothing to do' 表示该片已耕，别重复调用；混合种子至今没有可用播种动作，别反复 interact_at/use_tool_at。
- Farm 允许负坐标导航（go_to (-1,54) ok），x<0 不是越界，可作为绕行点。

## 2026-10-16
- 目标格 go_to 返回 NO_PATH 时先用 interact_at 试一次，常常仍能交互成功（今天 (52,32) 就是如此）。
- 锄头 use_tool_at 返回 ok 不代表能播种：plant 返回 FAILED 就说明种子放不下这一格，别在原地反复 select_slot+plant。
- Farm 南侧 (44,50)、(44,55)、(30,45)、(52,63) 全 NO_PATH，别当导航目标；(44,49)、(48,56) 实际可达。
- plant 失败后不要死磕同一格（今天在 (9,22)/(9,23) 反复 FAILED），换格或用别的动作验证耕地状态。

## 2026-10-16
- plant 返回 FAILED 'that tile is nothing / is it tilled soil?' 就是那格没耕成，别在同一格反复 select_slot+plant，换格或先 till_area。
- 混合种子是能播种的（(10,23)、(73,18)、(64,21) 今天成功），之前'播种不可能'的结论作废，先确认 tile 是 hoe_dirt 再 plant。
- till_area {x:75,y:18,radius:2} 这类小半径能一次排队 3 格并在 (73,18) 顺利下种，东北片可继续用同样方式扩张。
- 同一格 go_to 先 ok 后 NO_PATH（(80,17)、(-1,54)、(9,22)）是站位问题，靠近后重试即可，别当永久 NO_PATH。

## 2026-10-16
- Farm 的 enter 出口列表会变：先看返回的 Known exits 再决定，今天要进城得先 enter BusStop。
- 遇到 'cutscene/event playing' 就别再 go_to/enter，先连续 dialogue_advance 推完事件。
- exit_building 出门后立刻再调会被 NOT_FREE 挡住，等一拍或先做别的动作。
- 出门落点仍是 Farm (64,15)，可用 water_all 一次排队 3 格浇水再走。

## 2026-10-16
- Farm 的 (44,23) 是可达的（从 (64,15) 34 步），但那一带会触发剧情事件，去之前先规划好抵达后要做什么。
- 一旦出现 'cutscene/event playing'，别再用 go_to/enter/move，只反复 dialogue_advance，遇到 'no dialogue box open' 就 noop 一拍再继续。
- enter 'BusStop' 会先走门，返回 ok 后要等落定再操作，立刻再 enter 必被 NOT_FREE 挡。
- 想进城仍走 Farm -> enter BusStop，直接从 Farm enter 'Town' 永远 FAILED。

## 2026-10-16
- 连续 dialogue_advance 都返回 'no dialogue box open' 时，说明是无对话框演出，别再刷 dialogue_advance/interact，改成 noop 或 pause_time 等待。
- 今天被事件锁死、零农活进展：这种无对话事件推不动时别硬刷步数，记录状态等下一个 game day。
- pause_time 在事件期间可用（返回 ok），可用来控制时间，但无法解除 go_to/interact 的 NOT_FREE。
- 事件结束前 go_to 目标格（如 (3,12)）根本测不出可达性，别把这种 NOT_FREE 记成 NO_PATH。

## 2026-10-16
- 无对话事件里 menu_click 'Skip' 也是 NO_MENU，别浪费时间找跳过按钮，直接 noop 等事件结束。
- 事件锁定期只发 noop（或 pause_time 控时间），反复 dialogue_advance/interact 只会刷出 FAILED/NOT_FREE。
- 无对话事件可能连续好几天锁死（spring-06→spring-07），这两天什么都测不出来，诚实记录'被事件锁住、零进展'即可。
- 被事件锁死当天结束时不要下任何地形可达性结论（NO_PATH 可能是事件造成的假象），等能自由行动时再验证。

## 2026-10-16
- 无对话事件已连续锁死 spring-06→08 三天，别指望隔天自动解除，但也别靠刷步数耗掉一整天，记录状态即可。
- 事件期只发 noop（必要时 pause_time），dialogue_advance/interact/move/go_to 全是 FAILED 或 NOT_FREE，纯浪费步数。
- 被锁当天不要下任何地形可达性结论，NO_PATH/NOT_FREE 都可能是事件造成的假象。
- 连续多天开局即被事件锁住时，把'事件未解、零进展'当成当天唯一可信结果上报。

## 2026-10-16
- 无对话事件已从 spring-06 连续锁到 spring-09 共四天，别指望隔天自动解除；当天只发少量 noop 并如实记录'零进展'，不要刷满步数。
- 这次事件内容是社区中心剧情，台词不弹对话框，继续刷 dialogue_advance 只会得到 FAILED，先 noop 等待。
- 被事件锁住的当天不要下任何地形可达性结论（没测过 NO_PATH，也没法验证出路），一切等能自由行动时再测。
- 事件期间唯一稳的动作是 noop（必要时 pause_time），go_to/enter/interact/move/dialogue_advance 全是 FAILED 或 NOT_FREE，纯浪费步数。

## 2026-10-16
- 无对话事件已连续锁死 spring-06→spring-10 共五天，别指望隔天自动解除：当天只发少量 noop（必要时 pause_time）并如实上报零进展，不要刷满步数。
- 事件期间 interact_at/go_to 的 NOT_FREE 不是地形或可交互性的证据，(9,9)、(3,12) 这类结论必须等能自由行动后再重测。

## 2026-10-16
- 事件锁定期当天只发少量 noop（必要时 pause_time），go_to/enter/interact/move/dialogue_advance 全是 FAILED 或 NOT_FREE，纯浪费步数。
- 同一无对话框事件已连续锁死 spring-06→11，别指望隔天自动解除，也别刷满步数，如实上报'零进展'即可。
- 社区中心剧情台词不弹对话框，遇到它时别再刷 dialogue_advance，直接 noop 等事件结束。
- 被锁当天不要下任何地形或可交互性结论，(3,12) 这类 NOT_FREE 不是 NO_PATH，等能自由行动时再重测。

## 2026-10-16
- 事件一解除就优先重测被锁期间悬空的东西（Farm 出口名、可达格），别沿用上锁日期的旧结论。
- enter 的目标名要以返回里的 'Known exits' 列表为准：这版 Farm 没有 BusStop，进城请用 Town。
- water_all 只在附近确实有需要浇水的格子时才 ok，先 go_to 到耕地旁再按喷壶（槽 2）。
- Farm 里 (64,20) 这类 NO_PATH 是真实阻挡（当天无事件），排路线时要绕开。

## 2026-10-16
- 出门后先 go_to (64,18) 再 water_all；在落点 (64,15) 直接 water_all 只会返回 FAILED 'nothing to do'。
- Farm 的 enter 目标名以返回的 Known exits 为准（Desert/Town/Farm/Backwoods），没有 BusStop。
- 出现 NOT_FREE 'player not free' 时先 noop 等事件，别连续 enter/go_to 刷步数。
- (9,21) 从 (43,23) 直接 go_to 是 NO_PATH，要先 enter 'Farm' 再走过去。

## 2026-10-16
- 先 clear_area 清障，再对同一坐标执行 till_area，能在一次调用里排到更多耕地（(69,25) r3 从 10 格变成 22 格）。
- 刚 exit_building 出门后的第一次 go_to 可能报 NOT_FREE 'farmer cannot move right now'，先 noop 一次再重发同一 go_to，别当成路被挡。
- 出门后仍按老路线 go_to (64,18) 再 water_all，8 天后依然有效（3 格排队）。

## 2026-10-16
- 播种流程走通：select_slot 5（混合种子）+ plant_all 一次排了 37 格，别再试 use_tool_at/interact_at 播种。
- 出门后第一次 go_to 报 NOT_FREE 'farmer cannot move right now' 时，noop 一次再重发同一 go_to 即可（今晨再次验证）。
- 出门后固定 catch：go_to (64,18) → select_slot 2 → water_all（3 格），这条流水线 14 天仍有效。
- 清障→耕地→播种按 clear_area → till_area → plant_all 顺序发，同一天内可把清出的地块直接耕完种上。

## 2026-10-16
- 出门后固定流水线仍是 go_to (64,18) → select_slot 2 → water_all，第 15 天依然有效；播种次日浇水格数会从 3 格暴涨到 38 格，别以为还是小活。
- 第一次 go_to 报 NOT_FREE 'farmer cannot move right now' 时照旧 noop 一次再重发同一 go_to，别误判成路被挡。
- water_all 一次排队后角色会自己走完全部格子，当天不要再重复下单或逐格手动浇。
- 播种后的第二天优先留出浇水时间（38 格），清障/耕地/播种这类活儿留到浇完再排。

## 2026-10-16
- 出门固定流水线 go_to (64,18) → select_slot 2 → water_all 到第 16 天仍然有效，一天 7 步就能收工，别多加多余动作。
- 播种后第二天起浇水量级稳定在 37~38 格，浇水日就把时间全留给浇水，清障/耕地/播种另排一天。
- 第一次 go_to 报 NOT_FREE 'farmer cannot move right now' 照旧 noop 一次再重发同一 go_to，别误判成路被挡或事件锁。

## 2026-10-16
- 出门 catch 后重发同一 go_to 可能从 NOT_FREE 变成 NO_PATH：别死磕 (64,18)，改发相邻的 (65,18)（5 步）再继续。
- Farm 的 Known exits / BusStop 可用性随站位变化，以当场 enter 的返回为准，别沿用旧日期的'没有 BusStop'结论。
- enter 一旦返回 'walking to the door...' 就已排队，别再重复发同一个 enter，重复只会拿到 NOT_FREE 或 nothing here leads to。
- 今天全部时间花在赶路和试出口、零农活：明天出门优先补 water_all，作物不会自己浇水。

## 2026-10-16
- 出门固定流水线不要再死磕 go_to (64,18)：今天它已 NO_PATH，先试相邻的 (65,18) 或另找可达的作物区。
- enter 一旦返回 ok 'walking to the door' 就是已排队，别再重发同一个 enter（只会拿 NOT_FREE）；真要重发先 noop 一次。
- Farm 的 BusStop 出口可用：从落点走 go_to (79,16) 再 enter 'BusStop' 即可，别沿用'没有 BusStop'的旧结论。
- 在镇上长距离移动前先 scan/go_to 分段试：(30,55)、(35,55)、(45,55) 都是 NO_PATH，绕 (12,54)→(25,55)→(40,58) 才走得通。

## 2026-10-16
- 出门固定流水线别再用 (64,18)：今天它已 NO_PATH，直接 go_to (65,18)（5 步）再 water_all。
- 第一次 go_to 报 NOT_FREE 后重发同一目标可能变 NO_PATH；一旦变成 NO_PATH 就立刻换相邻格，别死磕原坐标。
- water_all 排队 37 格后角色会自己走完，当天不要再重复下单或逐格手动浇。
- 浇水日就专心浇水（37 格量级），清障/耕地/播种另排一天。

## 2026-10-16
- 出门固定流水线换点：go_to (65,18)（5 步）→ select_slot 2 → water_all，别再死磕 (64,18)。
- 第一次 go_to 报 NOT_FREE 后重发同一目标可能直接变 NO_PATH，此时立刻换相邻格（如 (65,18)），别连续重试原坐标。
- water_all 一次排队 37 格后角色自己走完，当天不要再重复下单或手动逐格浇。
- 出门后不要再发 exit_building（户外执行会返回 NOT_FREE 'cutscene/event playing'），那是室内动作。

## 2026-10-16
- 出门流水线第 21 天仍有效：go_to (65,18)（5 步）→ select_slot 2 → water_all，全程 7 步即收工。
- go_to 报 NOT_FREE 'cutscene/event playing' 时先 noop 一次再重发同一目标，今天直接成功；不必立刻换成相邻格。
- 浇水量级稳定在 36~38 格，浇水日只干浇水，清障/耕地/播种另排一天。
- water_all 排队后角色自行走完所有格子，当天不要再重复下单或手动逐格浇。

## 2026-10-16
- go_to 报 NOT_FREE 别急着换坐标：今天连续两次 NOT_FREE（含 'cutscene/event playing'）后各 noop 一次、第三次重发同一目标就 ok。
- 出门流水线第 22 天仍可收工：落地 (64,15) → go_to (65,18) → water_all，全程 9 步。
- 今天没发 select_slot，water_all 仍 ok；若喷壶已在手上可省掉 select_slot，失败再加。
- 浇水量级 35~38 格，浇水日只干浇水，清障/耕地/播种另排一天。

## 2026-10-16
- 出门流水线第 23 天照旧有效：exit_building → go_to (65,18) → water_all，连续两次 NOT_FREE 时各补一次 noop 再重发同一目标即可。
- 喷壶若已在手上可省掉 select_slot：今天没发 select_slot，water_all 仍 ok。
- 浇水量级稳定在 35~38 格，浇水日只干浇水，清障/耕地/播种另排一天。
- water_all 排队后角色自行走完所有格子，当天不要再重复下单或逐格手动浇。

## 2026-10-16
- 出门流水线第 24 天照旧：exit_building → go_to (65,18)（连报 NOT_FREE 就各补一次 noop 再重发）→ water_all，共 8 步收工。
- 喷壶若已在手上就省掉 select_slot：今天没发 select_slot，water_all 仍 ok。
- 浇水量级稳定在 34~38 格，浇水日只干浇水，清障/耕地/播种另排一天。
- water_all 排队后角色自行走完所有格子，当天不要再重复下单或逐格手动浇。

## 2026-10-16
- clear_area 可一次排队 25~38 格并自动完成，下单后当天别重复下单或逐格手动清障。
- 清障前先 select_slot 0 拿斧头，再发 clear_area{x,y,radius}。
- 出门后不要再发 exit_building：户外执行只会返回 ALREADY_OUTDOORS（且可能先报一次 NOT_FREE）。
- 首次 exit_building/go_to 报 NOT_FREE 'farmer cannot move right now' 时补一次 noop 再重发即可。

## 2026-10-16
- 出门流水线第 26 天照旧：exit_building → go_to (65,18)（连报 NOT_FREE 就各补一次 noop 再重发同一目标）→ select_slot 2 → water_all，共 9 步收工。
- go_to 报 NOT_FREE 'farmer cannot move right now'/'cutscene/event playing' 时不要换坐标，补 noop 后重发同一目标即可成功。
- water_all 排队 33 格后角色自行走完，当天不要再重复下单或逐格手动浇。
- 浇水量级 33~38 格，浇水日只干浇水，清障/耕地/播种另排一天。

## 2026-10-16
- 出门流水线第 27 天照旧：exit_building → go_to (65,18)（连报两次 NOT_FREE 就各补一次 noop 再重发同一目标）→ water_all，共 9 步收工。
- 作物开始成熟：到 (65,18) 后先发 harvest_all 再 water_all，两者都会自动排队走完。
- harvest_all 排队后角色自行处理，当天不要重复下单或逐格手动收。
- 浇水 33 格量级仍稳定，收作物+浇水同一天即可收工，清障/耕地另排一天。

## 2026-10-16
- 去镇子走 Farm (78,15) 的 enter 'BusStop'；在 Farm 直接 enter 'Town' 必然 FAILED。
- 进入 Town 后别再 enter 'Town'，镇内换地图要用 enter 'BusStop'/'CommunityCenter' 等 Town 自己的出口。
- harvest_all 只排队 2 格说明作物已收完，接下来该排清障（斧头槽 0 + clear_area）或耕地（锄头槽 1 + 播种）。
- go_to 报 NO_PATH 且提示 'farmer is at (11,23)' 时说明已换地图，别拿旧地图坐标继续试，改发当前地图内的相邻格。

## 2026-10-16
- 出 Farm 去镇子：exit_building → go_to (78,15)（约 17 步）→ enter 'BusStop'，进门后就是 BusStop 地图。
- 进入 BusStop 地图后别再 enter 'BusStop'，换地图要用该地图自己的出口 Desert/Town/Farm/Backwoods。
- go_to 报 NO_PATH 且提示 'farmer is at (x,y)' 时说明位置已变，别拿旧坐标继续试，改朝当前地图内相邻格走或用 move 脱身。
- 出门后 go_to (65,18) 仍会先报一次 NOT_FREE 'farmer cannot move right now'，补一次 noop 再重发同一目标即可成功。

## 2026-10-16
- 出门流水线今天照旧：exit_building → go_to (65,18)（首次报 NOT_FREE 就补一次 noop 再重发同一目标）→ 再做后续动作。
- 进 BusStop 后别再 enter 'BusStop'，换地图要用该地图自己的出口（Desert/Town/Farm/Backwoods）。
- go_to 报 NO_PATH 且提示 'farmer is at (x,y)' 时别继续用旧坐标，改用 move 方向脱身，再发当前地图内的新目标。
- 到新地图后可用 scan radius 15~20 确认周边可行走格，再决定下一个 go_to 目标。

## 2026-10-16
- enter 报 NOT_FREE 'farmer cannot move right now' 时不要换目标，补一次 noop 后重发同一 enter 即可成功。
- 去镇子流程：exit_building 落在 Farm (64,15) → go_to (78,15)（约 19 步）→ enter 'BusStop' → 在 BusStop 内再 enter 'Town'。
- 在 BusStop 内换地图要用该地图自己的出口（Town/Desert/Farm/Backwoods），不要重复 enter 'BusStop'。
- 到 Town 后可直接发镇内坐标 go_to（如 (30,58) 35 步、(50,55) 24 步），无需再出门动作。

## 2026-10-16
- 浇水日到 (65,18) 先直接 select_slot 2 + water_all，别再先发 harvest_all（今天它返回 nothing to do）。
- water_all 队列被某个 thirsty_crop 卡住会整体取消，改单独 go_to 到 (68,18) 附近手动补浇那一格。
- 夏季浇水量级降到 31 格且可能中途取消，water_all 发完要看返回的 done/given up 计数确认是否真浇完。
- go_to (65,18) 报 NOT_FREE 时的“补 noop 再重发”只在报错时用；今天首次即 ok，不必预加重试。

## 2026-10-16
- water_all 连续两次 cancelled 就别再重发，直接 go_to 到卡住的 thirsty_crop 旁边（今天 (66,24)，可站 (67,24)/(66,23) 一带）用喷壶 use_tool_at 手动补浇。
- 夏季 water_all 会中途取消，每次发完必须读返回的 done/given up 计数，不能默认浇完。
- 出门 go_to (65,18) 只有报 NOT_FREE 时才补 noop 重发；今天首次即 ok，不用预加重试。
- 一天浇水被多次取消时，优先保证把 (66,24) 这类顽固 thirsty_crop 手动浇掉，再收工，别把清障/耕地挤进同一天。

## 2026-10-16
- 夏季 water_all 被个别 thirsty_crop 卡住取消且 0 done 时，直接 go_to 到它旁边一格（如 (66,24) 旁站 (67,24)）用喷壶 use_tool_at 手动补浇，别硬重发。
- 补浇一个顽固 thirsty_crop 后重发 water_all 往往会在下一个卡住（今天 (68,18)）；连续两次 cancelled 就放弃 water_all，改逐个 go_to + use_tool_at 手动浇。
- 卡在 (68,18) 时去相邻可行走的 (67,18)（从 (67,24) 约 7 步）站定再 use_tool_at (68,18)。
- 每次 water_all 发完必读返回的 done/given up 计数，今天两次都是 0 done，等于没浇，不能当成功。

## 2026-10-16
- water_all 返回 '0 done ... cancelled (thirsty_crop at (x,y) would not budge)' 时不要重发，先 go_to 到该格相邻可行走格（如 (67,23) 旁站 (66,23)，2 步可达）用 use_tool_at 手动补浇。
- 站在 (66,23) 时 use_tool 打的是 (66,24)，要浇正对的 (67,23) 必须显式 use_tool_at (67,23)。
- 补浇一个卡住的 thirsty_crop 后重发 water_all 往往会卡在下一个（今天 (67,23)→(68,18)），再次 0 done 就放弃 water_all，改逐个 go_to + use_tool_at 手动浇。
- water_all 只看 done 计数：done=0 就等于没浇，不能因为 rc=ok 就当当天浇完收工。

## 2026-10-16
- 去镇子用今天验证过的直达流水线：exit_building → go_to (78,15)（19 步）→ enter 'BusStop' → enter 'Town'，全程无 NOT_FREE，不必预加 noop 重试。
- 目的地是 BusStop 门口时直接 go_to (78,15)，不要先绕去 (65,18)。
- Town 内可以一次长距离 go_to（今天 (45,58) 50 步成功），不需要分段走。

## 2026-10-16
- 夏季 water_all 返回 '0 done' 就是没浇：今天卡在 (65,23)，必须读 done/given up 计数并手动补浇该格。
- 补浇 water_all 卡住格可直接 go_to 卡住格旁（今天 (65,18) 站定用 use_tool_at (65,23) 一次 ok），不必绕远。
- 出门 go_to (65,18) 只在报 NOT_FREE 时才补 noop 重发；今天（y1-summer-08）首次即 ok。
- 去镇子流水线 exit_building → go_to (78,15) → enter 'BusStop' → enter 'Town' 今天再次全程成功，步数按实际返回（今天 21 步）为准。

## 2026-10-16
- 夏季 water_all 今天又是 0 done 卡在 (65,22)：直接 go_to (66,22)（从 (65,18) 仅 2 步）站定 use_tool_at (65,22) 手动补浇，一次 ok。
- water_all 返回 '0 done ... cancelled' 时不要重发，改逐个 go_to 卡住格旁 + use_tool_at 补浇，done=0 就等于当天没浇。
- 补浇完顽固 thirsty_crop 后可直接走镇子流水线：go_to (78,15)（今天 20 步）→ enter 'BusStop' → enter 'Town'，全程无 NOT_FREE。
- 出门 go_to (65,18) 今天首次即 ok（5 步），不用预加 noop 重试；只有返回 NOT_FREE 时才补 noop 再重发。

## 2026-10-16
- (65,22) 已连续两天（y1-summer-09/10）卡住 water_all 且 0 done，直接在 (65,18) 站定 use_tool_at (65,22) 手动补浇，不要重发 water_all。
- use_tool_at 一次只打一格：补完 (65,22) 要再显式 use_tool_at (65,23)，两格各自返回 ok 才算当天浇完。
- 出门 go_to (65,18) 只在返回 NOT_FREE 时才补 noop 重发；今天首次即 ok（5 步），不必预加重试。
- 补浇完顽固格后直接走镇子流水线：go_to (78,15)（今天 21 步）→ enter 'BusStop' → enter 'Town'，全程无 NOT_FREE。

## 2026-10-16
- water_all 已连续三天 0 done 卡在 (65,22)，且手动 use_tool_at (65,22) 返回 ok 后再发 water_all 仍卡同一格：当天直接放弃 water_all，改逐个 go_to 旁站 + use_tool_at 手动浇再收工。
- use_tool_at 返回 ok 只代表挥了一下工具，不代表 thirsty_crop 被解除；判断浇没浇成只看 water_all 的 done 计数。
- 出门 go_to (65,18) 今天两次都 ok，不必预加 noop；只有返回 NOT_FREE 时才补 noop 再重发。
- 补浇顽固格可按相邻格一次站定连打：(65,22)、(65,23)、(66,22) 三个 use_tool_at 今天全 ok，比来回 go_to 省步。

## 2026-10-16
- 进 SeedShop 用 Town 内流水线：go_to (45,58) → go_to (45,57) → enter 'SeedShop'，今天一次成功。
- 去镇子仍走 exit_building → go_to (78,15)（今天 19 步）→ enter 'BusStop' → enter 'Town'，不必预加 noop 重试。
- 今天一整天没浇水：出门前若作物还渴，先补一次 water_all 或手动补浇再进城，别把农活全跳掉。

## 2026-10-16
- 当天不打算浇水的进城流水线：exit_building → go_to (78,15)（今天 19 步）→ enter 'BusStop' → enter 'Town'，今天四步全 ok，不必预加 noop。
- 进 SeedShop 前别往 (54,56) 走（今天 NO_PATH）；到 (45,58) 后直接 go_to (45,57)（2 步）再 enter 'SeedShop'。
- 已连续两天（summer-12/13）没浇水就进城：出门前若看到 thirsty_crop，先手动 use_tool_at 补浇再走镇子流水线。

## 2026-10-16
- (65,22) 又卡 water_all（summer-09/10/11/14 四次）时不要再重发或指望手动挥壶解卡：今天四次 use_tool_at 全 ok 该格依然卡，判断浇没浇成只看 done 计数。
- 出门 go_to (65,18) 今天首次即 ok（5 步），不必预加 noop，只有返回 NOT_FREE 时才补 noop 重发。
- water_all 返回 done=0 就当天没浇：已连续多天（summer-12~14）零浇水，确认计数后干脆改用逐个 use_tool_at 补浇再走镇子流水线。
- 卡格随顺序漂移（今天 (65,22)→(66,22)），所以别盯单格，用 done 计数判断还剩多少没浇。

## 2026-10-16
- water_all 第一次返回 'refill: 0 done ... blocked - farmer cannot move right now' 时不要当失败，原样重发一次即可真正开始浇，done 计数才是判断依据。
- 卡格会漂移（今天 (69,24)），别盯着 (65,22)：看 water_all 返回里的 given up 坐标，再 go_to 格旁 use_tool_at 手动补浇。
- 出门 go_to (65,18) 首次返回 ok 就不用补 noop；只有 NOT_FREE 时才 noop 后重发。
- 当天 water_all 有 done>0 就算有效浇水，收工后可再走镇子流水线 go_to (78,15) → enter 'BusStop' → enter 'Town'。

## 2026-10-16
- water_all 的 'of N' 总量今天从 30 左右掉到 8：先核对 done 与总量，总数骤降说明田地可浇作物大幅减少，别再按旧规模预期 30 多格。
- 卡格继续漂移，今天漂到 (70,27)：判断卡在哪只看 water_all 返回里的 given up 坐标，不要盯旧的 (65,22)。
- 出门 go_to (65,18) 今天首次即 ok（5 步），不必预加 noop；只有返回 NOT_FREE 时才补 noop 重发。
- water_all 返回 done=0 就等于当天没浇，补浇完顽固格后直接走 town 流水线 go_to (78,15)（21 步）→ enter 'BusStop' → enter 'Town'。

## 2026-10-16
- water_all 已连续五次（summer-09/10/11/14/17）0 done 卡在 (65,22)，且当天三次手动 use_tool_at 全 ok 后重发仍卡同格：当天直接放弃 water_all，只看 done/given up 计数判断浇没浇成。
- 可浇总量继续走低（今天 28），别再按 30+ 预期 water_all 规模，先看返回里的 of N 与 done。
- 到 (65,18) 后 go_to (65,21)（4 步）即可站定在顽固格旁手动补浇，比反复 go_to (65,18) 重发 water_all 省步。
- 出门 go_to (65,18) 今天首次即 ok（5 步），不必预加 noop；只有返回 NOT_FREE 时才补 noop 再重发。

## 2026-10-16
- 出门 go_to (65,18) 今天首次即 ok（5 步），只有返回 NOT_FREE 时才补 noop 重发。
- water_all 返回 done=0 就等于当天没浇；卡格今天漂到 (66,22)，只按返回里的 given up 坐标判断，不要盯旧的 (65,22)。
- 补浇前先 go_to 到返回坐标的相邻格再 use_tool_at，别在原地反复重发 water_all。
- 当天确认没浇成后直接走镇子流水线 go_to (78,15)（21 步）→ enter 'BusStop' → enter 'Town' → go_to (45,58)（50 步），今天四步全 ok。

## 2026-10-16
- water_all 又返回 done=0 且卡 (65,22)：当天直接放弃 water_all，改到 (65,21) 用 use_tool_at 逐格补浇，别指望重发能过。
- 可浇总量继续下滑（今天 28），判断浇没浇只看 done 与 of N，不要按 30+ 预期。
- 出门 go_to (65,18) 今天首次即 ok（5 步），不必预加 noop；只有 NOT_FREE 时才 noop 重发。
- 确认没浇成后就走镇子流水线 go_to (78,15)（21 步）→ enter 'BusStop' → enter 'Town'，今天四步全 ok。

## 2026-10-16
- water_all 又返回 done=0 且卡 (65,22)：当天不要重发或指望手动挥壶解卡，确认计数后直接走镇子流水线。
- 可浇总量今天 28，与近几天一致，判断浇没浇只看 done 与 of N，不要按 30+ 预期。
- 确认没浇成后就走 go_to (78,15)（21 步）→ enter 'BusStop' → enter 'Town'，今天三步全 ok。
- 进城后 go_to (-1,89)（71 步）可走通，可作为 Town 内的一个可用目标点。

## 2026-10-16
- water_all 卡 (65,22) 时再去 (65,21) 手动 use_tool_at 也没用（今天照旧 0 done 同格），确认 done=0 后当天直接走流水线，别浪费动作。
- 出门 go_to (65,18) 今天首次即 ok（5 步），不必预加 noop；只有返回 NOT_FREE 时才 noop 重发。
- 判断浇没浇只看返回里的 done 与 of N（今天 of 28、done 0），不要按 30+ 格预期水量。
- 收工走镇子流水线时 go_to (78,15)（今天从 (65,21) 出发 24 步）ok，且该点 refill 可用，出 Farm 前可先在这补满喷壶。

## 2026-10-16
- 判断浇没浇只看返回里的 done 与 of N：今天 of 1、done 1，可浇量已跌到个位数，别再按 30 格预期规模。
- 可浇总量降到 1 说明田里几乎没剩可浇作物，浇完立刻把当天重点转到播种/收获等别的农活，别在 (65,18) 反复重发 water_all。
- water_all 返回以 'refill' 开头就是它自己去补喷壶水、随后正常 finished，不必手动补水或重发。
- 收工照走 go_to (78,15)（今天从 (65,18) 出发 23 步 ok）→ enter 'BusStop' → enter 'Town'。

## 2026-10-16
- 要进城采购时可直接跳过浇水：出门 go_to (78,15) → enter 'BusStop' → enter 'Town' → go_to (45,58) → enter 'SeedShop'，今天五步全 ok。
- 站在 Town (45,58) 直接 enter 'SeedShop' 即可开门，不必再多走一步到 (45,57)。
- SeedShop 内 go_to (5,17) 可达（15 步），可作为店内固定站位点。
- water_all 卡 (65,22) 的日子干脆不发浇水指令、直接走镇子流水线，比反复重发省时间。

## 2026-10-16
- 出门 go_to (65,18) 今天首次即 ok（5 步），不必预加 noop；只有返回 NOT_FREE 时才补一次 noop 重发。
- 浇水卡格连续多天 done=0 的日子，直接跳过浇水，改做 till_area/plant_all：今天 (65,21) radius 2 锄地 14 格 + 混合种子 plant_all 16 格全成，比反复重发 water_all 有产出。
- till_area 卡住的是树（今天 (63,20) tree），树/障碍不用反复重试，换块地或直接用 radius 覆盖可耕格即可。
- 收工照旧 go_to (78,15)（今天 17 步 ok）→ enter 'BusStop'，进镇前可先在 (78,15) refill 补满喷壶。

## 2026-10-16
- 出门 go_to (65,18) 今天首次即 ok（5 步），不必预加 noop；只有返回 NOT_FREE 时才补 noop 重发。
- water_all 今天 16 done / of 16、零卡格，说明 (65,22) 卡格已解除，可以恢复每天正常发 water_all，不必再跳过浇水。
- 判断浇没浇只看返回里的 done 与 of N：今天 of 16、done 16，可浇量从个位数回升，别按 1 格或 30+ 格预期。
- 卡格消失后不必再去 (65,21) 手动 use_tool_at 补浇，直接一次 water_all 即可。

## 2026-10-16
- water_all 卡格换了新格 ((66,23))：只要返回 done<of 且带 given up，当天别原地重发，直接转 till_area 等别的农活。
- till_area 可耕格从 14 掉到 3，说明 (65,21) 一带基本耕完，别按大批量预期，radius 2 一次即可收尾。
- 出门 go_to (65,18) 首次即 ok（5 步），不必预加 noop；只有返回 NOT_FREE 时才补 noop 重发。
- 判断浇没浇只看 done 与 of N：今天 of 41、done 12，可浇量已回升，别按 16/1 的小量或 30 格旧值预期。

## 2026-10-16
- (65,21) 锄地今天直接 FAILED（卡 (65,20) 作物），别再把它当默认耕地，改发 (69,26) 或 (66,27) radius 2。
- till_area 返回里出现 'water_all: ... thirsty_crop ... would not budge' 就是被未浇作物挡死，别原地重发，直接换地块。
- 翻完地立刻 select_slot 5 + plant_all，今天三次全部 4/4/9 done 零放弃，是可靠产出组合。
- 出门 go_to (65,18) 首次即 ok 的收益有限，可直接从 (65,18) 转去新地块 (69,26) 开工。

## 2026-10-16
- 去 (69,26) 一带翻地/播种时，别把 'plant_all: N done ... - finished' 当成结果：若多条命令返回逐字相同、耗时也相同（今天都是 6.9s），视为缓存，换目标格或换命令验证。
- 换块地前先避开树格：go_to (65,26) 会 NO_PATH，应直接瞄相邻的 (65,25)（今天从 (69,26) 出发 6 步 ok）。
- till_area 返回里带 'tree at (x,y) would not budge' 就是被树挡死，别原地重发，绕到树另一侧的可耕格。
- (65,25) radius1 只出了 2 格，说明这块地很小，收尾时用 radius 1 即可，别按大批量预期。

## 2026-10-16
- water_all 返回 done<of 且带 'tree at (x,y) would not budge' 时，当天放弃格是树，别原地重发，只在剩余可浇格手动收尾。
- 可浇量已掉到 of 4 这种个位数，别再按 of 30+ 预期排产，浇水一两分钟就结束。
- 判断 water_all 结果只看 done/of/given up，返回前缀可能是 'till_area:'，别被误导当锄地。
- 出门 go_to (65,18) 首次即 ok（5 步），不必预加 noop；只有返回 NOT_FREE 时才补 noop 重发。

## 2026-10-16
- 出门 go_to (65,18) 首次即 ok（5 步）已是常态，不必预加 noop；只有返回 NOT_FREE 时才补一次 noop 重发。
- 不想碰 water_all 卡格的日子，直接照今天这条流水线走：(65,18) → (78,15) → BusStop → Town → (45,58) → (45,57) → SeedShop。
- 进镇前一天若没发任何农活指令，当天就是零产出，出门前先决定是浇水/锄地还是纯跑镇子，别默认只赶路。
- 季节刚换（fall-01）时流水线仍然全 ok，说明换季不重置已知出口与镇内路线，可直接沿用旧路径。

## 2026-10-16
- 换季后出门照旧走 (64,15) → (65,18) → (78,15) → BusStop → Town → (45,58) → (45,57)，今天全 ok，可直接照抄。
- 要从 Town (45,57) 往镇子更深处走，go_to (55,55) 13 步可到。
- 只排跑腿指令的日子农活就是零产出，出门前先决定要不要先 water_all / till_area 再进镇。
- 今天没发 water_all，不能据此断言 (65,22) 卡格已解除，下次浇水仍按 done/of 判断。

## 2026-10-16
- water_all 返回 '0 done, N given up' 时当天这次浇水等于零产出，别原地重发，直接转 till_area/播种或收工。
- (65,22) 是跨季节固定卡格：排产时先把它从可浇格里扣掉，再拿 done/of 判断实际浇了多少。
- 换季（fall 头几天）不会重置 water_all 的卡格，仍按夏天那套卡格预期，别指望新季节变干净。
- 出门 go_to (65,18) 仍可首次直发，只有返回 NOT_FREE 才补一次 noop 重发。

## 2026-10-16
- (63,20) 是 fall-04 新出现的 water_all 卡格，说明卡格不只在 (65,22)，排产时把这两个已见卡格一起从可浇格里扣掉。
- water_all 返回 done=0（哪怕 of 49）时当天这次浇水就是零产出，别原地重发，直接转别的活或收工。
- 可浇量已从 of 30 回升到 of 49，别再按 of 30 或 of 4 的旧值预估当天浇水量。
- 出门 go_to (65,18) 首次直发即可，只有返回 NOT_FREE 时才补一次 noop 重发。

## 2026-10-16
- 出门 go_to (65,18) 首次直发即可（今天 5 步 ok），只有返回 NOT_FREE 时才补一次 noop 重发。
- water_all 返回 done=0（哪怕 of 49、given up 11）时当天这次浇水零产出，别原地重发，直接转 till_area/播种或收工。
- 排产前把 (65,22)、(63,20) 这两个已见卡格从可浇格里扣掉，fall 季节仍按这套卡格预期。
- water_all 卡格拖时间：given up 数越大耗时越长（11 个卡格拖到 45s），别把这 45s 当成浇了 49 格。

## 2026-10-16
- 只跑腿的日子农活就是零产出，出门前先决定要不要先 water_all / till_area 再进镇。
- 今天没发 water_all，不能据此断言 (65,22)/(63,20) 卡格已解除，下次浇水仍按 done/of/given up 判断。
- fall-06 这条流水线全 ok，可直接照抄：(64,15)→(65,18)→(78,15)→BusStop→Town→(45,58)→(45,57)→SeedShop。
- go_to (65,18) 首次直发即 ok（5 步）是常态，只有返回 NOT_FREE 时才补一次 noop 重发。

## 2026-10-16
- 农活标准流水线今天全 ok，可直接照抄：(64,15) → go_to (65,18) → select_slot 1 → till_area (64,18) r2 → select_slot 5 → plant_all → select_slot 2。
- till_area 也会卡格：今天 (62,20) 返回 'no path to (62,20)'，排锄地任务时把该格从预期格数里扣掉，done/of 才准。
- plant_all 在 till_area 之后可一次播完（今天 8 done 0 given up），锄完就接播种，别空等。
- go_to (65,18) 首次直发即 ok（5 步）仍是常态，只有返回 NOT_FREE 时才补一次 noop 重发。

## 2026-10-16
- 可浇格数已骤降到 of 8（0 given up、finished），别再按 fall 早前的 of 49 预估当天浇水量，一律按 done/of 判断。
- water_all 返回的文本标签可能写成 plant_all，语义仍是浇水；结果只看 done/of/given up 与 finished/cancelled。
- 今天只有 8 格可浇，(65,22)/(63,20) 卡格没被触发，不能据此断言卡格已解除，下次浇水仍按 done/given up 判断。
- 农活标准流水线 (64,15) → go_to (65,18) → select_slot 2 → water_all 今天一次全 ok，可直接照抄。

## 2026-10-16
- water_all 已见卡格至少三个：(65,22)、(63,20)、(63,19)，排产前一起从可浇格里扣掉，再按 done/of 判断。
- of=53、given up=11 时耗时 42.4s，done=0 就是零产出，别原地重发，直接转 till_area/播种或收工。
- go_to (65,18) 首次直发即 ok（5 步）仍是常态，只有返回 NOT_FREE 时才补一次 noop 重发。
- 可浇格数波动大（of 8 ↔ of 53），一律按当天 done/of 判断，别用旧值预估浇水量。

## 2026-10-16
- water_all 回 done=0（今天 of=51、given up=11、46s）就是当天浇水零产出，别原地重发，直接转 till_area/播种或收工。
- (63,19) 卡格在 fall-10 复现，排产前把 (65,22)、(63,20)、(63,19) 一起从可浇格里扣掉，再按 done/of 判断。
- go_to (65,18) 首次直发即 ok（5 步）仍是常态，只有返回 NOT_FREE 时才补一次 noop 重发。
- 出门只接了 water_all 且全卡的日子等于零农活产出，出门前先决定是否先 till_area/播种再进镇。

## 2026-10-16
- go_to (65,18) 首次直发即 ok（5 步）仍是常态，只有返回 NOT_FREE 时才补一次 noop 重发。
- water_all 回 done=0（今天 of=51、given up=11、46s）就是当天浇水零产出，别原地重发，直接转 till_area/播种或收工。
- (63,19) 卡格在 fall-11 复现，排产前把 (65,22)、(63,20)、(63,19) 一起从可浇格里扣掉，再按 done/of 判断。
- 只接 water_all 且全卡的日子等于零农活产出，出门前先决定要不要先 till_area/播种再进镇。

## 2026-10-16
- harvest_all 也会卡格：今天 6 格全 given up（卡在 (69,23) ready_crop）、耗时 23.5s，排产前把卡格从可收格里扣掉，done/of 才是真产量。
- 卡住的 ready_crop 可以手动兜底：今天 interact_at (69,23) 返回 True，harvest_all 报 would not budge 时直接对那格发 interact_at。
- till_area 的 (62,20) 卡格在 fall-12 复现（no path），排锄地任务时把它从预期格数里扣掉。
- 今天只锄 1 格、播 1 格、收 0 格，等于零农活产出：出门前先确认 harvest_all 的 done/of，别把 23.5s 的 given up 耗时当成收成。

## 2026-10-16
- 锄地别用 (64,18) 半径 2（(62,20) 必卡），直接改发 till_area (61,18) 半径 2 一次可 done=9。
- till_area 回 given up 后不要接 plant_all：它会 FAILED 并复述同一报文，先换中心点重锄再播。
- water_all 报文可能被标成 plant_all，只看 done/of/given up 与 finished/cancelled 判断结果。
- 排产时把 (62,20) 从可锄/可播格数里扣掉，按 done/of 计产量（今天锄 9、播 8、浇 8）。

## 2026-10-16
- water_all 卡格名单新增 (61,20)，排产前把 (65,22)、(63,20)、(63,19)、(61,20) 一起从可浇格里扣掉，再按 done/of 判断。
- water_all 回 done=0（今天 of=60、given up=2、10.4s）就是零浇水产出，别原地重发，直接转 till_area/播种或收工。
- 出门先跑 (64,15) → go_to (65,18) → select_slot 2 → water_all 的流水线今天一次全 ok，只有 NOT_FREE 时才补 noop 重发。
- of 值在 30~60 间波动大，一律按当天 done/of/given up 判断，别用旧值预估浇水量。

## 2026-10-16
- 出门 (64,15) → go_to (65,18) → select_slot 2 → water_all 流水线今天一次全 ok（5 步直达），NOT_FREE 时才补 noop 重发。
- water_all 卡格名单新增 (62,18)：排产前把 (65,22)、(63,20)、(63,19)、(61,20)、(62,18) 一起从可浇格里扣掉，再按 done/of 判断。
- water_all 回 done=0（今天 of=59、given up=11、41.9s）就是当天浇水零产出，别原地重发，直接转 till_area/播种或收工。
- 只排一条 water_all 且全卡的日子等于零农活产出，出门前先决定要不要先 till_area/播种再收工。

## 2026-10-16
- 回包命令名会串：今天 till_area 回 'water_all: ...'、water_all 回上一条 plant_all 的报文，只看 done/of/given up 和卡格，别按报文头判断做了什么。
- water_all 回包与上一条 plant_all 完全重复时按无效回包处理，视作零浇水产量，出门前先决定要不要先 till_area/播种再收工。
- 卡格名单更新：(62,18) 加入 (65,22)、(63,20)、(63,19)、(61,20)，排产前一起从可锄/可浇格数里扣掉，再按当天 done/of 计产量。
- 出门 (64,15) → go_to (65,18)（5 步）今天一次 ok，只有在 NOT_FREE 时才补一次 noop 重发。

## 2026-10-16
- till_area 中心 (61,18) 半径 2 全天 0 产出（必卡 (62,20) 无路），要锄地就改发 (61,19) 或 (60,19) 半径 2。
- water_all 回包今天与上一条 plant_all 完全相同（3 done, 0 given up），按无效回包处理，当天浇水产量不可信。
- 排产锄地时把 (62,20)、(60,21) 与 (62,21) 的树一起从预期格数里扣掉，只按 done/of 计产量（今天锄 3、播 3）。
- 出门链 (64,15) → go_to (65,18)（5 步）→ 锄/播 → go_to (78,15)（22 步）→ enter 'BusStop' 今天全 ok，只有 NOT_FREE 时才补 noop 重发。

## 2026-10-16
- [处方] 给 water_all 加了前置条件：{'fact': 'held.charges', 'op': '>', 'value': 0}，不足时先 refill_watering_can。原因：喷壶是空的（charges 0/40），所以每次浇水都没有效果，水壶不会消耗也不改变土壤，agent 就此卡在 (61,20)。
