"""Compact, access-scoped management exports built from the Reports services."""

from datetime import UTC, date, datetime
from html import escape
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased
from xlsxwriter import Workbook

from app.core.errors import ApiError
from app.modules.field_dispatch.models import FieldWorkHandoff, FieldWorkReschedule
from app.modules.identity.models import User
from app.modules.reports.phase2b import (
    field_work_report,
    initial_category_expression,
    reasons_report,
)
from app.modules.reports.service import (
    LOCAL_TZ,
    _visible,
    callbacks_report,
    employees_report,
    report,
    utc_bounds,
)
from app.modules.tickets.models import Ticket, TicketCallback, TicketResolution

FONT_FILE = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
BOLD_FONT_FILE = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
MAX_XLSX_ROWS = 20_000
MAX_PDF_ROWS = 2_000
DATE_FORMAT = "dd.mm.yyyy hh:mm"
DIRECTION_NAMES = {"SUKHUM": "Сухум", "OCHAMCHIRA": "Очамчира", "UNKNOWN": "Не указано"}
OUTCOME_NAMES = {
    "CONFIRMED": "Проблема устранена",
    "STILL_BROKEN": "Проблема осталась",
    "NO_ANSWER": "Не дозвонились",
}
STATUS_NAMES = {
    "OPEN": "Открыта",
    "IN_PROGRESS": "В работе",
    "RESOLVED": "Решена",
    "CLOSED": "Закрыта",
}


