"""Ticket state is independent of future field-work state."""

from enum import StrEnum


class TicketType(StrEnum):
    SUBSCRIBER_LINKED = "SUBSCRIBER_LINKED"
    MANUAL = "MANUAL"
    INTERNAL = "INTERNAL"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class TicketPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class ResolutionReason(StrEnum):
    CABLE_BREAK = "CABLE_BREAK"
    CUSTOMER_EQUIPMENT = "CUSTOMER_EQUIPMENT"
    ONU_POWER = "ONU_POWER"
    NETWORK_FAULT = "NETWORK_FAULT"
    CONFIGURATION_IP = "CONFIGURATION_IP"
    CONNECTION_SETUP = "CONNECTION_SETUP"
    CONSULTATION = "CONSULTATION"
    OTHER = "OTHER"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
