KEYWORDS = ("晚查寝", "查寝", "学生查寝")


def is_attendance_task(name: str) -> bool:
    return any(keyword in (name or "") for keyword in KEYWORDS)

