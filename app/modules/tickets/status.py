"""Ticket state is independent of future field-work state."""

from enum import StrEnum


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"
