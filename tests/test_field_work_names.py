"""Only confirmed saved data determines the Mini App subscriber name."""
from app.api.v1.field_work_miniapp import _subscriber_name
from app.modules.tickets.models import SubscriberSnapshot, Ticket


def test_saved_name_depends_on_ticket_type() -> None:
    snapshot = SubscriberSnapshot(full_name="  Абонент из snapshot  ", organization_name=None)
    ticket = Ticket(ticket_type="SUBSCRIBER_LINKED", caller_full_name="Другой звонящий")
    assert _subscriber_name(ticket, snapshot) == "Абонент из snapshot"
    assert _subscriber_name(ticket, None) is None
    for kind in ("MANUAL", "NEW_CONNECTION"):
        ticket.ticket_type = kind
        assert _subscriber_name(ticket, snapshot) == "Другой звонящий"
        ticket.caller_full_name = " "
        assert _subscriber_name(ticket, snapshot) is None
        ticket.caller_full_name = "Другой звонящий"
