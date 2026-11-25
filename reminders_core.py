"""Data, storage and date logic for Reminders.  No GTK in here, so it can be tested alone."""

import calendar
import datetime as dt
import json
import os
import re
import uuid

PRIORITIES = ["None", "Low", "Medium", "High"]

REPEATS = [
    ("none", "Never"),
    ("daily", "Every day"),
    ("weekdays", "Every weekday"),
    ("weekly", "Every week"),
    ("monthly", "Every month"),
    ("yearly", "Every year"),
]
REPEAT_LABELS = dict(REPEATS)

COLORS = [
    ("blue", "#3584e4"),
    ("green", "#2ec27e"),
    ("yellow", "#e5a50a"),
    ("orange", "#ff7800"),
    ("red", "#e01b24"),
    ("pink", "#d6409f"),
    ("purple", "#9141ac"),
    ("brown", "#986a44"),
    ("slate", "#6f8396"),
]
COLOR_NAMES = [c[0] for c in COLORS]

SMART_VIEWS = [
    ("today", "Today"),
    ("scheduled", "Scheduled"),
    ("all", "All"),
    ("flagged", "Flagged"),
    ("completed", "Completed"),
]

DEFAULT_SETTINGS = {
    "background": True,       # keep running when the window closes, start at login
    "default_hour": 9,        # alert time for reminders that have a date but no time
    "snooze_minutes": 10,
    "sound": True,
    "smart_parse": True,      # understand "tomorrow 5pm" etc. in the quick-add box
    "show_completed": False,
}

ITEM_DEFAULTS = {
    "title": "",
    "notes": "",
    "list_id": None,
    "due_date": None,         # "YYYY-MM-DD"
    "due_time": None,         # "HH:MM", only with a date
    "repeat": "none",
    "repeat_anchor": None,    # first due date of a repeating series (keeps day-of-month stable)
    "priority": 0,
    "flagged": False,
    "done": False,
    "completed_at": None,
    "created": None,
    "alerted": None,          # alert_key() of the last alert sent (or skipped)
    "snooze_until": None,     # "YYYY-MM-DDTHH:MM"
    "claude_sent_at": None,   # last time it was sent to Claude Code
    "images": (),             # file names in Store.images_dir (always replaced, never mutated)
}

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def new_id():
    return uuid.uuid4().hex[:12]


def now_str():
    return dt.datetime.now().isoformat(timespec="seconds")


def parse_date(s):
    try:
        return dt.date.fromisoformat(s) if s else None
    except ValueError:
        return None


def parse_time(s):
    try:
        return dt.time.fromisoformat(s) if s else None
    except ValueError:
        return None


def parse_datetime(s):
    try:
        return dt.datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


# ---------------------------------------------------------------- dates


def add_months(d, n, day=None):
    """d moved by n months, keeping `day` (default d.day) clamped to the month length."""
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    day = day or d.day
    return dt.date(y, m, min(day, calendar.monthrange(y, m)[1]))


def step_date(d, repeat, anchor=None):
    """The next occurrence after d."""
    if repeat == "daily":
        return d + dt.timedelta(days=1)
    if repeat == "weekdays":
        d += dt.timedelta(days=1)
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        return d
    if repeat == "weekly":
        return d + dt.timedelta(days=7)
    if repeat == "monthly":
        return add_months(d, 1, (anchor or d).day)
    if repeat == "yearly":
        a = anchor or d
        y = d.year + 1
        return dt.date(y, a.month, min(a.day, calendar.monthrange(y, a.month)[1]))
    return None


def next_occurrence(item, now):
    """Due date for the copy made when a repeating reminder is completed.

    Steps at least once, then keeps stepping until the reminder is in the future, so
    completing a long-overdue daily task doesn't leave a pile of overdue copies.
    """
    d = parse_date(item["due_date"])
    if not d or item["repeat"] == "none":
        return None
    anchor = parse_date(item.get("repeat_anchor")) or d
    t = parse_time(item["due_time"]) or dt.time(23, 59)
    d = step_date(d, item["repeat"], anchor)
    for _ in range(5000):
        if dt.datetime.combine(d, t) > now:
            break
        d = step_date(d, item["repeat"], anchor)
    return d


