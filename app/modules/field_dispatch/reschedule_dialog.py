"""Small, durable inline Telegram flow for one visit at a time."""

import calendar
import re
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.field_dispatch.models import (
    FieldRescheduleDialog,
    FieldWorkHandoff,
    FieldWorkReschedule,
)
from app.modules.field_dispatch.rescheduling import ABKHAZIA

REASONS = ("Абонент не дома", "Попросил позже", "Неудобный день")
TIME_CHOICES = (time(9), time(12), time(15), time(18))
ACTIVE_STAGES = (
    "REASON",
    "REASON_TEXT",
    "DATE",
    "CALENDAR",
    "DATE_TEXT",
    "TIME",
    "TIME_TEXT",
    "CONFIRM",
)
TEXT_STAGES = ("REASON_TEXT", "DATE_TEXT", "TIME_TEXT")
INPUT_ERRORS = {
    "reason_invalid": "Причина должна содержать от 1 до 2000 символов. Ответьте ещё раз.",
    "date_invalid": "Укажите дату как ДД.ММ.ГГГГ, позже сегодняшнего дня.",
    "time_invalid": "Укажите время как ЧЧ:ММ. Ответьте ещё раз.",
}


def active_dialog(
    db: Session, handoff_id: UUID, *, lock: bool = False
) -> FieldRescheduleDialog | None:
    statement = select(FieldRescheduleDialog).where(
        FieldRescheduleDialog.handoff_id == handoff_id,
        FieldRescheduleDialog.stage.in_(ACTIVE_STAGES),
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def touch(dialog: FieldRescheduleDialog) -> None:
    dialog.render_revision += 1
    dialog.sync_status = "PENDING"
    dialog.sync_attempts = 0
    dialog.sync_error_code = None
    dialog.updated_at = datetime.now(UTC)


def _future(day: date) -> bool:
    return day > datetime.now(ABKHAZIA).date()


def advance(dialog: FieldRescheduleDialog, action: str) -> str:
    """Return 'STEP', 'CONFIRM' or 'CANCEL'; reject callbacks from an obsolete step."""
    stage = dialog.stage
    today = datetime.now(ABKHAZIA).date()
    dialog.input_error = None
    if action == "cancel" and stage in ACTIVE_STAGES:
        dialog.stage = "CANCELED"
        touch(dialog)
        return "CANCEL"
    if action == "back":
        previous = {
            "REASON_TEXT": "REASON",
            "DATE": "REASON",
            "CALENDAR": "DATE",
            "DATE_TEXT": "DATE",
            "TIME": "DATE",
            "TIME_TEXT": "TIME",
            "CONFIRM": "TIME" if dialog.local_date else "DATE",
        }.get(stage)
        if previous is None:
            raise ValueError("reschedule_action_stale")
        dialog.stage = previous
        touch(dialog)
        return "STEP"
    if stage == "REASON" and action in {"r0", "r1", "r2", "rx"}:
        if action == "rx":
            dialog.reason = None
            dialog.stage = "REASON_TEXT"
        else:
            dialog.reason = REASONS[int(action[1])]
            dialog.stage = "DATE"
    elif stage == "DATE" and action in {"dt0", "dt1", "dtx", "dtn", "cal"}:
        dialog.local_time = None
        if action in {"dt0", "dt1"}:
            dialog.local_date = today + timedelta(days=1 if action == "dt0" else 2)
            dialog.stage = "TIME"
        elif action == "dtn":
            dialog.local_date = None
            dialog.stage = "CONFIRM"
        elif action == "cal":
            dialog.calendar_month = today.replace(day=1)
            dialog.stage = "CALENDAR"
        else:
            dialog.stage = "DATE_TEXT"
    elif stage == "CALENDAR" and action in {"mp", "mn"}:
        month = dialog.calendar_month or today.replace(day=1)
        step = -1 if action == "mp" else 1
        index = month.year * 12 + month.month - 1 + step
        candidate = date(index // 12, index % 12 + 1, 1)
        if candidate < today.replace(day=1):
            raise ValueError("reschedule_date_invalid")
        dialog.calendar_month = candidate
    elif stage == "CALENDAR" and action == "dtx":
        dialog.stage = "DATE_TEXT"
    elif stage == "CALENDAR" and action.startswith("d") and len(action) == 9:
        try:
            selected = date.fromisoformat(f"{action[1:5]}-{action[5:7]}-{action[7:9]}")
        except ValueError:
            raise ValueError("reschedule_date_invalid") from None
        if (
            not _future(selected)
            or selected.year != dialog.calendar_month.year
            or selected.month != dialog.calendar_month.month
        ):
            raise ValueError("reschedule_date_invalid")
        dialog.local_date = selected
        dialog.stage = "TIME"
    elif stage == "TIME" and action in {"t0", "t1", "t2", "t3", "tx", "tn"}:
        if action == "tx":
            dialog.stage = "TIME_TEXT"
        else:
            dialog.local_time = None if action == "tn" else TIME_CHOICES[int(action[1])]
            dialog.stage = "CONFIRM"
    elif stage == "CONFIRM" and action == "ok":
        return "CONFIRM"
    else:
        raise ValueError("reschedule_action_stale")
    touch(dialog)
    return "STEP"


def accept_text(dialog: FieldRescheduleDialog, value: str) -> None:
    body = value.strip()
    dialog.input_error = None
    if dialog.stage == "REASON_TEXT":
        if not body or len(body) > 2000 or any(ord(char) < 32 for char in body):
            dialog.input_error = "reason_invalid"
        else:
            dialog.reason = body
            dialog.stage = "DATE"
    elif dialog.stage == "DATE_TEXT":
        try:
            if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", body):
                raise ValueError
            selected = datetime.strptime(body, "%d.%m.%Y").date()
        except ValueError:
            dialog.input_error = "date_invalid"
        else:
            if not _future(selected):
                dialog.input_error = "date_invalid"
            else:
                dialog.local_date = selected
                dialog.stage = "TIME"
    elif dialog.stage == "TIME_TEXT":
        try:
            if not re.fullmatch(r"\d{2}:\d{2}", body):
                raise ValueError
            selected_time = datetime.strptime(body, "%H:%M").time()
        except ValueError:
            dialog.input_error = "time_invalid"
        else:
            dialog.local_time = selected_time
            dialog.stage = "CONFIRM"
    else:
        raise ValueError("reschedule_text_not_expected")
    touch(dialog)


def scheduled_instant(dialog: FieldRescheduleDialog) -> datetime | None:
    if dialog.local_date is None or dialog.local_time is None:
        return None
    value = datetime.combine(dialog.local_date, dialog.local_time, ABKHAZIA)
    if value.astimezone(UTC) <= datetime.now(UTC):
        raise ValueError("reschedule_date_past")
    return value.astimezone(UTC)


def card_text(handoff: FieldWorkHandoff, latest: FieldWorkReschedule | None) -> str:
    original = handoff.message_text or ""
    if latest is None:
        return original
    if latest.scheduled_for:
        when = latest.scheduled_for.astimezone(ABKHAZIA).strftime("%d.%m.%Y %H:%M") + " (UTC+3)"
    elif latest.scheduled_date:
        when = latest.scheduled_date.strftime("%d.%m.%Y") + " · время не согласовано"
    else:
        when = "дата не согласована"
    suffix = f"\n\n📅 ПЕРЕНЕСЕНА\nПричина: {latest.reason}\nНовая дата: {when}"
    return original[: max(0, 4096 - len(suffix))] + suffix


def normal_keyboard(handoff_id: UUID) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Выполнено", "callback_data": f"field_done:{handoff_id}"},
                {"text": "📅 Перенести", "callback_data": f"field_reschedule:{handoff_id}"},
            ]
        ]
    }


