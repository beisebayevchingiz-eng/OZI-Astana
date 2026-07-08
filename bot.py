#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════════
# OZI TELEGRAM BOT v2.0 — Stateless Edition
# Верифицированный навигатор кружков Астаны
# Dana Abiltayeva + OZI Team | Июль 2026
# ════════════════════════════════════════════════════════════════
# Главное отличие v2.0: все кнопки работают ВСЕГДА, даже после
# перезапуска сервера. Каждая кнопка несёт нужные данные в себе.
# Новое: 🗺 карта, ⭐ отзывы, /history, трекинг WhatsApp-контактов.
# ════════════════════════════════════════════════════════════════

import os
import logging
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
from centers_data import (
    CENTERS, DIRECTIONS, DISTRICTS, AGE_GROUPS,
    BUDGETS, search_centers, format_center_card
)

# ─── LOGGING ─────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── ENV ─────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")
BOT_TOKEN     = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ─── TEXTS ───────────────────────────────────────────────────
WELCOME_TEXT = """
👋 Привет! Я *OZI* — бесплатный навигатор кружков в Астане.

✅ Только верифицированные центры
✅ Актуальные цены и расписание

Помогу найти подходящий кружок за 2 минуты 🎯
"""

NO_RESULTS_TEXT = """
😔 По вашему запросу ничего не нашлось.

Попробуйте:
• Район → «Любой район»
• Бюджет → «Любой бюджет»
• Другое направление

🔄 Начать заново: /start
"""

# ─── KEEPALIVE WEB SERVER (для бесплатного тарифа Render) ────
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("OZI Bot is alive ✅".encode("utf-8"))
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass

def start_keepalive_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info(f"Keepalive server on port {port}")

# ─── HELPERS ─────────────────────────────────────────────────

def get_center(cid: int):
    return next((x for x in CENTERS if x["id"] == cid), None)

def maps_url(c) -> str:
    """Кнопка «На карте»: если у центра есть точная ссылка 2ГИС — она,
    иначе поиск по адресу в Google Maps (работает без координат)."""
    if c.get("link_2gis"):
        return c["link_2gis"]
    q = urllib.parse.quote(f"{c['name']}, {c['address']}, Астана")
    return f"https://www.google.com/maps/search/?api=1&query={q}"

def wa_number(c) -> str:
    return c["whatsapp"].replace("+", "").replace(" ", "")

def log_event(ctx, kind: str, detail: str):
    """Личная история пользователя: поиски, контакты, заявки, отзывы"""
    ctx.user_data.setdefault("history", []).append({
        "t": datetime.now().strftime("%d.%m %H:%M"),
        "kind": kind,
        "detail": detail,
    })

async def notify_admin(bot, text: str):
    if not ADMIN_CHAT_ID:
        return
    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    except Exception as e:
        logger.warning(f"Admin notify failed: {e}")

# ─── KEYBOARDS ───────────────────────────────────────────────

def kb_start():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔍 Найти кружок", callback_data="action:search"),
    ], [
        InlineKeyboardButton("📋 Все центры", callback_data="action:all"),
        InlineKeyboardButton("❓ О нас", callback_data="action:about"),
    ]])

def kb_direction():
    buttons, row = [], []
    for key, (label, _) in DIRECTIONS.items():
        row.append(InlineKeyboardButton(label, callback_data=f"dir:{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🗺 Показать всё", callback_data="dir:any")])
    return InlineKeyboardMarkup(buttons)

def kb_age():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"age:{key}")]
         for key, (_, _, label) in AGE_GROUPS.items()]
    )

def kb_district():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"dist:{key}")]
         for key, label in DISTRICTS.items()]
    )

def kb_budget():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"budget:{key}")]
         for key, (_, _, label) in BUDGETS.items()]
    )

