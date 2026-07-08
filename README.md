# OZI Bot — навигатор кружков Астаны (родители) · v2

> v2: тип сервиса — web (бесплатный Render не даёт worker); добавлен keepalive-сервер для UptimeRobot.

Продукт 1 из трёх. Stateless, PicklePersistence, читает 134 реальных центра.
База встроена в код (быстрый старт без Google API); переход на Sheets — заменой `load_centers()` и `storage.py`.

## Что уже умеет
- Поиск: 6 категорий → возраст → район → бюджет
- Поиск по буквам (напишите «англ», «шахмат», «плавание»)
- Карточка со статусом доверия (✅ Данные подтверждены / ⏳ Кандидат); подтверждённые — выше
- 🎟 «Записаться на пробное» → согласие → телефон → **лид** (пишется в ozi_leads.jsonl)
- Уведомление команды в OZI Bridge о каждом лиде
- Избранное, «О нас» (голос соцстартапа), логирование событий

## Запуск локально (проверить у себя)
```bash
pip install -r requirements.txt
cp .env.example .env         # впишите токены в .env
python ozi_bot.py
```

## Деплой на Render (бесплатно, 24/7)
1. Залить папку `ozi_bot/` в репозиторий на GitHub.
2. render.com → New → **Blueprint** → подключить репозиторий (возьмёт render.yaml → создаст бесплатный **web-сервис**).
3. В Environment вписать переменные (НЕ в код!):
   - `BOT_TOKEN` — токен OZI Astana от @BotFather
   - `BRIDGE_TOKEN` — токен OZI Bridge (для уведомлений команде)
   - `ADMIN_IDS` — 358966860,6478465134
4. Deploy. Бот работает.
5. UptimeRobot → HTTP-монитор на сервис (keepalive против засыпания free-плана).

## Файлы
- `ozi_bot.py` — логика бота (stateless)
- `centers_data.py` — 134 центра + поиск + формат карточки (слой данных изолирован)
- `storage.py` — лиды/профили/события (сейчас JSONL, позже Google Sheets)
- `render.yaml` · `requirements.txt` · `.env.example`

## Переход на Google Sheets (позже)
Заменить `load_centers()` в `centers_data.py` и функции в `storage.py` на чтение/запись
через Google Sheets API (service account). Интерфейс функций сохраняется — остальной код не трогается.