def friendly_date(d, today):
    delta = (d - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta == -1:
        return "Yesterday"
    if 1 < delta < 7:
        return d.strftime("%A")
    if d.year == today.year:
        return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
    return f"{d.day} {d.strftime('%b')} {d.year}"


def format_time(t, use_24h=False):
    if use_24h:
        return t.strftime("%H:%M")
    h = t.hour % 12 or 12
    suffix = "AM" if t.hour < 12 else "PM"
    return f"{h}:{t.minute:02d} {suffix}"


def format_due(item, today, use_24h=False):
    d = parse_date(item.get("due_date"))
    if not d:
        return ""
    s = friendly_date(d, today)
    t = parse_time(item.get("due_time"))
    if t:
        s += ", " + format_time(t, use_24h)
    return s


# ---------------------------------------------------------------- alerts


def alert_key(item):
    return f"{item['due_date']}|{item['due_time']}|{item.get('snooze_until')}"


def alert_time(item, default_hour):
    """When this reminder should notify, or None."""
    if item["done"]:
        return None
    snooze = parse_datetime(item.get("snooze_until"))
    if snooze:
        return snooze
    d = parse_date(item["due_date"])
    if not d:
        return None
    t = parse_time(item["due_time"]) or dt.time(default_hour, 0)
    return dt.datetime.combine(d, t)


def is_overdue(item, now):
    if item["done"]:
        return False
    d = parse_date(item["due_date"])
    if not d:
        return False
    t = parse_time(item["due_time"])
    if t:
        return dt.datetime.combine(d, t) < now
    return d < now.date()


# ---------------------------------------------------------------- quick-add parsing

_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20,
              "thirty": 30, "forty-five": 45, "half": 0.5}
WEEKDAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
            "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
            "sat": 5, "saturday": 5, "sun": 6, "sunday": 6}
MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4,
          "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
          "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11,
          "november": 11, "dec": 12, "december": 12}

_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_NUM = r"\d+|" + "|".join(k for k in _NUM_WORDS if k != "half")
_ORD = r"(?:st|nd|rd|th)?"
_PART_OF_DAY = {"morning": 9, "afternoon": 14, "evening": 18, "night": 20, "tonight": 20}


def _num(s):
    s = s.lower()
    return float(s) if s.replace(".", "", 1).isdigit() else _NUM_WORDS.get(s, 1)


def _upcoming_weekday(today, wd, include_today=False):
    delta = (wd - today.weekday()) % 7
    if delta == 0 and not include_today:
        delta = 7
    return today + dt.timedelta(days=delta)


def _fix_year(d, today):
    return d if d >= today else d.replace(year=d.year + 1)


