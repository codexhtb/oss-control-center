# Telegram operations

## Единственный consumer

Текущий Field Dispatch poller — единственный consumer Telegram Bot API `getUpdates`. В коде он обрабатывает групповые callbacks/answers, переносы, personal-notification outbox и синхронизацию карточек. Не запускайте второй poller вручную или из одноразового контейнера.

## Проверки

- Read-only проверить, что утверждённый poller service запущен и нет второй копии.
- Просмотреть ограниченные по времени логи; не выгружать bot token, полный update, телефон, адрес или отчёт монтажника.
- Сверить delivery attempt, group binding, Telegram message ID, prompt/delivery status штатными API/DB read-only инструментами.
- Для `UNCERTAIN` сначала подтвердить факт доставки в Telegram и привязать реальный message ID только штатным recovery flow; не ставить `SENT` вручную.
- Проверить Bot API/network с хоста/контейнера без отправки сообщения. Не вызывать `sendMessage` в production как smoke test.

Доступность личных сообщений зависит от того, начал ли пользователь private chat с ботом; ошибка доставки не должна отменять выездную работу. Реальные group IDs и token в документацию не включаются.
