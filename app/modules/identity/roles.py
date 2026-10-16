"""Named capabilities and role presets for migration and new accounts."""

from enum import StrEnum


class Role(StrEnum):
    OPERATOR = "operator"
    ENGINEER = "engineer"
    ADMIN = "admin"


class Permission(StrEnum):
    SUBSCRIBER_READ = "subscribers.read"
    SUBSCRIBER_FINANCE_READ = "subscribers.finance.read"
    TICKET_READ = "tickets.read.all"
    TICKET_READ_OWN = "tickets.read.own"
    TICKET_CREATE = "tickets.create"
    TICKET_EDIT_ALL = "tickets.edit.all"
    TICKET_EDIT_OWN = "tickets.edit.own"
    TICKET_DELETE_OWN = "tickets.delete.own"
    TICKET_DELETE_ALL = "tickets.delete.all"
    TICKET_RESTORE = "tickets.restore"
    TICKET_PURGE = "tickets.purge"
    TICKET_COMMENT = "tickets.comment"
    TICKET_START = "tickets.start"
    TICKET_ASSIGN = "tickets.assign"
    TICKET_FIELD_WORK = "tickets.field_work"
    TICKET_MANUAL_DIAGNOSTIC = "tickets.diagnose"
    DIAGNOSTICS_READ = "diagnostics.read"
    DIAGNOSTICS_RUN = "diagnostics.run"
    TICKET_DECISION = "tickets.resolve"
    TICKET_CLOSE = "tickets.close"
    TICKET_CALLBACK = "tickets.callback"
    TICKET_AUDIT_READ = "audit.read"
    IDENTITY_MANAGE = "users.manage"
    USERS_READ = "users.read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OPERATOR: frozenset(
        {
            Permission.SUBSCRIBER_READ,
            Permission.SUBSCRIBER_FINANCE_READ,
            Permission.TICKET_READ,
            Permission.TICKET_CREATE,
            Permission.TICKET_EDIT_OWN,
            Permission.TICKET_COMMENT,
            Permission.DIAGNOSTICS_READ,
        }
    ),
    Role.ENGINEER: frozenset(
        {
            Permission.SUBSCRIBER_READ,
            Permission.TICKET_READ,
            Permission.TICKET_CREATE,
            Permission.TICKET_EDIT_ALL,
            Permission.TICKET_COMMENT,
            Permission.TICKET_START,
            Permission.TICKET_ASSIGN,
            Permission.TICKET_FIELD_WORK,
            Permission.TICKET_MANUAL_DIAGNOSTIC,
            Permission.DIAGNOSTICS_READ,
            Permission.DIAGNOSTICS_RUN,
            Permission.TICKET_DECISION,
            Permission.TICKET_CLOSE,
            Permission.TICKET_AUDIT_READ,
        }
    ),
    Role.ADMIN: frozenset(Permission),
}


def permissions_for(role: Role) -> frozenset[Permission]:
    """Default preset only; runtime checks use persisted user grants."""
    return ROLE_PERMISSIONS[role]
