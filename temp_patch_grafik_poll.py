import os
from pathlib import Path

path = Path("bm_automation/app/notifications/ops/grafik_poll.py")
cnt = path.read_text(encoding="utf-8")

# 1. In build_poll_data: preserve all_confirmed_sent and all_confirmed_at
old_poll_dict = """        "created_at": existing.get("created_at") or datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "items": items,"""
new_poll_dict = """        "created_at": existing.get("created_at") or datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "all_confirmed_sent": existing.get("all_confirmed_sent", False),
        "all_confirmed_at": existing.get("all_confirmed_at", ""),
        "items": items,"""
assert old_poll_dict in cnt, "old_poll_dict not found"
cnt = cnt.replace(old_poll_dict, new_poll_dict, 1)

# 2. In handle_group_callback: add group permission check
old_hgc = """    poll = get_poll(pid)
    if not poll:
        _answer_cq(cq_id, "⚠️ So'rovnoma ma'lumoti topilmadi yoki eskirgan.", alert=True)
        return

    items = poll.get("items", [])"""

new_hgc = """    poll = get_poll(pid)
    if not poll:
        _answer_cq(cq_id, "⚠️ So'rovnoma ma'lumoti topilmadi yoki eskirgan.", alert=True)
        return

    # Guruh va yo'nalish nazorati (Cross-group / cross-route protection)
    chat_id_str = str(chat_id).strip()
    poll_rid = str(poll.get("route_id") or "").strip()
    if chat_id_str.startswith("-"):
        allowed_chats = {str(m.get("chat_id")).strip() for m in poll.get("chat_messages", []) if m.get("chat_id")}
        from .group_departures import _route_chats
        route_chats = _route_chats()
        for c in route_chats.get(poll_rid) or []:
            allowed_chats.add(str(c).strip())

        if allowed_chats and chat_id_str not in allowed_chats:
            _answer_cq(
                cq_id,
                f"⛔️ Ushbu grafik so'rovnomasi ({poll.get('route_name')}) bu guruhga tegishli emas!",
                alert=True,
            )
            return

    items = poll.get("items", [])"""

assert old_hgc in cnt, "old_hgc not found"
cnt = cnt.replace(old_hgc, new_hgc, 1)

