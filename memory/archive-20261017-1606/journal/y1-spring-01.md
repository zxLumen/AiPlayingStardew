# 游戏内 y1-spring-01

- step 1: `exit_building` {} -> ok=True rc=- left FarmHouse -> Farm (64,15)
- step 2: `scan` {"radius": 8} -> ok=True rc=- scan around (64,15) r=8
- step 3: `clear_area` {"x": 64, "y": 18, "radius": 3} -> ok=True rc=- clear_area: 10 done, 4 given up of 14 in 8.9s - finished ((no path to (62,21)))
- step 4: `till_area` {"x": 67, "y": 21, "radius": 2} -> ok=True rc=- till_area: 20 done, 0 given up of 20 in 20.6s - finished
- step 5: `go_to` {"x": 80, "y": 16} -> ok=True rc=- walking to (80,16) - 20 steps
- step 6: `warp` {"location": "BusStop", "x": 59, "y": 16} -> ok=True rc=- warped to BusStop (59, 16)
- step 7: `go_to` {"x": 65, "y": 22} -> ok=False rc=NO_PATH no walkable path to (65,22) - it is probably blocked. The farmer is at (59,16); aim at an adjacent free tile instead.
- step 8: `warp` {"location": "Town", "x": 30, "y": 60} -> ok=True rc=- warped to Town (30, 60)
