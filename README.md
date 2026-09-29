# Videomattie membership answers

When someone joins the Facebook group **Videomattie**, they answer a few membership
questions. Facebook shows those answers on each member's group profile page, behind a
button, one person at a time. There is no way to export them in bulk.

This script does that clicking for you. Give it a member export, and it opens every
profile, reads the answers, picks up the real date the person joined, and writes one
clean spreadsheet.

It only reads. It never approves, declines, removes or messages anyone.

---

## What you need

- A computer with **Python 3.9 or newer**. macOS and most Linux systems already have it.
  On Windows, install it from [python.org](https://www.python.org/downloads/) and tick
  "Add Python to PATH" during setup.
- A Facebook account that is **an admin or moderator of the group**. Without that,
  Facebook does not show the answers at all.
- A member export as a CSV file. Both the older Dutch-headed export and the newer
  English-headed one work; the script figures out which is which.

---

## Setting up, once

Open a terminal (**Terminal** on macOS, **PowerShell** on Windows) and run:

```
pip3 install playwright
python3 -m playwright install chromium
```

The second command downloads a browser the script can drive. It is a few hundred
megabytes and takes a minute or two. You only do this once.

---

## Running it

Put the script and your export in the same folder, then:

```
python3 videomattie_answers.py members.csv
```

A browser window opens on Facebook.

**The first time, you will not be logged in.** Log in yourself in that window. The
script waits for you and says so in the terminal, with a sign of life every thirty
seconds. You do not have to press anything. As soon as Facebook confirms your session,
it starts working through the list.

Your login is remembered in a folder called `fb-session`, right next to the script, so
later runs begin immediately.

While it runs you will see one line per member:

```
[7/50] Kevin Simons: ok
[8/50] Sandesh Jagroep: no_answers
```

Leave the window alone, but do not close it. You can keep working in other apps.

---

## What you get

A file next to your export called `members_cleaned.csv`, with these columns:

| Column | What it holds |
| --- | --- |
| Naam, Gebruikers-ID | straight from the export |
| Groepsprofiel | direct link to that member's admin page in the group |
| Profiel URL, Bio | straight from the export |
| Lid sinds | the join date, as `2026-09-22`, so spreadsheets sort it properly |
| Lid sinds nauwkeurigheid | how solid that date is, see below |
| Lid sinds (origineel) | the original phrase from the export, for checking |
| Vriendschapsstatus | `CAN_REQUEST` and friends, written out in plain words |
| Geverifieerd | Ja or Nee |
| *one column per question* | the member's answer, named after the question itself |
| Groepsregels | whether they agreed to the group rules |
| E-mail, Telefoon | pulled out of all the answers, phone normalised to `+316...` |
| Antwoorden aanwezig | Ja or Nee, handy for filtering |
| Status | how the fetch went, see the table further down |
| Opgehaald op | when this member was fetched |
| Avatar URL | the profile picture; leave it out with `--no-avatar` |

The question columns are built from whatever questions the script actually encounters.
Change the membership questions in the group and the new ones show up by themselves.

### About the dates

Facebook only gives the export a vague phrase like *"Ongeveer 3 weken geleden lid
geworden"*. The script prefers the real date shown on the profile page, and falls back
to working out the phrase. The accuracy column tells you which happened:

| Value | Meaning |
| --- | --- |
| `exact` | read straight off the profile page, correct to the day |
| `op de dag` | worked out from "6 hours ago", "yesterday", "last Friday" |
| `week bij benadering` | from "about 2 weeks ago", so within a few days |
| `maand bij benadering` | from "about a month ago", so roughly right |

The phrases are counted back from the moment you made the export, not from today. The
script takes the file's modification date for that. If that is wrong, say so:

```
python3 videomattie_answers.py members.csv --export-date 2026-09-23
```

---

## Options

| Option | What it does |
| --- | --- |
| `--export-date YYYY-MM-DD` | the reference point for phrases like "about 3 weeks ago" |
| `--no-avatar` | leaves out the avatar URL column, which makes the file much smaller |
| `--login-patience SECONDS` | how long to wait for you to log in, 900 by default |
| `--clean-only` | rebuilds the spreadsheet from what was already fetched, no browser |

---

## Files it creates

| File | What it is |
| --- | --- |
| `answers.jsonl` | one line per fetched member; this is what makes runs resumable |
| `fb-session/` | the browser profile that keeps you logged in |
| `*_cleaned.csv` | the spreadsheet you are after |

Keep `answers.jsonl` and `fb-session/` next to the script. Delete `answers.jsonl` if you
want to fetch everybody again from scratch.

---

## Stopping, resuming, retrying

Press **Ctrl-C** to stop at any point. Everything fetched so far is already saved.
Run the same command again and it picks up where it left off, skipping what it has.

If some members ended up with a status other than `ok` or `no_answers`, open
`answers.jsonl` in a text editor, delete those lines, and run the script again. Only
those members are fetched again.

To rebuild the spreadsheet without touching Facebook, for instance after changing an
option:

```
python3 videomattie_answers.py members.csv --clean-only
```

---

## Status values

| Status | What happened |
| --- | --- |
| `ok` | answers were read |
| `no_answers` | the member never filled anything in; nothing is wrong |
| `no_button` | the section was there but the button never appeared; worth retrying |
| `no_dialog` | the button was clicked but the window did not open; worth retrying |
| `unreachable` | the profile did not load, for example a deleted account |
| `error:...` | something unexpected; the name after the colon is the technical cause |
| `not_fetched` | this member has not been through the script yet |

---

## Adjusting it

The settings sit at the top of the script, above everything else.

**Another group.** Change `GROUP` to the slug in your group's URL.

**English column headers.** Set `OUTPUT_LANGUAGE` to `"en"`. Dutch is the default
because the spreadsheet usually ends up in a Dutch Excel.

**Comma instead of semicolon.** Set `OUTPUT_DELIMITER` to `","`. The semicolon is there
because Dutch Excel opens that without an import dialog.

**A different Facebook language.** The block marked *Facebook interface strings* holds
the Dutch labels the script looks for on the page. Set your account to another language
and those lines are the ones to translate.

**Faster or slower.** `PAUSE` is the gap between members and `LONG_PAUSE` is the longer
breather it takes every `LONG_PAUSE_EVERY` members. They are there on purpose, see below.

---

## Going easy on Facebook

Roughly four seconds per member, so fifty members take about five minutes and five
hundred take under an hour. The pauses between members are deliberate: opening hundreds
of admin pages back to back looks like a bot, Facebook throttles that, and in the worst
case your account gets a warning. Do not set the pauses to zero.

For very large lists, split the work over a few days. Stopping and resuming costs you
nothing.

---

## When something goes wrong

**"command not found: python3"** — Python is not installed, or not on your PATH.
Try `python` instead of `python3`.

**"No module named playwright"** — the setup step did not complete. Run
`pip3 install playwright` again and read what it says.

**Everything comes back as `unreachable`** — you are logged in with an account that is
not an admin of the group, or the group slug in `GROUP` is wrong.

**Every member gets `no_button` while the answers do exist** — your Facebook is probably
set to a different language, so the script is looking for labels that are not on the
page. See *Adjusting it* above.

**The browser window closed on its own** — that happens when the script finishes or
crashes. Look at the terminal for the reason.

**Excel puts everything in one column** — your Excel expects commas. Set
`OUTPUT_DELIMITER` to `","`, or use Data > From Text and pick the semicolon.
