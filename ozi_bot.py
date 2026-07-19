#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ══════════════════════════════════════════════════════════════
#  OZI BOT — навигатор кружков Астаны (родители)
#  Продукт 1 из трёх. Концепция 2.2. Спринт 1 «Тройка».
#  Архитектура: STATELESS — вся навигация в callback-строках.
#  Память: PicklePersistence (профиль, избранное переживают рестарт).
#  Запуск: python ozi_bot.py (переменные — в .env / Render env)
#
#  ОБНОВЛЕНИЕ (19.07.2026):
#   - фикс: лейбл района в уведомлении Bridge («Алматинский», а не «almaty»)
#   - фикс: видимая реакция на кнопку «В избранное» (toggle + текст на кнопке)
# ══════════════════════════════════════════════════════════════

import os
import _loadenv  # подгружает .env локально
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, PicklePersistence,
)

from centers_data import (
    CATEGORIES, DISTRICTS, AGE_GROUPS, BUDGETS,
    search_centers, text_search, get_center, format_card,
)
import storage

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
)
log = logging.getLogger("ozi_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_TOKEN_IN_ENV")
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")  # чтобы слать уведомления команде
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

KZ = timezone(timedelta(hours=5))

# ─────────────────────────── ТЕКСТЫ (голос соцстартапа) ───────────────────────────

WELCOME = (
    "👋 Привет! Я *OZI* — навигатор детских кружков Астаны.\n\n"
    "Мы — социальный стартап двух людей, уставших от хаоса в поиске кружков. "
    "Бесплатно собираем проверенную карту детского образования города — "
    "и она растёт каждый день.\n\n"
    "✅ Реальные центры с координатами\n"
    "🔎 Поиск за 2 минуты\n"
    "🎟 Запись на пробное занятие\n\n"
    "Как удобнее искать?"
)

ABOUT = (
    "🎯 *OZI — навигатор кружков Астаны*\n\n"
    "Мы не реклама и не случайный каталог. Мы социальный проект, который строит "
    "*карту доверия* детского образования Астаны.\n\n"
    "Сейчас в базе — сотни реальных центров с адресами и координатами. "
    "Центры со статусом «✅ Данные подтверждены» проверены командой OZI; "
    "остальные — кандидаты, данные на верификации.\n\n"
    "Полезно? Перешлите бота другим родителям или подскажите центр, "
    "которого не хватает — так карта станет полнее для всех 🌱"
)

CONSENT = (
    "🤝 Чтобы центр вам перезвонил, нужно передать ему ваш контакт.\n\n"
    "Нажимая «Согласен», вы разрешаете OZI передать этому центру ваш телефон "
    "и возраст ребёнка — только для записи на пробное. Мы не публикуем ваши данные "
    "и не передаём их третьим сторонам."
)

# ─────────────────────────── КНОПКИ ───────────────────────────

def kb_start():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Подобрать кружок", callback_data="go:cat")],
        [InlineKeyboardButton("⌨️ Искать по названию", callback_data="go:text")],
        [InlineKeyboardButton("❤️ Избранное", callback_data="go:fav"),
         InlineKeyboardButton("❓ О нас", callback_data="go:about")],
    ])


def kb_categories():
    rows, row = [], []
    for key, label in CATEGORIES.items():
        row.append(InlineKeyboardButton(label, callback_data=f"cat:{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🗺 Показать все направления", callback_data="cat:any")])
    return InlineKeyboardMarkup(rows)


def kb_age(cat):
    rows = [[InlineKeyboardButton(lbl, callback_data=f"age:{cat}:{k}")]
            for k, (_, _, lbl) in AGE_GROUPS.items()]
    return InlineKeyboardMarkup(rows)


def kb_dist(cat, age):
    rows = [[InlineKeyboardButton(lbl, callback_data=f"dist:{cat}:{age}:{k}")]
            for k, lbl in DISTRICTS.items()]
    return InlineKeyboardMarkup(rows)


def kb_budget(cat, age, dist):
    rows = [[InlineKeyboardButton(lbl, callback_data=f"res:{cat}:{age}:{dist}:{k}")]
            for k, (_, _, lbl) in BUDGETS.items()]
    return InlineKeyboardMarkup(rows)


