from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class AmsSlot:
    index: int          # 0-3
    material: str       # "PLA", "PETG", etc.
    color: str          # hex color e.g. "#FF0000"
    remaining_pct: int  # 0-100 from AMS sensor


@dataclass
class Printer:
    name: str
    model: str          # "P1S" or "A1"
    serial: str
    ams_slots: list[AmsSlot] = field(default_factory=list)
    current_job: str | None = None
    state: str = "IDLE"  # IDLE/RUNNING/PAUSE/FINISH/FAILED


@dataclass
class FilamentSpool:
    id: int
    name: str
    material: str
    color: str
    brand: str
    total_weight_g: float
    remaining_g: float
    printer_name: str
    ams_slot: int
    low_stock_threshold_g: float


@dataclass
class PrintJob:
    id: int
    printer_name: str
    subtask_name: str
    start_time: str
    end_time: str | None
    status: str
    filament_used: dict  # {slot_index: grams}
