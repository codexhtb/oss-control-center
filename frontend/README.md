# OSS Control Center Web — Phase 1.4 Stage 3A

Основа интерфейса на React, TypeScript, Vite, React Router и TanStack Query. Stage 3A включает вход/выход через существующую серверную сессию, восстановление сессии, маршруты «Обзор» и «Мой доступ», проверку backend readiness, базовые компоненты и дизайн-систему. Экраны заявок и Subscriber 360 вводятся в Stage 3B/3C; текущий обзор явно показывает эту границу и не имитирует показатели заявок.

## Локальный просмотр

Требуется Node.js 24. Пакеты устанавливаются только в `frontend/node_modules`:

```bash
cd /opt/oss-control-center/frontend
npm ci
npm run dev
```

Откройте `http://127.0.0.1:5173/` в браузере VM. Для браузера на рабочем компьютере используйте SSH port forwarding с **локальным** адресом `127.0.0.1:5173`; dev server слушает только loopback. Запущенный API остаётся на `127.0.0.1:8000` и не перезапускается. Локальный Vite proxy направляет только `/api/v1` к backend и приводит `Origin`/`Host` к его точному dev origin. Изменяющие запросы с чужим браузерным `Origin` блокируются до proxy. Если API слушает другой loopback-порт, задайте `DEV_API_ORIGIN=http://127.0.0.1:<port>` в окружении процесса Vite; значение должно совпадать с `AUTH_PUBLIC_ORIGIN` backend.

В браузере используется обычная cookie-сессия backend: `oss_session` остаётся HttpOnly, `oss_csrf` читается только для заголовка `X-CSRF-Token`. Пароли, сессионные токены и ответы API не сохраняются в localStorage, sessionStorage или IndexedDB. HTTP-клиент отправляет запросы с `credentials: same-origin` и `cache: no-store`; TanStack Query хранит ответы только в оперативной памяти во время наблюдения, а при выходе очищается. Для production нужен отдельный согласованный HTTPS origin с маршрутизацией `/api/v1`; Vite dev proxy для production не предназначен.

## Проверка

```bash
cd /opt/oss-control-center/frontend
npm run typecheck
npm test
npm run build
```

Изолированный тестовый образ из текущих файлов, без запуска или перезапуска рабочих сервисов:

```bash
cd /opt/oss-control-center
sg docker -c 'docker build -f frontend/Dockerfile -t oss-control-center-web:stage3a-test frontend'
```

Тесты frontend используют синтетические ответы и не создают пользователей или заявки в `oss_control`. Сборка создаёт `frontend/dist/`, который исключён из Git.

## Следующие этапы и ограничения API

- Stage 3B добавит список, создание трёх типов и карточку заявки. Backend поддерживает `GET /tickets` с фильтрами и `total`, но отдельного фильтра «неназначенные», общего потока последних событий и списка пользователей для выбора исполнителя пока нет. Эти элементы интерфейса не будут выдавать расчёты или локальные предположения за серверные данные.
- В `POST /tickets` для `MANUAL`/`INTERNAL` есть `description` и `priority`; отдельного поля «тема» нет. Прежде чем вводить отдельную тему в UI, нужен согласованный backend-контракт. Свободное описание уже поддерживается.
- Stage 3C покажет Subscriber 360 через backend. Нельзя выводить сырые DHCP ranges в первой версии; `UNKNOWN`, `CONFLICT` и `STALE` останутся явными. Прямого доступа браузера к MariaDB или PostgreSQL не будет.
- Stage 3D проверит полные HTTP-сценарии, ошибки, роли, доступность и адаптивность. Вход на рабочем dev API для тестов Stage 3A не выполнялся, чтобы не изменять рабочую БД.
