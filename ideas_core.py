"""Data and storage for the to-do lists.  No GTK in here, so it can be tested alone."""

import datetime as dt
import json
import os
import re
import uuid

PRIORITIES = ["None", "Low", "Medium", "High"]

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

DEFAULT_SETTINGS = {
    "show_completed": False,
}

ITEM_DEFAULTS = {
    "title": "",
    "notes": "",
    "list_id": None,
    "priority": 0,
    "done": False,
    "completed_at": None,
    "created": None,
    "claude_sent_at": None,   # last time it was sent to Claude Code
    "claude_run": None,       # id of the Claude Code session working on it; cleared when that finishes
    "images": (),             # file names in Store.images_dir (always replaced, never mutated)
    "teams_id": None,         # "<message id>@<chat id>" of the Teams message it came from
    "teams_url": None,        # link that opens that message in Teams
}

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

TEAMS_LIST = "Teams"          # the list Teams to-dos go to (made when first needed)
TEAMS_CHATS_KEPT = 300        # chats whose "checked up to here" bookmark is remembered


def new_id():
    return uuid.uuid4().hex[:12]


def now_str():
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- quick add


def parse_quick(text, list_names=()):
    """Pull "!", "!!", "!!!" (priority) and "#ListName" out of a quick-add line.

    Returns {"title", "priority", "list_name"}.  A #tag that isn't a list name stays in the title.
    """
    r = {"priority": 0, "list_name": None}
    s = f" {text} "
    m = re.search(r"(?<=\s)(!{1,3})(?=\s)", s)
    if m:
        r["priority"] = len(m.group(1))
        s = s[:m.start()] + " " + s[m.end():]
    for m in re.finditer(r"(?<=\s)#([\w-]+)(?=\s)", s):
        want = re.sub(r"[\s_-]", "", m.group(1).lower())
        name = next((n for n in list_names if re.sub(r"[\s_-]", "", n.lower()) == want), None)
        if name:
            r["list_name"] = name
            s = s[:m.start()] + " " + s[m.end():]
            break
    title = re.sub(r"\s+", " ", s).strip()
    r["title"] = title or text.strip()
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
    """Lists, to-dos and settings in one JSON file, saved after every change."""

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
            self.lists = [{"id": new_id(), "name": "Ideas", "color": "blue", "folder": None}]
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

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 2, "lists": self.lists, "items": self.items,
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
        """Delete image files no to-do uses (left by deletes that can't be undone any more)."""
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
        self.discard_image_file(name)
        self.changed()

    def discard_image_file(self, name):
        if name not in self._referenced_images():
            try:
                os.remove(self.image_path(name))
            except OSError:
                pass

    # -- items

    def add_item(self, title, list_id=None, **fields):
        item = dict(ITEM_DEFAULTS, id=new_id(), title=title, created=now_str(),
                    list_id=list_id or self.lists[0]["id"])
        item.update(fields)
        item["images"] = list(item["images"] or ())
        self.items.append(item)
        self.changed()
        return item

    def update_item(self, item_id, **changes):
        item = self.get_item(item_id)
        if item:
            item.update(changes)
            self.changed()
        return item

    # -- Claude Code sessions
    #
    # A to-do sent to Claude Code gets a run id.  The session leaves "<run>.done" in
    # claude_runs_dir when Claude finishes its turn (a Stop hook), and "<run>.exit" when
    # the claude command ends.  finish_claude_runs() turns those into done / not-working.

    @property
    def claude_runs_dir(self):
        return os.path.join(os.path.dirname(self.path), "claude-runs")

    def start_claude_run(self, item_ids, run):
        """Mark items as being worked on by Claude Code session `run` (replacing any earlier one)."""
        stamp = now_str()
        for iid in item_ids:
            it = self.get_item(iid)
            if it:
                it["claude_sent_at"], it["claude_run"] = stamp, run
        self.changed()

    def finish_claude_runs(self):
        """Apply the markers sessions left.  Returns (items marked done, items whose session
        ended without finishing).  Markers are removed once read."""
        try:
            names = os.listdir(self.claude_runs_dir)
        except OSError:
            return [], []
        finished_runs = {n[:-5] for n in names if n.endswith(".done")}
        exited_runs = {n[:-5] for n in names if n.endswith(".exit")}
        finished, abandoned = [], []
        stamp, touched = now_str(), False
        for it in self.items:
            run = it.get("claude_run")
            if run and (run in finished_runs or run in exited_runs):
                touched = True
            if run in finished_runs:
                it["claude_run"] = None
                if not it["done"]:
                    it["done"], it["completed_at"] = True, stamp
                    finished.append(it)
            elif run in exited_runs:
                it["claude_run"] = None
                if not it["done"]:
                    abandoned.append(it)
        for n in names:
            if n.endswith((".done", ".exit")):
                try:
                    os.remove(os.path.join(self.claude_runs_dir, n))
                except OSError:
                    pass
        if touched:
            self.changed()
        return finished, abandoned

    def set_done(self, item_id, done):
        item = self.get_item(item_id)
        if item and item["done"] != done:
            item["done"] = done
            item["completed_at"] = now_str() if done else None
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

    # -- Microsoft Teams

    def teams_request(self):
        """What the Teams check needs to know: how far earlier checks got in each chat."""
        return {"checked": dict(self.settings.get("teams_checked") or {})}

    def teams_list(self, create=False):
        lst = next((x for x in self.lists if x["name"].strip().lower() == TEAMS_LIST.lower()), None)
        if not lst and create:
            lst = {"id": new_id(), "name": TEAMS_LIST, "color": "purple", "folder": None}
            self.lists.append(lst)
        return lst

    def add_teams_tasks(self, tasks, checked=None):
        """Add the to-dos a Teams check found and remember how far it got.  Returns the new items.

        They all go to the "Teams" list.  A message that already has a to-do isn't added again.
        """
        have = {it["teams_id"] for it in self.items if it.get("teams_id")}
        added = []
        for t in tasks or []:
            title = (t.get("title") or "").strip()
            if not title or (t.get("teams_id") and t["teams_id"] in have):
                continue
            lst = self.teams_list(create=True)
            prio = t.get("priority")
            item = dict(ITEM_DEFAULTS, id=new_id(), title=title, notes=t.get("notes") or "", created=now_str(),
                        list_id=lst["id"], priority=prio if prio in (0, 1, 2, 3) else 0, images=[],
                        teams_id=t.get("teams_id"), teams_url=t.get("url"))
            self.items.append(item)
            added.append(item)
        marks = dict(self.settings.get("teams_checked") or {})
        for chat, msg in (checked or {}).items():
            try:
                if int(msg) > int(marks.get(chat) or 0):
                    marks[chat] = str(msg)
            except (TypeError, ValueError):
                pass
        if len(marks) > TEAMS_CHATS_KEPT:     # keep the chats with the newest messages
            marks = dict(sorted(marks.items(), key=lambda kv: int(kv[1]))[-TEAMS_CHATS_KEPT:])
        self.settings["teams_checked"] = marks
        self.changed()
        return added

    def set_setting(self, key, value):
        if self.settings.get(key) != value:
            self.settings[key] = value
            self.changed()

    # -- views

    def open_counts(self):
        per_list = {lst["id"]: 0 for lst in self.lists}
        for it in self.items:
            if not it["done"]:
                per_list[it["list_id"]] = per_list.get(it["list_id"], 0) + 1
        return per_list

    def sections(self, list_id, show_completed=False, search=""):
        """[(heading or None, [items])] for one list, or for a search across all lists."""
        def done_key(it):
            return it["completed_at"] or ""

        q = search.strip().lower()
        if q:
            hits = [it for it in self.items if q in it["title"].lower() or q in it["notes"].lower()]
            open_ = [it for it in hits if not it["done"]]
            closed = sorted((it for it in hits if it["done"]), key=done_key, reverse=True)
            return [(None, open_ + closed)] if hits else []

        mine = [it for it in self.items if it["list_id"] == list_id]
        secs = [(None, [it for it in mine if not it["done"]])]
        done = [it for it in mine if it["done"]]
        if show_completed and done:
            secs.append(("Completed", sorted(done, key=done_key, reverse=True)))
        return [(h, its) for h, its in secs if its]
