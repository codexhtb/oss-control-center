"""Bounded, access-scoped management exports built from the existing Reports services."""

from datetime import UTC, date, datetime
from html import escape
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from reportlab.graphics.shapes import Drawing, Rect, String
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
from sqlalchemy.orm import Session
from xlsxwriter import Workbook

from app.core.errors import ApiError
from app.modules.identity.models import User
from app.modules.reports.phase2b import field_work_report, reasons_report
from app.modules.reports.service import (
    LOCAL_TZ,
    callbacks_report,
    employees_report,
    report,
)

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
GENERAL_LABELS = {
    "created": "Поступили за период",
    "resolved_events": "Решения за период (события)",
    "closed_events": "Закрытия за период (события)",
    "returned_events": "Возвраты на доработку (события)",
    "active_now": "Активные заявки сейчас",
    "average_resolution_seconds": "Среднее время до решения, сек",
    "average_closure_seconds": "Среднее время до закрытия, сек",
}
FIELD_LABELS = {
    "sent_handoffs": "Отправленные задания (handoff)",
    "sent_unique_tickets": "Уникальные заявки с отправкой",
    "reported_handoffs": "Полученные отчёты монтажников",
    "verified_handoffs": "Проверенные инженером работы",
    "reschedule_events": "События переноса",
    "rescheduled_handoffs": "Задания с переносом",
    "awaiting_report_now": "Ожидают отчёта сейчас",
    "date_pending_now": "Дата не согласована сейчас",
}
REASON_LABELS = {
    "created_tickets": "Созданные заявки",
    "without_category": "Заявки без категории при регистрации",
    "decision_events": "Решения инженеров (события)",
    "decision_unique_tickets": "Уникальные заявки с решением",
    "decisions_without_reason": "Решения без указанной причины",
    "created_without_reason_now": "Созданные, без причины сейчас",
}


def _metric_rows(metrics: dict, labels: dict[str, str]):
    return ((labels.get(key, key), value) for key, value in metrics.items())


def _safe_cell(value):
    """Keep untrusted labels as literal text in Excel, including whitespace prefixes."""
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
    """Reuse Reports calculations and refuse oversized exports before loading detail rows."""
    base = {"db": db, "start": start, "end": end, "user": user, "read_all": read_all}
    general = report(**base, page=1, size=1)
    employees = employees_report(**base)
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
    grouped_rows = (
        len(general["daily"])
        + len(employees["items"])
        + len(employees["daily"])
        + len(callbacks["daily"])
        + len(callbacks["employees"])
        + len(field["daily"])
        + len(field["reschedule_reasons"])
        + len(categories["daily"])
        + len(categories["categories"])
        + len(categories["reasons"])
    )
    if sum(totals) + grouped_rows > max_rows:
        raise ApiError(413, "report_too_large", "Choose a shorter report period")

    if totals[0] > 1:
        general = report(**base, page=1, size=totals[0])
    if totals[1] > 1:
        callbacks = callbacks_report(**base, page=1, size=totals[1])
    if totals[2] > 1:
        field = field_work_report(**base, direction=direction, page=1, size=totals[2])
    if totals[3] > 1:
        categories = reasons_report(
            **base, category=category, detail_kind="category", page=1, size=totals[3]
        )
    if totals[4] > 1:
        decisions = reasons_report(
            **base, category=category, detail_kind="decision", page=1, size=totals[4]
        )
    # An export is never silently truncated if new events arrive during construction.
    actual = (
        general["details"]["total"],
        callbacks["details"]["total"],
        field["details"]["total"],
        categories["details"]["total"],
        decisions["details"]["total"],
    )
    if actual != totals or any(
        len(part["details"]["items"]) != total
        for part, total in zip(
            (general, callbacks, field, categories, decisions), totals, strict=True
        )
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
    }


