import uuid
from datetime import datetime, date as _date, timedelta

from app.pipeline.types import SessionState
from app.pipeline.stt_openai import OpenAISTT
from app.pipeline.tts_openai import OpenAITTS
from app.pipeline.dialog_openai import OpenAIDialogManager

from app.backend.calendar_utils import resolve_date, parse_time_to_hhmm
from app.backend.availability import find_earliest_availability, check_slot_and_suggest
from app.backend.appointments import (
    pick_appointment_for_action,
    format_appointment_choices,
    parse_appointment_id_from_text,
)

from app.backend.db import (
    init_db,
    get_or_create_business,
    upsert_customer,
    attach_customer_to_call,
    log_call_start,
    log_call_end,
    log_message,
    get_service_by_name_or_code,
    get_appointment_detail,
    create_appointment,
    cancel_appointment,
    update_appointment_time_and_staff,
    update_appointment_service,
    list_upcoming_appointments,
)

BUSINESS_NAME = "Downtown Barber Shop"


def _is_unspecified(v: str) -> bool:
    return not v or str(v).strip().lower() == "unspecified"


def _contains_goodbye(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in ["bye", "goodbye", "hang up", "see you", "take care"])


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%a %Y-%m-%d %H:%M")


def run_voice_call():
    init_db()

    print("=== AI Voice Facilitator — Voice Call Simulator ===")
    print("Instructions:")
    print("  - Speak naturally. The mic will record until a pause.")
    print("  - Say 'bye' or 'goodbye' to end the call.\n")

    stt = OpenAISTT()
    tts = OpenAITTS()
    dialog_manager = OpenAIDialogManager()

    session = SessionState(session_id=str(uuid.uuid4()))
    business_id = get_or_create_business(BUSINESS_NAME)
    call_id = log_call_start(session.session_id, business_id=business_id)

    customer_id = None
    customer_name = None
    phone_number = None

    pending_proposed_slot = None  # for booking flow

    # ✅ Disambiguation state
    pending_pick = None
    # example:
    # {"purpose": "cancel"|"reschedule"|"modify_service"|"change_barber",
    #  "candidates": [id...],
    #  "payload": { ...stuff from original request... }}

    greeting = "Hello! Thanks for calling. Before we get started, can I have your name and phone number?"
    print(f"Agent: {greeting}")
    log_message(call_id, "assistant", greeting)
    tts.speak(greeting)

    while True:
        user_text = stt.record_and_transcribe()
        if not user_text:
            continue

        print(f"Caller (STT): {user_text}")
        log_message(call_id, "user", user_text)

        if _contains_goodbye(user_text):
            goodbye = "You’re welcome. Have a great day!"
            print(f"Agent: {goodbye}")
            log_message(call_id, "assistant", goodbye)
            tts.speak(goodbye)
            break

        # ✅ Handle pending pick turn first
        if pending_pick:
            picked = parse_appointment_id_from_text(user_text)
            if picked and picked in pending_pick["candidates"]:
                purpose = pending_pick["purpose"]
                payload = pending_pick.get("payload") or {}
                pending_pick = None
                # “re-inject” into the normal logic by setting fields
                payload["appointment_id"] = picked
                # we’ll handle it by overriding entities later
                injected_entities = payload
                injected_intent = purpose
            else:
                msg = "Sorry — please say the appointment number, like “#12”."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue
        else:
            injected_intent = None
            injected_entities = None

        reply, nlu = dialog_manager.handle_user_utterance(session, user_text)
        intent = injected_intent or nlu.intent
        entities = injected_entities or (nlu.entities or {})
        confidence = float(getattr(nlu, "confidence", 0.0))

        # -------------------------
        # Identity gating
        # -------------------------
        if customer_id is None:
            extracted_name = entities.get("customer_name", "unspecified")
            extracted_phone = entities.get("phone_number", "unspecified")

            if _is_unspecified(extracted_name) and len(user_text.split()) <= 3:
                extracted_name = user_text.strip()

            if not _is_unspecified(extracted_phone):
                phone_number = extracted_phone.strip()
            if not _is_unspecified(extracted_name):
                customer_name = extracted_name.strip()

            if not customer_name or not phone_number:
                ask = "Thanks — could you please tell me your name and phone number to proceed?"
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent=intent, confidence=confidence, entities=entities)
                tts.speak(ask)
                continue

            customer_id = upsert_customer(business_id, customer_name, phone_number)
            attach_customer_to_call(call_id, customer_id)

            ok = f"Perfect, thanks {customer_name}. How can I help you today?"
            print(f"Agent: {ok}")
            log_message(call_id, "assistant", ok, intent=intent, confidence=confidence, entities=entities)
            tts.speak(ok)
            continue

        # -------------------------
        # LIST
        # -------------------------
        if intent == "list_appointments":
            upcoming = list_upcoming_appointments(business_id, customer_id, now=datetime.now(), limit=10)
            if not upcoming:
                msg = "You don’t have any upcoming appointments."
            else:
                parts = []
                for a in upcoming[:5]:
                    parts.append(f"#{a['id']} — {a['service_name']} with {a['staff_name']} at {_fmt_dt(a['start_time'])}")
                msg = "Here are your upcoming appointments: " + "; ".join(parts) + "."
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
            tts.speak(msg)
            continue

        # -------------------------
        # CANCEL
        # -------------------------
        if intent == "cancel_appointment":
            appt_id = None
            if not _is_unspecified(entities.get("appointment_id", "unspecified")):
                try:
                    appt_id = int(str(entities["appointment_id"]).replace("#", "").strip())
                except Exception:
                    appt_id = None
            if appt_id is None:
                appt_id = parse_appointment_id_from_text(user_text)

            date_text = entities.get("date", "unspecified")
            time_text = entities.get("time", "unspecified")

            date_iso = None
            if not _is_unspecified(date_text):
                res = resolve_date(date_text, today=_date.today())
                if res.is_ambiguous:
                    msg = res.clarification_prompt
                    print(f"Agent: {msg}")
                    log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                    tts.speak(msg)
                    continue
                date_iso = res.resolved_date

            time_hhmm = None
            if not _is_unspecified(time_text):
                time_hhmm = parse_time_to_hhmm(time_text)

            if appt_id is None:
                pick = pick_appointment_for_action(business_id, customer_id, date_iso=date_iso, time_hhmm=time_hhmm)
                if pick.ok:
                    appt_id = pick.appointment_id
                else:
                    if pick.reason == "no_upcoming":
                        msg = "You don’t have any upcoming appointments to cancel."
                        print(f"Agent: {msg}")
                        log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                        tts.speak(msg)
                        continue

                    cands = pick.candidates or []
                    msg = "Which one should I cancel? " + format_appointment_choices(cands)
                    msg += " Please say the appointment number, like “#12”."
                    pending_pick = {
                        "purpose": "cancel_appointment",
                        "candidates": [int(a["id"]) for a in cands],
                        "payload": {},  # nothing else needed
                    }
                    print(f"Agent: {msg}")
                    log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                    tts.speak(msg)
                    continue

            ok = cancel_appointment(business_id, customer_id, int(appt_id), reason="customer_request")
            msg = f"Done — I cancelled appointment #{appt_id}. Anything else?" if ok else "I couldn’t cancel that appointment (it might not be confirmed)."
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
            tts.speak(msg)
            continue

        # -------------------------
        # RESCHEDULE
        # -------------------------
        if intent == "reschedule_appointment":
            appt_id = parse_appointment_id_from_text(user_text)
            if not _is_unspecified(entities.get("appointment_id", "unspecified")):
                try:
                    appt_id = int(str(entities["appointment_id"]).replace("#", "").strip())
                except Exception:
                    pass

            date_text = entities.get("date", "unspecified")
            time_text = entities.get("time", "unspecified")

            if _is_unspecified(date_text) or _is_unspecified(time_text):
                msg = "Sure — what new day and time should I move it to?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            res = resolve_date(date_text, today=_date.today())
            if res.is_ambiguous:
                msg = res.clarification_prompt
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue
            date_iso = res.resolved_date

            time_hhmm = parse_time_to_hhmm(time_text)
            if not time_hhmm:
                msg = "Sorry — what time did you mean? For example: 4 PM or 16:30."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            # If no appt id: pick it (upcoming or by date/time)
            if appt_id is None:
                pick = pick_appointment_for_action(business_id, customer_id)
                if pick.ok:
                    appt_id = pick.appointment_id
                else:
                    cands = pick.candidates or []
                    msg = "Which appointment should I reschedule? " + format_appointment_choices(cands)
                    msg += " Please say the appointment number, like “#12”."
                    pending_pick = {
                        "purpose": "reschedule_appointment",
                        "candidates": [int(a["id"]) for a in cands],
                        "payload": {"date": date_text, "time": time_text},
                    }
                    print(f"Agent: {msg}")
                    log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                    tts.speak(msg)
                    continue

            appt = get_appointment_detail(business_id, customer_id, int(appt_id))
            if not appt or appt["status"] != "confirmed":
                msg = "I couldn’t find that appointment (or it’s not confirmed)."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            # ✅ INFER SERVICE from DB (no re-asking)
            service_name = appt["service_name"]

            slot = check_slot_and_suggest(
                business_id=business_id,
                service_name=service_name,
                date_str=date_iso,
                time_hhmm=time_hhmm,
                preferred_staff=None,
                max_alternatives=3,
            )
            if not slot.ok:
                if slot.alternatives:
                    parts = [f"{a['time']} with {a['staff_name']}" for a in slot.alternatives]
                    msg = "That time is taken. I can do " + ", or ".join(parts) + ". Which one works?"
                else:
                    msg = "That time is taken, and I don’t see alternatives that day. Want another day?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            start_dt = datetime.strptime(f"{date_iso} {time_hhmm}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=int(appt["duration_min"]))

            ok = update_appointment_time_and_staff(
                business_id, customer_id, int(appt_id),
                new_staff_id=int(slot.staff_id),
                new_start=start_dt,
                new_end=end_dt,
            )
            msg = f"Done — moved #{appt_id} to {date_iso} {time_hhmm} with {slot.staff_name}. Anything else?" if ok else "I couldn’t reschedule that appointment."
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
            tts.speak(msg)
            continue

        # -------------------------
        # MODIFY (service change OR barber change)
        # -------------------------
        if intent == "modify_appointment":
            appt_id = parse_appointment_id_from_text(user_text)
            if not _is_unspecified(entities.get("appointment_id", "unspecified")):
                try:
                    appt_id = int(str(entities["appointment_id"]).replace("#", "").strip())
                except Exception:
                    pass

            new_service = entities.get("service", "unspecified")
            preferred_staff = entities.get("preferred_staff", "unspecified")
            if _is_unspecified(preferred_staff):
                preferred_staff = None

            if appt_id is None:
                pick = pick_appointment_for_action(business_id, customer_id)
                if pick.ok:
                    appt_id = pick.appointment_id
                else:
                    cands = pick.candidates or []
                    msg = "Which appointment do you mean? " + format_appointment_choices(cands)
                    msg += " Please say the appointment number, like “#12”."
                    pending_pick = {
                        "purpose": "modify_appointment",
                        "candidates": [int(a["id"]) for a in cands],
                        "payload": {"service": new_service, "preferred_staff": preferred_staff},
                    }
                    print(f"Agent: {msg}")
                    log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                    tts.speak(msg)
                    continue

            appt = get_appointment_detail(business_id, customer_id, int(appt_id))
            if not appt or appt["status"] != "confirmed":
                msg = "I couldn’t find that appointment (or it’s not confirmed)."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            # A) Change barber only (keep same slot if possible)
            if preferred_staff and _is_unspecified(new_service):
                slot = check_slot_and_suggest(
                    business_id=business_id,
                    service_name=appt["service_name"],
                    date_str=appt["start_time"].strftime("%Y-%m-%d"),
                    time_hhmm=appt["start_time"].strftime("%H:%M"),
                    preferred_staff=preferred_staff,
                    max_alternatives=3,
                )
                if slot.ok:
                    ok = update_appointment_time_and_staff(
                        business_id, customer_id, int(appt_id),
                        new_staff_id=int(slot.staff_id),
                        new_start=appt["start_time"],
                        new_end=appt["end_time"],
                    )
                    msg = f"Done — I changed #{appt_id} to {slot.staff_name} at the same time." if ok else "I couldn’t update that appointment."
                else:
                    if slot.alternatives:
                        parts = [f"{a['time']} with {a['staff_name']}" for a in slot.alternatives]
                        msg = f"{preferred_staff} isn’t free at that time. I can offer " + ", or ".join(parts) + ". Which one works?"
                    else:
                        msg = f"{preferred_staff} isn’t free at that time. Want a different day or time?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            # B) Change service (keep same start time; end time/price updated)
            if _is_unspecified(new_service):
                msg = "Sure — what would you like to change? You can say ‘change barber to Omar’ or ‘change service to fade’."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            svc = get_service_by_name_or_code(business_id, new_service)
            if not svc:
                msg = "I couldn’t find that service. Try: haircut, fade, beard trim, or combo."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            new_end = appt["start_time"] + timedelta(minutes=int(svc["duration_min"]))
            ok = update_appointment_service(
                business_id, customer_id, int(appt_id),
                new_service_id=int(svc["id"]),
                new_end=new_end,
                new_price=float(svc["price"]),
                currency=str(svc["currency"]),
            )
            msg = f"Done — appointment #{appt_id} is now {svc['name']}." if ok else "I couldn’t update that appointment."
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
            tts.speak(msg)
            continue

        # -------------------------
        # BOOKING (unchanged core)
        # -------------------------
        if intent == "check_availability":
            service = entities.get("service", "unspecified")
            date_text = entities.get("date", "unspecified")

            if _is_unspecified(service):
                ask = "Sure — what service are you looking for? Haircut, fade, beard trim, or combo."
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent=intent, confidence=confidence, entities=entities)
                tts.speak(ask)
                continue

            if _is_unspecified(date_text):
                ask = "What day would you like to come in?"
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent=intent, confidence=confidence, entities=entities)
                tts.speak(ask)
                continue

            res = resolve_date(date_text, today=_date.today())
            if res.is_ambiguous:
                msg = res.clarification_prompt
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            date_iso = res.resolved_date
            result = find_earliest_availability(business_id, service, date_iso, preferred_staff=None)

            if not result.ok:
                msg = "I don’t have availability on that day. Want another date?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            pending_proposed_slot = {"service": service, "date_iso": date_iso, "time_hhmm": result.start_hhmm, "staff_id": result.staff_id, "staff_name": result.staff_name}
            msg = f"Earliest for a {service} is {result.start_hhmm} with {result.staff_name} on {date_iso}. Want me to book it?"
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
            tts.speak(msg)
            continue

        if intent == "schedule_appointment":
            service = entities.get("service", "unspecified")
            date_text = entities.get("date", "unspecified")
            time_text = entities.get("time", "unspecified")

            if pending_proposed_slot and any(x in user_text.lower() for x in ["yes", "confirm", "book", "sure", "okay"]):
                appt = pending_proposed_slot
                svc = get_service_by_name_or_code(business_id, appt["service"])
                if not svc:
                    msg = "I couldn’t find that service. Try: haircut, fade, beard trim, or combo."
                    print(f"Agent: {msg}")
                    log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                    tts.speak(msg)
                    continue

                start_dt = datetime.strptime(f"{appt['date_iso']} {appt['time_hhmm']}", "%Y-%m-%d %H:%M")
                end_dt = start_dt + timedelta(minutes=int(svc["duration_min"]))

                create_appointment(
                    business_id=business_id,
                    customer_id=customer_id,
                    staff_id=int(appt["staff_id"]),
                    service_id=int(svc["id"]),
                    booking_type="phone",
                    start_time=start_dt,
                    end_time=end_dt,
                    quoted_price=float(svc["price"]),
                    currency=str(svc["currency"]),
                    notes=None,
                )
                pending_proposed_slot = None

                msg = f"All set — booked {svc['name']} at {appt['time_hhmm']} with {appt['staff_name']} on {appt['date_iso']}. Anything else?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            # If user said "haircut tomorrow" and model gave a vague reply:
            # we enforce a real flow.
            if _is_unspecified(service):
                ask = "Which service did you want? Haircut, fade, beard trim, or combo."
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent=intent, confidence=confidence, entities=entities)
                tts.speak(ask)
                continue

            if _is_unspecified(date_text):
                ask = "What day would you like to come in?"
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent=intent, confidence=confidence, entities=entities)
                tts.speak(ask)
                continue

            res = resolve_date(date_text, today=_date.today())
            if res.is_ambiguous:
                msg = res.clarification_prompt
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue
            date_iso = res.resolved_date

            if _is_unspecified(time_text):
                earliest = find_earliest_availability(business_id, service, date_iso, preferred_staff=None)
                if earliest.ok:
                    pending_proposed_slot = {"service": service, "date_iso": date_iso, "time_hhmm": earliest.start_hhmm, "staff_id": earliest.staff_id, "staff_name": earliest.staff_name}
                    msg = f"What time works? Earliest is {earliest.start_hhmm} with {earliest.staff_name}. Want that?"
                else:
                    msg = "What time would you like? For example: 4 PM or 16:30."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            time_hhmm = parse_time_to_hhmm(time_text)
            if not time_hhmm:
                msg = "Sorry — what time did you mean? For example: 4 PM or 16:30."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
                tts.speak(msg)
                continue

            slot = check_slot_and_suggest(business_id, service, date_iso, time_hhmm, preferred_staff=None, max_alternatives=3)
            if slot.ok:
                pending_proposed_slot = {"service": service, "date_iso": date_iso, "time_hhmm": time_hhmm, "staff_id": slot.staff_id, "staff_name": slot.staff_name}
                msg = f"Yes — {time_hhmm} on {date_iso} with {slot.staff_name}. Confirm?"
            else:
                if slot.alternatives:
                    parts = [f"{a['time']} with {a['staff_name']}" for a in slot.alternatives]
                    msg = "That time is taken. I can offer " + ", or ".join(parts) + ". Which one works?"
                else:
                    msg = "That time is taken and I don’t see other open slots that day. Want another day?"
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg, intent=intent, confidence=confidence, entities=entities)
            tts.speak(msg)
            continue

        # Default
        print(f"Agent: {reply}")
        log_message(call_id, "assistant", reply, intent=intent, confidence=confidence, entities=entities)
        tts.speak(reply)

    log_call_end(call_id)
    print("\n=== Call ended ===")
    print(f"Session ID: {session.session_id}")


if __name__ == "__main__":
    run_voice_call()