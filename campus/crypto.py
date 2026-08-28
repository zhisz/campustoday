"""Protocol crypto boundary.

The legacy algorithms are intentionally not activated. Public examples are from
2021/2022 and cannot establish the current protocol or keys. Implement this only
after observing and authorizing the current institution-specific flow.
"""


class UnsupportedProtocol(RuntimeError):
    pass


def build_signed_payload(*_args, **_kwargs):
    raise UnsupportedProtocol("Current CampusToday signing protocol is not verified")