def _summary(bundle: dict) -> list[tuple[str, int | float | None]]:
    g = bundle["general"]["metrics"]
    c = bundle["callbacks"]
    f = bundle["field"]["metrics"]
    r = bundle["categories"]["metrics"]
    return [
        ("Поступившие заявки", g["created"]),
        ("События решений", g["resolved_events"]),
        ("События закрытия", g["closed_events"]),
        ("Активные заявки сейчас", g["active_now"]),
        ("Контрольные звонки", c["total_events"]),
        ("Отправленные выездные задания", f["sent_handoffs"]),
        ("Полученные отчёты монтажников", f["reported_handoffs"]),
        ("Проверенные инженером работы", f["verified_handoffs"]),
        ("События переноса", f["reschedule_events"]),
        ("Категории не указаны", r["without_category"]),
        ("Решения без установленной причины", r["decisions_without_reason"]),
    ]


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
        self.decimal_fmt = book.add_format({"num_format": "#,##0.00"})
        self.date_fmt = book.add_format({"num_format": DATE_FORMAT})
        self.text_fmt = book.add_format({"text_wrap": True})
        self._row([name], self.title_fmt)
        self._row([f"Период UTC+3: {bundle['start']} — {bundle['end']}"])
        self._row([f"Сформирован: {_local_text(bundle['generated_at'])} UTC+3"])

    def _row(self, values, fmt=None):
        for column, value in enumerate(values):
            clean = _safe_cell(value)
            target = fmt
            if target is None:
                target = (
                    self.date_fmt
                    if isinstance(clean, datetime)
                    else (
                        self.decimal_fmt
                        if isinstance(clean, float)
                        else self.number_fmt
                        if isinstance(clean, int)
                        else self.text_fmt
                    )
                )
            self.ws.write(self.row_index, column, clean, target)
        self.row_index += 1

    def section(self, title: str, headers: tuple[str, ...], rows):
        self._row([])
        self._row([title], self.title_fmt)
        self._row(headers, self.header_fmt)
        count = 0
        for row in rows:
            self._row(row)
            count += 1
        if count == 0:
            self._row(["Нет данных за период"])


