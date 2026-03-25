import asyncio
import base64
import logging
import os
from typing import Dict, List, Optional, Tuple

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("tebex-link-bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TEBEX_PUBLIC_TOKEN = os.getenv("TEBEX_PUBLIC_TOKEN", "").strip()
TEBEX_PRIVATE_KEY = os.getenv("TEBEX_PRIVATE_KEY", "").strip()
TEBEX_STORE_IDENTIFIER = os.getenv("TEBEX_STORE_IDENTIFIER", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN")
if not TEBEX_PUBLIC_TOKEN:
    raise RuntimeError("Missing TEBEX_PUBLIC_TOKEN")
if not TEBEX_PRIVATE_KEY:
    raise RuntimeError("Missing TEBEX_PRIVATE_KEY")
if not TEBEX_STORE_IDENTIFIER:
    raise RuntimeError("Missing TEBEX_STORE_IDENTIFIER")

BASE_URL = "https://headless.tebex.io/api"
USER_STATE: Dict[int, Dict] = {}
PACKAGE_CACHE: Dict[str, Dict] = {}

def is_admin(user_id: int) -> bool:
    return (not ADMIN_IDS) or (user_id in ADMIN_IDS)

def auth_headers() -> Dict[str, str]:
    token = base64.b64encode(
        f"{TEBEX_PUBLIC_TOKEN}:{TEBEX_PRIVATE_KEY}".encode("utf-8")
    ).decode("utf-8")
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

def tebex_get(path: str, params: Optional[Dict] = None) -> Dict:
    url = f"{BASE_URL}{path}"
    r = requests.get(url, headers=auth_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def tebex_post(path: str, payload: Optional[Dict] = None) -> Dict:
    url = f"{BASE_URL}{path}"
    r = requests.post(url, headers=auth_headers(), json=payload or {}, timeout=30)
    r.raise_for_status()
    if not r.text.strip():
        return {}
    return r.json()

def fetch_packages() -> List[Dict]:
    data = tebex_get(f"/accounts/{TEBEX_PUBLIC_TOKEN}/categories", params={"includePackages": 1})
    categories = data.get("data", []) or []
    packages: List[Dict] = []
    PACKAGE_CACHE.clear()

    for category in categories:
        for pkg in category.get("packages", []) or []:
            package_id = str(pkg.get("id"))
            price = ""
            if isinstance(pkg.get("total_price"), dict):
                amount = pkg["total_price"].get("amount")
                currency = pkg["total_price"].get("currency")
                if amount is not None:
                    price = f"{amount} {currency or ''}".strip()
            if not price:
                price = str(pkg.get("price", ""))

            item = {
                "id": package_id,
                "name": pkg.get("name", f"Package {package_id}"),
                "price": price,
                "category": category.get("name", ""),
                "raw": pkg,
            }
            packages.append(item)
            PACKAGE_CACHE[package_id] = item

    packages.sort(key=lambda x: (x["category"], x["name"]))
    return packages

def create_basket(username: str, custom: Optional[Dict] = None) -> Dict:
    payload = {
        "complete_url": f"https://{TEBEX_STORE_IDENTIFIER}.tebex.io/",
        "cancel_url": f"https://{TEBEX_STORE_IDENTIFIER}.tebex.io/",
        "complete_auto_redirect": False,
        "username": username,
        "custom": custom or {},
    }
    return tebex_post(f"/accounts/{TEBEX_PUBLIC_TOKEN}/baskets", payload).get("data", {})

def add_package_to_basket(basket_ident: str, package_id: str) -> Dict:
    payload = {"package_id": str(package_id), "quantity": 1}
    return tebex_post(f"/baskets/{basket_ident}/packages", payload)

def get_basket(basket_ident: str) -> Dict:
    return tebex_get(f"/accounts/{TEBEX_PUBLIC_TOKEN}/baskets/{basket_ident}").get("data", {})

def build_payment_link(package_id: str, username: str, created_by: int) -> str:
    basket = create_basket(
        username=username,
        custom={"created_by_telegram": str(created_by), "minecraft_username": username},
    )
    basket_ident = basket.get("ident")
    if not basket_ident:
        raise RuntimeError("Tebex did not return basket ident")
    add_package_to_basket(basket_ident, package_id)
    basket_data = get_basket(basket_ident)
    link = (((basket_data.get("links") or {}).get("checkout")) or "").strip()
    if not link:
        raise RuntimeError("Tebex did not return checkout link")
    return link

def chunk_text(lines: List[str], max_len: int = 3500) -> List[str]:
    chunks: List[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            if current:
                chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current:
        chunks.append(current.rstrip())
    return chunks

def package_keyboard(packages: List[Dict]) -> InlineKeyboardMarkup:
    rows = []
    for pkg in packages[:40]:
        label = f'{pkg["name"]} — {pkg["price"]}'
        rows.append([InlineKeyboardButton(label[:64], callback_data=f'pkg:{pkg["id"]}')])
    rows.append([InlineKeyboardButton("🔄 Обновить список", callback_data="refresh_packages")])
    return InlineKeyboardMarkup(rows)

def count_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("10", callback_data="count:10"),
            InlineKeyboardButton("15", callback_data="count:15"),
            InlineKeyboardButton("20", callback_data="count:20"),
        ],
        [
            InlineKeyboardButton("5", callback_data="count:5"),
            InlineKeyboardButton("25", callback_data="count:25"),
        ],
    ])

async def require_admin(update: Update) -> bool:
    user = update.effective_user
    if not user or not is_admin(user.id):
        target = update.message or update.callback_query.message
        await target.reply_text("Нет доступа.")
        return False
    return True

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    USER_STATE[update.effective_user.id] = {}
    packages = await asyncio.to_thread(fetch_packages)
    if not packages:
        await update.message.reply_text("Не смог получить товары из Tebex.")
        return
    await update.message.reply_text(
        "Выбери товар. Бот создаст пачку Tebex-ссылок на оплату.",
        reply_markup=package_keyboard(packages),
    )

async def cmd_packages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    packages = await asyncio.to_thread(fetch_packages)
    if not packages:
        await update.message.reply_text("Не смог получить товары из Tebex.")
        return
    lines = ["Товары из Tebex:"]
    for pkg in packages:
        lines.append(f'• {pkg["name"]} | {pkg["price"]} | id={pkg["id"]}')
    for chunk in chunk_text(lines):
        await update.message.reply_text(chunk)

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    USER_STATE.pop(update.effective_user.id, None)
    await update.message.reply_text("Сбросил текущий сценарий.")

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = USER_STATE.setdefault(user_id, {})
    data = query.data or ""

    if data == "refresh_packages":
        packages = await asyncio.to_thread(fetch_packages)
        await query.edit_message_text("Выбери товар.", reply_markup=package_keyboard(packages))
        return

    if data.startswith("pkg:"):
        package_id = data.split(":", 1)[1]
        pkg = PACKAGE_CACHE.get(package_id)
        if not pkg:
            await asyncio.to_thread(fetch_packages)
            pkg = PACKAGE_CACHE.get(package_id)
            if not pkg:
                await query.message.reply_text("Не нашёл этот товар. Нажми /start ещё раз.")
                return
        state["package_id"] = package_id
        state["package_name"] = pkg["name"]
        state["package_price"] = pkg["price"]
        await query.edit_message_text(
            f'Товар: {pkg["name"]}\nЦена: {pkg["price"]}\n\nТеперь выбери количество ссылок:',
            reply_markup=count_keyboard(),
        )
        return

    if data.startswith("count:"):
        count = int(data.split(":", 1)[1])
        state["count"] = count
        await query.edit_message_text(
            f'Количество: {count}\n\nТеперь отправь ник или список ников.\n'
            f'1 ник = этот же ник для всех {count} ссылок.\n'
            f'{count} строк = по 1 нику на каждую ссылку.'
        )
        return

def parse_usernames(text: str, expected_count: int) -> Tuple[Optional[List[str]], Optional[str]]:
    usernames = [line.strip() for line in text.splitlines() if line.strip()]
    if not usernames:
        return None, "Не вижу ников."
    if len(usernames) == 1:
        return usernames * expected_count, None
    if len(usernames) != expected_count:
        return None, (
            f"Ты отправил {len(usernames)} ников, а нужно либо 1 ник, "
            f"либо ровно {expected_count} строк."
        )
    return usernames, None

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    user_id = update.effective_user.id
    state = USER_STATE.get(user_id) or {}
    package_id = state.get("package_id")
    count = state.get("count")
    if not package_id or not count:
        await update.message.reply_text("Нажми /start и выбери товар с количеством.")
        return

    usernames, error = parse_usernames(update.message.text or "", count)
    if error:
        await update.message.reply_text(error)
        return

    package_name = state.get("package_name", package_id)
    package_price = state.get("package_price", "")

    await update.message.reply_text(
        f'Генерирую {count} ссылок...\nТовар: {package_name}\nЦена: {package_price}'
    )

    lines = [f'Товар: {package_name}', f'Цена: {package_price}', f'Количество: {count}', ""]
    success = 0
    for idx, username in enumerate(usernames, start=1):
        try:
            link = await asyncio.to_thread(build_payment_link, package_id, username, user_id)
            lines.append(f"{idx}. {username} — {link}")
            success += 1
        except Exception as e:
            log.exception("Failed to generate link %s", idx)
            lines.append(f"{idx}. {username} — ОШИБКА: {e}")

    for chunk in chunk_text(lines):
        await update.message.reply_text(chunk, disable_web_page_preview=True)
    await update.message.reply_text(f"Готово. Успешно: {success}/{count}")
    USER_STATE.pop(user_id, None)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("packages", cmd_packages))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Bot started")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
