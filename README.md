# Tebex bulk payment-link Telegram bot

Этот бот:
- забирает товары из Tebex Headless API
- даёт выбрать товар кнопками
- даёт выбрать количество ссылок
- просит 1 ник или список ников
- создаёт отдельные Tebex payment links пачкой

## Команды
- /start — начать сценарий
- /packages — показать товары и package id
- /cancel — сбросить текущий шаг

## Настройка
1. Скопируй .env.example в .env или задай env-переменные на сервере.
2. Поставь зависимости:
   pip install -r requirements.txt
3. Запусти:
   python main.py

## Env
- TELEGRAM_TOKEN — токен телеграм-бота
- TEBEX_PUBLIC_TOKEN — public token Tebex Headless API
- TEBEX_PRIVATE_KEY — private key Tebex
- TEBEX_STORE_IDENTIFIER — идентификатор магазина, например solar-webshop
- ADMIN_IDS — id телеграм-пользователей через запятую, кому можно пользоваться ботом

## Как пользоваться
1. Напиши /start
2. Выбери товар
3. Выбери количество: 10 / 15 / 20
4. Отправь:
   - либо 1 ник — он повторится на все ссылки
   - либо список из N ников — по одному нику на ссылку

## Важно
Токены, которые уже были отправлены в чат, лучше перевыпустить в Tebex и BotFather.
