# OSS Field Work Mini App

**Назначение.** Мобильная Telegram WebView для общей очереди монтажников и сотрудников с отдельным staff access. Не является отдельным сервисом заявок.

**Код:** frontend `frontend/src/features/field-work/FieldWorkApp.tsx`; API `app/api/v1/field_work_miniapp.py` и `field_work_staff.py`; auth `app/modules/field_dispatch/miniapp_auth.py`.

**Авторизация/права.** Backend проверяет подпись и срок Telegram `initData`; также используется зарегистрированный installer или связанный staff account, активность и разрешённые направления. URL/query ticket IDs не дают доступ сами по себе. Список монтажников требует `users.manage`; staff access управляется персональными permissions.

**Очереди:** open (`AWAITING_REPORT`), completed (`REPORTED`), history (`VERIFIED`). Публикация (`PUBLISHED`) независима от Telegram `SENT`. Несколько handoff на одну заявку поддерживаются как разные выезды.

**Отчёт/перенос:** report фиксирует текст, автора/исполнителей и время для конкретного handoff; перенос добавляет историю. Engineer verification переводит field work в `VERIFIED`. Основная заявка остаётся независимой.

**Ошибки и ограничения:** неизвестный, неактивный или ограниченный по направлению пользователь получает отказ; отсутствующие contact/snapshot поля не придумываются. Реальная доступность Telegram WebView, iOS/Android и внешнего порта проверяется вручную.

## Identity и данные карточки

Installer registry хранит Telegram user ID, имя, active flag и directions. Staff access отдельно связывает OCC user с Telegram identity и permissions; access request/link code не заменяет администраторское решение. `initData` проверяется сервером с auth age limit.

Карточка берёт ФИО/контакты из сохранённого ticket snapshot или caller fields, без нового NetUP lookup. Report выбирает необязательного напарника (не себя), блокирует handoff и вызывает общую reporting logic, поэтому callback бота и Mini App не создают два отчёта. Reschedule сохраняет новую историческую запись.

При возврате приложения queue перечитывается. `tel:` зависит от WebView; copy phone — обязательный fallback. Deep link на handoff не обходит direction/identity checks.
