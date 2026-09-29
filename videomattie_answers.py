#!/usr/bin/env python3
"""Harvest Facebook group membership answers for the Videomattie group.

Reads a member export (GroupExtractor, old or new format), opens each member's
group profile page, expands the membership questions, reads the answers and
picks up the real date the person joined the group.

Writes a cleaned CSV with ISO dates, one column per membership question and the
contact details pulled out into their own columns.

Resumable: every fetched profile is appended to answers.jsonl straight away.
Stop with Ctrl-C and start again; whatever was already fetched is skipped.

Usage:
    python3 videomattie_answers.py members.csv
    python3 videomattie_answers.py members.csv --export-date 2026-09-23
    python3 videomattie_answers.py members.csv --no-avatar
    python3 videomattie_answers.py members.csv --clean-only

One-time setup:
    pip3 install playwright
    python3 -m playwright install chromium
"""

import argparse
import csv
import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------- settings

GROUP = "videomattie"                  # the group slug in the Facebook URL
SESSION_DIR = Path("./fb-session")     # browser profile; keeps you logged in
HARVEST_FILE = Path("./answers.jsonl") # append-only scratch file, makes runs resumable
PAUSE = (3.0, 6.0)                     # random seconds between profiles
LONG_PAUSE = (25.0, 40.0)              # a longer breather...
LONG_PAUSE_EVERY = 40                  # ...after this many profiles
TIMEOUT_MS = 20_000                    # how long to wait for the button or dialog
LOGIN_PATIENCE = 900                   # seconds to wait for you to log in
OUTPUT_DELIMITER = ";"                 # semicolon: Dutch Excel opens that correctly
OUTPUT_LANGUAGE = "nl"                 # "nl" or "en"; controls the CSV headers only
FB_LANGUAGES = ("nl", "en")            # Facebook interface languages to recognise

# --------------------------------------------------- Facebook interface strings
# The labels the script looks for on the page, per Facebook interface language.
# The script recognises every language listed in FB_LANGUAGES, so it works
# whether your account is set to Dutch or English. The English labels still
# need to be checked against a real English Facebook account.

FB_TEXT = {
    "nl": {
        "view_answers": ["Antwoorden bekijken"],
        "answer_list": ["Lidmaatschapsvragen en -antwoorden"],
        "questions_heading": ["Lidmaatschapsvragen"],
        "no_answers": ["Nog geen antwoorden"],
        "no_answer_given": ["Geen antwoord"],
        "rules_agreed": ["Akkoord met groepsregels"],
        "rules_not_agreed": ["Niet akkoord met de groepsregels", "niet akkoord bent gegaan"],
    },
    "en": {
        "view_answers": ["View answers", "View Answers"],
        "answer_list": ["Membership questions and answers", "Membership Questions and Answers"],
        "questions_heading": ["Membership questions", "Membership Questions"],
        "no_answers": ["No answers yet"],
        "no_answer_given": ["No answer"],
        "rules_agreed": ["Agreed to group rules", "Agreed to the group rules"],
        "rules_not_agreed": ["Has Not Agreed to Group Rules", "Did not agree to the group rules"],
    },
}


def fb_texts(key):
    """Every known spelling of one interface label, over all enabled languages."""
    return [text for language in FB_LANGUAGES for text in FB_TEXT[language][key]]


def fb_contains(page_text, key):
    return any(text in page_text for text in fb_texts(key))


def fb_startswith(line, key):
    return any(line.startswith(text) for text in fb_texts(key))


MONTHS = {name: number for number, name in enumerate(
    ["januari", "februari", "maart", "april", "mei", "juni",
     "juli", "augustus", "september", "oktober", "november", "december"], start=1)}
