# Утверждённый roadmap

| Фаза | Объём | Граница готовности |
| --- | --- | --- |
| Phase 0 — завершена | Архитектура, домен, workflow, модель данных, API, RBAC и план | Решения ревью отражены в документах |
| Phase 1 — Service Desk foundation | PostgreSQL как source of truth, RBAC, заявки, комментарии, решения, история, аудит, Subscriber 360 через mock BillingProvider | Mock-based MVP проходит [критерии приёмки](acceptance-and-testing.md) без NetUP, Telegram и мониторинга |
| Phase 2 — NetUP | Реальный read-only BillingProvider и сопоставление услуг/подключений | Контракт данных подтверждён; Ethernet, EPON, GPON и неоднозначные связки обрабатываются без догадок |
| Phase 3 — Telegram Field Dispatch | `field_dispatch`: проверка сообщения, отправка, сохранение `ticket_id` ↔ (`chat_id`, `message_id`), импорт ответа, уведомление, подтверждение инженера, аудит и fallback | Состояния доставки, работ и заявки независимы; Telegram-ответ не закрывает заявку |
| Phase 4 — Network Diagnostics | LibreNMS, Icinga, BDCOM collectors, диагностические snapshots и `UNKNOWN` | Наблюдения имеют источник и время; отсутствие данных не трактуется как неисправность |
| Phase 5 — массовые аварии | Корреляция обращений и аналитика | Правила корреляции согласованы отдельно |

Границы Phase 1 указаны в [плане реализации](phase-1-plan.md). Поздние фазы не блокируют разработку mock-based MVP.