def _safe_cell(value):
    """Keep untrusted text literal in Excel and prevent formula injection."""
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.astimezone(LOCAL_TZ).replace(tzinfo=None)
    if isinstance(value, str) and value.lstrip()[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _local_text(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)


def _latest_id(model, timestamp):
    return (
        select(model.id)
        .where(model.ticket_id == Ticket.id)
        .order_by(timestamp.desc(), model.id.desc())
        .limit(1)
        .correlate(Ticket)
        .scalar_subquery()
    )


def _ticket_rows(
    db: Session,
    *,
    start: date,
    end: date,
    user: User,
    read_all: bool,
    category: str | None,
) -> list[dict]:
    """One selected-period row per ticket with its latest decision and callback."""
    lower, upper = utc_bounds(start, end)
    creator = aliased(User)
    assignee = aliased(User)
    decision = aliased(TicketResolution)
    callback = aliased(TicketCallback)
    initial_category = initial_category_expression()
    scope = [
        *_visible(user, read_all),
        Ticket.created_at >= lower,
        Ticket.created_at < upper,
    ]
    if category:
        scope.append(initial_category == category)
    rows = db.execute(
        select(
            Ticket.id,
            Ticket.number,
            Ticket.created_at,
            Ticket.subject,
            initial_category.label("initial_category"),
            decision.reason_name,
            decision.decision_text,
            creator.display_name,
            assignee.display_name,
            Ticket.status,
            Ticket.closed_at,
            callback.outcome,
            callback.occurred_at,
        )
        .join(creator, creator.id == Ticket.created_by)
        .outerjoin(assignee, assignee.id == Ticket.assigned_engineer_id)
        .outerjoin(
            decision, decision.id == _latest_id(TicketResolution, TicketResolution.created_at)
        )
        .outerjoin(callback, callback.id == _latest_id(TicketCallback, TicketCallback.occurred_at))
        .where(*scope)
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
    ).all()
    keys = (
        "id",
        "number",
        "created_at",
        "subject",
        "category",
        "reason_name",
        "decision_text",
        "creator_name",
        "assignee_name",
        "status",
        "closed_at",
        "callback_outcome",
        "callback_at",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def _active_rows(db: Session, *, user: User, read_all: bool) -> list[dict]:
    """Current active state, deliberately independent from the selected event period."""
    assignee = aliased(User)
    callback = aliased(TicketCallback)
    handoff = aliased(FieldWorkHandoff)
    latest_handoff_id = (
        select(FieldWorkHandoff.id)
        .where(FieldWorkHandoff.ticket_id == Ticket.id)
        .order_by(FieldWorkHandoff.created_at.desc(), FieldWorkHandoff.id.desc())
        .limit(1)
        .correlate(Ticket)
        .scalar_subquery()
    )
    latest_reschedule_reason = (
        select(FieldWorkReschedule.reason)
        .where(FieldWorkReschedule.handoff_id == handoff.id)
        .order_by(FieldWorkReschedule.created_at.desc(), FieldWorkReschedule.id.desc())
        .limit(1)
        .correlate(handoff)
        .scalar_subquery()
    )
    rows = db.execute(
        select(
            Ticket.id,
            Ticket.number,
            Ticket.created_at,
            Ticket.subject,
            Ticket.category_name,
            Ticket.status,
            assignee.display_name,
            callback.outcome,
            handoff.work_status,
            handoff.schedule_status,
            latest_reschedule_reason,
        )
        .outerjoin(assignee, assignee.id == Ticket.assigned_engineer_id)
        .outerjoin(callback, callback.id == _latest_id(TicketCallback, TicketCallback.occurred_at))
        .outerjoin(handoff, handoff.id == latest_handoff_id)
        .where(*_visible(user, read_all), Ticket.status != "CLOSED")
        .order_by(Ticket.created_at, Ticket.id)
    ).all()
    result = []
    for row in rows:
        reason = None
        if row[7] == "STILL_BROKEN":
            reason = "Проблема осталась после контрольного звонка"
        elif row[9] == "DATE_PENDING":
            reason = f"Перенос: {row[10]}" if row[10] else "Дата выезда не согласована"
        elif row[8] == "AWAITING_REPORT":
            reason = "Ожидается отчёт монтажников"
        elif row[5] == "RESOLVED":
            reason = "Ожидает контрольного звонка или закрытия"
        elif row[5] == "OPEN" and row[6] is None:
            reason = "Не назначен исполнитель"
        result.append(
            {
                "id": row[0],
                "number": row[1],
                "created_at": row[2],
                "subject": row[3],
                "category": row[4],
                "status": row[5],
                "assignee_name": row[6],
                "delay_reason": reason,
            }
        )
    return result


def _decision_cases(
    db: Session, *, start: date, end: date, user: User, read_all: bool
) -> list[dict]:
    """Latest decision event per ticket during the selected period."""
    lower, upper = utc_bounds(start, end)
    ranked = (
        select(
            TicketResolution.ticket_id,
            TicketResolution.reason_name,
            TicketResolution.decision_text,
            TicketResolution.created_at,
        )
        .join(Ticket, Ticket.id == TicketResolution.ticket_id)
        .where(
            *_visible(user, read_all),
            TicketResolution.created_at >= lower,
            TicketResolution.created_at < upper,
        )
        .distinct(TicketResolution.ticket_id)
        .order_by(
            TicketResolution.ticket_id,
            TicketResolution.created_at.desc(),
            TicketResolution.id.desc(),
        )
        .subquery()
    )
    rows = db.execute(
        select(
            Ticket.number,
            Ticket.subject,
            ranked.c.reason_name,
            ranked.c.decision_text,
            ranked.c.created_at,
        )
        .join(ranked, ranked.c.ticket_id == Ticket.id)
        .order_by(ranked.c.created_at.desc(), Ticket.number.desc())
    ).all()
    return [
        {
            "ticket_number": row[0],
            "subject": row[1],
            "reason_name": row[2],
            "decision_text": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]


def load_bundle(
    db: Session,
    *,
    start: date,
    end: date,
    user: User,
    read_all: bool,
    direction: str | None,
    category: str | None,
    max_rows: int,
) -> dict:
    """Reuse report calculations and load bounded management-only detail."""
    base = {"db": db, "start": start, "end": end, "user": user, "read_all": read_all}
    general = report(**base, page=1, size=1)
    employees = employees_report(**base)
    employees["items"] = [
        row
        for row in employees["items"]
        if row["created_tickets"]
        or row["decision_events"]
        or row["closed_events"]
        or row["callback_events"]
    ]
    callbacks = callbacks_report(**base, page=1, size=1)
    field = field_work_report(**base, direction=direction, page=1, size=1)
    categories = reasons_report(**base, category=category, detail_kind="category", page=1, size=1)
    decisions = reasons_report(**base, category=category, detail_kind="decision", page=1, size=1)
    totals = (
        general["details"]["total"],
        callbacks["details"]["total"],
        field["details"]["total"],
        categories["details"]["total"],
        decisions["details"]["total"],
    )
    grouped_rows = sum(
        len(rows)
        for rows in (
            employees["items"],
            callbacks["employees"],
            field["reschedule_reasons"],
            categories["categories"],
            categories["reasons"],
        )
    )
    if sum(totals) + grouped_rows > max_rows:
        raise ApiError(413, "report_too_large", "Choose a shorter report period")

    ticket_rows = _ticket_rows(
        db, start=start, end=end, user=user, read_all=read_all, category=category
    )
    active_rows = _active_rows(db, user=user, read_all=read_all)
    decision_cases = _decision_cases(db, start=start, end=end, user=user, read_all=read_all)
    if len(ticket_rows) + len(active_rows) + len(decision_cases) + grouped_rows > max_rows:
        raise ApiError(413, "report_too_large", "Choose a shorter report period")

    callback_total = callbacks["details"]["total"]
    field_total = field["details"]["total"]
    if callback_total > 1:
        callbacks = callbacks_report(**base, page=1, size=callback_total)
    if field_total > 1:
        field = field_work_report(**base, direction=direction, page=1, size=field_total)
    if (
        callbacks["details"]["total"] != callback_total
        or field["details"]["total"] != field_total
        or len(callbacks["details"]["items"]) != callback_total
        or len(field["details"]["items"]) != field_total
    ):
        raise ApiError(409, "report_changed", "Report data changed during export; retry")

    return {
        "start": start,
        "end": end,
        "generated_at": datetime.now(UTC).astimezone(LOCAL_TZ),
        "direction": direction,
        "category_filter": category,
        "general": general,
        "employees": employees,
        "callbacks": callbacks,
        "field": field,
        "categories": categories,
        "decisions": decisions,
        "tickets": ticket_rows,
        "active_tickets": active_rows,
        "decision_cases": decision_cases,
    }


def _summary(bundle: dict) -> list[tuple[str, int]]:
    general = bundle["general"]["metrics"]
    callbacks = bundle["callbacks"]
    field = bundle["field"]["metrics"]
    return [
        ("Поступившие заявки", general["created"]),
        ("Уникальные решённые заявки", general["resolved_unique_tickets"]),
        ("Уникальные закрытые заявки", general["closed_unique_tickets"]),
        ("Активные заявки сейчас", general["active_now"]),
        ("Контрольные звонки", callbacks["total_events"]),
        ("Уникальные заявки с обзвоном", callbacks["unique_tickets"]),
        ("Выездные задания переданы", field["sent_handoffs"]),
        ("Отчёты о выполнении получены", field["reported_handoffs"]),
        ("Работы подтверждены инженером", field["verified_handoffs"]),
        ("Ожидают отчёта сейчас", field["awaiting_report_now"]),
    ]


def _share(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


class _Sheet:
    def __init__(self, book: Workbook, name: str, bundle: dict, widths: list[int]):
        self.ws = book.add_worksheet(name)
        self.ws.set_tab_color("#14866e")
        for index, width in enumerate(widths):
            self.ws.set_column(index, index, width)
        self.ws.freeze_panes(3, 0)
        self.row_index = 0
        self.title_fmt = book.add_format({"bold": True, "font_size": 16, "font_color": "#173a36"})
        self.header_fmt = book.add_format(
            {"bold": True, "bg_color": "#173a36", "font_color": "#ffffff", "text_wrap": True}
        )
        self.number_fmt = book.add_format({"num_format": "#,##0"})
        self.decimal_fmt = book.add_format({"num_format": "0.0%"})
        self.date_fmt = book.add_format({"num_format": DATE_FORMAT})
        self.text_fmt = book.add_format({"text_wrap": True, "valign": "top"})
        self._row([name], self.title_fmt)
        self._row([f"Период UTC+3: {bundle['start']} — {bundle['end']}"])
        self._row([f"Сформирован: {_local_text(bundle['generated_at'])} UTC+3"])

    def _row(self, values, fmt=None):
        for column, value in enumerate(values):
            clean = _safe_cell(value)
            target = fmt
            if target is None:
                if isinstance(clean, datetime):
                    target = self.date_fmt
                elif isinstance(clean, int):
                    target = self.number_fmt
                else:
                    target = self.text_fmt
            self.ws.write(self.row_index, column, clean, target)
        self.row_index += 1

    def section(self, title: str, headers: tuple[str, ...], rows, *, percent_columns=()):
        self._row([])
        self._row([title], self.title_fmt)
        self._row(headers, self.header_fmt)
        count = 0
        for row in rows:
            for column, value in enumerate(row):
                if column in percent_columns:
                    self.ws.write(self.row_index, column, value, self.decimal_fmt)
                else:
                    self._write_cell(column, value)
            self.row_index += 1
            count += 1
        if count == 0:
            self._row(["Нет данных за период"])

    def _write_cell(self, column, value):
        clean = _safe_cell(value)
        if isinstance(clean, datetime):
            fmt = self.date_fmt
        elif isinstance(clean, int):
            fmt = self.number_fmt
        else:
            fmt = self.text_fmt
        self.ws.write(self.row_index, column, clean, fmt)


def build_xlsx(bundle: dict) -> bytes:
    output = BytesIO()
    with TemporaryDirectory(prefix="oss-report-") as scratch:
        book = Workbook(
            output,
            {
                "constant_memory": True,
                "tmpdir": scratch,
                "strings_to_formulas": False,
                "strings_to_urls": False,
            },
        )
        sheet = _Sheet(book, "Сводка", bundle, [48, 22])
        sheet.section("Ключевые показатели", ("Показатель", "Значение"), _summary(bundle))
        sheet.section(
            "Пояснения",
            ("Правило расчёта",),
            [
                ("Период рассчитан по календарным границам UTC+3.",),
                ("Решённые и закрытые — уникальные заявки; повторные события не удваивают итог.",),
                ("Активные и ожидающие отчёта — текущие состояния на момент формирования.",),
                ("Отправка выездного задания не означает выполнение работы.",),
            ],
        )

        categories = bundle["categories"]
        sheet = _Sheet(book, "Причины и решения", bundle, [42, 18, 18, 18, 68])
        created_total = categories["metrics"]["created_tickets"]
        decision_total = categories["metrics"]["decision_events"]
        sheet.section(
            "Категории обращений при регистрации",
            ("Категория", "Заявки", "Доля"),
            [
                (row["name"] or "Без категории", row["count"], _share(row["count"], created_total))
                for row in categories["categories"]
            ],
            percent_columns=(2,),
        )
        sheet.section(
            "Причины, установленные инженерами",
            ("Причина", "Решения", "Доля"),
            [
                (
                    row["name"] or "Причина не указана",
                    row["count"],
                    _share(row["count"], decision_total),
                )
                for row in categories["reasons"]
            ],
            percent_columns=(2,),
        )
        sheet.section(
            "Краткие результаты инженерной работы",
            ("Заявка", "Проблема", "Причина", "Решение", "Дата UTC+3"),
            [
                (
                    row["ticket_number"],
                    row["subject"],
                    row["reason_name"],
                    row["decision_text"],
                    row["created_at"],
                )
                for row in bundle["decision_cases"]
            ],
        )

        sheet = _Sheet(book, "Работа сотрудников", bundle, [38, 18, 18, 20, 18, 18, 18])
        sheet.section(
            "Действия за период",
            (
                "Сотрудник",
                "Созданы заявки",
                "Решения (события)",
                "Уникальные решённые",
                "Закрытия (события)",
                "Уникальные закрытые",
                "Контрольные звонки",
            ),
            [
                (
                    row["display_name"],
                    row["created_tickets"],
                    row["decision_events"],
                    row["decision_tickets"],
                    row["closed_events"],
                    row["closed_tickets"],
                    row["callback_events"],
                )
                for row in bundle["employees"]["items"]
            ],
        )

        callbacks = bundle["callbacks"]
        sheet = _Sheet(book, "Обзвоны", bundle, [18, 52, 34, 30, 24])
        sheet.section(
            "Итоги",
            ("Показатель", "Значение"),
            [
                ("Контрольные звонки (события)", callbacks["total_events"]),
                ("Уникальные заявки", callbacks["unique_tickets"]),
                *[
                    (OUTCOME_NAMES.get(key, key), value)
                    for key, value in callbacks["outcomes"].items()
                ],
            ],
        )
        sheet.section(
            "История звонков",
            ("Заявка", "Тема", "Сотрудник", "Результат", "Дата UTC+3"),
            [
                (
                    row["ticket_number"],
                    row["subject"],
                    row["actor_name"],
                    OUTCOME_NAMES.get(row["outcome"], row["outcome"]),
                    row["occurred_at"],
                )
                for row in callbacks["details"]["items"]
            ],
        )

        field = bundle["field"]
        sheet = _Sheet(book, "Выездные и незавершённые работы", bundle, [18, 34, 24, 24, 24, 52])
        sheet.section(
            "Выездные работы по направлениям",
            ("Направление", "Передано", "Отчёты о выполнении", "Подтверждено", "Переносы"),
            [
                (
                    DIRECTION_NAMES.get(key, key),
                    value["sent"],
                    value["reported"],
                    value["verified"],
                    value["reschedules"],
                )
                for key, value in field["directions"].items()
            ],
        )
        sheet.section(
            "Невыполненные и проблемные заявки (текущее состояние)",
            ("Заявка", "Категория", "Ответственный", "Создана UTC+3", "Статус", "Причина задержки"),
            [
                (
                    row["number"],
                    row["category"],
                    row["assignee_name"],
                    row["created_at"],
                    STATUS_NAMES.get(row["status"], row["status"]),
                    row["delay_reason"],
                )
                for row in bundle["active_tickets"]
            ],
        )

        sheet = _Sheet(
            book, "Детализация заявок", bundle, [14, 22, 42, 34, 34, 70, 32, 32, 20, 22, 30]
        )
        sheet.section(
            "Одна строка на заявку, созданную за выбранный период",
            (
                "Номер",
                "Дата UTC+3",
                "Тема",
                "Категория при регистрации",
                "Установленная причина",
                "Актуальный текст решения",
                "Создатель",
                "Исполнитель",
                "Статус",
                "Дата закрытия UTC+3",
                "Последний контрольный звонок",
            ),
            [
                (
                    row["number"],
                    row["created_at"],
                    row["subject"],
                    row["category"],
                    row["reason_name"],
                    row["decision_text"],
                    row["creator_name"],
                    row["assignee_name"],
                    STATUS_NAMES.get(row["status"], row["status"]),
                    row["closed_at"],
                    OUTCOME_NAMES.get(row["callback_outcome"], row["callback_outcome"]),
                )
                for row in bundle["tickets"]
            ],
        )
        book.close()
    return output.getvalue()


def _font() -> None:
    if "OSS" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("OSS", str(FONT_FILE)))
        pdfmetrics.registerFont(TTFont("OSS-Bold", str(BOLD_FONT_FILE)))
        pdfmetrics.registerFontFamily("OSS", normal="OSS", bold="OSS-Bold")


def _paragraph(value, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_local_text(value)), style)


def _short(value, length: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def build_pdf(bundle: dict) -> bytes:
    """Build a deliberately concise three-page executive report."""
    _font()
    output = BytesIO()
    page_size = landscape(A4)
    doc = SimpleDocTemplate(
        output,
        pagesize=page_size,
        leftMargin=28,
        rightMargin=28,
        topMargin=30,
        bottomMargin=32,
        title="OSS Control Center — управленческий отчёт",
        author="OSS Control Center",
    )
    heading = ParagraphStyle(
        "oss-heading",
        fontName="OSS-Bold",
        fontSize=12,
        leading=14,
        textColor=colors.HexColor("#173a36"),
        spaceBefore=6,
        spaceAfter=5,
    )
    title = ParagraphStyle(
        "oss-title", parent=heading, fontSize=19, leading=22, alignment=TA_CENTER, spaceAfter=6
    )
    body = ParagraphStyle(
        "oss-body", fontName="OSS", fontSize=7.2, leading=9, textColor=colors.HexColor("#263941")
    )
    note = ParagraphStyle("oss-note", parent=body, fontSize=7.5, leading=10, spaceAfter=4)
    header = ParagraphStyle(
        "oss-table-head", parent=body, fontName="OSS-Bold", textColor=colors.white
    )
    story = [
        _paragraph("OSS Control Center", title),
        _paragraph("Управленческий отчёт", title),
        _paragraph(
            f"Период: {bundle['start']} — {bundle['end']} (UTC+3) · "
            f"сформирован {_local_text(bundle['generated_at'])}",
            note,
        ),
    ]

    def section(name: str):
        story.append(_paragraph(name, heading))

    def table(headers, rows, widths, *, limit=None):
        materialized = list(rows)
        if limit is not None:
            materialized = materialized[:limit]
        if not materialized:
            return False
        data = [[_paragraph(value, header) for value in headers]]
        data.extend([[_paragraph(value, body) for value in row] for row in materialized])
        grid = LongTable(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitInRow=1)
        grid.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173a36")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f2f6f5")],
                    ),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(grid)
        story.append(Spacer(1, 5))
        return True

    section("Ключевые показатели")
    summary = _summary(bundle)
    paired = []
    for index in range(0, len(summary), 2):
        left = summary[index]
        right = summary[index + 1] if index + 1 < len(summary) else ("", "")
        paired.append((left[0], left[1], right[0], right[1]))
    table(("Показатель", "Значение", "Показатель", "Значение"), paired, [260, 70, 260, 70])

    categories = bundle["categories"]
    section("Причины обращений и установленные причины неисправностей")
    created_total = categories["metrics"]["created_tickets"]
    decision_total = categories["metrics"]["decision_events"]
    category_rows = [
        (row["name"] or "Без категории", row["count"], f"{_share(row['count'], created_total):.1%}")
        for row in categories["categories"]
    ]
    reason_rows = [
        (
            row["name"] or "Причина не указана",
            row["count"],
            f"{_share(row['count'], decision_total):.1%}",
        )
        for row in categories["reasons"]
    ]
    max_rows = max(len(category_rows[:9]), len(reason_rows[:9]))
    combined = []
    for index in range(max_rows):
        left = category_rows[index] if index < len(category_rows) else ("", "", "")
        right = reason_rows[index] if index < len(reason_rows) else ("", "", "")
        combined.append((*left, *right))
    if not table(
        ("Категория обращения", "Кол-во", "Доля", "Причина решения", "Кол-во", "Доля"),
        combined,
        [230, 48, 48, 230, 48, 48],
    ):
        story.append(_paragraph("За выбранный период категории и решения отсутствуют.", note))

    story.append(PageBreak())
    section("Работа сотрудников")
    table(
        ("Сотрудник", "Создано", "Решено уник.", "Закрыто уник.", "Обзвоны"),
        [
            (
                row["display_name"],
                row["created_tickets"],
                row["decision_tickets"],
                row["closed_tickets"],
                row["callback_events"],
            )
            for row in bundle["employees"]["items"]
        ],
        [310, 110, 110, 110, 110],
        limit=15,
    )
    callbacks = bundle["callbacks"]
    section("Контрольные звонки")
    table(
        ("Показатель", "Количество", "Показатель", "Количество"),
        [
            (
                "События звонков",
                callbacks["total_events"],
                "Уникальные заявки",
                callbacks["unique_tickets"],
            ),
            (
                "Проблема устранена",
                callbacks["outcomes"].get("CONFIRMED", 0),
                "Проблема осталась",
                callbacks["outcomes"].get("STILL_BROKEN", 0),
            ),
            ("Не дозвонились", callbacks["outcomes"].get("NO_ANSWER", 0), "", ""),
        ],
        [260, 70, 260, 70],
    )
    field = bundle["field"]
    section("Выездные работы")
    story.append(
        _paragraph(
            "Передано — сообщение отправлено монтажникам; выполнение подтверждается "
            "отчётом, а затем проверкой инженера.",
            note,
        )
    )
    direction_rows = []
    for direction in ("SUKHUM", "OCHAMCHIRA"):
        values = field["directions"].get(
            direction, {"sent": 0, "reported": 0, "verified": 0, "reschedules": 0}
        )
        direction_rows.append(
            (
                DIRECTION_NAMES[direction],
                values["sent"],
                values["reported"],
                values["verified"],
                values["reschedules"],
            )
        )
    direction_rows.append(
        (
            "Текущее состояние",
            "—",
            field["metrics"]["awaiting_report_now"],
            "ожидают отчёта",
            field["metrics"]["date_pending_now"],
        )
    )
    table(
        ("Направление", "Передано", "Отчёты", "Подтверждено", "Переносы / без даты"),
        direction_rows,
        [250, 110, 110, 110, 110],
    )

    story.append(PageBreak())
    section("Невыполненные и проблемные заявки — текущее состояние")
    active = bundle["active_tickets"]
    if not table(
        ("№", "Категория", "Ответственный", "Дата", "Статус", "Причина задержки"),
        [
            (
                row["number"],
                _short(row["category"] or row["subject"] or "Без категории", 32),
                row["assignee_name"],
                row["created_at"],
                STATUS_NAMES.get(row["status"], row["status"]),
                _short(row["delay_reason"], 55),
            )
            for row in active
        ],
        [48, 155, 135, 105, 90, 175],
        limit=12,
    ):
        story.append(_paragraph("Невыполненных заявок нет.", note))
    elif len(active) > 12:
        story.append(
            _paragraph(
                f"Показаны 12 из {len(active)} активных заявок. Полный список доступен в Excel.",
                note,
            )
        )

    section("Краткие результаты инженерной работы")
    cases = bundle["decision_cases"]
    if not table(
        ("№", "Проблема", "Установленная причина", "Краткий результат"),
        [
            (
                row["ticket_number"],
                _short(row["subject"], 42),
                row["reason_name"] or "Причина не указана",
                _short(row["decision_text"] or row["reason_name"], 170),
            )
            for row in cases
        ],
        [48, 190, 175, 295],
        limit=8,
    ):
        story.append(_paragraph("Инженерных решений за период нет.", note))
    elif len(cases) > 8:
        story.append(
            _paragraph(
                f"Показаны 8 из {len(cases)} заявок с решениями. Полные тексты доступны в Excel.",
                note,
            )
        )

    def footer(canvas, document):
        canvas.setFont("OSS", 7)
        canvas.setFillColor(colors.HexColor("#667b80"))
        canvas.drawString(30, 18, "OSS Control Center · данные UTC+3")
        canvas.drawRightString(page_size[0] - 30, 18, f"Страница {document.page}")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
