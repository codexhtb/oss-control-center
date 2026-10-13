"""Central role and permission policy; no implicit administrator superuser."""

from enum import StrEnum


class Role(StrEnum):
    OPERATOR = "operator"
    ENGINEER = "engineer"
    ADMIN = "admin"


class Permission(StrEnum):
    SUBSCRIBER_READ = "subscriber.read"
    TICKET_READ = "ticket.read"
    TICKET_CREATE = "ticket.create"
    TICKET_COMMENT = "ticket.comment"
    TICKET_START = "ticket.start"
    TICKET_MANUAL_DIAGNOSTIC = "ticket.manual_diagnostic"
    TICKET_DECISION = "ticket.decision"
    TICKET_CLOSE = "ticket.close"
    TICKET_AUDIT_READ = "ticket.audit.read"
    FIELD_DISPATCH = "field_dispatch.send"
    IDENTITY_MANAGE = "identity.manage"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OPERATOR: frozenset(
        {
            Permission.SUBSCRIBER_READ,
            Permission.TICKET_READ,
            Permission.TICKET_CREATE,
            Permission.TICKET_COMMENT,
        }
    ),
    Role.ENGINEER: frozenset(
        {
            Permission.SUBSCRIBER_READ,
            Permission.TICKET_READ,
            Permission.TICKET_COMMENT,
            Permission.TICKET_START,
            Permission.TICKET_MANUAL_DIAGNOSTIC,
            Permission.TICKET_DECISION,
            Permission.TICKET_CLOSE,
            Permission.TICKET_AUDIT_READ,
            Permission.FIELD_DISPATCH,
        }
    ),
    Role.ADMIN: frozenset({Permission.IDENTITY_MANAGE}),
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]
