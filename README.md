# market-searchers

Два Telegram-бота, которые ищут выгодные лоты и присылают новые совпадения:

- **Kufar** (`@kufarresearchbot`) — б/у объявления kufar.by, правило «дешевле медианы на N%».
- **Sendico → Mercari** — японский Mercari через залогиненный аккаунт Sendico, правило «любое новое совпадение».

Один репозиторий, общее ядро, оба источника. Смерть бота больше не зависит от чужого хостинга.

## Как устроено

```
GitHub Actions cron (*/20)            Render web service
  scan.py                               webhook.py
    kufar:   открытый JSON-API            POST /tg/kufar    → @kufarresearchbot
    sendico: автологин → /api/…           POST /tg/sendico  → бот Sendico
    считает по правилу источника          /status, /deals ← state.json (raw)
    шлёт уведомления в Bot API            /menu → правит config.json (Contents API)
    коммитит state.json
```

Два намеренных решения:

- **Уведомления уходят из Actions, а не из Render.** Render в этот момент может спать — на доставку это не влияет (холодный старт бьёт только по ручным командам).
- **Два писателя — два файла.** Actions пишет только `state.json`, Render — только `config.json`. Конфликтов нет; в workflow перед push стоит `git pull --rebase`.

## Структура

```
core/telegram.py   Bot API (send/answerCallback/editMessage/setWebhook), нарезка 3500
core/store.py      атомарное чтение/запись JSON (.tmp + rename, битый → .broken)
core/gh.py         GitHub Contents API (config.json) + raw-чтение (state.json)
core/deals.py      обрезанная по IQR медиана (kufar) и потолок цены (sendico)
sources/kufar.py   сборка запроса, копейки→BYN, фильтры, стоп-слова
sources/sendico.py автологин Sanctum + /api/mercari/items, устойчивый маппинг полей
scan.py            вход сканера (Actions): все подписки, --dry-run, --source
webhook.py         FastAPI: /tg/kufar, /tg/sendico, /health, inline-меню
config.json        подписки (правит меню)
state.json         виденные id, последние сделки, время прогонов (токенов нет)
```

## Правила сделки

- **Kufar** — считаем медиану цен, предварительно срезав выбросы по IQR (чтобы запчасти
  и случайные дорогие лоты её не тянули). Лот — сделка, если он дешевле медианы на
  `threshold_pct` **и** не ниже нижней границы IQR (это отсекает приманки «слишком дёшево»).
- **Sendico** — уведомление на **любой** новый (не виденный) лот по запросу. Необязательный
  `max_price_yen` — предохранитель от вала при широком ключевом слове.

## Подписки (`config.json`)

```json
{
  "paused": false,
  "subscriptions": [
    { "id": "iphone13", "source": "kufar", "enabled": true, "query": "iphone 13",
      "cat": "17010", "rgn": "7", "min_byn": 50, "max_byn": 5000,
      "threshold_pct": 30, "private_only": true },
    { "id": "switch", "source": "sendico", "enabled": true, "query": "nintendo switch",
      "max_price_yen": null }
  ]
}
```

Kufar-поля: `cat` — категория (`17010` = мобильные телефоны), `rgn` — область
(**7** = Минск, **1** = Брестская, **2** = Гомельская, **3** = Гродненская,
**4** = Могилёвская, **5** = Витебская, **6** = Минская область), `min_byn`/`max_byn`
в **рублях** (внутри переводятся в копейки), `private_only` — только частники.

Править можно прямо из Telegram: `/menu` рисует кнопки (пауза, вкл/выкл подписки,
±5 % порога Kufar, ±5000 ¥ потолка Sendico). Изменение коммитит `config.json`, и
следующий прогон (≤ 20 мин) его подхватывает.

## Настройка

1. **Секреты Actions** (Settings → Secrets → Actions):
   `KUFAR_BOT_TOKEN`, `KUFAR_CHAT_ID`, `SENDICO_BOT_TOKEN`, `SENDICO_CHAT_ID`,
   `SENDICO_EMAIL`, `SENDICO_PASSWORD`.
2. **Render** — web service из этого репозитория:
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn webhook:app --host 0.0.0.0 --port $PORT`
   - Env: `KUFAR_BOT_TOKEN`, `SENDICO_BOT_TOKEN`, `KUFAR_WEBHOOK_SECRET`,
     `SENDICO_WEBHOOK_SECRET` (≥ 16 символов), `ALLOWED_TELEGRAM_USER_IDS`,
     `PUBLIC_BASE_URL` (адрес сервиса), `AUTO_SET_WEBHOOK=true`, `GH_TOKEN`
     (fine-grained, `contents:write` только на этот репо), `GITHUB_REPO=<owner>/market-searchers`.
   - Sendico-логин/пароль на Render **не** нужны — поиск живёт в Actions.
   - При старте сервис сам вызывает `setWebhook` для обоих ботов.
3. **Запуск.** Репозиторий поставляется на **паузе** (`config.json: "paused": true`), чтобы
   cron не копил состояние вхолостую до настройки. Когда секреты на месте — снимите паузу
   кнопкой в `/menu` (или поставьте `"paused": false`). После этого сканер идёт каждые 20 минут
   сам. `--dry-run` работает независимо от паузы (для проверки).

## Команды

- `/menu` — подписки, пороги, пауза
- `/status` — время последнего прогона и статус
- `/deals` — последние найденные сделки

## Проверка

- **Kufar без секретов:** Actions → `scan` → Run workflow, `dry_run=true`, `source=kufar` —
  в логе медиана и список лотов, без 422, ничего не отправлено.
- **Полный прогон:** `dry_run=false` — появляется коммит `state.json`.
- **Render:** `curl -s https://<service>.onrender.com/health` → `{"status":"ok",...}`.
- **Вебхуки:** `getWebhookInfo` обоих ботов → `url` на Render, ошибок нет.
- **Доступ:** сообщение с чужого аккаунта → тишина, в логах Render `Rejected … unauthorized`.

## Замечания

- Все токены/креды — только в секретах Actions и env Render. В репозитории (`state.json`)
  токенов нет: только id лотов и цены.
- Точную форму ответа Sendico `/api/mercari/items` видно лишь на первом авторизованном
  прогоне — адаптер устойчив к разным именам полей, а `--dry-run` печатает первый сырой
  элемент для сверки. Если имена разойдутся — правится только `sources/sendico.py:_normalize`.
- Прямой Mercari (подпись устройства) и Cloudflare-челлендж Sendico не обходятся — путь к
  Mercari идёт через твой залогиненный аккаунт Sendico.
