# 游戏内 y1-spring-06

- step 1: `exit_building` {} -> ok=True rc=- left FarmHouse -> Farm (64,15)
- step 2: `scan` {"radius": 8} -> ok=True rc=- scan around (64,15) r=8
- step 1: `exit_building` {} -> ok=True rc=- left FarmHouse -> Farm (64,15)
- step 2: `scan` {"radius": 8} -> ok=True rc=- scan around (64,15) r=8
- step 3: `till_area` {"x": 67, "y": 21, "radius": 2} -> ok=True rc=- till_area: 1 done, 0 given up of 1 in 3.3s - finished
- step 4: `till_area` {"x": 67, "y": 25, "radius": 2} -> ok=True rc=- till_area: 2 done, 0 given up of 2 in 2s - finished
- step 5: `till_area` {"x": 69, "y": 28, "radius": 2} -> ok=True rc=- till_area: 2 done, 5 given up of 7 in 1.2s - finished ((no path to (67,30)))
- step 6: `till_area` {"x": 69, "y": 27, "radius": 2} -> ok=True rc=- till_area: 0 done, 2 given up of 2 in 0.1s - finished ((no path to (68,29)))
- step 7: `go_to` {"x": 80, "y": 16} -> ok=True rc=- walking to (80,16) - 23 steps
- step 1: `enter` {"target": "Town"} -> ok=True rc=- walking to the door of 'Town' - it opens on arrival
- step 1: `exit_building` {} -> ok=True rc=- walking to the exit at (3,12) -> Farm
- step 2: `scan` {"radius": 8} -> ok=True rc=- scan around (64,15) r=8
- step 1: `exit_building` {} -> ok=True rc=- walking to the exit at (3,12) -> Farm
- step 2: `scan` {"radius": 8} -> ok=True rc=- scan around (64,15) r=8
- step 3: `go_to` {"x": 80, "y": 16} -> ok=True rc=- walking to (80,16) - 20 steps