MONTHS |= {name: number for number, name in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"], start=1)}

WEEKDAYS = {"maandag": 0, "dinsdag": 1, "woensdag": 2, "donderdag": 3,
            "vrijdag": 4, "zaterdag": 5, "zondag": 6,
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}

# "Lid van Videomattie sinds 22 september 2026"
JOINED_DAY_MONTH_YEAR = re.compile(
    r"(?:[Ll]id van|[Mm]ember of|[Jj]oined).{0,80}?(?:sinds|since|on)\s+(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})")
# "Member of Videomattie since September 22, 2026"
JOINED_MONTH_DAY_YEAR = re.compile(
    r"(?:[Mm]ember of|[Jj]oined).{0,80}?(?:since|on)\s+([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})")

JS_READ_ANSWERS = """
(labels) => {
  for (const label of labels) {
    const list = document.querySelector(`[aria-label="${label}"]`);
    if (list) return [...list.children].map(item => item.innerText);
  }
  return null;
}
"""

# ----------------------------------------------------------- input column names
# Every field, with the header spellings seen in the exports, lowercased.

INPUT_COLUMNS = {
    "user_id":     ["user id", "gebruikers-id", "gebruikers id", "userid"],
    "name":        ["username", "gebruikersnaam", "name", "naam"],
    "bio":         ["biography", "bio"],
    "verified":    ["is verified", "geverifieerd"],
    "friendship":  ["friendship status", "vriendschapsstatus"],
    "joined_raw":  ["join status text", "lid geworden (origineel)", "join status"],
    "joined_date": ["lid geworden (datum)", "join date", "joined at"],
    "profile_url": ["profile url", "profiel url"],
    "avatar_url":  ["avatar url", "avatar"],
}

# ------------------------------------------------------------- output labelling
# Internal keys are English. These tables decide what ends up in the file.

HEADERS = {
    "nl": {
        "name": "Naam", "user_id": "Gebruikers-ID", "group_profile": "Groepsprofiel",
        "profile_url": "Profiel URL", "bio": "Bio", "joined": "Lid sinds",
        "precision": "Lid sinds nauwkeurigheid", "joined_raw": "Lid sinds (origineel)",
        "friendship": "Vriendschapsstatus", "verified": "Geverifieerd",
        "rules": "Groepsregels", "email": "E-mail", "phone": "Telefoon",
        "has_answers": "Antwoorden aanwezig", "status": "Status",
        "fetched": "Opgehaald op", "avatar": "Avatar URL",
    },
    "en": {
        "name": "Name", "user_id": "User ID", "group_profile": "Group profile",
        "profile_url": "Profile URL", "bio": "Bio", "joined": "Member since",
        "precision": "Member since accuracy", "joined_raw": "Member since (original)",
        "friendship": "Friendship status", "verified": "Verified",
        "rules": "Group rules", "email": "Email", "phone": "Phone",
        "has_answers": "Has answers", "status": "Status",
        "fetched": "Fetched at", "avatar": "Avatar URL",
    },
}

VALUES = {
    "nl": {
        "yes": "Ja", "no": "Nee",
        "exact": "exact", "day": "op de dag", "week": "week bij benadering",
        "month": "maand bij benadering", "year": "jaar bij benadering",
        "agreed": "Akkoord", "not_agreed": "Niet akkoord",
        "ok": "ok", "no_answers": "geen antwoorden", "unreachable": "profiel niet bereikbaar",
        "no_button": "knop niet gevonden", "no_dialog": "venster kwam niet op",
        "not_fetched": "niet opgehaald", "error": "fout",
        "CAN_REQUEST": "Kan vriendschapsverzoek krijgen",
        "CANNOT_REQUEST": "Kan geen vriendschapsverzoek krijgen",
        "ARE_FRIENDS": "Vrienden", "OUTGOING_REQUEST": "Verzoek verstuurd",
        "INCOMING_REQUEST": "Verzoek ontvangen", "unknown": "Onbekend",
    },
    "en": {
        "yes": "Yes", "no": "No",
        "exact": "exact", "day": "to the day", "week": "week, approximate",
        "month": "month, approximate", "year": "year, approximate",
        "agreed": "Agreed", "not_agreed": "Not agreed",
        "ok": "ok", "no_answers": "no answers", "unreachable": "profile unreachable",
        "no_button": "button not found", "no_dialog": "dialog did not open",
        "not_fetched": "not fetched", "error": "error",
        "CAN_REQUEST": "Can receive friend request",
        "CANNOT_REQUEST": "Cannot receive friend request",
        "ARE_FRIENDS": "Friends", "OUTGOING_REQUEST": "Request sent",
        "INCOMING_REQUEST": "Request received", "unknown": "Unknown",
    },
}

# Older harvest files used Dutch status values; keep reading those.
LEGACY_STATUS = {
    "geen antwoorden": "no_answers", "profiel niet bereikbaar": "unreachable",
    "knop niet gevonden": "no_button", "venster kwam niet op": "no_dialog",
    "niet opgehaald": "not_fetched",
}
LEGACY_RULES = {"Akkoord": "agreed", "Niet akkoord": "not_agreed"}

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"(?:\+31|0031|0)\s?6[\s\-]?(?:\d[\s\-]?){8}")


