"""Ticket state is independent of future field-work state."""

from enum import StrEnum


class TicketType(StrEnum):
    SUBSCRIBER_LINKED = "SUBSCRIBER_LINKED"
    MANUAL = "MANUAL"
    INTERNAL = "INTERNAL"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"


class TicketPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"