# 3. Add helper functions before _confirm_item
helper_code = '''def routes_for_chat(chat_id: int | str) -> list[str]:
    """Berilgan chat (ayniqsa Telegram guruh) ga biriktirilgan routeVariantId'lar."""
    cid_str = str(chat_id).strip()
    if not cid_str:
        return []
    from .group_departures import _route_chats
    route_chats = _route_chats()
    matching = []
    for rid, chats in route_chats.items():
        if cid_str in [str(c).strip() for c in chats]:
            matching.append(rid)
    return matching


def get_or_create_sheet_image(
    route_id: str,
    date_str: str,
    poll: dict | None = None,
) -> str | None:
    """Ushbu yo'nalish va sana uchun jadval rasmini (PNG) topadi yoki generatsiya qiladi."""
    clean_rid = str(route_id or "").replace("-", "")[:8]
    clean_date = str(date_str or "").replace("-", "")[:8]
    expected_file = f"driver-sheet_{clean_rid}_{clean_date}.png"

    # 1. reports/ papkasidagi mavjud fayllarni tekshirish
    from ...core.profiles import all_profiles
    profiles = all_profiles()
    prof = next((p for p in profiles if str(p.get("routeVariantId") or "").strip() == route_id), {})

    candidate_paths = [
        Path("reports") / (prof.get("name") or "") / expected_file,
        Path("reports") / expected_file,
    ]
    for p in profiles:
        pname = p.get("name")
        if pname:
            candidate_paths.append(Path("reports") / pname / expected_file)

    for cand in candidate_paths:
        try:
            if cand.exists() and cand.stat().st_size > 0:
                return str(cand.resolve())
        except Exception:
            pass

    # 2. driver_sheet_service orqali yuklashga urinish
    if prof and not os.getenv("BM_TEST_MODE"):
        try:
            from ...core.companies import client_for_profile
            from ...services.driver_sheet_service import run as run_sheet
            client = client_for_profile(prof, auto_relogin=False)
            if client:
                out_dir = Path("reports") / (prof.get("name") or "default")
                res = run_sheet(
                    client,
                    route_id,
                    date_str=date_str,
                    out_dir=str(out_dir),
                    send=False,
                    profile=prof,
                )
                img = res.get("image")
                if img and Path(img).exists() and Path(img).stat().st_size > 0:
                    return str(Path(img).resolve())
        except Exception as exc:
            log.info("driver_sheet_service rasm yaratish xatosi (fallback ishlatiladi): %s", exc)

    # 3. Fallback: make_sheet_image orqali so'rovnoma ma'lumotlaridan chizish
    try:
        from ...exporters.sheet_image import make_sheet_image
        route_name = (poll.get("route_name") if poll else "") or prof.get("routeName") or "Yo'nalish"
        up_konechka = prof.get("start1") or "1-chiqish"
        down_konechka = prof.get("start2") or "2-chiqish"

        items = (poll.get("items") if poll else []) or []
        groups = {"UP": [], "DOWN": []}
        mid = (len(items) + 1) // 2
        for idx, it in enumerate(items):
            row = {
                "graph": str(it.get("graph") or (idx + 1)),
                "driver": str(it.get("driver_name") or "Haydovchi"),
                "bus": str(it.get("plate") or "-"),
                "start": str(it.get("start_time") or "06:00"),
                "end": str(it.get("end_time") or "19:00"),
            }
            if idx < mid:
                groups["UP"].append(row)
            else:
                groups["DOWN"].append(row)

        sheet_data = {
            "date": date_str,
            "routeName": route_name,
            "konechka": {"UP": up_konechka, "DOWN": down_konechka},
            "groups": groups,
            "drivers": [],
        }
        prof_name = prof.get("name") or "reports"
        out_dir = Path("reports") / prof_name
        out_dir.mkdir(parents=True, exist_ok=True)
        target_img = out_dir / expected_file
        make_sheet_image(sheet_data, str(target_img))
        if target_img.exists() and target_img.stat().st_size > 0:
            return str(target_img.resolve())
    except Exception as exc:
        log.warning("Fallback make_sheet_image xatosi: %s", exc)

    return None


def _check_and_notify_all_confirmed(poll: dict) -> bool:
    """Barcha haydovchilar grafikni tasdiqlaganda rasm va yakuniy xabarni guruhlarga yuboradi."""
    items = poll.get("items", [])
    total = len(items)
    if total == 0:
        return False

    confirmed_count = sum(1 for it in items if it.get("confirmed"))
    if confirmed_count < total:
        return False

    if poll.get("all_confirmed_sent"):
        return False

    poll["all_confirmed_sent"] = True
    poll["all_confirmed_at"] = datetime.now().isoformat()
    save_poll(poll)

    route_name = poll.get("route_name") or "Yo'nalish"
    d_str = poll.get("date") or ""
    weekday = poll.get("weekday") or ""
    try:
        d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
        date_formatted = d_obj.strftime("%d.%m.%Y")
    except Exception:
        date_formatted = d_str

    caption = (
        f"🎉 <b>BARCHA SHAFYORLAR XABAR TOPDI!</b>\\n\\n"
        f"🚌 <b>Yo'nalish:</b> {escape(route_name)}\\n"
        f"📅 <b>Sana:</b> {date_formatted} ({escape(weekday)})\\n"
        f"✅ <b>Kunlik grafik to'liq tasdiqlandi!</b> Barcha ({total}/{total}) haydovchilar ertaga ishga chiqishini tasdiqladi.\\n\\n"
        f"📋 <b>Tasdiqlangan yakuniy chiqish jadvali:</b>"
    )

    rid = poll.get("route_id", "")
    img_path = get_or_create_sheet_image(rid, d_str, poll=poll)

    targets = set()
    for m in poll.get("chat_messages", []):
        cid = m.get("chat_id")
        if cid:
            targets.add(str(cid).strip())

    from .group_departures import _route_chats
    route_chats = _route_chats()
    for cid in route_chats.get(rid) or []:
        if cid:
            targets.add(str(cid).strip())

    sent_any = False
    from ..telegram import send_message, send_photo
    for cid in targets:
        if not cid:
            continue
        try:
            if img_path and Path(img_path).exists() and Path(img_path).stat().st_size > 0:
                send_photo(str(img_path), caption=caption, chat_id=cid)
            else:
                send_message(caption, chat_id=cid)
            sent_any = True
            log.info("Barcha tasdiqlagandan keyingi rasm/xabar yuborildi: chat_id=%s route=%s", cid, route_name)
        except Exception as exc:
            log.warning("Barcha tasdiqlangan rasmni yuborishda xatolik (%s): %s", cid, exc)
            try:
                send_message(caption, chat_id=cid)
                sent_any = True
            except Exception:
                pass

    try:
        st = get_storage()
        if st and st.enabled:
            st.record_notification(
                channel="poll_all_confirmed",
                target=route_name,
                message=f"{route_name} ({d_str}) barcha {total} ta grafik tasdiqlandi, rasm va xulosa guruhga yuborildi",
                status="SUCCESS" if sent_any else "FAILED",
            )
    except Exception:
        pass

    return sent_any

'''

