from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

class Operation(str, Enum):
    PICKUP = "pickup"
    DELIVERY = "delivery"

@dataclass
class TimeWindow:
    start_min: int
    end_min: int
    raw: str = ""

@dataclass
class Payment:
    amount_rub: int | None = None
    method: str | None = None
    raw: str = ""

@dataclass
class GeoPoint:
    lat: float
    lon: float
    provider: str
    normalized_address: str
    precision: str = "unknown"
    confidence: float = 0.0
    provider_ref: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

@dataclass
class Stop:
    source_row: int
    operation: Operation
    order_no: int
    phone: str
    district: str
    address_raw: str
    access: str
    window: TimeWindow | None
    payment: Payment
    comment: str = ""
    service_min: int = 10
    shipment_id: str | None = None
    geo: GeoPoint | None = None
    warnings: list[str] = field(default_factory=list)

@dataclass
class RouteVisit:
    stop_index: int
    arrival_min: int
    departure_min: int
    travel_sec_from_prev: int
    distance_m_from_prev: int
    late_by_min: int = 0

@dataclass
class RouteSolution:
    visits: list[RouteVisit]
    total_distance_m: int
    total_travel_sec: int
    total_service_sec: int
    total_wait_sec: int
    feasible: bool = True
    warnings: list[str] = field(default_factory=list)
    used_soft_windows: bool = False
    total_late_min: int = 0