def render(
    dialog: FieldRescheduleDialog, handoff: FieldWorkHandoff, latest: FieldWorkReschedule | None
) -> tuple[str, dict]:
    if dialog.stage == "CANCELED":
        return card_text(handoff, latest), normal_keyboard(handoff.id)
    base = card_text(handoff, latest)
    def button(label: str, action: str) -> dict:
        return {"text": label, "callback_data": f"rs:{dialog.id}:{action}"}
    stage = dialog.stage
    if stage == "REASON":
        title = "Причина переноса"
        rows = [[button(name, f"r{index}")] for index, name in enumerate(REASONS)]
        rows.append([button("Другая причина", "rx")])
    elif stage == "REASON_TEXT":
        title = "Ответьте на эту карточку текстом причины."
        rows = []
    elif stage == "DATE":
        title = "Новая дата (время Абхазии)"
        rows = [
            [button("Завтра", "dt0"), button("Послезавтра", "dt1")],
            [button("Выбрать дату", "cal"), button("Дата не согласована", "dtn")],
        ]
    elif stage == "CALENDAR":
        month = dialog.calendar_month or datetime.now(ABKHAZIA).date().replace(day=1)
        title = f"Выберите дату · {month.strftime('%m.%Y')}"
        rows = [[button("◀", "mp"), button("▶", "mn")]]
        for week in calendar.monthcalendar(month.year, month.month):
            rows.append(
                [
                    button(str(day), f"d{month.year:04d}{month.month:02d}{day:02d}")
                    for day in week
                    if day and _future(date(month.year, month.month, day))
                ]
            )
        rows = [row for row in rows if row]
        rows.append([button("Ввести дату", "dtx")])
    elif stage == "DATE_TEXT":
        title = "Ответьте на эту карточку датой ДД.ММ.ГГГГ."
        rows = []
    elif stage == "TIME":
        title = f"Дата {dialog.local_date.strftime('%d.%m.%Y')} · выберите время"
        rows = [
            [
                button(f"{value.hour:02d}:00", f"t{index}")
                for index, value in enumerate(TIME_CHOICES[:2])
            ],
            [
                button(f"{value.hour:02d}:00", f"t{index + 2}")
                for index, value in enumerate(TIME_CHOICES[2:])
            ],
            [button("Другое время", "tx"), button("Время не согласовано", "tn")],
        ]
    elif stage == "TIME_TEXT":
        title = "Ответьте на эту карточку временем ЧЧ:ММ."
        rows = []
    else:
        when = (
            dialog.local_date.strftime("%d.%m.%Y") if dialog.local_date else "дата не согласована"
        )
        if dialog.local_time:
            when += f" {dialog.local_time.strftime('%H:%M')}"
        elif dialog.local_date:
            when += " · время не согласовано"
        title = f"Подтвердить перенос?\nПричина: {dialog.reason}\nДата: {when}"
        rows = [[button("Подтвердить", "ok")]]
    if dialog.input_error:
        title += "\n" + INPUT_ERRORS[dialog.input_error]
    rows.append([button("Назад", "back"), button("Отмена", "cancel")])
    suffix = "\n\n" + title
    return base[: max(0, 4096 - len(suffix))] + suffix, {"inline_keyboard": rows}
