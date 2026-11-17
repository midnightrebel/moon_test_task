# Payment Processing Service

Сервис асинхронной обработки платежей. Клиент создаёт платёж через API и сразу получает ответ
`202 Accepted`. Дальше платёж обрабатывается в фоне через эмулятор платёжного шлюза, а результат
приходит клиенту на его webhook.

Стек: FastAPI, Pydantic v2, SQLAlchemy 2.0 (async), PostgreSQL, Alembic, RabbitMQ (FastStream), Docker Compose.

## Что нужно для запуска

- Docker и Docker Compose v2 (команда `docker compose`)
- свободные порты `8000` (API) и `15672` (веб-интерфейс RabbitMQ), но их можно поменять

Больше ничего ставить не надо: Python, PostgreSQL и RabbitMQ запускаются внутри контейнеров.

## Запуск

**1. Перейдите в папку проекта**

```bash
cd payment-service
```

**2. Создайте файл настроек**

```bash
cp .env.example .env
```

Откройте `.env` и при желании поменяйте `API_KEY`: с этим ключом нужно будет обращаться к API.
Если порты 8000 или 15672 уже заняты, поменяйте там же `API_PORT` и `RABBITMQ_UI_PORT`.

Этот шаг можно пропустить: тогда сервис запустится с ключом `dev-secret-key` на порту 8000.

**3. Соберите и запустите**

```bash
docker compose up -d --build
```

Первый запуск занимает пару минут, потому что нужно скачать образы и собрать приложение. Миграции
базы применяются автоматически: сервисы стартуют только после того, как они прошли.

**4. Проверьте, что всё поднялось**

```bash
docker compose ps
```

Сервисы `api`, `consumer`, `outbox-relay`, `postgres`, `rabbitmq` и `webhook-echo` должны быть в
статусе `Up`, а `migrate` — в статусе `Exited (0)`. Это нормально: он одноразовый, применил миграции и
завершился.

После этого доступны:

- документация API (Swagger): http://localhost:8000/docs
- RabbitMQ: http://localhost:15672, логин `guest`, пароль `guest`

## Как пользоваться

Во всех запросах нужен заголовок `X-API-Key`. В примерах ниже используется ключ по умолчанию
`dev-secret-key`; если вы поменяли его в `.env`, подставьте свой.

### Создать платёж

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H 'X-API-Key: dev-secret-key' \
  -H 'Idempotency-Key: order-42' \
  -H 'Content-Type: application/json' \
  -d '{
        "amount": "1500.50",
        "currency": "RUB",
        "description": "Заказ #42",
        "metadata": {"order_id": 42},
        "webhook_url": "http://webhook-echo:8080/hooks/payments"
      }'
```

Ответ:

```json
{"payment_id": "1454033b-9887-4a9b-a3ec-cf0624466c00", "status": "pending", "created_at": "2026-11-17T14:47:09.596981Z"}
```

Поля запроса:

| Поле | Обязательно | Описание |
|---|---|---|
| `amount` | да | сумма больше нуля, до 2 знаков после запятой |
| `currency` | да | `RUB`, `USD` или `EUR` |
| `description` | да | описание платежа |
| `metadata` | нет | любой JSON-объект |
| `webhook_url` | да | адрес, на который придёт результат |

`Idempotency-Key` — это уникальный ключ запроса, обычно номер заказа или UUID. Если отправить тот же
запрос с тем же ключом ещё раз, второй платёж не создастся: вернётся уже существующий. Если отправить
с тем же ключом *другие* данные, сервис вернёт `409 Conflict`.

### Узнать статус платежа

```bash
curl http://localhost:8000/api/v1/payments/<payment_id> -H 'X-API-Key: dev-secret-key'
```

Сразу после создания платёж в статусе `pending`. Примерно через 2–5 секунд статус меняется на
`succeeded` (в 90% случаев) или `failed` (в 10%: так эмулятор изображает отказ банка). Когда
webhook доставлен, заполняется поле `webhook_delivered_at`.

### Посмотреть, какой webhook пришёл

Для демонстрации в compose есть сервис `webhook-echo`: он принимает любые запросы и пишет их в лог.
Адрес `http://webhook-echo:8080/...` из примера выше указывает как раз на него.

```bash
docker compose logs -f webhook-echo
```

В реальной интеграции в `webhook_url` указывается адрес вашего сервиса. Тело webhook'а:

```json
{
  "event": "payment.succeeded",
  "payment_id": "1454033b-9887-4a9b-a3ec-cf0624466c00",
  "status": "succeeded",
  "amount": "1500.50",
  "currency": "RUB",
  "description": "Заказ #42",
  "metadata": {"order_id": 42},
  "idempotency_key": "order-42",
  "created_at": "2026-11-17T14:47:09.596981Z",
  "processed_at": "2026-11-17T14:47:12.734006Z"
}
```

В заголовке `X-Event-Id` приходит уникальный id события. Один и тот же webhook в редких случаях может
прийти дважды, поэтому по `X-Event-Id` удобно отбрасывать повторы.