def kb_center_actions(center_id: int, idx: int, ids: list):
    """Stateless-клавиатура: список результатов зашит прямо в кнопки
    навигации, поэтому они работают даже после перезапуска сервера."""
    c = get_center(center_id)
    if not c:
        return InlineKeyboardMarkup([])
    ids_str = ",".join(str(i) for i in ids)
    total = len(ids)

    rows = [
        [InlineKeyboardButton("📝 Записаться на пробное", callback_data=f"trial:{center_id}")],
        [
            InlineKeyboardButton("💬 WhatsApp", callback_data=f"wa:{center_id}"),
            InlineKeyboardButton("🗺 На карте", url=maps_url(c)),
        ],
    ]
    if total > 1:
        nav = []
        if idx > 0:
            nav.append(InlineKeyboardButton("◀ Пред.", callback_data=f"nav:prev:{idx}:{ids_str}"))
        nav.append(InlineKeyboardButton(f"{idx+1}/{total}", callback_data="nav:noop:0:0"))
        if idx < total - 1:
            nav.append(InlineKeyboardButton("След. ▶", callback_data=f"nav:next:{idx}:{ids_str}"))
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("❤️ Сохранить", callback_data=f"save:{center_id}"),
        InlineKeyboardButton("📲 Поделиться", callback_data=f"share:{center_id}"),
    ])
    rows.append([
        InlineKeyboardButton("⭐ Оставить отзыв", callback_data=f"review:{center_id}"),
        InlineKeyboardButton("🔄 Новый поиск", callback_data="action:search"),
    ])
    rows.append([
        InlineKeyboardButton("👍 Подборка полезна", callback_data="fb:yes"),
        InlineKeyboardButton("👎 Не то", callback_data="fb:no"),
    ])
    return InlineKeyboardMarkup(rows)

def kb_rating(center_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⭐" * n, callback_data=f"rate:{center_id}:{n}")
        for n in range(1, 6)
    ]])

def kb_subscribe():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔔 Подписаться", callback_data="sub:yes"),
        InlineKeyboardButton("Позже", callback_data="sub:no"),
    ]])

# ─── SAFE SEND ───────────────────────────────────────────────

