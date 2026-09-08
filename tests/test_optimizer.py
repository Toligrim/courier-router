import pytest

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


def test_unreachable_arc_is_not_treated_as_zero_cost():
    stops = [stop(1), stop(2)]
    # 0->1 is unavailable. A zero-coercion bug would make 0->1->2 look best;
    # the valid route is 0->2->1->0.
    dur = [
        [0, None, 60],
        [60, 0, 60],
        [60, 60, 0],
    ]
    dist = [
        [0, None, 1000],
        [1000, 0, 1000],
        [1000, 1000, 0],
    ]
    sol = solve_single_vehicle(stops, dur, dist, depart_min=600, time_limit_sec=1)
    assert sol.feasible
    assert [v.stop_index for v in sol.visits] == [1, 0]


def test_rejects_malformed_matrix_shape():
    stops = [stop(1)]
    with pytest.raises(ValueError, match="duration matrix"):
        solve_single_vehicle(
            stops,
            durations=[[0, 60]],
            distances=[[0, 1000], [1000, 0]],
            depart_min=600,
            time_limit_sec=1,
        )


def test_rejects_negative_matrix_values():
    stops = [stop(1)]
    with pytest.raises(ValueError, match="некорректное значение"):
        solve_single_vehicle(
            stops,
            durations=[[0, -1], [60, 0]],
            distances=[[0, 1000], [1000, 0]],
            depart_min=600,
            time_limit_sec=1,
        )