### Что будет, если webhook недоступен

Сервис делает 3 попытки отправить webhook: сначала сразу, потом через 2 секунды, потом через 4.
Если все три не удались, сообщение попадает в очередь `payments.new.dlq`, где его можно разобрать
вручную. Деньги при повторных попытках повторно не списываются: платёж обрабатывается один раз.

Это легко проверить на недоступном адресе:

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H 'X-API-Key: dev-secret-key' -H 'Idempotency-Key: dlq-demo' -H 'Content-Type: application/json' \
  -d '{"amount": "10", "currency": "USD", "description": "test", "webhook_url": "http://webhook-echo:9999/down"}'

docker compose logs -f consumer
```

Через ~10 секунд в логе будут три попытки и перенос в DLQ. Сообщение можно найти в веб-интерфейсе
RabbitMQ: вкладка Queues → `payments.new.dlq`.

### Коды ответов

| Код | Когда |
|---|---|
| `202` | платёж принят (или повторный запрос с тем же ключом) |
| `200` | данные платежа |
| `401` | нет заголовка `X-API-Key` или ключ неверный |
| `404` | платежа с таким id нет |
| `409` | `Idempotency-Key` уже использовался с другими данными |
| `422` | ошибка в данных запроса или нет заголовка `Idempotency-Key` |

## Остановка

```bash
docker compose down
```

Данные (платежи, очереди) сохраняются в Docker-томах и будут на месте при следующем запуске.
Чтобы удалить их и начать с чистой базы, выполните `docker compose down -v`.

## Если что-то пошло не так

**`port is already allocated`** — порт занят другим приложением. Поменяйте `API_PORT` или
`RABBITMQ_UI_PORT` в `.env` и перезапустите `docker compose up -d`.

**Сервис не стартует или перезапускается** — посмотрите его лог:

```bash
docker compose logs api
docker compose logs consumer
docker compose logs outbox-relay
docker compose logs migrate
```

**`401 Unauthorized`** — проверьте, что ключ в заголовке `X-API-Key` совпадает с `API_KEY` в `.env`.
После изменения `.env` нужно перезапустить контейнеры: `docker compose up -d`.

**Платёж долго висит в `pending`** — проверьте, что запущены `outbox-relay` и `consumer`
(`docker compose ps`), и посмотрите их логи.

## Настройки

Все параметры задаются в `.env`:

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `API_PORT` | `8000` | порт API на вашей машине |
| `RABBITMQ_UI_PORT` | `15672` | порт веб-интерфейса RabbitMQ |
| `API_KEY` | `dev-secret-key` | ключ для заголовка `X-API-Key`, минимум 8 символов |
| `MAX_ATTEMPTS` | `3` | сколько раз пытаться обработать сообщение до отправки в DLQ |
| `RETRY_BASE_DELAY` | `2` | первая пауза между попытками в секундах, дальше она удваивается |
| `CONSUMER_PREFETCH` | `10` | сколько платежей обрабатывается одновременно |
| `LOG_LEVEL` | `INFO` | уровень логирования |

## Как это устроено

```
клиент ──► api ──► PostgreSQL (платёж + событие в outbox, одной транзакцией)
                        │
                 outbox-relay ──► RabbitMQ, очередь payments.new
                                        │
                                    consumer ──► шлюз (эмуляция) ──► статус в БД ──► webhook клиенту
                                        │
                     ошибка ──► retry через 2 с ──► retry через 4 с ──► payments.new.dlq
```

- **api** принимает запросы и пишет в базу платёж вместе с событием «платёж создан». API не зависит
  от RabbitMQ: даже если брокер недоступен, платежи принимаются.
- **outbox-relay** забирает новые события из базы и отправляет их в RabbitMQ (Outbox pattern).
  Событие помечается отправленным только после того, как брокер подтвердил приём, поэтому ничего не
  теряется.
- **consumer** берёт сообщение из очереди, «проводит» платёж через эмулятор шлюза, сохраняет статус
  и отправляет webhook. При ошибке сообщение уходит на повтор с растущей задержкой, а после трёх
  неудач — в Dead Letter Queue.
- **migrate** один раз при запуске применяет миграции Alembic и завершается.

Код:

```
app/
  api/          эндпоинты, схемы запросов и ответов, проверка API-ключа
  services/     создание платежа и защита от дублей
  db/           модели таблиц payments и outbox
  messaging/    очереди и exchange'и RabbitMQ
  outbox/       outbox-relay
  worker/       consumer, эмулятор шлюза, отправка webhook, повторы и DLQ
alembic/        миграции
tests/          тесты
```

## Тесты

Нужен Python 3.12.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/test_unit.py
```

End-to-end тесты работают против запущенного `docker compose`:

```bash
E2E_BASE_URL=http://localhost:8000 .venv/bin/pytest tests/test_e2e.py
```

Если вы поменяли `API_KEY`, передайте его тестам: `E2E_API_KEY=ваш-ключ`.