def label(key):
    """Look up an output value in the chosen language, falling back to the key."""
    return VALUES[OUTPUT_LANGUAGE].get(key, key)


# ------------------------------------------------------------------- small tools

def find_phone(text):
    """Pull a Dutch mobile number out of free text and normalise it to +31......"""
    match = PHONE_PATTERN.search(text.replace(" ", " "))
    if not match:
        return ""
    digits = re.sub(r"\D", "", match.group(0))
    for prefix, cut in (("0031", 4), ("31", 2), ("0", 1)):
        if digits.startswith(prefix):
            digits = digits[cut:]
            break
    return "+31" + digits if len(digits) == 9 else ""


def find_email(text):
    match = EMAIL_PATTERN.search(text)
    return match.group(0).lower() if match else ""


def yes_no(value):
    return label("yes") if str(value).strip().lower() in ("true", "ja", "yes", "1") else label("no")


def previous_weekday(reference, weekday):
    """The most recent past occurrence of a weekday, never the reference day itself."""
    gap = (reference.weekday() - weekday) % 7
    return reference - timedelta(days=gap or 7)


def joined_date_from_page(text):
    """'Lid van Videomattie sinds 22 september 2026' or
    'Member of Videomattie since September 22, 2026' -> date(2026, 9, 22)."""
    match = JOINED_DAY_MONTH_YEAR.search(text)
    if match:
        day, month, year = match.group(1), match.group(2).lower(), match.group(3)
    else:
        match = JOINED_MONTH_DAY_YEAR.search(text)
        if not match:
            return None
        month, day, year = match.group(1).lower(), match.group(2), match.group(3)
    if month not in MONTHS:
        return None
    try:
        return date(int(year), MONTHS[month], int(day))
    except ValueError:
        return None


def estimate_joined_date(text, reference):
    """Turn 'Ongeveer 3 weken geleden lid geworden' into a date plus how solid it is.

    Returns (date or None, precision key). The reference is the moment the export
    was made, because Facebook writes these phrases relative to that.
    """
    lowered = (text or "").lower()
    day_zero = reference.date() if isinstance(reference, datetime) else reference

    if not lowered.strip():
        return None, ""
    if re.search(r"\b(zojuist|net|nu|just now)\b", lowered) or \
       re.search(r"\d+\s*(minuut|minuten|uur|uren|minutes?|hours?)\b", lowered) or \
       "vandaag" in lowered or "today" in lowered:
        return day_zero, "day"
    if "gisteren" in lowered or "yesterday" in lowered:
        return day_zero - timedelta(days=1), "day"
    for name, number in WEEKDAYS.items():
        if name in lowered:
            return previous_weekday(day_zero, number), "day"

    match = re.search(r"(\d+)\s*(dag|dagen|days?)\b", lowered)
    if match:
        return day_zero - timedelta(days=int(match.group(1))), "day"
    if re.search(r"\b(een|1|a|one)\s*week\b", lowered):
        return day_zero - timedelta(days=7), "week"
    match = re.search(r"(\d+)\s*(weken|weeks)", lowered)
    if match:
        return day_zero - timedelta(weeks=int(match.group(1))), "week"
    if re.search(r"\b(een|1|a|one)\s*(maand|month)\b", lowered):
        return day_zero - timedelta(days=30), "month"
    match = re.search(r"(\d+)\s*(maanden|months)", lowered)
    if match:
        return day_zero - timedelta(days=30 * int(match.group(1))), "month"
    if re.search(r"\b(een|1|a|one)\s*(jaar|year)\b", lowered):
        return day_zero - timedelta(days=365), "year"
    match = re.search(r"(\d+)\s*(jaar|years)", lowered)
    if match:
        return day_zero - timedelta(days=365 * int(match.group(1))), "year"
    return None, ""