def build_xlsx(bundle: dict) -> bytes:
    output = BytesIO()
    # The API container's /tmp is private tmpfs; TemporaryDirectory is 0700 and
    # removed even when workbook assembly fails.
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
        summary = _Sheet(book, "Сводка OSS", bundle, [53, 22])
        summary.section("Ключевые показатели", ("Показатель", "Значение"), _summary(bundle))
        summary.section(
            "Применимые фильтры",
            ("Фильтр", "Значение"),
            [
                ("Монтажное направление", DIRECTION_NAMES.get(bundle["direction"], "Все")),
                ("Категория обращений", bundle["category_filter"] or "Все"),
            ],
        )
        summary.section(
            "Правила",
            ("Пояснение",),
            [
                ("События считаются по времени UTC+3; текущие состояния не ограничены периодом.",),
                ("Отправка монтажного задания не означает выполнение работы.",),
            ],
        )

        g = bundle["general"]
        sh = _Sheet(book, "Общая статистика", bundle, [26, 48, 20, 22, 24, 26, 23])
        sh.section(
            "Показатели",
            ("Показатель", "Значение"),
            _metric_rows(g["metrics"], GENERAL_LABELS),
        )
        sh.section(
            "Динамика",
            ("Дата UTC+3", "Поступили", "Закрыты"),
            [(item["date"], item["created"], item["closed"]) for item in g["daily"]],
        )
        for title, key in (
            ("Типы", "types"),
            ("Статусы", "statuses"),
            ("Приоритеты", "priorities"),
        ):
            sh.section(title, ("Значение", "Заявки"), g["distribution"][key].items())
        sh.section(
            "Заявки, созданные за период",
            ("№", "Тема", "Тип", "Статус", "Приоритет", "Исполнитель", "Создана UTC+3"),
            [
                (
                    r["number"],
                    r["subject"],
                    r["ticket_type"],
                    r["status"],
                    r["priority"],
                    r["assignee_name"],
                    r["created_at"],
                )
                for r in g["details"]["items"]
            ],
        )

        e = bundle["employees"]
        sh = _Sheet(book, "Сотрудники", bundle, [38, 26, 20, 20, 20, 20, 20, 20, 20])
        sh.section(
            "Действия сотрудников",
            (
                "Сотрудник",
                "Профиль",
                "Созданы",
                "Решения",
                "Звонки",
                "Уникальные заявки со звонками",
                "Устранена",
                "Осталась",
                "Нет ответа",
            ),
            [
                (
                    r["display_name"],
                    r["role"],
                    r["created_tickets"],
                    r["decision_events"],
                    r["callback_events"],
                    r["callback_tickets"],
                    r["outcomes"].get("CONFIRMED", 0),
                    r["outcomes"].get("STILL_BROKEN", 0),
                    r["outcomes"].get("NO_ANSWER", 0),
                )
                for r in e["items"]
            ],
        )
        sh.section(
            "Динамика действий",
            ("Сотрудник ID", "Дата UTC+3", "Созданы", "Решения", "Звонки"),
            [
                (str(r["user_id"]), r["date"], r["created"], r["decisions"], r["callbacks"])
                for r in e["daily"]
            ],
        )

        c = bundle["callbacks"]
        sh = _Sheet(book, "Контрольные звонки", bundle, [22, 48, 36, 28, 24, 25])
        sh.section(
            "Итоги",
            ("Показатель", "Значение"),
            [("События звонков", c["total_events"]), ("Уникальные заявки", c["unique_tickets"])],
        )
        sh.section(
            "Результаты",
            ("Результат", "Звонки"),
            [(OUTCOME_NAMES.get(k, k), v) for k, v in c["outcomes"].items()],
        )
        sh.section(
            "Динамика",
            ("Дата UTC+3", "Результат", "Звонки"),
            [
                (r["date"], OUTCOME_NAMES.get(r["outcome"], r["outcome"]), r["count"])
                for r in c["daily"]
            ],
        )
        sh.section(
            "По сотрудникам",
            ("Сотрудник", "Результат", "Звонки"),
            [
                (r["display_name"], OUTCOME_NAMES.get(r["outcome"], r["outcome"]), r["count"])
                for r in c["employees"]
            ],
        )
        sh.section(
            "Журнал звонков",
            ("Заявка", "Тема", "Сотрудник", "Результат", "Когда UTC+3"),
            [
                (
                    r["ticket_number"],
                    r["subject"],
                    r["actor_name"],
                    OUTCOME_NAMES.get(r["outcome"], r["outcome"]),
                    r["occurred_at"],
                )
                for r in c["details"]["items"]
            ],
        )

        f = bundle["field"]
        sh = _Sheet(book, "Монтажные работы", bundle, [32, 48, 24, 23, 23, 24, 24])
        sh.section(
            "Показатели", ("Показатель", "Значение"), _metric_rows(f["metrics"], FIELD_LABELS)
        )
        sh.section(
            "Направления",
            ("Направление", "Отправлены", "Отчёты", "Проверены", "Переносы"),
            [
                (
                    DIRECTION_NAMES.get(key, key),
                    value["sent"],
                    value["reported"],
                    value["verified"],
                    value["reschedules"],
                )
                for key, value in f["directions"].items()
            ],
        )
        sh.section(
            "Причины переносов",
            ("Причина", "События"),
            [(r["reason"], r["count"]) for r in f["reschedule_reasons"]],
        )
        sh.section(
            "Динамика",
            ("Дата UTC+3", "Отправлены", "Отчёты", "Проверены", "Переносы"),
            [
                (r["date"], r["sent"], r["reported"], r["verified"], r["reschedules"])
                for r in f["daily"]
            ],
        )
        sh.section(
            "Выездные задания с событиями за период",
            ("Заявка", "Тема", "Направление", "Состояние", "Отправлена", "Отчёт", "Проверена"),
            [
                (
                    r["ticket_number"],
                    r["subject"],
                    DIRECTION_NAMES.get(r["direction"] or "UNKNOWN"),
                    r["work_status"],
                    r["sent_at"],
                    r["reported_at"],
                    r["verified_at"],
                )
                for r in f["details"]["items"]
            ],
        )

        r = bundle["categories"]
        d = bundle["decisions"]
        sh = _Sheet(book, "Причины обращений", bundle, [25, 48, 48, 23])
        sh.section(
            "Показатели", ("Показатель", "Значение"), _metric_rows(r["metrics"], REASON_LABELS)
        )
        sh.section(
            "Категории при регистрации",
            ("Категория", "Заявки"),
            [(item["name"], item["count"]) for item in r["categories"]],
        )
        sh.section(
            "Причины инженерных решений",
            ("Причина", "События"),
            [(item["name"], item["count"]) for item in r["reasons"]],
        )
        sh.section(
            "Динамика по категориям",
            ("Дата UTC+3", "Категория", "Заявки"),
            [(item["date"], item["category"], item["count"]) for item in r["daily"]],
        )
        sh.section(
            "Обращения",
            ("Заявка", "Тема", "Категория при регистрации", "Создана UTC+3"),
            [
                (item["ticket_number"], item["subject"], item["name"], item["occurred_at"])
                for item in r["details"]["items"]
            ],
        )
        sh.section(
            "Версии решений",
            ("Заявка", "Тема", "Установленная причина", "Решена UTC+3"),
            [
                (item["ticket_number"], item["subject"], item["name"], item["occurred_at"])
                for item in d["details"]["items"]
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


def _chart(title: str, rows: list[tuple[str, int]]) -> Drawing | None:
    values = [(label, count) for label, count in rows[:10] if count is not None]
    if not values:
        return None
    canvas = Drawing(750, 36 + len(values) * 24)
    canvas.add(
        String(
            0,
            canvas.height - 15,
            title,
            fontName="OSS-Bold",
            fontSize=10,
            fillColor=colors.HexColor("#173a36"),
        )
    )
    ceiling = max(1, *(count for _, count in values))
    for index, (label, count) in enumerate(values):
        y = canvas.height - 42 - index * 24
        canvas.add(
            String(
                0, y, label[:38], fontName="OSS", fontSize=8, fillColor=colors.HexColor("#334750")
            )
        )
        canvas.add(
            Rect(
                255,
                y - 1,
                400 * count / ceiling,
                9,
                fillColor=colors.HexColor("#178c74"),
                strokeColor=None,
            )
        )
        canvas.add(
            String(
                665,
                y,
                str(count),
                fontName="OSS-Bold",
                fontSize=8,
                fillColor=colors.HexColor("#173a36"),
            )
        )
    return canvas


def build_pdf(bundle: dict) -> bytes:
    _font()
    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=30,
        rightMargin=30,
        topMargin=42,
        bottomMargin=42,
        title="OSS Control Center — управленческий отчёт",
        author="OSS Control Center",
    )
    heading = ParagraphStyle(
        "oss-heading",
        fontName="OSS-Bold",
        fontSize=15,
        leading=19,
        textColor=colors.HexColor("#173a36"),
        spaceAfter=12,
    )
    title = ParagraphStyle(
        "oss-title", parent=heading, fontSize=23, leading=29, alignment=TA_CENTER, spaceAfter=18
    )
    body = ParagraphStyle(
        "oss-body", fontName="OSS", fontSize=8, leading=11, textColor=colors.HexColor("#263941")
    )
    note = ParagraphStyle("oss-note", parent=body, fontSize=9, leading=14, spaceAfter=8)
    header = ParagraphStyle(
        "oss-table-head", parent=body, fontName="OSS-Bold", textColor=colors.white
    )
    story = [
        Spacer(1, 45),
        _paragraph("OSS Control Center", title),
        _paragraph("Единый управленческий отчёт", title),
        _paragraph(f"Период: {bundle['start']} — {bundle['end']} (UTC+3)", note),
        _paragraph(f"Сформирован: {_local_text(bundle['generated_at'])} (UTC+3)", note),
        _paragraph(
            "Данные получены из PostgreSQL OSS. События и текущие состояния показаны отдельно.",
            note,
        ),
        Spacer(1, 20),
        _paragraph("Краткая сводка", heading),
    ]

    def section(name: str):
        story.extend((Spacer(1, 16), _paragraph(name, heading)))

    def table(headers: tuple[str, ...], rows, widths: list[int]):
        data = [[_paragraph(value, header) for value in headers]]
        count = 0
        for row in rows:
            data.append([_paragraph(value, body) for value in row])
            count += 1
        if not count:
            data.append([_paragraph("Нет данных за период", body)] + [""] * (len(headers) - 1))
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
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(grid)

    table(("Показатель", "Значение"), _summary(bundle), [570, 204])
    story.append(PageBreak())
    g = bundle["general"]
    section("Общая статистика обращений")
    table(("Показатель", "Значение"), _metric_rows(g["metrics"], GENERAL_LABELS), [570, 204])
    for distribution_title, distribution_key in (
        ("Типы", "types"),
        ("Статусы", "statuses"),
        ("Приоритеты", "priorities"),
    ):
        table(
            (distribution_title, "Заявки"), g["distribution"][distribution_key].items(), [570, 204]
        )
    chart = _chart(
        "Поступившие заявки по датам", [(str(r["date"]), r["created"]) for r in g["daily"][-10:]]
    )
    if chart:
        story.append(chart)
    table(
        ("Дата UTC+3", "Поступили", "Закрыты"),
        [(r["date"], r["created"], r["closed"]) for r in g["daily"]],
        [258, 258, 258],
    )
    table(
        ("Заявка", "Тема", "Тип", "Статус", "Создана UTC+3"),
        [
            (r["number"], r["subject"], r["ticket_type"], r["status"], r["created_at"])
            for r in g["details"]["items"]
        ],
        [65, 325, 115, 115, 154],
    )

    e = bundle["employees"]
    section("Работа сотрудников")
    table(
        ("Сотрудник", "Созданы", "Решения", "Звонки", "Заявки со звонками"),
        [
            (
                r["display_name"],
                r["created_tickets"],
                r["decision_events"],
                r["callback_events"],
                r["callback_tickets"],
            )
            for r in e["items"]
        ],
        [310, 116, 116, 116, 116],
    )
    table(
        ("Сотрудник ID", "Дата UTC+3", "Созданы", "Решения", "Звонки"),
        [
            (r["user_id"], r["date"], r["created"], r["decisions"], r["callbacks"])
            for r in e["daily"]
        ],
        [310, 116, 116, 116, 116],
    )
    table(
        ("Сотрудник", "Устранена", "Осталась", "Нет ответа"),
        [
            (
                r["display_name"],
                r["outcomes"].get("CONFIRMED", 0),
                r["outcomes"].get("STILL_BROKEN", 0),
                r["outcomes"].get("NO_ANSWER", 0),
            )
            for r in e["items"]
        ],
        [300, 158, 158, 158],
    )

    c = bundle["callbacks"]
    section("Контрольный обзвон")
    table(
        ("Показатель", "Значение"),
        [("События звонков", c["total_events"]), ("Уникальные заявки", c["unique_tickets"])],
        [570, 204],
    )
    chart = _chart(
        "Исходы звонков",
        [(OUTCOME_NAMES.get(key, key), value) for key, value in c["outcomes"].items()],
    )
    if chart:
        story.append(chart)
    table(
        ("Дата UTC+3", "Исход", "Звонки"),
        [
            (r["date"], OUTCOME_NAMES.get(r["outcome"], r["outcome"]), r["count"])
            for r in c["daily"]
        ],
        [258, 258, 258],
    )
    table(
        ("Сотрудник", "Результат", "Звонки"),
        [
            (r["display_name"], OUTCOME_NAMES.get(r["outcome"], r["outcome"]), r["count"])
            for r in c["employees"]
        ],
        [300, 300, 174],
    )
    table(
        ("Заявка", "Тема", "Сотрудник", "Результат", "Когда UTC+3"),
        [
            (
                r["ticket_number"],
                r["subject"],
                r["actor_name"],
                OUTCOME_NAMES.get(r["outcome"], r["outcome"]),
                r["occurred_at"],
            )
            for r in c["details"]["items"]
        ],
        [65, 240, 170, 155, 144],
    )

    f = bundle["field"]
    section("Монтажные работы: Сухум / Очамчира")
    story.append(
        _paragraph(
            "Отправка задания не подтверждает выполнение. Отчёты и проверки считаются отдельно.",
            note,
        )
    )
    table(("Показатель", "Значение"), _metric_rows(f["metrics"], FIELD_LABELS), [570, 204])
    table(
        ("Направление", "Отправлены", "Отчёты", "Проверены", "Переносы"),
        [
            (DIRECTION_NAMES.get(k, k), v["sent"], v["reported"], v["verified"], v["reschedules"])
            for k, v in f["directions"].items()
        ],
        [310, 116, 116, 116, 116],
    )
    table(
        ("Причина переноса", "События"),
        [(r["reason"], r["count"]) for r in f["reschedule_reasons"]],
        [570, 204],
    )
    table(
        ("Дата UTC+3", "Отправлены", "Отчёты", "Проверены", "Переносы"),
        [
            (r["date"], r["sent"], r["reported"], r["verified"], r["reschedules"])
            for r in f["daily"]
        ],
        [310, 116, 116, 116, 116],
    )
    table(
        ("Заявка", "Тема", "Направление", "Состояние", "Отправлена UTC+3"),
        [
            (
                r["ticket_number"],
                r["subject"],
                DIRECTION_NAMES.get(r["direction"] or "UNKNOWN"),
                r["work_status"],
                r["sent_at"],
            )
            for r in f["details"]["items"]
        ],
        [65, 300, 130, 135, 144],
    )

    r = bundle["categories"]
    d = bundle["decisions"]
    section("Причины обращений и инженерных решений")
    story.append(
        _paragraph(
            "Категория при регистрации и причина решения — разные данные и разные даты событий.",
            note,
        )
    )
    table(("Показатель", "Значение"), _metric_rows(r["metrics"], REASON_LABELS), [570, 204])
    chart = _chart(
        "Категории обращений",
        [(item["name"] or "Без категории", item["count"]) for item in r["categories"]],
    )
    if chart:
        story.append(chart)
    table(
        ("Категория при регистрации", "Заявки"),
        [(item["name"], item["count"]) for item in r["categories"]],
        [570, 204],
    )
    table(
        ("Установленная причина", "Решения"),
        [(item["name"], item["count"]) for item in r["reasons"]],
        [570, 204],
    )
    table(
        ("Дата UTC+3", "Категория", "Заявки"),
        [(item["date"], item["category"], item["count"]) for item in r["daily"]],
        [180, 470, 124],
    )
    table(
        ("Заявка", "Тема", "Категория", "Создана UTC+3"),
        [
            (item["ticket_number"], item["subject"], item["name"], item["occurred_at"])
            for item in r["details"]["items"]
        ],
        [65, 350, 205, 154],
    )
    table(
        ("Заявка", "Тема", "Причина", "Решение UTC+3"),
        [
            (item["ticket_number"], item["subject"], item["name"], item["occurred_at"])
            for item in d["details"]["items"]
        ],
        [65, 350, 205, 154],
    )

    def footer(canvas, document):
        canvas.setFont("OSS", 8)
        canvas.setFillColor(colors.HexColor("#667b80"))
        canvas.drawString(34, 24, "OSS Control Center · данные UTC+3")
        canvas.drawRightString(landscape(A4)[0] - 34, 24, str(document.page))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
