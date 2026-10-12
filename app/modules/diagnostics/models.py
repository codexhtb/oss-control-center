"""Provenance and availability labels for later snapshots."""

from enum import StrEnum


class Availability(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


class DiagnosticSource(StrEnum):
    MANUAL = "MANUAL"
    LIBRENMS = "LIBRENMS"
    ICINGA = "ICINGA"
    BDCOM = "BDCOM"
