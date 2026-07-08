# -*- coding: utf-8 -*-
"""
OZI — слой хранения (лиды, профили родителей, события).
СЕЙЧАС: локальные JSONL-файлы рядом с ботом (работает на Render без Google API).
ПОЗЖЕ: этот модуль заменяется на запись в Google Sheets — интерфейс тот же.
Регуляторная граница: по ребёнку — только год рождения/интересы, без имени и точной даты.
"""
import json, os, threading

_LOCK = threading.Lock()
LEADS   = "ozi_leads.jsonl"
PARENTS = "ozi_parents.jsonl"
EVENTS  = "ozi_events.jsonl"

def _append(path, obj):
    with _LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def _count(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for _ in f)

def create_lead(date, center_id, center_name, direction, child_age,
                district, contact, consent):
    lead_id = _count(LEADS) + 1
    _append(LEADS, {
        "id_lead": lead_id, "date": date, "center_id": center_id,
        "center_name": center_name, "direction": direction,
        "child_age": child_age, "district": district,
        "contact": contact, "consent": consent,
        "status": "новый", "review_7d": "",
    })
    return lead_id

def upsert_parent(tg_id, username, dist, interests, first_seen):
    # простая дозапись; дедуп по tg_id — на этапе переезда в Sheets/БД
    _append(PARENTS, {
        "tg_id": tg_id, "username": username, "district": dist,
        "interests": interests, "first_seen": first_seen,
        # НЕ собираем: имя, точную дату рождения, фото, медданные
    })

def log_event(date, etype, tg_id, details):
    _append(EVENTS, {"date": date, "type": etype, "tg_id": tg_id, "details": details})

def stats():
    return {"leads": _count(LEADS), "parents": _count(PARENTS), "events": _count(EVENTS)}