def kb_card(c, idx, ids_key, total, is_fav=False):
    cid = c["id"]
    row_contact = []
    if c.get("phone"):
        digits = "".join(ch for ch in c["phone"] if ch.isdigit())
        if c.get("whatsapp"):
            row_contact.append(InlineKeyboardButton("💬 WhatsApp", url=f"https://wa.me/{digits}"))

    row_nav = []
    if idx > 0:
        row_nav.append(InlineKeyboardButton("◀", callback_data=f"nav:{ids_key}:{idx-1}"))
    row_nav.append(InlineKeyboardButton(f"{idx+1}/{total}", callback_data="noop"))
    if idx < total - 1:
        row_nav.append(InlineKeyboardButton("▶", callback_data=f"nav:{ids_key}:{idx+1}"))

    row_act = [
        InlineKeyboardButton("🎟 Записаться на пробное", callback_data=f"trial:{cid}"),
    ]

    # ФИКС: видимая реакция — текст кнопки меняется, если центр уже в избранном
    fav_label = "✅ В избранном" if is_fav else "❤️ В избранное"
    row_extra = [
        InlineKeyboardButton(fav_label, callback_data=f"fav:{cid}"),
        InlineKeyboardButton("🔄 Новый поиск", callback_data="go:cat"),
    ]

    rows = [r for r in (row_contact, row_nav, row_act, row_extra) if r]
    return InlineKeyboardMarkup(rows)

# ─────────────────────────── ХЕЛПЕРЫ ───────────────────────────

def _log_event(ctx, uid, etype, details=""):
    storage.log_event(datetime.now(KZ).isoformat(), etype, uid, details)


async def _notify_bridge(text):
    """Уведомление команды через бота Bridge (если задан токен)."""
    if not BRIDGE_TOKEN or not ADMIN_IDS:
        return
    from telegram import Bot
    b = Bot(BRIDGE_TOKEN)
    for aid in ADMIN_IDS:
        try:
            await b.send_message(aid, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            log.warning(f"bridge notify fail {aid}: {e}")


async def _show_center(update, ctx, ids, idx):
    """Показать карточку по позиции в сохранённом списке результатов."""
    if not ids:
        return
    idx = max(0, min(idx, len(ids) - 1))
    c = get_center(ids[idx])
    if not c:
        return
    ids_key = ctx.user_data.get("ids_key", "r")
    fav = ctx.user_data.get("favorites", [])
    q = update.callback_query
    await q.edit_message_text(
        format_card(c, idx, len(ids)),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_card(c, idx, ids_key, len(ids), is_fav=(c["id"] in fav)),
    )

# ─────────────────────────── КОМАНДЫ ───────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    _log_event(ctx, u.id, "start", u.username or "")
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN,
                                     reply_markup=kb_start())


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 *OZI*\n/start — начать\n/favorites — избранное\n\n"
        "Можно просто написать название или направление — я поищу.",
        parse_mode=ParseMode.MARKDOWN)


