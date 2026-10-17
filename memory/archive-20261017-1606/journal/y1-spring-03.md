# 游戏内 y1-spring-03

- step 1: `exit_building` {} -> ok=True rc=- left FarmHouse -> Farm (64,15)
- step 2: `scan` {"radius": 8} -> ok=True rc=- scan around (64,15) r=8
- step 3: `till_area` {"x": 67, "y": 21, "radius": 2} -> ok=True rc=- till_area: 3 done, 0 given up of 3 in 4.7s - finished
- step 4: `till_area` {"x": 67, "y": 25, "radius": 2} -> ok=True rc=- till_area: 10 done, 0 given up of 10 in 11.5s - finished
- step 5: `go_to` {"x": 79, "y": 16} -> ok=True rc=- walking to (79,16) - 25 steps
- step 6: `go_to` {"x": 80, "y": 16} -> ok=True rc=- walking to (80,16) - 2 steps
- step 7: `go_to` {"x": 30, "y": 60} -> ok=False rc=NO_PATH no walkable path to (30,60) - it is probably blocked. The farmer is at (11,23); aim at an adjacent free tile instead.
- step 8: `go_to` {"x": 43, "y": 23} -> ok=True rc=- walking to (43,23) - 33 steps
- step 9: `enter` {"target": "Town"} -> ok=True rc=- walking to the door of 'Town' - it opens on arrival
