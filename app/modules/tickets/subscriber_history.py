"""Shared identity rule for ticket history across Subscriber 360 snapshots."""

from sqlalchemy import and_

from app.modules.tickets.models import SubscriberSnapshot


def matching_subscriber(snapshot: SubscriberSnapshot):
    identity = (
        SubscriberSnapshot.subscriber_id == snapshot.subscriber_id
        if snapshot.subscriber_id is not None
        else SubscriberSnapshot.account_no == snapshot.account_no
    )
    return and_(SubscriberSnapshot.source == snapshot.source, identity)
