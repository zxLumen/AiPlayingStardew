# 游戏内 y1-spring-05

- step 1: `exit_building` {} -> ok=True rc=- left FarmHouse -> Farm (64,15)
- step 2: `scan` {"radius": 8} -> ok=True rc=- scan around (64,15) r=8
- step 3: `select_slot` {"index": 1} -> ok=True rc=- selected slot 1: 锄头
- step 4: `till_area` {"x": 67, "y": 21, "radius": 2} -> ok=True rc=- till_area: 4 done, 0 given up of 4 in 5.2s - finished
- step 5: `till_area` {"x": 67, "y": 25, "radius": 2} -> ok=True rc=- till_area: 3 done, 0 given up of 3 in 2.8s - finished
- step 6: `go_to` {"x": 43, "y": 23} -> ok=True rc=- walking to (43,23) - 47 steps
- step 7: `enter` {"target": "Town"} -> ok=False rc=FAILED nothing here leads to 'Town'. Known exits: BusStop, Forest, Backwoods, FarmCave
- step 8: `go_to` {"x": 80, "y": 15} -> ok=True rc=- walking to (80,15) - 52 steps
- step 9: `go_to` {"x": 43, "y": 23} -> ok=True rc=- walking to (43,23) - 33 steps