# ----------------------------------------------------------------- reading input

def match_column(headers, field):
    lowered = {h.strip().lower(): h for h in headers if h}
    for candidate in INPUT_COLUMNS[field]:
        if candidate in lowered:
            return lowered[candidate]
    return None


def read_members(path):
    """Read the export, whatever delimiter and header language it uses."""
    raw = path.read_text(encoding="utf-8-sig")
    lines = raw.splitlines()
    first = lines[0] if lines else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","

    rows = list(csv.DictReader(lines, delimiter=delimiter))
    if not rows:
        sys.exit("No rows found in the CSV.")

    headers = list(rows[0].keys())
    columns = {field: match_column(headers, field) for field in INPUT_COLUMNS}
    if not columns["user_id"]:
        sys.exit(f"No user ID column found. Headers are: {headers}")

    def value(row, field):
        column = columns[field]
        return (row.get(column) or "").strip() if column else ""

    members = []
    for row in rows:
        user_id = value(row, "user_id").strip('"')
        if not user_id.isdigit():
            continue
        members.append({field: value(row, field) for field in INPUT_COLUMNS} |
                       {"user_id": user_id})
    return members


def determine_export_date(members, path, given):
    """The moment 'about 3 weeks ago' should be counted back from."""
    if given:
        return datetime.strptime(given, "%Y-%m-%d").date()
    for member in members:                  # the old export format carries a timestamp
        if member["joined_date"]:
            try:
                return datetime.fromisoformat(member["joined_date"]).date()
            except ValueError:
                pass
    return datetime.fromtimestamp(path.stat().st_mtime).date()


# --------------------------------------------------------------- harvest storage

def read_harvest():
    if not HARVEST_FILE.exists():
        return {}
    harvested = {}
    for line in HARVEST_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        for old_key, new_key in (("regels", "rules"), ("lid_sinds", "joined"),
                                 ("opgehaald", "fetched"), ("naam", "name")):
            if old_key in record and new_key not in record:
                record[new_key] = record.pop(old_key)
        record["status"] = LEGACY_STATUS.get(record.get("status", ""), record.get("status", ""))
        record["rules"] = LEGACY_RULES.get(record.get("rules", ""), record.get("rules", ""))
        harvested[record["id"]] = record
    return harvested


def append_harvest(record):
    with HARVEST_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------------ login

def is_logged_in(context, page):
    """Facebook only sets the c_user cookie once you are really in."""
    try:
        has_cookie = any(cookie["name"] == "c_user"
                         for cookie in context.cookies("https://www.facebook.com"))
    except Exception:
        has_cookie = False
    if not has_cookie:
        return False
    try:                        # during a checkpoint the password field is still there
        return page.locator("input[name='pass']").count() == 0
    except Exception:
        return True


def wait_for_login(context, page, patience=LOGIN_PATIENCE):
    """Block until the user is logged in. Never touches their credentials."""
    if is_logged_in(context, page):
        return True

    try:
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
    except Exception:
        pass

    print("\n>> Please log in to Facebook in the window that just opened.")
    print(">> This script waits by itself and starts as soon as you are in. Ctrl-C to quit.\n")

    started = time.time()
    last_notice = 0.0
    while not is_logged_in(context, page):
        elapsed = time.time() - started
        if elapsed > patience:
            print(f"Still not logged in after {int(patience / 60)} minutes. Stopping.")
            return False
        if elapsed - last_notice >= 30:
            print(f"   still waiting... ({int(elapsed)}s)")
            last_notice = elapsed
        time.sleep(3)

    print(">> Logged in. Starting.\n")
    time.sleep(2)
    return True


# ------------------------------------------------------------- fetching a member