async def cmd_favorites(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_favorites(update.message, ctx)


async def _send_favorites(msg, ctx):
    fav = ctx.user_data.get("favorites", [])
    if not fav:
        await msg.reply_text("💔 Пока пусто. Нажмите ❤️ на карточке центра.\n\nПоиск: /start")
        return
    await msg.reply_text(f"❤️ *Избранное ({len(fav)}):*", parse_mode=ParseMode.MARKDOWN)
    for cid in fav:
        c = get_center(cid)
        if c:
            await msg.reply_text(format_card(c), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_card(c, 0, "fav", 1, is_fav=True))

# ─────────────────────────── CALLBACK-роутер ───────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    u = q.from_user

    if data == "noop":
        return

    # навигация меню
    if data == "go:cat":
        await q.edit_message_text("🎯 *Шаг 1* — выберите направление:",
                                   parse_mode=ParseMode.MARKDOWN, reply_markup=kb_categories())
        return

    if data == "go:text":
        await q.edit_message_text(
            "⌨️ Напишите название центра или направление "
            "(например: «англ», «шахмат», «плавание»):")
        ctx.user_data["awaiting_text"] = True
        return

    if data == "go:about":
        await q.edit_message_text(ABOUT, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                                       "🔎 Подобрать кружок", callback_data="go:cat")]]))
        return

    if data == "go:fav":
        await _send_favorites(q.message, ctx)
        return

    # шаги подбора
    if data.startswith("cat:"):
        cat = data.split(":")[1]
        await q.edit_message_text("👦 *Шаг 2* — возраст ребёнка:",
                                   parse_mode=ParseMode.MARKDOWN, reply_markup=kb_age(cat))
        return

    if data.startswith("age:"):
        _, cat, age = data.split(":")
        await q.edit_message_text("📍 *Шаг 3* — район:",
                                   parse_mode=ParseMode.MARKDOWN, reply_markup=kb_dist(cat, age))
        return

    if data.startswith("dist:"):
        _, cat, age, dist = data.split(":")
        await q.edit_message_text("💰 *Шаг 4* — бюджет в месяц:",
                                   parse_mode=ParseMode.MARKDOWN, reply_markup=kb_budget(cat, age, dist))
        return

    if data.startswith("res:"):
        _, cat, age, dist, budget = data.split(":")
        amin, amax, _lbl = AGE_GROUPS.get(age, (0, 99, ""))
        amid = (amin + amax) // 2 if age != "any" else None
        results = search_centers(cat, amid, dist, budget)

        # мини-профиль: район + интерес (категория) запоминаем
        _save_profile(ctx, u, dist, cat)
        _log_event(ctx, u.id, "search", f"{cat}/{age}/{dist}/{budget}={len(results)}")

        if not results:
            await q.edit_message_text(
                "😔 По этому набору ничего не нашлось.\n\n"
                "Попробуйте «Любой район» или другое направление.\n/start",
            )
            return

        ids = [c["id"] for c in results]
        ctx.user_data["results"] = ids
        ctx.user_data["ids_key"] = "r"
        await q.edit_message_text(
            f"🎉 Нашёл *{len(results)}* вариантов! Показываю по одному 👇",
            parse_mode=ParseMode.MARKDOWN)
        c = results[0]
        fav = ctx.user_data.get("favorites", [])
        await q.message.reply_text(format_card(c, 0, len(ids)),
                                    parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=kb_card(c, 0, "r", len(ids), is_fav=(c["id"] in fav)))
        return

    # навигация по результатам
    if data.startswith("nav:"):
        _, ids_key, idx = data.split(":")
        ids = ctx.user_data.get("results", []) if ids_key == "r" \
            else ctx.user_data.get("favorites", [])
        await _show_center(update, ctx, ids, int(idx))
        return

    # избранное — ФИКС: toggle + видимое обновление кнопки на карточке
    if data.startswith("fav:"):
        cid = int(data.split(":")[1])
        fav = ctx.user_data.setdefault("favorites", [])
        if cid not in fav:
            fav.append(cid)
            await q.answer("❤️ Добавлено в избранное!")
        else:
            fav.remove(cid)
            await q.answer("Убрано из избранного")

        c = get_center(cid)
        if c:
            # перерисовываем клавиатуру карточки, чтобы кнопка сразу показала новое состояние
            try:
                idx = 0
                ids = ctx.user_data.get("results", [])
                total = len(ids) if ids else 1
                if ids and cid in ids:
                    idx = ids.index(cid)
                await q.edit_message_reply_markup(
                    reply_markup=kb_card(c, idx, ctx.user_data.get("ids_key", "r"),
                                          total, is_fav=(cid in fav))
                )
            except Exception as e:
                log.warning(f"fav redraw fail: {e}")
        return

    # запись на пробное — согласие
    if data.startswith("trial:"):
        cid = int(data.split(":")[1])
        ctx.user_data["pending_trial"] = cid
        await q.edit_message_text(CONSENT, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Согласен, записаться", callback_data=f"consent:{cid}")],
            [InlineKeyboardButton("← Назад", callback_data="go:cat")],
        ]))
        return

    if data.startswith("consent:"):
        cid = int(data.split(":")[1])
        await q.edit_message_text(
            "📞 Оставьте телефон, по которому центру с вами связаться "
            "(напишите сообщением, например 87001234567):")
        ctx.user_data["awaiting_phone_for"] = cid
        return

