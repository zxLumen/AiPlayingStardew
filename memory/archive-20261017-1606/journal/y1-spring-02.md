# 游戏内 y1-spring-02

- step 1: `exit_building` {} -> ok=True rc=- left FarmHouse -> Farm (64,15)
- step 2: `scan` {"radius": 8} -> ok=True rc=- scan around (64,15) r=8
- step 3: `clear_area` {"x": 64, "y": 18, "radius": 3} -> ok=True rc=- clear_area: 2 done, 2 given up of 4 in 2.4s - finished ((no path to (61,20)))
- step 4: `till_area` {"x": 67, "y": 21, "radius": 2} -> ok=True rc=- till_area: 8 done, 0 given up of 8 in 10.3s - finished
- step 5: `till_area` {"x": 67, "y": 21, "radius": 3} -> ok=True rc=- till_area: 19 done, 0 given up of 19 in 30.9s - finished
- step 6: `warp` {"location": "Town", "x": 30, "y": 60} -> ok=True rc=- warped to Town (30, 60)
- step 7: `scan` {"radius": 8} -> ok=True rc=- scan around (30,60) r=8
- step 8: `go_to` {"x": 35, "y": 55} -> ok=False rc=NO_PATH no walkable path to (35,55) - it is probably blocked. The farmer is at (30,60); aim at an adjacent free tile instead.
