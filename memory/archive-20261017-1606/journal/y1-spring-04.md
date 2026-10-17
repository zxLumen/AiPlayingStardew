# 游戏内 y1-spring-04

- step 1: `exit_building` {} -> ok=True rc=- left FarmHouse -> Farm (64,15)
- step 2: `scan` {"radius": 8} -> ok=True rc=- scan around (64,15) r=8
- step 3: `till_area` {"x": 67, "y": 21, "radius": 2} -> ok=True rc=- till_area: 3 done, 0 given up of 3 in 4.3s - finished
- step 4: `till_area` {"x": 69, "y": 24, "radius": 2} -> ok=True rc=- till_area: 1 done, 0 given up of 1 in 0.6s - finished
- step 5: `till_area` {"x": 67, "y": 25, "radius": 2} -> ok=True rc=- till_area: 1 done, 0 given up of 1 in 1.6s - finished
- step 6: `till_area` {"x": 66, "y": 25, "radius": 2} -> ok=True rc=- till_area: 1 done, 0 given up of 1 in 1.1s - finished
- step 7: `till_area` {"x": 70, "y": 22, "radius": 2} -> ok=False rc=FAILED till_area: 1 done, 0 given up of 1 in 1.1s - finished
- step 8: `till_area` {"x": 65, "y": 25, "radius": 2} -> ok=True rc=- till_area: 1 done, 2 given up of 3 in 1.2s - finished ((no path to (63,27)))
- step 9: `clear_area` {"x": 67, "y": 23, "radius": 3} -> ok=True rc=- clear_area: 5 done, 0 given up of 5 in 8.8s - finished
- step 10: `go_to` {"x": 43, "y": 23} -> ok=True rc=- walking to (43,23) - 48 steps
