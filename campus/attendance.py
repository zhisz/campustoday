from dataclasses import dataclass


@dataclass(frozen=True)
class AttendanceTask:
    task_id: str
    name: str
    start_time: str
    end_time: str
    completed: bool
    requires_location: bool


class AttendanceClient:
    def list_today(self) -> list[AttendanceTask]:
        raise RuntimeError("Current attendance API is not configured")

    def detail(self, task_id: str) -> AttendanceTask:
        raise RuntimeError("Current attendance API is not configured")

    def submit(self, task: AttendanceTask, location: dict):
        raise RuntimeError("Current attendance API is not configured")