def parse_quick(text, now, list_names=()):
    """Pull a date, time, repeat, priority, flag and #list out of a quick-add line.

    Returns a dict with title, due_date (date|None), due_time (time|None), repeat,
    priority, flagged and list_name (or None).
    """
    today = now.date()
    r = {"due_date": None, "due_time": None, "repeat": "none", "priority": 0,
         "flagged": False, "list_name": None, "part_of_day": None}
    s = f" {text} "

    def take(pattern, fn):
        nonlocal s
        m = re.search(pattern, s, re.I)
        if m and fn(m) is not False:
            s = s[:m.start()] + " " + s[m.end():]
            return True
        return False

    # !, !!, !!! → priority
    take(r"(?<=\s)(!{1,3})(?=\s)", lambda m: r.__setitem__("priority", len(m.group(1))))
    # #flag / #flagged
    take(r"(?<=\s)#flag(?:ged)?(?=\s)", lambda m: r.__setitem__("flagged", True))

    # #listname (only when such a list exists)
    def list_tag(m):
        want = re.sub(r"[\s_-]", "", m.group(1).lower())
        for name in list_names:
            if re.sub(r"[\s_-]", "", name.lower()) == want:
                r["list_name"] = name
                return True
        return False
    for m in list(re.finditer(r"(?<=\s)#([\w-]+)(?=\s)", s)):
        if take(r"(?<=\s)#(" + re.escape(m.group(1)) + r")(?=\s)", list_tag):
            break

    # Repeats
    def every(m):
        w = m.group(1).lower()
        if w in WEEKDAYS:
            r["repeat"] = "weekly"
            r["due_date"] = _upcoming_weekday(today, WEEKDAYS[w], include_today=True)
        else:
            r["repeat"] = {"day": "daily", "weekday": "weekdays", "week": "weekly",
                           "month": "monthly", "year": "yearly"}[w.rstrip("s")]
    take(rf"\bevery\s+(day|weekdays?|week|month|year|{_WD})\b", every)
    take(r"\b(daily|weekly|monthly|yearly|annually)\b",
         lambda m: r.__setitem__("repeat", {"annually": "yearly"}.get(m.group(1).lower(), m.group(1).lower())))

    # Relative: in 20 min, in 2 hours, in 3 days, in an hour, in half an hour
    def relative(m):
        n = 0.5 if m.group(1) and m.group(1).lower() == "half" else _num(m.group(2) or "1")
        unit = m.group(3).lower()
        if unit.startswith(("m",)) and not unit.startswith("mo"):
            target = now + dt.timedelta(minutes=n)
        elif unit.startswith("h"):
            target = now + dt.timedelta(hours=n)
        elif unit.startswith("d"):
            r["due_date"] = today + dt.timedelta(days=int(n))
            return
        elif unit.startswith("w"):
            r["due_date"] = today + dt.timedelta(weeks=int(n))
            return
        else:
            r["due_date"] = add_months(today, int(n))
            return
        target = target.replace(second=0, microsecond=0)
        r["due_date"], r["due_time"] = target.date(), target.time()
    take(rf"\bin\s+(?:(half)\s+an?|({_NUM}))\s*"
         r"(minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|wks?|w|months?)\b", relative)

    # ISO date
    take(r"\b(\d{4}-\d{2}-\d{2})\b", lambda m: (r.__setitem__("due_date", parse_date(m.group(1)))
                                              if parse_date(m.group(1)) else False))

    # 25 dec / 25th of december / dec 25 / december 25th
    def day_month(m, day, mon):
        try:
            r["due_date"] = _fix_year(dt.date(today.year, MONTHS[mon.lower()], int(day)), today)
        except ValueError:
            return False
    take(rf"\b(?:on\s+)?(\d{{1,2}}){_ORD}(?:\s+of)?\s+({_MON})\b",
         lambda m: day_month(m, m.group(1), m.group(2)))
    take(rf"\b(?:on\s+)?({_MON})\s+(\d{{1,2}}){_ORD}\b",
         lambda m: day_month(m, m.group(2), m.group(1)))

    # Named days
    def named_day(m):
        w = m.group(1).lower()
        if w == "day after tomorrow":
            r["due_date"] = today + dt.timedelta(days=2)
        elif w in ("tomorrow", "tmrw", "tmr"):
            r["due_date"] = today + dt.timedelta(days=1)
        elif w == "tonight":
            r["due_date"] = today
            r["part_of_day"] = 20
        else:
            r["due_date"] = today
    take(r"\b(day after tomorrow|tomorrow|tmrw|tmr|today|tonight)\b", named_day)

    def next_period(m):
        which, what = m.group(1).lower(), m.group(2).lower()
        if what == "week":
            r["due_date"] = _upcoming_weekday(today, 0) if which == "next" else today
        elif what == "weekend":
            r["due_date"] = _upcoming_weekday(today, 5, include_today=which == "this")
            if which == "next" and r["due_date"] - today < dt.timedelta(days=7) and today.weekday() >= 5:
                r["due_date"] += dt.timedelta(days=7)
        else:
            r["due_date"] = add_months(today.replace(day=1), 1) if which == "next" else today
    take(r"\b(next|this)\s+(week|weekend|month)\b", next_period)

    if r["due_date"] is None:
        # "friday" is the coming one (never today).  "next friday" skips a Friday that is
        # still in this week, so on a Wednesday it means the Friday after this one.
        def weekday(m):
            d = _upcoming_weekday(today, WEEKDAYS[m.group(2).lower()])
            if (m.group(1) or "").strip().lower() == "next" and d.isocalendar()[:2] == today.isocalendar()[:2]:
                d += dt.timedelta(days=7)
            r["due_date"] = d
        # Full day names can stand alone; short ones ("sun", "sat", "wed") only after on/next/this,
        # so "buy sun screen" stays a plain title.
        full = "|".join(sorted((k for k in WEEKDAYS if len(k) > 4 or k == "friday"), key=len, reverse=True))
        if not take(rf"\b(?:on\s+)?(next\s+|this\s+)?({full})\b", weekday):
            take(rf"\b(?:on\s+|(next\s+|this\s+))({_WD})\b", weekday)

    # Times
    def set_time(h, mi=0):
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return False
        r["due_time"] = dt.time(h, mi)

    def ampm(m):
        h, mi, p = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()[0]
        if not 1 <= h <= 12:
            return False
        return set_time(h % 12 + (12 if p == "p" else 0), mi)
    if r["due_time"] is None:
        take(r"\b(?:at\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*([ap]\.?m\.?)(?=\W)", ampm)
    if r["due_time"] is None:
        take(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b", lambda m: set_time(int(m.group(1)), int(m.group(2))))
    if r["due_time"] is None:
        def bare_hour(m):
            h = int(m.group(1))
            if not 1 <= h <= 12:
                return False
            return set_time(h + 12 if h <= 7 else h % 24)   # "at 5" is 5 PM, "at 9" is 9 AM
        take(r"\bat\s+(\d{1,2})\b(?![:.]\d)", bare_hour)
    if r["due_time"] is None:
        take(r"\b(?:at\s+)?(noon|midday|midnight)\b",
             lambda m: set_time(0 if m.group(1).lower() == "midnight" else 12))
    if r["due_time"] is None:
        # "in the morning", "this evening", or a bare part of day right after a date word
        def part(m):
            r["part_of_day"] = _PART_OF_DAY[m.group(2).lower()]
        if not take(r"\b(in the|this)\s+(morning|afternoon|evening|night)\b", part) and r["due_date"]:
            take(r"\b()(morning|afternoon|evening|night)\b", part)
        if r["part_of_day"] is not None:
            r["due_time"] = dt.time(r["part_of_day"], 0)
            if r["due_date"] is None:
                r["due_date"] = today

    if r["due_time"] is not None and r["due_date"] is None:
        r["due_date"] = today if dt.datetime.combine(today, r["due_time"]) > now else today + dt.timedelta(days=1)
    if r["repeat"] != "none" and r["due_date"] is None:
        r["due_date"] = today

    title = re.sub(r"\s+", " ", s).strip()
    found = r["due_date"] or r["priority"] or r["flagged"] or r["list_name"]
    if found:
        # Words left dangling where a date was cut out ("Pay rent on", "Call at").
        title = re.sub(r"^(?:(?:on|at|by|due)\s+)+|(?:\s+(?:on|at|by|due|,))+$", "", title,
                       flags=re.I).strip(" ,")
    if not title:
        # Nothing but date words: keep the text as the title rather than an empty reminder.
        title = text.strip()
    del r["part_of_day"]
    r["title"] = title
    r["found"] = bool(found)
    return r


# ---------------------------------------------------------------- Claude Code


def claude_prompt(items, list_name=None, image_path=None):
    """The prompt sent to Claude Code for one to-do, or for several from a list.

    Attached images are listed by absolute path so Claude can open them.
    """
    def paths(it):
        return [image_path(n) for n in it.get("images") or ()] if image_path else []

    if len(items) == 1:
        it = items[0]
        notes = it["notes"].strip()
        text = it["title"].strip() + (f"\n\n{notes}" if notes else "")
        imgs = paths(it)
        if imgs:
            text += ("\n\nAttached image" + ("s" if len(imgs) > 1 else "") + " (open and look at "
                     + ("them" if len(imgs) > 1 else "it") + "):\n" + "\n".join(imgs))
        return text
    head = f"Here are the open to-dos for {list_name or 'this project'}. Work through them in order:"
    parts = []
    for i, it in enumerate(items, 1):
        notes = it["notes"].strip()
        part = f"{i}. {it['title'].strip()}" + ("\n   " + notes.replace("\n", "\n   ") if notes else "")
        imgs = paths(it)
        if imgs:
            part += "\n   Attached images (open and look at them):\n" + "\n".join("   " + p for p in imgs)
        parts.append(part)
    return head + "\n\n" + "\n".join(parts)


def short_path(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path and (path == home or path.startswith(home + os.sep)) else path


# ---------------------------------------------------------------- store


class Store:
    """Lists, reminders and settings in one JSON file, saved after every change."""

    def __init__(self, path):
        self.path = path
        self.lists = []
        self.items = []
        self.settings = dict(DEFAULT_SETTINGS)
        self._listeners = []
        self.load()

    # -- persistence

    def load(self):
        data = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = None
        except (OSError, ValueError):
            # Keep the unreadable file instead of overwriting it with an empty one.
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                os.replace(self.path, f"{self.path}.broken-{stamp}")
            except OSError:
                pass
        fresh = data is None
        data = data or {}
        self.lists = [dict({"color": "blue", "folder": None}, **lst) for lst in data.get("lists", []) if lst.get("id")]
        self.items = [dict(ITEM_DEFAULTS, **it) for it in data.get("items", []) if it.get("id")]
        for it in self.items:
            it["images"] = list(it["images"] or ())
        self.settings = dict(DEFAULT_SETTINGS, **data.get("settings", {}))
        if not self.lists:
            self.lists = [{"id": new_id(), "name": "Reminders", "color": "blue", "folder": None}]
        ids = {lst["id"] for lst in self.lists}
        for it in self.items:
            if it["list_id"] not in ids:
                it["list_id"] = self.lists[0]["id"]
        if fresh:
            try:
                self.save()
            except OSError:
                pass
        self._cleanup_images()

    # -- images

    @property
    def images_dir(self):
        return os.path.join(os.path.dirname(self.path), "images")

    def image_path(self, name):
        return os.path.join(self.images_dir, name)

    def new_image_name(self, ext=".png"):
        os.makedirs(self.images_dir, exist_ok=True)
        return uuid.uuid4().hex[:16] + ext

    def _referenced_images(self):
        return {n for it in self.items for n in it["images"]}

    def _cleanup_images(self):
        """Delete image files no reminder uses (left by deletes that can't be undone any more)."""
        try:
            files = os.listdir(self.images_dir)
        except OSError:
            return
        used = self._referenced_images()
        for f in files:
            if f not in used:
                try:
                    os.remove(self.image_path(f))
                except OSError:
                    pass

    def add_images(self, item_id, names):
        item = self.get_item(item_id)
        if item and names:
            item["images"] = item["images"] + [n for n in names if n not in item["images"]]
            self.changed()

    def remove_image(self, item_id, name):
        item = self.get_item(item_id)
        if not item or name not in item["images"]:
            return
        item["images"] = [n for n in item["images"] if n != name]
        if name not in self._referenced_images():   # a repeat copy may share it
            self.discard_image_file(name)
        self.changed()

    def discard_image_file(self, name):
        if name not in self._referenced_images():
            try:
                os.remove(self.image_path(name))
            except OSError:
                pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "lists": self.lists, "items": self.items,
                       "settings": self.settings}, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def connect(self, callback):
        self._listeners.append(callback)

    def disconnect(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def changed(self):
        self.save()
        for cb in list(self._listeners):
            cb()

    # -- lookups

    def get_item(self, item_id):
        return next((it for it in self.items if it["id"] == item_id), None)

    def get_list(self, list_id):
        return next((lst for lst in self.lists if lst["id"] == list_id), None)

    def list_by_name(self, name):
        return next((lst for lst in self.lists if lst["name"] == name), None)

    # -- items

    def _skip_past_alert(self, item, default_hour):
        """A reminder set for a time that already passed shouldn't pop up right away."""
        at = alert_time(item, default_hour)
        if at and at <= dt.datetime.now():
            item["alerted"] = alert_key(item)

    def add_item(self, title, list_id=None, **fields):
        item = dict(ITEM_DEFAULTS, id=new_id(), title=title, created=now_str(),
                    list_id=list_id or self.lists[0]["id"])
        item.update(fields)
        item["images"] = list(item["images"] or ())
        if item["repeat"] != "none":
            item["repeat_anchor"] = item["due_date"]
        self._skip_past_alert(item, self.settings["default_hour"])
        self.items.append(item)
        self.changed()
        return item

    def update_item(self, item_id, **changes):
        item = self.get_item(item_id)
        if not item:
            return None
        due_changed = any(k in changes and changes[k] != item[k] for k in ("due_date", "due_time"))
        if "repeat" in changes and changes["repeat"] != item["repeat"]:
            due_changed = True
        item.update(changes)
        if due_changed:
            item["snooze_until"] = None
            item["repeat_anchor"] = item["due_date"] if item["repeat"] != "none" else None
            self._skip_past_alert(item, self.settings["default_hour"])
        self.changed()
        return item

    def set_done(self, item_id, done):
        """Mark done/undone.  Completing a repeating reminder adds the next one, returned."""
        item = self.get_item(item_id)
        if not item or item["done"] == done:
            return None
        item["done"] = done
        item["completed_at"] = now_str() if done else None
        item["snooze_until"] = None
        nxt = None
        if done and item["repeat"] != "none" and item["due_date"]:
            d = next_occurrence(item, dt.datetime.now())
            nxt = dict(item, id=new_id(), done=False, completed_at=None, created=now_str(),
                       due_date=d.isoformat(), alerted=None, snooze_until=None, images=list(item["images"]),
                       repeat_anchor=item.get("repeat_anchor") or item["due_date"])
            self.items.insert(self.items.index(item) + 1, nxt)
            # The completed one no longer repeats; the copy carries the series on.
            item["repeat"] = "none"
        self.changed()
        return nxt

    def undo_done(self, item_id, created_id=None):
        item = self.get_item(item_id)
        if created_id:
            nxt = self.get_item(created_id)
            if nxt:
                if item:
                    item["repeat"] = nxt["repeat"]
                self.items.remove(nxt)
        if item:
            item["done"] = False
            item["completed_at"] = None
        self.changed()

    def snooze(self, item_id, minutes):
        item = self.get_item(item_id)
        if item:
            item["snooze_until"] = (dt.datetime.now() + dt.timedelta(minutes=minutes)).isoformat(timespec="minutes")
            self.changed()

    def delete_items(self, ids):
        """Remove items; returns [(index, item)] for restore_items()."""
        ids = set(ids)
        removed = [(i, it) for i, it in enumerate(self.items) if it["id"] in ids]
        self.items = [it for it in self.items if it["id"] not in ids]
        self.changed()
        return removed

    def restore_items(self, removed):
        for i, it in sorted(removed, key=lambda x: x[0]):
            if not self.get_item(it["id"]):
                self.items.insert(min(i, len(self.items)), it)
        self.changed()

    def mark_alerted(self, items):
        for it in items:
            it["alerted"] = alert_key(it)
        self.save()

    def pending_alerts(self, now):
        out = []
        for it in self.items:
            at = alert_time(it, self.settings["default_hour"])
            if at and at <= now and it["alerted"] != alert_key(it):
                out.append(it)
        return out

    # -- lists

    def add_list(self, name, color="blue", folder=None):
        lst = {"id": new_id(), "name": name, "color": color, "folder": folder}
        self.lists.append(lst)
        self.changed()
        return lst

    def update_list(self, list_id, **changes):
        lst = self.get_list(list_id)
        if lst:
            lst.update(changes)
            self.changed()

    def delete_list(self, list_id):
        """Returns (index, list, removed_items) for restore_list()."""
        lst = self.get_list(list_id)
        if not lst or len(self.lists) <= 1:
            return None
        index = self.lists.index(lst)
        removed = [(i, it) for i, it in enumerate(self.items) if it["list_id"] == list_id]
        self.lists.remove(lst)
        self.items = [it for it in self.items if it["list_id"] != list_id]
        self.changed()
        return index, lst, removed

    def restore_list(self, saved):
        index, lst, removed = saved
        if not self.get_list(lst["id"]):
            self.lists.insert(min(index, len(self.lists)), lst)
        for i, it in sorted(removed, key=lambda x: x[0]):
            if not self.get_item(it["id"]):
                self.items.insert(min(i, len(self.items)), it)
        self.changed()

    def move_list(self, list_id, delta):
        lst = self.get_list(list_id)
        if not lst:
            return
        i = self.lists.index(lst)
        j = max(0, min(len(self.lists) - 1, i + delta))
        if i != j:
            self.lists.insert(j, self.lists.pop(i))
            self.changed()

    def set_setting(self, key, value):
        if self.settings.get(key) != value:
            self.settings[key] = value
            self.changed()

    # -- views

    def counts(self, now):
        today = now.date()
        c = {"today": 0, "scheduled": 0, "all": 0, "flagged": 0, "completed": 0, "overdue": 0}
        per_list = {lst["id"]: 0 for lst in self.lists}
        for it in self.items:
            if it["done"]:
                c["completed"] += 1
                continue
            c["all"] += 1
            per_list[it["list_id"]] = per_list.get(it["list_id"], 0) + 1
            d = parse_date(it["due_date"])
            if d:
                c["scheduled"] += 1
                if d <= today:
                    c["today"] += 1
            if is_overdue(it, now):
                c["overdue"] += 1
            if it["flagged"]:
                c["flagged"] += 1
        return c, per_list

    def sections(self, view, now, show_completed=False, search=""):
        """[(heading or None, [items])] for a view: ("smart", name) or ("list", id)."""
        today = now.date()

        def sort_key(it):
            d = parse_date(it["due_date"])
            t = parse_time(it["due_time"]) or dt.time.min
            return (d is None, d or dt.date.max, t, -it["priority"], it["created"] or "")

        def done_key(it):
            return it["completed_at"] or ""

        q = search.strip().lower()
        if q:
            hits = [it for it in self.items
                    if q in it["title"].lower() or q in it["notes"].lower()]
            open_ = sorted((it for it in hits if not it["done"]), key=sort_key)
            closed = sorted((it for it in hits if it["done"]), key=done_key, reverse=True)
            return [(None, open_ + closed)] if hits else []

        kind, key = view
        open_items = [it for it in self.items if not it["done"]]
        done_items = [it for it in self.items if it["done"]]

        if kind == "list":
            secs = [(None, sorted((it for it in open_items if it["list_id"] == key), key=sort_key))]
            done = [it for it in done_items if it["list_id"] == key]
        elif key == "today":
            mine = [it for it in open_items if parse_date(it["due_date"]) and parse_date(it["due_date"]) <= today]
            overdue = sorted((it for it in mine if parse_date(it["due_date"]) < today), key=sort_key)
            todays = sorted((it for it in mine if parse_date(it["due_date"]) == today), key=sort_key)
            secs = [("Overdue", overdue), ("Today" if overdue else None, todays)]
            done = [it for it in done_items if (it["completed_at"] or "")[:10] == today.isoformat()]
        elif key == "scheduled":
            mine = sorted((it for it in open_items if it["due_date"]), key=sort_key)
            secs, groups = [], {}
            overdue = [it for it in mine if parse_date(it["due_date"]) < today]
            if overdue:
                secs.append(("Overdue", overdue))
            for it in mine:
                d = parse_date(it["due_date"])
                if d >= today:
                    groups.setdefault(d, []).append(it)
            for d in sorted(groups):
                secs.append((friendly_date(d, today), groups[d]))
            done = [it for it in done_items if it["due_date"]]
        elif key == "all":
            secs = []
            for lst in self.lists:
                mine = sorted((it for it in open_items if it["list_id"] == lst["id"]), key=sort_key)
                if mine:
                    secs.append((lst["name"], mine))
            done = done_items
        elif key == "flagged":
            secs = [(None, sorted((it for it in open_items if it["flagged"]), key=sort_key))]
            done = [it for it in done_items if it["flagged"]]
        else:  # completed
            return [(None, sorted(done_items, key=done_key, reverse=True))] if done_items else []

        secs = [(h, its) for h, its in secs if its]
        if show_completed and done:
            secs.append(("Completed", sorted(done, key=done_key, reverse=True)))
        return secs
