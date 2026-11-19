# Keitaro wrapper

Обёртка над Admin API трекера [Keitaro](https://keitaro.io): **создаватор кампаний** и **редактор офферов** существующей кампании.

Стек: **FastAPI + SQLAlchemy 2 + Alembic**, httpx для Keitaro API, фронт — чистый HTML/JS (без сборки).

## Что умеет

### 1. Создаватор кампаний (`/`)
Форма: название, гео (одна или несколько стран, с автокомплитом), оффер (поиск по ID/названию с автокомплитом), домен / группа / источник (подставляются по умолчанию из `.env`).

По кнопке **Create** сервис:
1. проверяет, что оффер существует;
2. создаёт кампанию (`type: position`, CPC, домен / группа / источник), имя получает префикс `CAMPAIGN_NAME_PREFIX` (`test_фамилия_`);
3. создаёт **поток 1** — фильтр `country accept [гео]` → HTTP-редирект на `https://google.com`;
4. создаёт **поток 2** — схема «офферы», выбранный оффер с долей 100%;
5. сохраняет кампанию в локальную БД.

Если создание потоков упало — кампания в Keitaro удаляется, чтобы не оставлять полуготовых кампаний.

### 2. Редактор кампании (`/campaign/{id}`)
Работает с **любой** кампанией Keitaro (список на главной, фильтр «только мои» — по префиксу).

* показывает потоки как в Keitaro: название, тип, фильтры, редирект / офферы, клики / уники / боты за выбранный период;
* по каждому офферу — доля, клики, уники, конверсии, доход (из `POST /report/build`);
* **добавить оффер** — поиск с автокомплитом; доли всех офферов потока пересчитываются поровну;
* **удалить оффер** — доли оставшихся пересчитываются поровну; последний оффер удалить нельзя (поток остался бы пустым);
* **изменить доли вручную** — с проверкой, что сумма = 100%;
* история изменений, сделанных через сервис (таблица `offer_changes`).

Равное деление всегда даёт ровно 100%: 3 оффера → 34/33/33, 6 → 17/17/17/17/16/16.

## Проверено на реальном Keitaro

Через этот сервис в tlgk.host создана кампания `test_grimaylo_campaign1` (ID 215286): группа FORTESTS, источник FORTESTS, домен kylovoa.com, поток «Google (AU)» (фильтр country accept AU, редирект на google.com) и поток «Offers» с оффером 18813 и долей 100%. В редакторе на ней проверены изменение долей и ошибки: повторный оффер, удаление последнего оффера, сумма долей не равна 100.

Нюанс статистики: Keitaro отдаёт клики, не попавшие ни в один поток, строкой с `stream_id = 0`. Они входят в итоги кампании и показаны в редакторе отдельной строкой «Вне потоков», поэтому итоги совпадают с цифрами Keitaro.
## Запуск

Нужен Python 3.11+. Все команды выполняются из папки `keitaro/`.

```bash
git clone https://github.com/Sanchezzxc/etolegko_test.git
cd etolegko_test/keitaro
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # Windows: copy .env.example .env
# заполнить KEITARO_API_KEY (Keitaro → Профиль → API ключи) и CAMPAIGN_NAME_PREFIX

alembic upgrade head        # миграции
uvicorn app.main:app --reload
```

Открыть http://127.0.0.1:8000 — UI, http://127.0.0.1:8000/docs — Swagger.

### Без ключа (демо-режим)
`KEITARO_MOCK=1` подменяет Keitaro in-memory фейком с демо-кампанией (как на скриншоте из ТЗ):

```bash
KEITARO_MOCK=1 uvicorn app.main:app      # PowerShell: $env:KEITARO_MOCK=1; uvicorn app.main:app
```

## Миграции

```bash
alembic upgrade head                          # применить
alembic downgrade -1                          # откатить последнюю
alembic revision --autogenerate -m "описание" # новая миграция после изменения app/models.py
```

БД по умолчанию — SQLite (`keitaro_wrapper.db`). Для Postgres: `pip install psycopg[binary]` и
`DATABASE_URL=postgresql+psycopg://user:pass@localhost/keitaro`.

## Тесты

```bash
pip install -r requirements-dev.txt
pytest
```

Тесты гоняют API через `TestClient` против in-memory Keitaro (`app/fake_keitaro.py`) и проверяют payload'ы создания, пересчёт долей, валидацию, откат при ошибке, плюс HTTP-клиент через `respx`.

## Настройки (`.env`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `KEITARO_URL` | `https://tlgk.host` | адрес трекера |
| `KEITARO_API_KEY` | — | ключ Admin API |
| `CAMPAIGN_NAME_PREFIX` | `test_grimaylo_` | префикс имени кампании |
| `DEFAULT_DOMAIN` / `DEFAULT_GROUP` / `DEFAULT_TRAFFIC_SOURCE` | `kylovoa.com` / `FORTESTS` / `FORTESTS` | что выбрано в форме по умолчанию (ищется по имени; если вариант один — берётся он) |
| `GOOGLE_URL` | `https://google.com` | куда редиректит гео-поток |
| `DATABASE_URL` | `sqlite:///./keitaro_wrapper.db` | БД |
| `REPORT_TIMEZONE` | `Europe/Moscow` | таймзона статистики |
| `KEITARO_MOCK` | `0` | фейковый Keitaro |

## API

| Метод | Путь | |
|---|---|---|
| GET | `/api/options` | домены, группы, источники + дефолты |
| GET | `/api/offers?q=` | поиск офферов (кэш 60 с) |
| GET | `/api/countries` | коды стран |
| POST | `/api/campaigns` | создать кампанию `{name, countries, offer_id, domain_id?, group_id?, traffic_source_id?}` |
| GET | `/api/campaigns?mine=&q=` | кампании из Keitaro |
| GET | `/api/campaigns/local` | кампании, созданные сервисом |
| GET | `/api/campaigns/{id}?interval=today` | потоки, офферы, доли, статистика |
| GET | `/api/campaigns/{id}/history` | история правок |
| POST | `/api/campaigns/{id}/streams/{sid}/offers` | добавить оффер `{offer_id}` |
| DELETE | `/api/campaigns/{id}/streams/{sid}/offers/{offer_id}` | удалить оффер |
| PUT | `/api/campaigns/{id}/streams/{sid}/shares` | задать доли `{offers: [{offer_id, share}]}` |

## Структура

```
app/
  main.py          FastAPI app, обработчики ошибок, отдача страниц
  api.py           роуты /api/*
  services.py      логика: создание кампании, офферы, доли, статистика
  keitaro.py       клиент Keitaro Admin API v1
  fake_keitaro.py  in-memory Keitaro для тестов и демо
  models.py        SQLAlchemy-модели (campaigns, offer_changes)
  schemas.py       Pydantic-схемы
  static/          index.html (создание + список), editor.html (редактор)
alembic/           миграции
tests/
```
