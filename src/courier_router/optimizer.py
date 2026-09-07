from __future__ import annotations
import math
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from .domain import RouteSolution, RouteVisit, Stop

DAY = 24 * 60
UNREACHABLE_DURATION_SEC = 37 * 3600
UNREACHABLE_DISTANCE_M = 1_000_000_000


def _normalize_matrix(matrix, size: int, unreachable_value: int, name: str) -> list[list[int]]:
    if len(matrix) != size or any(len(row) != size for row in matrix):
        raise ValueError(f"Размер {name} matrix не соответствует количеству точек")

    normalized = []
    for row in matrix:
        out = []
        for value in row:
            if value is None:
                out.append(unreachable_value)
                continue
            value = float(value)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} matrix содержит некорректное значение: {value!r}")
            out.append(int(round(value)))
        normalized.append(out)
    return normalized


def solve_single_vehicle(
    stops: list[Stop],
    durations: list[list[float | None]],
    distances: list[list[float | None]],
    depart_min: int,
    end_mode: str = "depot",
    time_limit_sec: int = 8,
) -> RouteSolution:
    """
    Matrix nodes: 0=depot, 1..N=stops.
    For open end we append a dummy end node with zero inbound/outbound cost.

    Routing providers may return null/None for unreachable pairs. Such arcs get a
    duration beyond the solver horizon, so they cannot be mistaken for free travel.
    """
    n_real = len(stops) + 1
    dur = _normalize_matrix(durations, n_real, UNREACHABLE_DURATION_SEC, "duration")
    dist = _normalize_matrix(distances, n_real, UNREACHABLE_DISTANCE_M, "distance")

    if end_mode == "open":
        for row in dur:
            row.append(0)
        dur.append([0] * (n_real + 1))
        for row in dist:
            row.append(0)
        dist.append([0] * (n_real + 1))
        end_node = n_real
        node_count = n_real + 1
    else:
        end_node = 0
        node_count = n_real

    manager = pywrapcp.RoutingIndexManager(node_count, 1, [0], [end_node])
    routing = pywrapcp.RoutingModel(manager)

    service_sec = [0] + [s.service_min * 60 for s in stops]
    if end_mode == "open":
        service_sec.append(0)

    def transit(index_from, index_to):
        a = manager.IndexToNode(index_from)
        b = manager.IndexToNode(index_to)
        return dur[a][b] + service_sec[a]

    transit_idx = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    # Time is relative to midnight in seconds.
    routing.AddDimension(
        transit_idx,
        12 * 3600,       # max waiting slack
        36 * 3600,       # horizon
        False,
        "Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")
    start_idx = routing.Start(0)
    time_dim.CumulVar(start_idx).SetRange(depart_min * 60, depart_min * 60)

    for i, stop in enumerate(stops, start=1):
        idx = manager.NodeToIndex(i)
        if stop.window:
            time_dim.CumulVar(idx).SetRange(stop.window.start_min * 60, stop.window.end_min * 60)
        else:
            time_dim.CumulVar(idx).SetRange(depart_min * 60, min(36 * 3600, (depart_min + 14*60) * 60))

    # Make waiting visible/minimized secondarily.
    time_dim.SetGlobalSpanCostCoefficient(1)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = int(time_limit_sec)

    sol = routing.SolveWithParameters(params)
    if not sol:
        return RouteSolution([], 0, 0, 0, 0, feasible=False,
                             warnings=["Маршрут с заданными временными окнами не найден"])

    visits = []
    idx = routing.Start(0)
    total_distance = total_travel = total_service = total_wait = 0

    while not routing.IsEnd(idx):
        node = manager.IndexToNode(idx)
        next_idx = sol.Value(routing.NextVar(idx))
        next_node = manager.IndexToNode(next_idx)
        if next_node == end_node and end_mode == "open":
            break
        if next_node == 0 and end_mode == "depot":
            total_distance += dist[node][0]
            total_travel += dur[node][0]
            break

        stop_idx = next_node - 1
        arrival_sec = sol.Value(time_dim.CumulVar(next_idx))
        travel_sec = dur[node][next_node]
        distance_m = dist[node][next_node]

        # Waiting = scheduled arrival - earliest physically possible after prev scheduled time.
        prev_time = sol.Value(time_dim.CumulVar(idx))
        physical = prev_time + service_sec[node] + travel_sec
        wait = max(0, arrival_sec - physical)

        visits.append(RouteVisit(
            stop_index=stop_idx,
            arrival_min=arrival_sec // 60,
            departure_min=(arrival_sec + service_sec[next_node]) // 60,
            travel_sec_from_prev=travel_sec,
            distance_m_from_prev=distance_m,
        ))
        total_distance += distance_m
        total_travel += travel_sec
        total_service += service_sec[next_node]
        total_wait += wait
        idx = next_idx

    return RouteSolution(
        visits=visits,
        total_distance_m=total_distance,
        total_travel_sec=total_travel,
        total_service_sec=total_service,
        total_wait_sec=total_wait,
    )