def blank_record(member, status, rules="", joined=""):
    return {"id": member["user_id"], "name": member["name"], "status": status,
            "items": [], "rules": rules, "joined": joined,
            "fetched": datetime.now().isoformat(timespec="seconds")}


def rules_status(page_text):
    if fb_contains(page_text, "rules_not_agreed"):
        return "not_agreed"
    if fb_contains(page_text, "rules_agreed"):
        return "agreed"
    return ""


def fetch_member(page, member):
    """Open one group profile, expand the answers and read them."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page.goto(f"https://www.facebook.com/groups/{GROUP}/user/{member['user_id']}/",
              wait_until="domcontentloaded")

    try:
        names = "|".join(re.escape(text) for text in fb_texts("view_answers"))
        button = page.get_by_role("button", name=re.compile(f"^({names})$")).first
        button.wait_for(timeout=TIMEOUT_MS)
        found_button = True
    except PlaywrightTimeout:
        found_button = False

    page_text = page.inner_text("body")
    joined = joined_date_from_page(page_text)
    joined = joined.isoformat() if joined else ""

    if not found_button:
        if fb_contains(page_text, "no_answers"):
            return blank_record(member, "no_answers", rules_status(page_text), joined)
        if not fb_contains(page_text, "questions_heading"):
            return blank_record(member, "unreachable", "", joined)
        return blank_record(member, "no_button", rules_status(page_text), joined)

    button.click()
    try:
        selector = ", ".join(f'[aria-label="{text}"]' for text in fb_texts("answer_list"))
        page.wait_for_selector(selector, timeout=TIMEOUT_MS)
    except PlaywrightTimeout:
        return blank_record(member, "no_dialog", "", joined)

    record = blank_record(member, "ok", "", joined)
    record["items"] = page.evaluate(JS_READ_ANSWERS, fb_texts("answer_list")) or []
    page.keyboard.press("Escape")
    return record


# ------------------------------------------------------------------ writing output

def split_questions(record):
    """Turn the raw blocks into {question: answer} plus the group rules status."""
    questions, rules = {}, record.get("rules", "")
    for block in record.get("items", []):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        head = lines[0].strip()
        if fb_startswith(head, "rules_not_agreed"):
            rules = "not_agreed"
        elif fb_startswith(head, "rules_agreed"):
            rules = "agreed"
        else:
            answer = " ".join(line.strip() for line in lines[1:]).strip()
            questions[head] = "" if answer in fb_texts("no_answer_given") else answer
    return questions, rules


def write_csv(members, harvest, target, export_date, with_avatar=True):
    """Build the cleaned CSV. Question columns follow the order they were first seen."""
    parsed, all_questions = {}, []
    for user_id, record in harvest.items():
        questions, rules = split_questions(record)
        parsed[user_id] = (questions, rules, record)
        for question in questions:
            if question not in all_questions:
                all_questions.append(question)

    head = HEADERS[OUTPUT_LANGUAGE]
    header_row = [head[key] for key in
                  ("name", "user_id", "group_profile", "profile_url", "bio",
                   "joined", "precision", "joined_raw", "friendship", "verified")] \
        + all_questions \
        + [head[key] for key in ("rules", "email", "phone", "has_answers", "status", "fetched")]
    if with_avatar:
        header_row.append(head["avatar"])

    output = [header_row]
    for member in members:
        questions, rules, record = parsed.get(member["user_id"], ({}, "", {}))
        status = record.get("status", "not_fetched")

        joined = record.get("joined", "")
        if joined:
            precision = "exact"
        else:
            estimated, precision = estimate_joined_date(member["joined_raw"], export_date)
            joined = estimated.isoformat() if estimated else ""

        answers = [questions.get(question, "") for question in all_questions]
        blob = " ".join(answer for answer in answers if answer)
        friendship = member["friendship"].strip().upper()

        row = [
            member["name"],
            member["user_id"],
            f"https://www.facebook.com/groups/{GROUP}/user/{member['user_id']}/",
            member["profile_url"],
            member["bio"],
            joined,
            label(precision) if precision else "",
            member["joined_raw"],
            label(friendship) if friendship else label("unknown"),
            yes_no(member["verified"]),
        ] + answers + [
            label(rules) if rules else "",
            find_email(blob),
            find_phone(blob),
            label("yes") if blob.strip() else label("no"),
            label(status.split(":")[0]) + (":" + status.split(":", 1)[1] if ":" in status else ""),
            record.get("fetched", ""),
        ]
        if with_avatar:
            row.append(member["avatar_url"])
        output.append(row)

    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        csv.writer(handle, delimiter=OUTPUT_DELIMITER).writerows(output)
    return all_questions


# ------------------------------------------------------------------------- main

def parse_arguments():
    parser = argparse.ArgumentParser(description="Harvest membership answers per profile.")
    parser.add_argument("csv", help="the member export")
    parser.add_argument("--export-date", metavar="YYYY-MM-DD",
                        help="reference point for phrases like 'about 3 weeks ago'")
    parser.add_argument("--no-avatar", action="store_true", help="leave out the avatar URL column")
    parser.add_argument("--login-patience", type=int, default=LOGIN_PATIENCE, metavar="SECONDS",
                        help=f"how long to wait for you to log in (default {LOGIN_PATIENCE})")
    parser.add_argument("--clean-only", action="store_true",
                        help="no browser, just rebuild the CSV from what was already fetched")
    return parser.parse_args()


def main():
    args = parse_arguments()

    source = Path(args.csv).expanduser()
    if not source.exists():
        sys.exit(f"File not found: {source}")

    members = read_members(source)
    export_date = determine_export_date(members, source, args.export_date)
    harvest = read_harvest()
    target = source.with_name(source.stem + "_cleaned.csv")
    with_avatar = not args.no_avatar

    print(f"{len(members)} members, reference date {export_date}, {len(harvest)} already fetched.")

    if args.clean_only:
        questions = write_csv(members, harvest, target, export_date, with_avatar)
        print(f"Cleaned into {target} ({len(questions)} questions).")
        return

    todo = [member for member in members if member["user_id"] not in harvest]
    if not todo:
        questions = write_csv(members, harvest, target, export_date, with_avatar)
        print(f"Nothing left to fetch. Written to {target} ({len(questions)} questions).")
        return

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(f"https://www.facebook.com/groups/{GROUP}", wait_until="domcontentloaded")

        if not wait_for_login(context, page, args.login_patience):
            context.close()
            sys.exit("Not logged in, so nothing was fetched.")

        done = 0
        try:
            for index, member in enumerate(todo, 1):
                if not is_logged_in(context, page):
                    print("\nYour session was interrupted.")
                    if not wait_for_login(context, page, args.login_patience):
                        break

                try:
                    record = fetch_member(page, member)
                except Exception as error:
                    record = blank_record(member, f"error:{type(error).__name__}")

                # An unreachable profile right after being logged out is not a real
                # failure, so log back in and give this member one more try.
                if record["status"] == "unreachable" and not is_logged_in(context, page):
                    print("\nLogged out during this profile.")
                    if not wait_for_login(context, page, args.login_patience):
                        break
                    try:
                        record = fetch_member(page, member)
                    except Exception as error:
                        record = blank_record(member, f"error:{type(error).__name__}")

                append_harvest(record)
                harvest[member["user_id"]] = record
                done += 1
                print(f"[{index}/{len(todo)}] {member['name'] or member['user_id']}: {record['status']}")

                if index < len(todo):
                    breather = LONG_PAUSE if done % LONG_PAUSE_EVERY == 0 else PAUSE
                    time.sleep(random.uniform(*breather))
        except KeyboardInterrupt:
            print("\nInterrupted. Everything fetched so far is in answers.jsonl.")
        finally:
            context.close()

    questions = write_csv(members, harvest, target, export_date, with_avatar)
    failed = [record for record in harvest.values()
              if record.get("status") not in ("ok", "no_answers")]
    print(f"\n{len(questions)} questions found. Written to {target}")
    if failed:
        print(f"{len(failed)} profiles did not work out. Remove those lines from "
              f"answers.jsonl and run the script again to retry them.")


if __name__ == "__main__":
    main()