async def safe_send_card(message, c, idx, ids):
    kb = kb_center_actions(c["id"], idx, ids)
    try:
        await message.reply_text(format_center_card(c),
                                 parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    except Exception as e:
        logger.warning(f"Card send fallback for {c['name']}: {e}")
        await message.reply_text(format_center_card(c), reply_markup=kb)

async def safe_edit_card(query, c, idx, ids):
    kb = kb_center_actions(c["id"], idx, ids)
    try:
        await query.edit_message_text(format_center_card(c),
                                      parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    except Exception as e:
        logger.warning(f"Card edit fallback for {c['name']}: {e}")
        try:
            await query.edit_message_text(format_center_card(c), reply_markup=kb)
        except Exception:
            pass

# ─── COMMANDS ────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Чистим только черновик поиска — избранное и историю НЕ трогаем
    for k in ("direction", "direction_label", "age_min", "age_max", "age_label",
              "district", "district_label", "budget_key", "budget_label",
              "trial_center", "pending_review"):
        ctx.user_data.pop(k, None)
    user = update.effective_user
    logger.info(f"[START] {user.id} | @{user.username} | {user.full_name}")
    await update.message.reply_text(WELCOME_TEXT,
                                    parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=kb_start())

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 *Помощь по OZI*\n\n"
        "/start — начать поиск\n"
        "/favorites — сохранённые центры\n"
        "/history — моя история: поиски, контакты, заявки\n"
        "/help — эта справка",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_favorites(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    saved = ctx.user_data.get("favorites", [])
    if not saved:
        await update.message.reply_text(
            "💔 Пока пусто. Нажмите ❤️ на карточке центра, чтобы сохранить.\n\n"
            "Начать поиск: /start"
        )
        return
    await update.message.reply_text(f"❤️ Ваши сохранённые центры ({len(saved)}):")
    for cid in saved:
        c = get_center(cid)
        if c:
            await safe_send_card(update.message, c, 0, [cid])

async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hist = ctx.user_data.get("history", [])
    if not hist:
        await update.message.reply_text(
            "📭 История пуста. Она наполняется, когда вы ищете кружки, "
            "запрашиваете WhatsApp или подаёте заявки.\n\nНачать: /start"
        )
        return
    icons = {"поиск": "🔍", "контакт": "💬", "заявка": "📝", "отзыв": "⭐", "сохранено": "❤️"}
    lines = ["🗂 Ваша история (последние 15):", ""]
    for e in hist[-15:]:
        lines.append(f"{icons.get(e['kind'], '•')} {e['t']} — {e['kind']}: {e['detail']}")
    await update.message.reply_text("\n".join(lines))

async def cmd_leads(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_CHAT_ID):
        return
    leads = ctx.bot_data.get("leads", [])
    reviews = ctx.bot_data.get("reviews", [])
    lines = [f"📊 Лидов: {len(leads)} | Отзывов: {len(reviews)}", ""]
    for l in leads[-10:]:
        lines.append(f"📝 {l['time']} | {l['center']} | {l['parent']} | {l['parent_phone']}")
    for r in reviews[-5:]:
        lines.append(f"⭐ {r['time']} | {r['center']} | {r['rating']}/5 | {r.get('text', '—')}")
    await update.message.reply_text("\n".join(lines) if len(lines) > 2 else "Пока пусто.")

# ─── ПОИСК: 4 ШАГА ───────────────────────────────────────────

async def handle_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[1]

    if action == "search":
        await query.message.reply_text(
            "🎯 *Шаг 1 из 4* — Выберите направление:",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_direction()
        )
    elif action == "all":
        await query.message.reply_text(
            f"📋 Все центры в базе OZI ({len(CENTERS)}). Показываю первые 5, "
            f"для точного подбора — /start"
        )
        for cid in [c["id"] for c in CENTERS[:5]]:
            await safe_send_card(query.message, get_center(cid), 0, [cid])
    elif action == "about":
        await query.message.reply_text(
            "🎯 OZI — верифицированный навигатор кружков Астаны.\n\n"
            "Мы не рекламируем центры — мы проверяем их вручную: "
            "адрес, цены, расписание, возрастные группы. "
            "Дата проверки указана в каждой карточке.\n\n"
            "🔍 Начать поиск: /start",
        )

async def handle_direction(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    direction = query.data.split(":")[1]
    ctx.user_data["direction"] = direction
    label = DIRECTIONS[direction][0] if direction in DIRECTIONS else "🗺 Все направления"
    ctx.user_data["direction_label"] = label
    await query.edit_message_text(
        f"✅ Направление: *{label}*\n\n👦 *Шаг 2 из 4* — Возраст ребёнка:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_age()
    )

async def handle_age(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.split(":")[1]
    a_min, a_max, label = AGE_GROUPS[key]
    ctx.user_data.update(age_min=a_min, age_max=a_max, age_label=label)
    await query.edit_message_text(
        f"✅ Направление: *{ctx.user_data.get('direction_label', '—')}*\n"
        f"✅ Возраст: *{label}*\n\n📍 *Шаг 3 из 4* — Удобный район:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_district()
    )

async def handle_district(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.split(":")[1]
    ctx.user_data["district"] = key
    ctx.user_data["district_label"] = DISTRICTS.get(key, "Любой")
    await query.edit_message_text(
        f"✅ Направление: *{ctx.user_data.get('direction_label', '—')}*\n"
        f"✅ Возраст: *{ctx.user_data.get('age_label', '—')}*\n"
        f"✅ Район: *{ctx.user_data.get('district_label')}*\n\n"
        f"💰 *Шаг 4 из 4* — Бюджет в месяц:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_budget()
    )

async def handle_budget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.split(":")[1]
    ctx.user_data["budget_key"] = key
    ctx.user_data["budget_label"] = BUDGETS[key][2]

    # Если сервер перезапустился посреди поиска — мягкий рестарт
    if "direction" not in ctx.user_data:
        await query.edit_message_text("Начнём поиск заново: /start")
        return

    age_min = ctx.user_data.get("age_min", 0)
    age_max = ctx.user_data.get("age_max", 18)
    results = search_centers(
        direction=ctx.user_data.get("direction"),
        age=(age_min + age_max) // 2,
        district=ctx.user_data.get("district", "any"),
        budget_key=key,
    )
    if not results:
        await query.edit_message_text(NO_RESULTS_TEXT)
        return

    ids = [c["id"] for c in results]
    d_label = ctx.user_data.get("direction_label", "Любое")
    summary = (
        f"🎉 Нашёл *{len(results)}* вариантов!\n\n"
        f"📌 {d_label}\n"
        f"👦 {ctx.user_data.get('age_label', '—')}\n"
        f"🏙 {ctx.user_data.get('district_label', '—')}\n"
        f"💰 {ctx.user_data.get('budget_label', '—')}\n\n"
        f"Листайте кнопками ◀ ▶ под карточкой 👇"
    )
    try:
        await query.edit_message_text(summary, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await query.edit_message_text(summary)

    await safe_send_card(query.message, results[0], 0, ids)

    log_event(ctx, "поиск", f"{d_label}, {ctx.user_data.get('age_label')}, "
                            f"{ctx.user_data.get('district_label')} → {len(results)} рез.")
    u = query.from_user
    await notify_admin(query.get_bot(),
        f"🔔 Новый поиск в OZI\n"
        f"👤 {u.full_name} @{u.username or '—'} (id {u.id})\n"
        f"📌 {d_label} | {ctx.user_data.get('age_label')} | "
        f"{ctx.user_data.get('district_label')} | {ctx.user_data.get('budget_label')}\n"
        f"🔢 Результатов: {len(results)} | 🕐 {datetime.now().strftime('%d.%m %H:%M')}"
    )

# ─── КНОПКИ КАРТОЧКИ (все stateless) ─────────────────────────

async def handle_navigation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, action, idx_s, ids_s = query.data.split(":", 3)
    if action == "noop":
        return
    idx = int(idx_s)
    ids = [int(x) for x in ids_s.split(",") if x]
    new_idx = min(idx + 1, len(ids) - 1) if action == "next" else max(idx - 1, 0)
    c = get_center(ids[new_idx])
    if c:
        await safe_edit_card(query, c, new_idx, ids)

async def handle_wa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """WhatsApp через callback — чтобы контакт попадал в историю и аналитику"""
    query = update.callback_query
    c = get_center(int(query.data.split(":")[1]))
    if not c:
        await query.answer("Центр не найден")
        return
    await query.answer()
    log_event(ctx, "контакт", c["name"])
    await query.message.reply_text(
        f"💬 WhatsApp «{c['name']}»:\n"
        f"https://wa.me/{wa_number(c)}\n\n"
        f"📞 Телефон: {c['phone']}"
    )
    u = query.from_user
    await notify_admin(query.get_bot(),
        f"💬 Запрошен контакт: {c['name']}\n👤 {u.full_name} @{u.username or '—'}")

async def handle_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cid = int(query.data.split(":")[1])
    favorites = ctx.user_data.setdefault("favorites", [])
    c = get_center(cid)
    if cid not in favorites:
        favorites.append(cid)
        log_event(ctx, "сохранено", c["name"] if c else str(cid))
        await query.answer("❤️ Сохранено! Смотреть: /favorites", show_alert=False)
    else:
        await query.answer("Уже в избранном — /favorites", show_alert=False)

async def handle_share(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    c = get_center(int(query.data.split(":")[1]))
    if c:
        await query.message.reply_text(
            "📲 Скопируйте и отправьте:\n\n"
            f"🏫 {c['name']}\n"
            f"📌 {c['address']}\n"
            f"💰 {c['price_min']:,}–{c['price_max']:,} ₸/мес\n"
            f"📞 {c['phone']}\n\n"
            f"Найдено через OZI"
        )

# ─── ЗАЯВКА НА ПРОБНОЕ (лиды) ────────────────────────────────

async def handle_trial(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split(":")[1])
    c = get_center(cid)
    if not c:
        return
    ctx.user_data["trial_center"] = cid
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await query.message.reply_text(
        f"📝 Заявка на пробное занятие\n🏫 {c['name']}\n\n"
        f"Нажмите кнопку ниже, чтобы отправить свой номер, — "
        f"или просто напишите его в чат:",
        reply_markup=kb
    )

async def _register_trial(update: Update, ctx: ContextTypes.DEFAULT_TYPE, phone: str):
    cid = ctx.user_data.pop("trial_center", None)
    c = get_center(cid) if cid else None
    if not c:
        await update.message.reply_text(
            "Не вижу активной заявки. Откройте карточку центра и нажмите "
            "«📝 Записаться на пробное».",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    user = update.effective_user
    lead = {
        "center": c["name"], "center_phone": c["phone"],
        "parent": user.full_name, "username": user.username,
        "parent_phone": phone,
        "time": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }
    ctx.bot_data.setdefault("leads", []).append(lead)
    log_event(ctx, "заявка", c["name"])
    logger.info(f"[LEAD] {lead}")

    await update.message.reply_text(
        f"✅ Заявка принята!\n\n🏫 {c['name']}\n📞 Ваш номер: {phone}\n\n"
        f"Мы передадим заявку центру — с вами свяжутся для записи.\n\n"
        f"🔍 Новый поиск: /start   🗂 История: /history",
        reply_markup=ReplyKeyboardRemove()
    )
    await notify_admin(update.get_bot(),
        f"🔥 ЛИД — заявка на пробное!\n"
        f"🏫 {c['name']} ({c['phone']})\n"
        f"👤 {user.full_name} @{user.username or '—'}\n"
        f"📞 {phone}\n📊 Всего лидов: {len(ctx.bot_data['leads'])}"
    )

async def handle_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _register_trial(update, ctx, update.message.contact.phone_number)

# ─── ОТЗЫВЫ ──────────────────────────────────────────────────

async def handle_review(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split(":")[1])
    c = get_center(cid)
    if not c:
        return
    await query.message.reply_text(
        f"⭐ Оцените «{c['name']}»:",
        reply_markup=kb_rating(cid)
    )

async def handle_rate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, cid_s, rating_s = query.data.split(":")
    cid, rating = int(cid_s), int(rating_s)
    c = get_center(cid)
    if not c:
        return
    ctx.user_data["pending_review"] = {"cid": cid, "rating": rating}
    await query.edit_message_text(
        f"⭐ Ваша оценка «{c['name']}»: {'⭐' * rating} ({rating}/5)\n\n"
        f"Напишите пару слов о центре — или отправьте /skip, "
        f"чтобы оставить только оценку."
    )

async def _finalize_review(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str):
    pending = ctx.user_data.pop("pending_review", None)
    if not pending:
        return False
    c = get_center(pending["cid"])
    user = update.effective_user
    review = {
        "center": c["name"] if c else str(pending["cid"]),
        "rating": pending["rating"],
        "text": text or "",
        "parent": user.full_name, "username": user.username,
        "time": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }
    ctx.bot_data.setdefault("reviews", []).append(review)
    log_event(ctx, "отзыв", f"{review['center']} — {pending['rating']}/5")
    await update.message.reply_text(
        "🙏 Спасибо! Ваш отзыв поможет другим родителям.\n\n"
        "🔍 Новый поиск: /start"
    )
    await notify_admin(update.get_bot(),
        f"⭐ НОВЫЙ ОТЗЫВ\n🏫 {review['center']} — {review['rating']}/5\n"
        f"💬 {text or '(без текста)'}\n"
        f"👤 {user.full_name} @{user.username or '—'}"
    )
    return True

async def cmd_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _finalize_review(update, ctx, ""):
        await update.message.reply_text("Нечего пропускать 🙂 Поиск: /start")

# ─── ФИДБЕК И ПОДПИСКА ───────────────────────────────────────

async def handle_feedback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    fb = query.data.split(":")[1]
    if fb == "yes":
        await query.answer("Спасибо! 🎉")
        msg = "🎉 Рады, что подборка помогла!"
    else:
        await query.answer("Спасибо за честность")
        msg = "😔 Жаль. Напишите в чат, чего не хватило, — мы улучшим поиск."
    await query.message.reply_text(
        msg + "\n\n🔔 Хотите получать обновления о новых центрах и ценах?",
        reply_markup=kb_subscribe()
    )
    u = query.from_user
    await notify_admin(query.get_bot(),
        f"📊 Фидбек: {'👍' if fb == 'yes' else '👎'} | {u.full_name} @{u.username or '—'}")

async def handle_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.split(":")[1] == "yes":
        ctx.bot_data.setdefault("subscribers", set()).add(query.from_user.id)
        text = "🔔 Подписка оформлена! Сообщим о новых центрах, ценах и акциях.\n\n"
    else:
        text = "Хорошо, без подписки 👌\n\n"
    await query.edit_message_text(
        text + "Спасибо, что пользуетесь OZI! 🙏\n\n"
        "🔍 Поиск: /start   ❤️ Избранное: /favorites   🗂 История: /history"
    )

# ─── ТЕКСТОВЫЕ СООБЩЕНИЯ ─────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    # 1) Ждём телефон для заявки?
    digits = "".join(ch for ch in text if ch.isdigit())
    if ctx.user_data.get("trial_center") and len(digits) >= 10:
        await _register_trial(update, ctx, text)
        return
    # 2) Ждём текст отзыва?
    if ctx.user_data.get("pending_review"):
        await _finalize_review(update, ctx, text[:500])
        return
    # 3) Иначе — подсказка
    await update.message.reply_text(
        "🤷 Не понял. Используйте кнопки или /start",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔍 Найти кружок", callback_data="action:search")
        ]])
    )

# ─── MAIN ─────────────────────────────────────────────────────

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️  Установите BOT_TOKEN (см. ЗАПУСК.md)")
        return

    start_keepalive_server()
    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("favorites", cmd_favorites))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("leads", cmd_leads))
    app.add_handler(CommandHandler("skip", cmd_skip))

    # Все кнопки — глобально и stateless: работают всегда
    app.add_handler(CallbackQueryHandler(handle_action,     pattern="^action:"))
    app.add_handler(CallbackQueryHandler(handle_direction,  pattern="^dir:"))
    app.add_handler(CallbackQueryHandler(handle_age,        pattern="^age:"))
    app.add_handler(CallbackQueryHandler(handle_district,   pattern="^dist:"))
    app.add_handler(CallbackQueryHandler(handle_budget,     pattern="^budget:"))
    app.add_handler(CallbackQueryHandler(handle_navigation, pattern="^nav:"))
    app.add_handler(CallbackQueryHandler(handle_wa,         pattern="^wa:"))
    app.add_handler(CallbackQueryHandler(handle_save,       pattern="^save:"))
    app.add_handler(CallbackQueryHandler(handle_share,      pattern="^share:"))
    app.add_handler(CallbackQueryHandler(handle_trial,      pattern="^trial:"))
    app.add_handler(CallbackQueryHandler(handle_review,     pattern="^review:"))
    app.add_handler(CallbackQueryHandler(handle_rate,       pattern="^rate:"))
    app.add_handler(CallbackQueryHandler(handle_feedback,   pattern="^fb:"))
    app.add_handler(CallbackQueryHandler(handle_subscribe,  pattern="^sub:"))

    # Контакты и текст
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🚀 OZI Bot v2.0 запущен!")
    print(f"📊 Центров в базе: {len(CENTERS)}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
