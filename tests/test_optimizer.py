from courier_router.domain import Operation, Payment, Stop, TimeWindow
from courier_router.optimizer import solve_single_vehicle

def stop(n, a=0, b=24*60):
    return Stop(n, Operation.DELIVERY, n, "", "", f"addr {n}", "",
                TimeWindow(a,b,f"{a}-{b}"), Payment(), service_min=0)

def test_small_route():
    stops = [stop(1), stop(2)]
    # depot=0; best path 0->1->2->0
    dur = [
        [0, 60, 300],
        [60, 0, 60],
        [300,60,0],
    ]
    dist = [
        [0,1000,5000],
        [1000,0,1000],
        [5000,1000,0],
    ]
    sol = solve_single_vehicle(stops, dur, dist, depart_min=600, time_limit_sec=1)
    assert sol.feasible
    assert [v.stop_index for v in sol.visits] == [0,1]
