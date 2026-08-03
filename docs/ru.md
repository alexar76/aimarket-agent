# aimarket-agent — руководство по Python SDK и CLI

Эталонный **потребительский агент** для [AIMarket Protocol v2](https://github.com/alexar76/aimarket-protocol), написанный на чистом Python. Одна команда `pip install` позволяет любому серверному агенту, инструменту LangChain или CLI **находить → оплачивать → вызывать** capabilities по всей федерации AIMarket.

> **Живой hub:** [modelmarket.dev](https://modelmarket.dev) · **Экосистема:** [modeldev.modelmarket.dev](https://modeldev.modelmarket.dev) · **Репозиторий:** [alexar76/aimarket-agent](https://github.com/alexar76/aimarket-agent) · **Кросс-платформенные SDK:** [`../../aimarket-sdks/docs/en.md`](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md)

---

## 1. Что это

`aimarket-agent` — это **Python**-представитель семейства AIMarket SDK. Он делает ровно одно: превращает задачу на естественном языке в найденный, оплаченный и вызванный capability и возвращает подписанную ведомость материалов (bill of materials).

Он намеренно облегчён — только `httpx` + `cryptography`, без FastAPI, без базы данных — поэтому легко встраивается в инструмент LangChain, фоновый воркер, Lambda или однострочный вызов из CLI. Этот пакет относится к линии **Python 2.1.x** (SDK для Dart/TypeScript/Rust — это отдельная линия `0.1.x`, см. [§8 Версионирование](#8-версионирование)).

---

## 2. Установка

```bash
pip install aimarket-agent
```

| Требование | Значение |
|-------------|-------|
| Python | **>= 3.11** |
| Runtime-зависимости | `httpx>=0.28`, `cryptography>=44` |
| Консольный скрипт | `aimarket-agent` |
| Лицензия | MIT |

---

## 3. Модель — stateless-потребитель (кошелька здесь нет)

Это самое важное, что нужно понять, потому что Python-агент **архитектурно отличается** от кросс-платформенных SDK.

| | Python `aimarket-agent` (этот пакет) | Dart / TypeScript / Rust ([`../../aimarket-sdks/docs/en.md`](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md)) |
|---|---|---|
| Состояние | **Stateless HTTP-потребитель** | Хранит кошелёк / ключ подписи |
| Кошелёк/seed в публичном API | **Нет** | `walletKey` (seed Ed25519), опционально `ethereumPrivateKeyHex` |
| Подписывает invoke-запрос | **Нет** — заголовок `X-Market-Signature` не отправляется | Да — `X-Market-Signature: ed25519:<base64>` |
| Где живёт авторизация | **На стороне hub** | На стороне клиента |
| Какую криптографию *использует* | **Проверяет** receipt'ы hub по публичному ключу Ed25519 hub | Подписывает запросы + проверяет receipt'ы |

В публичном API Python **нет ни `walletKey`, ни seed, ни подписи**. Агент никогда не формирует заголовок с подписью. Авторизация платежа выполняется на стороне hub; агент просто общается с v2-эндпоинтами hub по обычному JSON/HTTP и ссылается на платёжный канал по идентификатору (`X-Payment-Channel`) и на опциональный аффилиатный идентификатор (`X-AIMarket-Affiliate`).

Что агент *умеет* в криптографическом смысле — это **проверять** то, что приходит в ответ. При `verify_receipts=True` (по умолчанию) он получает заявленный публичный ключ Ed25519 hub из `/.well-known/ai-market.json` и сверяет с ним каждый invoke-receipt. Результат проверки отражается в ответе (`receipt_verified`, `receipt_verify_reason`) и **никогда не выбрасывается** как исключение — провалившаяся проверка не прерывает ваш вызов, а лишь помечает вывод как непроверенный, чтобы вы сами решили, что с этим делать.

> Если вам нужен клиент, который подписывает запросы собственным кошельком (EVM-приложения, on-chain списания с канала через EIP-712), используйте кросс-платформенные SDK, описанные в [`../../aimarket-sdks/docs/en.md`](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md). Это отдельное семейство пакетов.

---

## 4. Использование как библиотеки

```python
from aimarket_agent import AIMarketAgent

agent = AIMarketAgent(
    base_url="https://modelmarket.dev",
    budget=3.00,
    affiliate_id="my_app",
    verify_receipts=True,
)
```

### Конструктор

```python
AIMarketAgent(
    base_url: str,
    budget: float = 3.0,
    timeout: float = 120.0,
    affiliate_id: str = "",
    verify_receipts: bool = True,
)
```

| Аргумент | По умолчанию | Назначение |
|----------|---------|---------|
| `base_url` | — (обязателен) | URL hub. Завершающий слеш отбрасывается. |
| `budget` | `3.0` | Максимальные траты в USD; ограничивает депозит канала и поиск при discovery. |
| `timeout` | `120.0` | HTTP-таймаут на запрос (в секундах). |
| `affiliate_id` | `""` | Отправляется как `X-AIMarket-Affiliate` для разделения дохода; пусто = не отправляется. |
| `verify_receipts` | `True` | Проверять каждый receipt по ключу Ed25519 hub из `/.well-known`. |

### Методы

```python
# Полный автономный цикл: discover → channel → invoke → settle → bill of materials.
result = agent.run("translate spec to 5 languages + legal review")
print(f"Spent: ${result['total_spent_usd']:.2f}")
print("verified OK:", result["bill_of_materials"]["all_ok"])

# Только discovery — возвращает список dict'ов с совпавшими capabilities (без invoke, без трат).
matches = agent.discover("summarize long documents", limit=8)
for m in matches:
    print(m["capability_id"], "$", m.get("price_per_call_usd", 0))

# Прямой вызов одного capability (открывает + закрывает канал вокруг вызова).
res = agent.invoke_single(
    "prod-translate",                       # product_id
    "translate.multi@v2",                   # capability_id
    {"text": "Hello world", "locales": ["ru", "fr", "de"]},  # input
    source_hub="local",                     # или URL федеративного hub
)

# Проверить один receipt вне основного потока по публичному ключу hub.
vr = agent.verify_receipt(res["receipt"])
print(bool(vr), vr.reason)
```

| Метод | Сигнатура | Возвращает |
|--------|-----------|---------|
| `run` | `run(task: str)` | `dict` с ведомостью материалов (см. §6) |
| `discover` | `discover(query: str, limit: int = 8)` | `list[dict]` совпавших capabilities |
| `invoke_single` | `invoke_single(product_id, capability_id, input_payload: dict, source_hub: str = "local")` | `dict` с результатом invoke |
| `verify_receipt` | `verify_receipt(receipt: dict)` | `receipts.VerifyResult` (truthy при успешной проверке; имеет `.reason`) |
| `close` | `close()` | закрывает базовый `httpx.Client` |

Агент также является контекстным менеджером, поэтому HTTP-клиент всегда корректно освобождается:

```python
with AIMarketAgent(base_url="https://modelmarket.dev", budget=1.50) as agent:
    matches = agent.discover("code review")
```

---

## 5. Использование через CLI

Установка пакета добавляет консольный скрипт `aimarket-agent` в ваш `PATH`.

```bash
# Полный автономный цикл
aimarket-agent run "translate spec to 5 languages + legal review" \
  --base-url https://modelmarket.dev \
  --budget 3.00

# Найти capabilities (без трат)
aimarket-agent search "code review" --base-url https://modelmarket.dev

# Вызвать один capability — ref имеет вид product_id/capability_id
aimarket-agent invoke prod-translate/translate.multi@v2 \
  --base-url https://modelmarket.dev \
  --input '{"text":"Hello world"}'
```

| Команда | Позиционный аргумент | Флаги |
|---------|-----------|-------|
| `run` | `task` | `--base-url`, `--budget`, `--affiliate`, `--json` |
| `search` | `query` | `--base-url`, `--limit`, `--json` |
| `invoke` | `capability_ref` (`product_id/capability_id`) | `--base-url`, `--input`, `--budget` |

| Флаг CLI | По умолчанию | Описание |
|----------|---------|-------------|
| `--base-url` | `http://127.0.0.1:9083` | URL hub (по умолчанию для локальной разработки; для живого hub используйте `https://modelmarket.dev`) |
| `--budget` | `3.0` | Максимальный бюджет в USD |
| `--affiliate` | `""` | Аффилиатный идентификатор для разделения дохода (только `run`) |
| `--limit` | `8` | Максимум результатов поиска (только `search`) |
| `--input` | `{}` | JSON-payload входных данных (только `invoke`) |
| `--json` | выкл. | Выводить сырой JSON вместо человекочитаемой сводки |

`run` записывает в текущую директорию аудит-файл `bill_of_materials.json` и завершается с ненулевым кодом, если цикл не отработал полностью. Пример человекочитаемого вывода:

```
[plan]  translate.multi@v2  (est $0.40)
[call]  translate.multi@v2 $0.40 ✓
[settle] used $0.40, refund $2.60
[total] $0.40
[saved] bill_of_materials.json
```

---

## 6. Цикл из 5 фаз — как его исполняет Python

`run(task)` проходит тот же канонический жизненный цикл создания ценности из 5 фаз, что и кросс-платформенные SDK, отображённый на v2-эндпоинты hub. Разница только в том, *кто держит ключ* (§3) — фазы идентичны.

| # | Каноническая фаза | Эндпоинт hub (v2) | Что делает `run()` |
|---|-----------------|-------------------|-------------------|
| 1 | **Discovery** | `GET /.well-known/ai-market.json` → `GET /ai-market/v2/search` | читает well-known-документ, строит верификатор receipt'ов по ключу hub, затем ищет по `intent`/`budget`/`limit` и формирует одношаговый план из первого совпадения |
| 2 | **Channel** | `POST /ai-market/v2/channel/open` | открывает предоплаченный канал под размер `budget`; если hub возвращает `404` (нет плагина каналов), молча продолжает работу без канала |
| 3 | **Invoke** | `POST /ai-market/v2/invoke` | вызывает capability с заголовками `X-Payment-Channel` (если канал открыт) и `X-AIMarket-Affiliate` (если задан) — **без заголовка подписи** |
| 4 | **Settle** | `POST /ai-market/v2/channel/close` | закрывает канал и фиксирует расчёт (использовано / возврат) |
| 5 | **Verify** | *(локально)* | при `verify_receipts=True` сверяет каждый receipt с публичным ключом Ed25519 hub и проставляет `receipt_verified` / `receipt_verify_reason` в результате |

Для предсказуемых трат `run()` ограничивает план **первым** совпавшим capability (многошаговые DAG — будущая возможность на уровне протокола). Если хотите управлять шагами самостоятельно, используйте `discover()` + `invoke_single()`.

Возвращаемый dict с ведомостью материалов содержит следующие ключи:

```python
{
  "task": "...",
  "plan": [{"product_id": ..., "capability_id": ..., "source_hub": ..., "est_price_usd": ...}],
  "results": [ { "success": True, "result": {...}, "price_usd": 0.40,
                 "receipt": {...}, "receipt_verified": True, "receipt_verify_reason": "..." } ],
  "settlement": {"used_usd": 0.40, "refund_usd": 2.60},
  "channel_id": "ch_a8f3",
  "total_spent_usd": 0.40,
  "all_ok": True,
  "protocol_version": "v2",
  "agent_version": "2.0.0"
}
```

`run()` возвращает `{"task": ..., "ok": ..., "bill_of_materials": <dict выше>, "total_spent_usd": ...}`.

---

## 7. Бюджет и ошибки

**Бюджет.** `budget` — это потолок трат в USD. Он передаётся как параметр `budget` при discovery (чтобы hub возвращал только те capabilities, которые вам по карману), и он же используется как `deposit_usd` для предоплаты платёжного канала. Неизрасходованный депозит возвращается при расчёте (`settlement.refund_usd`). Поскольку `run()` вызывает только первое совпадение, один вызов `run()` тратит самое большее `price_usd` одного capability.

**Ошибки.** Агент защищён по своей сути: сбои discovery и расчёта перехватываются и возвращаются как данные, а не выбрасываются как исключения. Ответы invoke со значимым HTTP-статусом превращаются в структурированные записи результата:

| Условие | HTTP | Что вы получаете в ответ |
|-----------|------|-------------------|
| Сбой discovery / search | 4xx/5xx | `{"error": "discovery failed: ..."}` или `{"error": "search failed: ..."}` (без плана) |
| На hub нет плагина каналов | 404 | канал молча открывается как `""`; цикл продолжается без канала |
| Сработал safety-гейт | 403 | запись результата `{"safety_blocked": True, "category": ..., "reason": ...}`; цикл останавливается |
| Требуется оплата | 402 | запись результата `{"payment_required": True, "detail": ...}`; цикл останавливается |
| Иной сбой invoke | прочее | запись результата `{"error": "HTTP <code>", "capability_id": ...}`; цикл останавливается |
| Receipt не прошёл проверку | 200 | результат сохраняется, но `receipt_verified=False` + `receipt_verify_reason`; исключение **не** выбрасывается |

Когда safety-гейт блокирует вызов (инъекция, PII и т. п.), hub возвращает HTTP 403 с подписанным receipt'ом отклонения, а канал автоматически возвращает средства — поэтому заблокированный вызов ничего не стоит.

---

## 8. Версионирование

Этот пакет следует линии **Python 2.1.x**. Это сделано намеренно и отдельно от SDK для Dart/TypeScript/Rust, которые используют общую мультиязычную линию **0.1.x**. Все они нацелены на **AIMarket Protocol v2**.

| Пакет | Реестр | Линия версий |
|---------|----------|--------------|
| `aimarket-agent` (этот) | PyPI | **2.1.x** |
| `@aimarket/agent` | npm | 0.1.x |
| `aimarket_agent` | pub.dev | 0.1.x |
| `aimarket-agent` (crate) | crates.io | 0.1.x |

Линия Python вышла первой (CLI, цикл `run()`, аудит-трейл BOM, каналы с доверием к hub, отсутствие кошелька в публичном API); мультиязычная линия запустилась вместе с клиентской подписью вызовов по **Ed25519** (плюс необязательный ключ EIP-712/secp256k1 для on-chain списаний с канала). Python может перейти на **3.x**, если/когда его публичный API получит явный паритет по кошельку и подписи. Полное обоснование и матрица триггеров релиза: [`../../docs/sdk-version-policy.md`](../../docs/sdk-version-policy.md).

---

## 9. Связанная документация

- [Кросс-платформенные SDK AIMarket (Dart / TypeScript / Rust)](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md) — семейство SDK с кошельком
- [Политика версионирования SDK](../../docs/sdk-version-policy.md) — почему Python имеет версию 2.x, а остальные — 0.1.x
- [Oracles](https://github.com/alexar76/oracles) — проверяемая случайность, VDF, консенсус, репутация — capabilities, доступные для discovery из этого агента
- [AIMarket Hub](https://github.com/alexar76/aimarket-hub) — каталогизирует и маршрутизирует capabilities (тот самый `base_url`, на который вы указываете)
- [Well-known-документ живого hub](https://modelmarket.dev/.well-known/ai-market.json)

---

🇬🇧 [English](en.md) · 🇷🇺 [Русский](ru.md) · 🇪🇸 [Español](es.md)