old_confirm_item_start = """def _confirm_item(
    poll: dict,"""
assert old_confirm_item_start in cnt, "old_confirm_item_start not found"
cnt = cnt.replace(old_confirm_item_start, helper_code + "\n" + old_confirm_item_start, 1)

# 4. In _confirm_item: call _check_and_notify_all_confirmed(poll)
old_ci_end = """    # 4. Guruhdagi xabarni yangilash
    update_poll_messages(poll)"""
new_ci_end = """    # 4. Guruhdagi xabarni yangilash
    update_poll_messages(poll)

    # 5. Barcha haydovchilar tasdiqlaganda rasm va yakuniy xabarni guruhlarga yuborish
    _check_and_notify_all_confirmed(poll)"""
assert old_ci_end in cnt, "old_ci_end not found"
cnt = cnt.replace(old_ci_end, new_ci_end, 1)

# 5. In reset_confirmation: clear all_confirmed_sent
old_rc = """    if found:
        save_poll(poll)
        update_poll_messages(poll)"""
new_rc = """    if found:
        poll["all_confirmed_sent"] = False
        save_poll(poll)
        update_poll_messages(poll)"""
assert old_rc in cnt, "old_rc not found"
cnt = cnt.replace(old_rc, new_rc, 1)

# 6. In confirm_by_admin: clean admin_name, clear all_confirmed_sent, check_and_notify
old_cba_start = """    found_item["confirmed_by_role"] = "DISPATCHER"
    poll["updated_at"] = datetime.now().isoformat()
    save_poll(poll)
    update_poll_messages(poll)"""

new_cba_start = """    found_item["confirmed_by_role"] = "DISPATCHER"
    poll["updated_at"] = datetime.now().isoformat()
    save_poll(poll)
    update_poll_messages(poll)
    _check_and_notify_all_confirmed(poll)"""
assert old_cba_start in cnt, "old_cba_start not found"
cnt = cnt.replace(old_cba_start, new_cba_start, 1)

# Also ensure admin_name is a string in confirm_by_admin
old_cba_fn = """def confirm_by_admin(
    route_id_or_poll: str,
    target_date: str,
    graph_name: str,
    admin_name: str = "Dispetcher",
) -> bool:"""
new_cba_fn = """def confirm_by_admin(
    route_id_or_poll: str,
    target_date: str,
    graph_name: str,
    admin_name: str = "Dispetcher",
) -> bool:
    if isinstance(admin_name, dict):
        admin_name = admin_name.get("name") or "Dispetcher"
    admin_name = str(admin_name or "Dispetcher")"""
assert old_cba_fn in cnt, "old_cba_fn not found"
cnt = cnt.replace(old_cba_fn, new_cba_fn, 1)

# 7. In swap_graphs and replace_driver: reset all_confirmed_sent
old_sg = """    poll["updated_at"] = datetime.now().isoformat()
    save_poll(poll)
    update_poll_messages(poll)

    return {
        "ok": True,"""
new_sg = """    poll["updated_at"] = datetime.now().isoformat()
    poll["all_confirmed_sent"] = False
    save_poll(poll)
    update_poll_messages(poll)

    return {
        "ok": True,"""
assert old_sg in cnt, "old_sg not found"
cnt = cnt.replace(old_sg, new_sg, 1)

old_rd = """    poll["updated_at"] = datetime.now().isoformat()
    save_poll(poll)
    update_poll_messages(poll)

    return {
        "ok": True,
        "graph": found.get("graph"),"""
new_rd = """    poll["updated_at"] = datetime.now().isoformat()
    poll["all_confirmed_sent"] = False
    save_poll(poll)
    update_poll_messages(poll)

    return {
        "ok": True,
        "graph": found.get("graph"),"""
assert old_rd in cnt, "old_rd not found"
cnt = cnt.replace(old_rd, new_rd, 1)

path.write_text(cnt, encoding="utf-8")
print("grafik_poll.py updated successfully!")