# ─────────────────────────── ТЕКСТ ───────────────────────────

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    u = update.effective_user

    # ждём телефон для лида
    cid = ctx.user_data.get("awaiting_phone_for")
    if cid:
        ctx.user_data.pop("awaiting_phone_for", None)
        c = get_center(cid)
        prof = ctx.user_data.get("profile", {})
        lead_id = storage.create_lead(
            date=datetime.now(KZ).isoformat(),
            center_id=cid, center_name=(c["name"] if c else ""),
            direction=(c["cat_label"] if c else ""),
            child_age=prof.get("interest_age", ""),
            district=prof.get("dist", ""),
            contact=txt, consent="да",
        )
        _log_event(ctx, u.id, "lead", f"{cid}:{lead_id}")
        await update.message.reply_text(
            "✅ Готово! Заявка передана центру — он свяжется с вами в течение дня.\n\n"
            "Через несколько дней я спрошу, как всё прошло 🙂\n\n/start — новый поиск")

        # уведомить команду в Bridge
        # ФИКС: переводим код района в читаемый лейбл через DISTRICTS,
        # раньше сюда попадал сырой ключ (например "almaty" вместо "Алматинский")
        district_raw = prof.get("dist", "")
        district_label = DISTRICTS.get(district_raw, district_raw)
        await _notify_bridge(
            f"🔔 *Новый лид #{lead_id}*\n🏫 {c['name'] if c else cid}\n"
            f"📞 {txt}\n📍 {district_label}\n👤 @{u.username or u.id}")
        return

    # свободный поиск по буквам
    if len(txt) >= 2:
        ctx.user_data.pop("awaiting_text", None)
        res = text_search(txt)
        _log_event(ctx, u.id, "text_search", f"{txt}={len(res)}")
        if not res:
            await update.message.reply_text(
                "😔 Ничего не нашёл по этому слову.\n"
                "Попробуйте иначе или подберите через /start.")
            return
        ids = [c["id"] for c in res]
        ctx.user_data["results"] = ids
        ctx.user_data["ids_key"] = "r"
        c = res[0]
        fav = ctx.user_data.get("favorites", [])
        await update.message.reply_text(
            f"🔎 Нашёл *{len(res)}* по запросу «{txt}»:",
            parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(format_card(c, 0, len(ids)),
                                         parse_mode=ParseMode.MARKDOWN,
                                         reply_markup=kb_card(c, 0, "r", len(ids), is_fav=(c["id"] in fav)))
        return

    await update.message.reply_text("Не понял 🤔 Нажмите /start или напишите направление.")

# ─────────────────────────── ПРОФИЛЬ ───────────────────────────

def _save_profile(ctx, user, dist, cat):
    prof = ctx.user_data.setdefault("profile", {})
    prof["dist"] = dist
    ints = set(prof.get("interests", []))
    if cat and cat != "any":
        ints.add(cat)
    prof["interests"] = list(ints)
    prof.setdefault("first_seen", datetime.now(KZ).isoformat())

    # обезличенно пишем в базу профилей (год рождения — только если сам укажет; здесь нет)
    storage.upsert_parent(
        tg_id=user.id, username=user.username or "",
        dist=dist, interests=",".join(prof["interests"]),
        first_seen=prof["first_seen"])

# ─────────────────────────── KEEPALIVE (для бесплатного Render web-сервиса) ──────

def _start_keepalive():
    """Крошечный HTTP-сервер: Render видит открытый порт → считает сервис web.
    UptimeRobot пингует этот адрес и не даёт бесплатному сервису уснуть."""
    port = int(os.environ.get("PORT", "10000"))

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"OZI Astana is alive")

        def do_HEAD(self):
            self.send_response(200); self.end_headers()

        def log_message(self, *a):
            pass

    try:
        HTTPServer(("0.0.0.0", port), H).serve_forever()
    except Exception as e:
        log.warning(f"keepalive server failed: {e}")

# ─────────────────────────── MAIN ───────────────────────────

def main():
    if BOT_TOKEN == "PUT_TOKEN_IN_ENV":
        print("⚠️ Установите BOT_TOKEN в переменных окружения (.env / Render).")
        return

    persistence = PicklePersistence(filepath="ozi_bot_data.pkl")
    app = Application.builder().token(BOT_TOKEN).persistence(persistence).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("favorites", cmd_favorites))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    threading.Thread(target=_start_keepalive, daemon=True).start()

    print(f"🚀 OZI Bot запущен. Центров в базе: {len(search_centers())}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
