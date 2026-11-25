"""Reminders: lists, to-dos and timed reminders for the GNOME desktop.

Everything lives in one JSON file (~/.local/share/reminders/data.json).  The app keeps
running in the background after its window closes (and starts at login) so reminders
can notify on time; that can be turned off in Preferences.
"""

import datetime as dt
import os
import shutil
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402

import reminders_core as core  # noqa: E402

APP_ID = "io.github.voidrlm.Reminders"
APP_NAME = "Reminders"
VERSION = "1.0"

DATA_FILE = os.environ.get("REMINDERS_DATA") or os.path.join(GLib.get_user_data_dir(), "reminders", "data.json")
AUTOSTART_FILE = os.path.join(GLib.get_user_config_dir(), "autostart", f"{APP_ID}.desktop")
ALERT_SOUND = "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"

SMART_ICONS = {
    "today": "x-office-calendar-symbolic",
    "scheduled": "alarm-symbolic",
    "all": "view-list-symbolic",
    "flagged": "starred-symbolic",
    "completed": "object-select-symbolic",
}
SMART_COLORS = {"today": "blue", "scheduled": "red", "all": "slate", "flagged": "orange", "completed": "green"}


def build_css():
    css = """
    .dot { min-width: 12px; min-height: 12px; border-radius: 99px; }
    .smart-icon { padding: 5px; border-radius: 99px; color: white; }
    .sidebar-count { font-feature-settings: "tnum"; opacity: 0.6; }
    .section-heading { margin: 6px 4px 0 4px; }
    .item-title.done { text-decoration-line: line-through; opacity: 0.5; }
    .item-meta { font-size: 0.88em; }
    .overdue { color: var(--error-color); }
    .due-today { color: var(--accent-color); }
    .priority-mark { font-weight: 800; color: var(--warning-color); }
    .flag-mark { color: #ff7800; }
    .quick-preview { font-size: 0.88em; margin: 2px 6px 0 6px; }
    .notes-view { padding: 10px; background: none; }
    .notes-frame { border-radius: 12px; }
    .color-choice { min-width: 26px; min-height: 26px; padding: 0; border-radius: 99px; }
    .color-choice:checked { box-shadow: 0 0 0 2px var(--window-bg-color), 0 0 0 4px var(--window-fg-color); }
    row.item-row { padding: 0; }
    """
    for name, hexc in core.COLORS:
        css += f"""
        .dot.{name}, .smart-icon.{name}, .color-choice.{name} {{ background-color: {hexc}; }}
        checkbutton.list-{name} check:checked {{ background-color: {hexc}; color: white; }}
        checkbutton.list-{name} check {{ border-color: alpha({hexc}, 0.7); }}
        """
    return css


def use_24h():
    try:
        return Gio.Settings.new("org.gnome.desktop.interface").get_string("clock-format") == "24h"
    except Exception:
        return False


def launcher_path():
    return shutil.which("reminders") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "reminders")


def set_autostart(on):
    if on:
        os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
        with open(AUTOSTART_FILE, "w") as f:
            f.write(
                "[Desktop Entry]\nType=Application\nName=Reminders (background)\n"
                f"Exec={launcher_path()} --background\nIcon={APP_ID}\nNoDisplay=true\n"
                "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\n"
            )
    else:
        try:
            os.remove(AUTOSTART_FILE)
        except FileNotFoundError:
            pass


def spawn(argv, cwd=None):
    """Start a program without waiting for it; GLib reaps it when it exits."""
    GLib.spawn_async(argv, working_directory=cwd,
                     flags=GLib.SpawnFlags.STDOUT_TO_DEV_NULL | GLib.SpawnFlags.STDERR_TO_DEV_NULL)


def play_alert_sound():
    player = shutil.which("pw-play") or shutil.which("paplay")
    if player and os.path.exists(ALERT_SOUND):
        try:
            spawn([player, ALERT_SOUND])
        except GLib.Error:
            pass


def find_claude():
    for p in (shutil.which("claude"), os.path.expanduser("~/.local/bin/claude"),
              os.path.expanduser("~/.claude/local/claude")):
        if p and os.access(p, os.X_OK):
            return p
    return None


# How to run a command in a new window, per terminal.  The command itself cds into the
# folder, so terminals without a working-directory option work too.
TERMINALS = [
    ("ptyxis", ["--new-window", "--"]),
    ("gnome-terminal", ["--"]),
    ("kgx", ["--"]),
    ("kitty", []),
    ("foot", []),
    ("wezterm", ["start", "--"]),
    ("alacritty", ["-e"]),
    ("konsole", ["-e"]),
    ("xterm", ["-e"]),
    ("xdg-terminal-exec", []),
]


def launch_claude(folder, prompt):
    """Open a terminal in `folder` running Claude Code with `prompt`.  Returns an error or None."""
    if not folder or not os.path.isdir(folder):
        return "The linked folder doesn't exist any more"
    claude = find_claude()
    if not claude:
        return "Claude Code (the “claude” command) isn't installed"
    term = next(((shutil.which(t), args) for t, args in TERMINALS if shutil.which(t)), None)
    if not term:
        return "No terminal app found"
    if prompt.startswith("-"):
        prompt = " " + prompt   # so claude doesn't read it as a command-line option
    # After Claude exits, stay in a shell in that folder instead of closing the window.
    script = 'cd "$1" || exit 1; "$2" "$3"; exec "${SHELL:-bash}" -l'
    cmd = [term[0], *term[1], "bash", "-lc", script, "claude-code", folder, claude, prompt]
    try:
        spawn(cmd, cwd=folder)
    except GLib.Error as e:
        return f"Couldn't open the terminal: {e.message}"
    return None


def color_dot(color, size=12):
    d = Gtk.Box(css_classes=["dot", color], valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
    d.set_size_request(size, size)
    return d


def color_picker(current, on_pick):
    """A row of round color buttons; returns the box."""
    box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
    first = None
    for name, _hex in core.COLORS:
        b = Gtk.ToggleButton(css_classes=["color-choice", name], tooltip_text=name.title())
        if first:
            b.set_group(first)
        else:
            first = b
        b.set_active(name == current)
        b.connect("toggled", lambda btn, n=name: btn.get_active() and on_pick(n))
        box.append(b)
    return box


# ===================================================================== application


class RemindersApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.store = None
        self.window = None
        self._held = False

    # -- lifecycle

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        provider = Gtk.CssProvider()
        provider.load_from_string(build_css())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.store = core.Store(DATA_FILE)

        def add(name, cb, param=None, accels=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

        add("open-item", lambda a, v: self._open_item(v.get_string()), "s")
        add("complete", lambda a, v: self._complete_from_notification(v.get_string()), "s")
        add("snooze", lambda a, v: self._snooze_from_notification(v.get_string()), "s")
        add("preferences", lambda *a: self._show_prefs(), accels=["<Control>comma"])
        add("about", lambda *a: self._show_about())
        add("shortcuts", lambda *a: self._show_shortcuts(), accels=["<Control>question"])
        add("quit", lambda *a: self._quit(), accels=["<Control>q"])
        self.set_accels_for_action("window.close", ["<Control>w"])

        self._apply_background(self.store.settings["background"])
        self._tick()
        GLib.timeout_add_seconds(10, self._tick)

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        if "--add" in args:
            i = args.index("--add")
            text = " ".join(args[i + 1:]).strip()
            if text:
                self.quick_add(text, ("smart", "all"))
            return 0
        if "--background" not in args:
            self.activate()
            if "--new" in args and self.window:
                self.window.activate_action("win.new-reminder", None)
        return 0

    def do_activate(self):
        # GTK 4 doesn't emit "destroy" on close, so also check the window is still ours.
        if self.window is None or self.window not in self.get_windows():
            self.window = MainWindow(self)
        self.window.present()

    def window_closed(self, win):
        if self.window is win:
            self.window = None

    def _quit(self):
        if self._held:
            self.release()
            self._held = False
        if self.window:
            self.window.destroy()
        self.quit()

    def _apply_background(self, on):
        if on and not self._held:
            self.hold()
            self._held = True
        elif not on and self._held:
            self.release()
            self._held = False
        try:
            set_autostart(on)
        except OSError as e:
            print("autostart:", e, file=sys.stderr)

    # -- alerts

    def _tick(self):
        now = dt.datetime.now()
        due = self.store.pending_alerts(now)
        if due:
            self.store.mark_alerted(due)
            if len(due) > 4:
                self._notify_summary(due)
            else:
                for it in due:
                    self._notify(it)
            if self.store.settings["sound"]:
                play_alert_sound()
        if self.window:
            self.window.maybe_refresh(now)
        return GLib.SOURCE_CONTINUE

    def _notify(self, item):
        today = dt.date.today()
        n = Gio.Notification.new(item["title"] or "Reminder")
        body = []
        due = core.format_due(item, today, use_24h())
        lst = self.store.get_list(item["list_id"])
        if due:
            body.append(due + (f" · {lst['name']}" if lst else ""))
        if item["notes"].strip():
            body.append(item["notes"].strip()[:200])
        n.set_body("\n".join(body) or None)
        n.set_priority(Gio.NotificationPriority.HIGH)
        n.set_icon(Gio.ThemedIcon.new(APP_ID))
        target = GLib.Variant("s", item["id"])
        n.set_default_action_and_target("app.open-item", target)
        n.add_button_with_target("Mark as Done", "app.complete", target)
        n.add_button_with_target(f"Snooze {self.store.settings['snooze_minutes']} min", "app.snooze", target)
        self.send_notification(f"item-{item['id']}", n)

    def _notify_summary(self, items):
        n = Gio.Notification.new(f"{len(items)} reminders are due")
        n.set_body("\n".join("• " + it["title"] for it in items[:6]) + ("\n…" if len(items) > 6 else ""))
        n.set_priority(Gio.NotificationPriority.HIGH)
        n.set_icon(Gio.ThemedIcon.new(APP_ID))
        n.set_default_action("app.open-item::")
        self.send_notification("summary", n)

    def withdraw(self, item_id):
        self.withdraw_notification(f"item-{item_id}")

    def send_test_notification(self):
        n = Gio.Notification.new("Reminders is working")
        n.set_body("This is how your reminders will appear.")
        n.set_icon(Gio.ThemedIcon.new(APP_ID))
        self.send_notification("test", n)
        if self.store.settings["sound"]:
            play_alert_sound()

    def _open_item(self, item_id):
        self.activate()
        if item_id and self.store.get_item(item_id):
            GLib.idle_add(lambda: self.window and self.window.open_item(item_id) and False)
        elif self.window:
            self.window.select_view(("smart", "today"))

    def _complete_from_notification(self, item_id):
        self.store.set_done(item_id, True)
        self.withdraw(item_id)

    def _snooze_from_notification(self, item_id):
        self.store.snooze(item_id, self.store.settings["snooze_minutes"])
        self.withdraw(item_id)

    # -- shared actions

    def quick_add(self, text, view):
        """Add a reminder typed in the quick-add box (or --add) to `view`."""
        s = self.store
        fields = {}
        list_id = view[1] if view[0] == "list" else None
        title = text.strip()
        if s.settings["smart_parse"]:
            p = core.parse_quick(text, dt.datetime.now(), [lst["name"] for lst in s.lists])
            title = p["title"]
            if p["due_date"]:
                fields["due_date"] = p["due_date"].isoformat()
            if p["due_time"]:
                fields["due_time"] = p["due_time"].strftime("%H:%M")
            fields.update(repeat=p["repeat"], priority=p["priority"])
            if p["flagged"]:
                fields["flagged"] = True
            if p["list_name"]:
                list_id = s.list_by_name(p["list_name"])["id"]
        if view == ("smart", "today") and "due_date" not in fields:
            fields["due_date"] = dt.date.today().isoformat()
        if view == ("smart", "scheduled") and "due_date" not in fields:
            fields["due_date"] = dt.date.today().isoformat()
        if view == ("smart", "flagged"):
            fields["flagged"] = True
        return s.add_item(title, list_id, **fields)

    def _show_prefs(self):
        self.activate()
        PrefsDialog(self).present(self.window)

    def _show_shortcuts(self):
        self.activate()
        d = Adw.ShortcutsDialog()
        for title, items in (
            ("Reminders", [("New reminder", "<Control>n"), ("Search", "<Control>f"),
                           ("Show or hide completed", "<Control>h")]),
            ("Lists", [("New list", "<Control><Shift>n"), ("Today", "<Control>1"), ("Scheduled", "<Control>2"),
                       ("All", "<Control>3"), ("Flagged", "<Control>4"), ("Completed", "<Control>5")]),
            ("General", [("Preferences", "<Control>comma"), ("Keyboard shortcuts", "<Control>question"),
                         ("Close window", "<Control>w"), ("Quit (stops reminders)", "<Control>q")]),
        ):
            sec = Adw.ShortcutsSection(title=title)
            for label, accel in items:
                sec.add(Adw.ShortcutsItem.new(label, accel))
            d.add(sec)
        d.present(self.window)

    def _show_about(self):
        about = Adw.AboutDialog(
            application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
            developer_name="Arjun",
            comments="Lists, to-dos and reminders that notify you on time.",
            website="https://github.com/voidrlm/reminders",
        )
        about.present(self.window)


# ===================================================================== main window


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        st = app.store.settings
        super().__init__(application=app, title=APP_NAME,
                         default_width=st.get("window_width", 980), default_height=st.get("window_height", 680))
        if st.get("window_maximized"):
            self.maximize()
        self.set_size_request(360, 400)
        self.app = app
        self.store = app.store
        self.view = ("smart", "today")
        self._building = False
        self._signature = None
        self._ctx_item = None
        self._ctx_list = None

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=230, max_sidebar_width=300)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self._build_actions()
        self._build_sidebar()
        self._build_content()
        self.store.connect(self._on_store_changed)
        self.connect("close-request", self._on_close_request)
        self.refresh()

    def _on_close_request(self, _win):
        st = self.store.settings
        st["window_maximized"] = self.is_maximized()
        if not self.is_maximized():
            st["window_width"], st["window_height"] = self.get_width(), self.get_height()
        self.store.save()
        self.store.disconnect(self._on_store_changed)
        self.app.window_closed(self)
        return False

    # -- actions

    def _build_actions(self):
        def add(name, cb, param=None, accels=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.app.set_accels_for_action(f"win.{name}", accels)

        add("new-reminder", lambda *a: self._focus_quick_add(), accels=["<Control>n"])
        add("new-list", lambda *a: self.edit_list_dialog(None), accels=["<Control><Shift>n", "<Control>l"])
        add("search", lambda *a: self.search_bar.set_search_mode(True), accels=["<Control>f"])
        add("rename-list", lambda a, v: self.edit_list_dialog(v.get_string()), "s")
        add("delete-list", lambda a, v: self.delete_list(v.get_string()), "s")
        add("move-list-up", lambda a, v: self.store.move_list(v.get_string(), -1), "s")
        add("move-list-down", lambda a, v: self.store.move_list(v.get_string(), 1), "s")
        add("clear-completed", lambda *a: self.clear_completed())
        add("item-open", lambda a, v: self.open_item(v.get_string()), "s")
        add("item-flag", lambda a, v: self._toggle_flag(v.get_string()), "s")
        add("item-today", lambda a, v: self._set_due_today(v.get_string()), "s")
        add("item-delete", lambda a, v: self.delete_item(v.get_string()), "s")
        add("item-claude", lambda a, v: self.send_to_claude([v.get_string()]), "s")
        add("list-claude", lambda a, v: self.send_list_to_claude(v.get_string()), "s")
        show = Gio.SimpleAction.new_stateful("show-completed", None,
                                             GLib.Variant("b", self.store.settings["show_completed"]))
        show.connect("change-state", self._on_show_completed)
        self.add_action(show)
        self.app.set_accels_for_action("win.show-completed", ["<Control>h"])
        for i, (key, _label) in enumerate(core.SMART_VIEWS, 1):
            a = Gio.SimpleAction.new(f"view-{key}", None)
            a.connect("activate", lambda *_a, k=key: self.select_view(("smart", k)))
            self.add_action(a)
            self.app.set_accels_for_action(f"win.view-{key}", [f"<Control>{i}"])

    def _on_show_completed(self, action, value):
        action.set_state(value)
        self.store.set_setting("show_completed", value.get_boolean())

    # -- sidebar

    def _build_sidebar(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("New List", "win.new-list")
        sec = Gio.Menu()
        sec.append("Preferences", "app.preferences")
        sec.append("Keyboard Shortcuts", "app.shortcuts")
        sec.append("About Reminders", "app.about")
        menu.append_section(None, sec)
        q = Gio.Menu()
        q.append("Quit", "app.quit")
        menu.append_section(None, q)
        hb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main Menu",
                                   primary=True))
        tv.add_top_bar(hb)

        self.sidebar_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.sidebar_list.connect("row-selected", self._on_sidebar_selected)
        self.sidebar_list.connect("row-activated", lambda lb, row: self.split.set_show_content(True))
        right = Gtk.GestureClick(button=3)
        right.connect("pressed", self._on_sidebar_right_click)
        self.sidebar_list.add_controller(right)

        self.sidebar_scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                   child=self.sidebar_list)
        tv.set_content(self.sidebar_scroller)

        add_btn = Gtk.Button(css_classes=["flat"], margin_start=6, margin_end=6, margin_bottom=6, margin_top=6)
        add_btn.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="New List"))
        add_btn.set_action_name("win.new-list")
        tv.add_bottom_bar(add_btn)

        self.list_popover = Gtk.PopoverMenu(has_arrow=False, halign=Gtk.Align.START)
        self.list_popover.set_parent(self.sidebar_scroller)
        self.split.set_sidebar(Adw.NavigationPage(title=APP_NAME, child=tv))

    def _sidebar_row(self, view, icon_widget, label, count):
        row = Gtk.ListBoxRow()
        row.view = view
        box = Gtk.Box(spacing=12, margin_top=4, margin_bottom=4)
        box.append(icon_widget)
        lbl = Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        box.append(lbl)
        cnt = Gtk.Label(label=str(count) if count else "", css_classes=["sidebar-count"])
        box.append(cnt)
        row.set_child(box)
        return row

    def _rebuild_sidebar(self, now):
        counts, per_list = self.store.counts(now)
        self._building = True
        self.sidebar_list.remove_all()
        selected = None
        for key, label in core.SMART_VIEWS:
            icon = Gtk.Image(icon_name=SMART_ICONS[key], css_classes=["smart-icon", SMART_COLORS[key]], pixel_size=14)
            n = counts[key] if key != "completed" else 0
            row = self._sidebar_row(("smart", key), icon, label, n)
            if key == "today" and counts["overdue"]:
                row.get_child().get_last_child().add_css_class("overdue")
                row.get_child().get_last_child().set_opacity(1)
            self.sidebar_list.append(row)
            if row.view == self.view:
                selected = row
        head = Gtk.ListBoxRow(selectable=False, activatable=False)
        head.set_child(Gtk.Label(label="My Lists", xalign=0, css_classes=["heading", "dim-label"],
                                 margin_top=14, margin_bottom=2, margin_start=6))
        head.view = None
        self.sidebar_list.append(head)
        for lst in self.store.lists:
            row = self._sidebar_row(("list", lst["id"]), color_dot(lst["color"], 14), lst["name"],
                                    per_list.get(lst["id"], 0))
            if lst.get("folder"):
                row.get_child().insert_child_after(
                    Gtk.Image(icon_name="folder-symbolic", pixel_size=12, css_classes=["dim-label"],
                              tooltip_text=core.short_path(lst["folder"])),
                    row.get_child().get_first_child().get_next_sibling())
            self.sidebar_list.append(row)
            if row.view == self.view:
                selected = row
        if selected is None:
            self.view = ("smart", "today")
            selected = self.sidebar_list.get_row_at_index(0)
        self.sidebar_list.select_row(selected)
        self._building = False

    def _on_sidebar_selected(self, _lb, row):
        if self._building or row is None or row.view is None:
            return
        if row.view != self.view:
            self.view = row.view
            if self.search_bar.get_search_mode():
                self.search_bar.set_search_mode(False)
            self.refresh(sidebar=False, scroll_top=True)

    def _on_sidebar_right_click(self, gesture, _n, x, y):
        row = self.sidebar_list.get_row_at_y(int(y))
        if not row or not row.view or row.view[0] != "list":
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.list_popover.set_menu_model(self._list_menu(row.view[1]))
        ok, p = self.sidebar_list.compute_point(self.sidebar_scroller, Graphene.Point().init(x, y))
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(p.x), int(p.y), 1, 1
        self.list_popover.set_pointing_to(rect)
        self.list_popover.popup()

    def _list_menu(self, list_id):
        menu = Gio.Menu()
        lst = self.store.get_list(list_id)
        if lst and lst.get("folder"):
            cc = Gio.Menu()
            cc.append("Send Open To-dos to Claude Code", f"win.list-claude::{list_id}")
            menu.append_section(None, cc)
        menu.append("Edit List…", f"win.rename-list::{list_id}")
        order = Gio.Menu()
        order.append("Move Up", f"win.move-list-up::{list_id}")
        order.append("Move Down", f"win.move-list-down::{list_id}")
        menu.append_section(None, order)
        danger = Gio.Menu()
        danger.append("Delete List…", f"win.delete-list::{list_id}")
        menu.append_section(None, danger)
        return menu

    # -- content

    def _build_content(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search (Ctrl+F)")
        hb.pack_end(self.search_btn)
        self.view_menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="List Options")
        hb.pack_end(self.view_menu_btn)
        self.window_title = Adw.WindowTitle(title="Today")
        hb.set_title_widget(self.window_title)
        tv.add_top_bar(hb)

        self.search_bar = Gtk.SearchBar(show_close_button=False)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search all reminders", hexpand=True)
        clamp = Adw.Clamp(maximum_size=720, child=self.search_entry)
        self.search_bar.set_child(clamp)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.search_bar.bind_property("search-mode-enabled", self.search_btn, "active",
                                      GObject.BindingFlags.BIDIRECTIONAL)
        self.search_entry.connect("search-changed", lambda *_: self.refresh(sidebar=False, scroll_top=True))
        self.search_bar.connect("notify::search-mode-enabled",
                                lambda *_: not self.search_bar.get_search_mode() and self.search_entry.set_text(""))
        tv.add_top_bar(self.search_bar)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Quick add
        self.quick_entry = Gtk.Entry(placeholder_text="Add a reminder…  (try “Call mom tomorrow 6pm”)",
                                     primary_icon_name="list-add-symbolic", hexpand=True)
        self.quick_entry.connect("activate", self._on_quick_add)
        self.quick_entry.connect("changed", self._on_quick_changed)
        self.quick_preview = Gtk.Label(xalign=0, css_classes=["quick-preview", "dim-label"], visible=False,
                                       ellipsize=Pango.EllipsizeMode.END)
        qbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, margin_bottom=6,
                       margin_start=12, margin_end=12)
        qbox.append(self.quick_entry)
        qbox.append(self.quick_preview)
        self.quick_box = Adw.Clamp(maximum_size=720, child=qbox)
        body.append(self.quick_box)

        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.items_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6,
                                 margin_bottom=24, margin_start=12, margin_end=12)
        self.items_clamp = Adw.Clamp(maximum_size=720, child=self.items_box)
        self.empty = Adw.StatusPage(vexpand=True)
        self.content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.add_named(self.items_clamp, "items")
        self.content_stack.add_named(self.empty, "empty")
        self.scroller.set_child(self.content_stack)
        body.append(self.scroller)

        self.item_popover = Gtk.PopoverMenu(has_arrow=False, halign=Gtk.Align.START)
        self.item_popover.set_parent(self.items_box)

        tv.set_content(body)
        self.content_page = Adw.NavigationPage(title="Today", child=tv)
        self.split.set_content(self.content_page)

    def _focus_quick_add(self):
        self.split.set_show_content(True)
        if self.view == ("smart", "completed"):
            self.select_view(("smart", "today"))
        self.quick_entry.grab_focus()

    def _on_quick_changed(self, entry):
        text = entry.get_text()
        if not text.strip() or not self.store.settings["smart_parse"]:
            self.quick_preview.set_visible(False)
            return
        p = core.parse_quick(text, dt.datetime.now(), [lst["name"] for lst in self.store.lists])
        bits = []
        if p["due_date"]:
            bits.append("📅 " + core.format_due({"due_date": p["due_date"].isoformat(),
                                                "due_time": p["due_time"] and p["due_time"].strftime("%H:%M")},
                                               dt.date.today(), use_24h()))
        if p["repeat"] != "none":
            bits.append("🔁 " + core.REPEAT_LABELS[p["repeat"]])
        if p["priority"]:
            bits.append("❗ " + core.PRIORITIES[p["priority"]])
        if p["flagged"]:
            bits.append("⭐ Flagged")
        if p["list_name"]:
            bits.append("→ " + p["list_name"])
        self.quick_preview.set_label(f"“{p['title']}”   " + "   ".join(bits) if bits else "")
        self.quick_preview.set_visible(bool(bits))

    def _on_quick_add(self, entry):
        text = entry.get_text().strip()
        if not text:
            return
        view = self.view if self.view != ("smart", "completed") else ("smart", "all")
        item = self.app.quick_add(text, view)
        entry.set_text("")
        # If the new reminder doesn't belong in this view (e.g. #otherlist), say where it went.
        if not any(it["id"] == item["id"] for _h, its in self.store.sections(self.view, dt.datetime.now(), True)
                   for it in its):
            lst = self.store.get_list(item["list_id"])
            self.toast(f"Added to {lst['name']}" if lst else "Added")

    # -- rendering

    def _on_store_changed(self):
        self.refresh()

    def maybe_refresh(self, now):
        sig = (now.date(), tuple(it["id"] for it in self.store.items if core.is_overdue(it, now)))
        if sig != self._signature:
            self.refresh()

    def refresh(self, sidebar=True, scroll_top=False):
        now = dt.datetime.now()
        self._signature = (now.date(), tuple(it["id"] for it in self.store.items if core.is_overdue(it, now)))
        if sidebar:
            self._rebuild_sidebar(now)
        adj = self.scroller.get_vadjustment()
        pos = 0 if scroll_top else adj.get_value()
        self._render_items(now)
        GLib.idle_add(lambda: adj.set_value(pos) and False)

    def _view_title(self):
        if self.view[0] == "smart":
            return dict(core.SMART_VIEWS)[self.view[1]]
        lst = self.store.get_list(self.view[1])
        return lst["name"] if lst else APP_NAME

    def _render_items(self, now):
        search = self.search_entry.get_text()
        title = f"Search: {search}" if search.strip() else self._view_title()
        self.content_page.set_title(title)
        self.window_title.set_title(title)
        lst = self.store.get_list(self.view[1]) if self.view[0] == "list" and not search.strip() else None
        self.window_title.set_subtitle(core.short_path(lst["folder"]) if lst and lst.get("folder") else "")
        is_completed_view = self.view == ("smart", "completed")
        self.quick_box.set_visible(not is_completed_view and not search.strip())

        menu = Gio.Menu()
        if not is_completed_view:
            menu.append("Show Completed", "win.show-completed")
        menu.append("Clear Completed…", "win.clear-completed")
        if self.view[0] == "list":
            menu.append_section(None, self._list_menu(self.view[1]))
        self.view_menu_btn.set_menu_model(menu)

        child = self.items_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            if child is not self.item_popover:
                self.items_box.remove(child)
            child = nxt

        sections = self.store.sections(self.view, now, self.store.settings["show_completed"], search)
        if not sections:
            self._show_empty(search)
            return
        self.content_stack.set_visible_child_name("items")
        show_list = self.view[0] == "smart" and self.view[1] != "all" or bool(search.strip())
        for heading, items in sections:
            if heading:
                lbl = Gtk.Label(label=heading, xalign=0, css_classes=["heading", "section-heading"])
                if heading == "Overdue":
                    lbl.add_css_class("overdue")
                self.items_box.append(lbl)
            lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
            lb.connect("row-activated", lambda _lb, row: self.open_item(row.item_id))
            for it in items:
                lb.append(self._item_row(it, now, show_list))
            self.items_box.append(lb)

    def _show_empty(self, search):
        self.content_stack.set_visible_child_name("empty")
        if search.strip():
            self.empty.set_icon_name("system-search-symbolic")
            self.empty.set_title("No Results")
            self.empty.set_description("No reminders match your search.")
            return
        key = self.view[1] if self.view[0] == "smart" else "list"
        icon, title, desc = {
            "today": ("x-office-calendar-symbolic", "Nothing Due Today", "Add something above, or enjoy the free time."),
            "scheduled": ("alarm-symbolic", "Nothing Scheduled", "Reminders with a date show up here."),
            "all": ("view-list-symbolic", "No Reminders", "Type above to add your first one."),
            "flagged": ("starred-symbolic", "No Flagged Reminders", "Flag the important ones to find them here."),
            "completed": ("object-select-symbolic", "Nothing Completed Yet", "Ticked-off reminders are kept here."),
            "list": ("checkbox-checked-symbolic", "All Done", "This list is empty. Add a reminder above."),
        }[key]
        self.empty.set_icon_name(icon)
        self.empty.set_title(title)
        self.empty.set_description(desc)

    def _item_row(self, it, now, show_list):
        today = now.date()
        lst = self.store.get_list(it["list_id"]) or {"name": "", "color": "blue"}
        row = Gtk.ListBoxRow(css_classes=["item-row"])
        row.item_id = it["id"]
        box = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)

        check = Gtk.CheckButton(active=it["done"], valign=Gtk.Align.CENTER,
                                css_classes=["selection-mode", f"list-{lst['color']}"],
                                tooltip_text="Mark as not done" if it["done"] else "Mark as done")
        check.connect("toggled", self._on_check, it["id"])
        box.append(check)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True, valign=Gtk.Align.CENTER)
        title_box = Gtk.Box(spacing=6)
        if it["priority"]:
            title_box.append(Gtk.Label(label="!" * it["priority"], css_classes=["priority-mark"], valign=Gtk.Align.START))
        title = Gtk.Label(label=it["title"], xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          hexpand=True, css_classes=["item-title"] + (["done"] if it["done"] else []))
        title_box.append(title)
        text.append(title_box)

        meta = Gtk.Box(spacing=8, css_classes=["item-meta"])
        due = core.format_due(it, today, use_24h())
        if due:
            lbl = Gtk.Label(label=due, xalign=0)
            if not it["done"]:
                if core.is_overdue(it, now):
                    lbl.add_css_class("overdue")
                elif core.parse_date(it["due_date"]) == today:
                    lbl.add_css_class("due-today")
                else:
                    lbl.add_css_class("dim-label")
            else:
                lbl.add_css_class("dim-label")
            meta.append(lbl)
        if it["repeat"] != "none":
            meta.append(Gtk.Image(icon_name="media-playlist-repeat-symbolic", pixel_size=12,
                                  css_classes=["dim-label"], tooltip_text=core.REPEAT_LABELS[it["repeat"]]))
        if it.get("snooze_until") and not it["done"]:
            snz = core.parse_datetime(it["snooze_until"])
            if snz and snz > now:
                meta.append(Gtk.Label(label="Snoozed to " + core.format_time(snz.time(), use_24h()),
                                      css_classes=["dim-label"]))
        if show_list:
            lb = Gtk.Box(spacing=5)
            lb.append(color_dot(lst["color"], 8))
            lb.append(Gtk.Label(label=lst["name"], css_classes=["dim-label"]))
            meta.append(lb)
        notes = it["notes"].strip().splitlines()
        if notes:
            meta.append(Gtk.Label(label=notes[0], css_classes=["dim-label"], ellipsize=Pango.EllipsizeMode.END,
                                  xalign=0, hexpand=True))
        if meta.get_first_child():
            text.append(meta)
        box.append(text)

        if it.get("claude_sent_at"):
            sent = Gtk.Box(spacing=4)
            sent.append(Gtk.Image(icon_name="utilities-terminal-symbolic", pixel_size=12))
            sent.append(Gtk.Label(label="Sent to Claude"))
            sent.add_css_class("dim-label")
            meta.prepend(sent)
            if not text.get_last_child() is meta:
                text.append(meta)

        if it["flagged"]:
            box.append(Gtk.Image(icon_name="starred-symbolic", css_classes=["flag-mark"], valign=Gtk.Align.CENTER,
                                 tooltip_text="Flagged"))
        if lst.get("folder") and not it["done"]:
            send = Gtk.Button(icon_name="utilities-terminal-symbolic", valign=Gtk.Align.CENTER,
                              css_classes=["flat", "circular"],
                              tooltip_text=f"Send to Claude Code in {core.short_path(lst['folder'])}")
            send.set_action_name("win.item-claude")
            send.set_action_target_value(GLib.Variant("s", it["id"]))
            box.append(send)
        row.set_child(box)

        right = Gtk.GestureClick(button=3)
        right.connect("pressed", self._on_item_right_click, row)
        row.add_controller(right)
        lp = Gtk.GestureLongPress()
        lp.connect("pressed", lambda g, x, y, r=row: self._on_item_right_click(g, 1, x, y, r))
        row.add_controller(lp)
        return row

    def _on_item_right_click(self, gesture, _n, x, y, row):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        it = self.store.get_item(row.item_id)
        if not it:
            return
        iid = it["id"]
        menu = Gio.Menu()
        menu.append("Edit Details…", f"win.item-open::{iid}")
        lst = self.store.get_list(it["list_id"])
        if lst and lst.get("folder"):
            menu.append("Send to Claude Code", f"win.item-claude::{iid}")
        menu.append("Unflag" if it["flagged"] else "Flag", f"win.item-flag::{iid}")
        if not it["done"] and it["due_date"] != dt.date.today().isoformat():
            menu.append("Due Today", f"win.item-today::{iid}")
        d = Gio.Menu()
        d.append("Delete", f"win.item-delete::{iid}")
        menu.append_section(None, d)
        self.item_popover.set_menu_model(menu)
        ok, p = row.compute_point(self.items_box, Graphene.Point().init(x, y))
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(p.x), int(p.y), 1, 1
        self.item_popover.set_pointing_to(rect)
        self.item_popover.popup()

    # -- item operations

    def _on_check(self, check, item_id):
        it = self.store.get_item(item_id)
        if not it or it["done"] == check.get_active():
            return
        done = check.get_active()
        title = it["title"]

        def apply():
            if check.get_active() != done:   # changed its mind before the delay ran out
                return False
            nxt = self.store.set_done(item_id, done)
            if done:
                self.app.withdraw(item_id)
                msg = f"Completed “{title}”"
                if nxt:
                    msg += f" · next {core.format_due(nxt, dt.date.today(), use_24h())}"
                self.toast(msg, lambda: self.store.undo_done(item_id, nxt and nxt["id"]))
            return False
        # Let the tick animation play before the row moves away.
        GLib.timeout_add(350 if done else 0, apply)

    def toast(self, text, undo=None):
        t = Adw.Toast(title=text, timeout=5)
        t.set_use_markup(False)
        if undo:
            t.set_button_label("Undo")
            t.connect("button-clicked", lambda *_: undo())
        self.toasts.add_toast(t)

    def send_to_claude(self, item_ids, list_id=None):
        items = [it for it in (self.store.get_item(i) for i in item_ids) if it]
        if not items:
            return
        lst = self.store.get_list(list_id or items[0]["list_id"])
        if not lst or not lst.get("folder"):
            self.toast("Link a folder to this list first (Edit List…)")
            return
        err = launch_claude(lst["folder"], core.claude_prompt(items, lst["name"]))
        if err:
            self.toast(err)
            return
        stamp = core.now_str()
        for it in items:
            it["claude_sent_at"] = stamp
        self.store.changed()
        what = f"“{items[0]['title']}”" if len(items) == 1 else f"{len(items)} to-dos"
        self.toast(f"Sent {what} to Claude Code in {os.path.basename(lst['folder'].rstrip(os.sep))}")

    def send_list_to_claude(self, list_id):
        now = dt.datetime.now()
        secs = self.store.sections(("list", list_id), now)
        ids = [it["id"] for _h, its in secs for it in its if not it["done"]]
        if not ids:
            self.toast("No open to-dos in this list")
            return
        self.send_to_claude(ids, list_id)

    def _toggle_flag(self, item_id):
        it = self.store.get_item(item_id)
        if it:
            self.store.update_item(item_id, flagged=not it["flagged"])

    def _set_due_today(self, item_id):
        self.store.update_item(item_id, due_date=dt.date.today().isoformat())

    def delete_item(self, item_id):
        it = self.store.get_item(item_id)
        if not it:
            return
        self.app.withdraw(item_id)
        removed = self.store.delete_items([item_id])
        self.toast(f"Deleted “{it['title']}”", lambda: self.store.restore_items(removed))

    def open_item(self, item_id):
        it = self.store.get_item(item_id)
        if it:
            ItemDialog(self, it).present(self)
        return False

    def select_view(self, view):
        self.view = view
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)
        self.refresh(scroll_top=True)
        self.split.set_show_content(True)

    def clear_completed(self):
        if self.view[0] == "list":
            ids = [it["id"] for it in self.store.items if it["done"] and it["list_id"] == self.view[1]]
            where = f"in “{self._view_title()}”"
        else:
            ids = [it["id"] for it in self.store.items if it["done"]]
            where = "in all lists"
        if not ids:
            self.toast("No completed reminders to clear")
            return
        d = Adw.AlertDialog(heading="Clear Completed?",
                            body=f"Delete {len(ids)} completed reminder{'s' if len(ids) != 1 else ''} {where}.")
        d.add_response("cancel", "Cancel")
        d.add_response("clear", "Clear")
        d.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, resp):
            if resp == "clear":
                removed = self.store.delete_items(ids)
                self.toast(f"Cleared {len(ids)} reminder{'s' if len(ids) != 1 else ''}",
                           lambda: self.store.restore_items(removed))
        d.connect("response", done)
        d.present(self)

    # -- list operations

    def edit_list_dialog(self, list_id):
        lst = self.store.get_list(list_id) if list_id else None
        d = Adw.AlertDialog(heading="Edit List" if lst else "New List")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        entry = Gtk.Entry(text=lst["name"] if lst else "", placeholder_text="List name", activates_default=True)
        box.append(entry)
        chosen = {"color": lst["color"] if lst else core.COLOR_NAMES[len(self.store.lists) % len(core.COLOR_NAMES)],
                  "folder": lst.get("folder") if lst else None}
        box.append(color_picker(chosen["color"], lambda c: chosen.__setitem__("color", c)))

        # Optional project folder: its to-dos can then be sent to Claude Code.
        fbox = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        fbtn = Gtk.Button(css_classes=["pill"])
        fclear = Gtk.Button(icon_name="edit-clear-symbolic", css_classes=["flat", "circular"],
                            tooltip_text="Unlink folder", valign=Gtk.Align.CENTER)
        fbox.append(fbtn)
        fbox.append(fclear)

        def sync_folder():
            f = chosen["folder"]
            content = Adw.ButtonContent(icon_name="folder-symbolic",
                                        label=os.path.basename(f.rstrip(os.sep)) or f if f else "Link a Folder…",
                                        tooltip_text=core.short_path(f) if f else
                                        "Link a project folder to send this list's to-dos to Claude Code")
            fbtn.set_child(content)
            fclear.set_visible(bool(f))

        def pick(*_):
            fd = Gtk.FileDialog(title="Choose the Project Folder", modal=True)
            if chosen["folder"] and os.path.isdir(chosen["folder"]):
                fd.set_initial_folder(Gio.File.new_for_path(chosen["folder"]))

            def picked(dlg, res):
                try:
                    f = dlg.select_folder_finish(res)
                except GLib.Error:
                    return
                if f and f.get_path():
                    chosen["folder"] = f.get_path()
                    sync_folder()
            fd.select_folder(self, None, picked)
        fbtn.connect("clicked", pick)
        fclear.connect("clicked", lambda *_: (chosen.__setitem__("folder", None), sync_folder()))
        sync_folder()
        box.append(fbox)
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", "Save" if lst else "Create")
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.set_close_response("cancel")
        d.set_response_enabled("ok", bool(entry.get_text().strip()))
        entry.connect("changed", lambda e: d.set_response_enabled("ok", bool(e.get_text().strip())))

        def done(_d, resp):
            name = entry.get_text().strip()
            if resp != "ok" or not name:
                return
            if lst:
                self.store.update_list(lst["id"], name=name, color=chosen["color"], folder=chosen["folder"])
            else:
                new = self.store.add_list(name, chosen["color"], chosen["folder"])
                self.select_view(("list", new["id"]))
        d.connect("response", done)
        d.present(self)
        entry.grab_focus()

    def delete_list(self, list_id):
        lst = self.store.get_list(list_id)
        if not lst:
            return
        if len(self.store.lists) <= 1:
            self.toast("You need at least one list")
            return
        n = sum(1 for it in self.store.items if it["list_id"] == list_id)
        d = Adw.AlertDialog(heading=f"Delete “{lst['name']}”?",
                            body=f"This also deletes its {n} reminder{'s' if n != 1 else ''}." if n else
                            "The list is empty.")
        d.add_response("cancel", "Cancel")
        d.add_response("delete", "Delete")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, resp):
            if resp != "delete":
                return
            for it in self.store.items:
                if it["list_id"] == list_id:
                    self.app.withdraw(it["id"])
            if self.view == ("list", list_id):
                self.view = ("smart", "today")
            saved = self.store.delete_list(list_id)
            if saved:
                self.toast(f"Deleted “{lst['name']}”", lambda: self.store.restore_list(saved))
        d.connect("response", done)
        d.present(self)


# ===================================================================== item details


class ItemDialog(Adw.Dialog):
    def __init__(self, win, item):
        super().__init__(title="Details", content_width=480, content_height=720)
        self.win = win
        self.store = win.store
        self.item_id = item["id"]
        self._deleted = False
        self._ready = False
        today = dt.date.today()
        due_d = core.parse_date(item["due_date"])
        due_t = core.parse_time(item["due_time"])

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        done_btn = Gtk.Button(label="Done", css_classes=["suggested-action"])
        done_btn.connect("clicked", lambda *_: self.close())
        hb.pack_end(done_btn)
        tv.add_top_bar(hb)
        page = Adw.PreferencesPage()
        tv.set_content(page)
        self.set_child(tv)
        self.set_default_widget(done_btn)

        g = Adw.PreferencesGroup()
        self.title_row = Adw.EntryRow(title="Title", text=item["title"])
        self.title_row.connect("entry-activated", lambda *_: self.close())
        g.add(self.title_row)
        page.add(g)

        gn = Adw.PreferencesGroup(title="Notes")
        self.notes = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, css_classes=["notes-view"], accepts_tab=False)
        self.notes.get_buffer().set_text(item["notes"])
        frame = Gtk.Frame(child=self.notes, css_classes=["card", "notes-frame"])
        self.notes.set_size_request(-1, 90)
        gn.add(frame)
        page.add(gn)

        # Date & time
        gd = Adw.PreferencesGroup(title="Date &amp; Time")
        self.date_row = Adw.ExpanderRow(title="Date", show_enable_switch=True, enable_expansion=bool(due_d),
                                        expanded=False)
        self.calendar = Gtk.Calendar(margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self._set_calendar(due_d or today)
        self.calendar.connect("day-selected", lambda *_: self._sync_subtitles())
        quick = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, margin_top=8, margin_bottom=4)
        for label, delta in (("Today", 0), ("Tomorrow", 1), ("Next Week", None)):
            b = Gtk.Button(label=label, css_classes=["pill", "small"])
            b.connect("clicked", self._quick_date, delta)
            quick.append(b)
        self.date_row.add_row(quick)
        self.date_row.add_row(self.calendar)
        gd.add(self.date_row)

        self.time_row = Adw.ExpanderRow(title="Time", show_enable_switch=True, enable_expansion=bool(due_t))
        tbox = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=10)
        h24 = use_24h()
        self.hour = Gtk.SpinButton.new_with_range(0 if h24 else 1, 23 if h24 else 12, 1)
        self.minute = Gtk.SpinButton.new_with_range(0, 59, 5)
        for sp in (self.hour, self.minute):
            sp.set_orientation(Gtk.Orientation.VERTICAL)
            sp.set_wrap(True)
            sp.set_numeric(True)
            sp.connect("output", self._two_digits)
            sp.connect("value-changed", lambda *_: self._sync_subtitles())
        tbox.append(self.hour)
        tbox.append(Gtk.Label(label=":", css_classes=["title-1"]))
        tbox.append(self.minute)
        self.ampm = None
        t = due_t or self._default_time()
        if h24:
            self.hour.set_value(t.hour)
        else:
            self.hour.set_value(t.hour % 12 or 12)
            self.ampm = Gtk.DropDown.new_from_strings(["AM", "PM"])
            self.ampm.set_valign(Gtk.Align.CENTER)
            self.ampm.set_selected(1 if t.hour >= 12 else 0)
            self.ampm.connect("notify::selected", lambda *_: self._sync_subtitles())
            tbox.append(self.ampm)
        self.minute.set_value(t.minute)
        self.time_row.add_row(tbox)
        gd.add(self.time_row)

        self.repeat_row = Adw.ComboRow(title="Repeat", model=Gtk.StringList.new([r[1] for r in core.REPEATS]))
        self.repeat_row.set_selected([r[0] for r in core.REPEATS].index(item["repeat"]))
        gd.add(self.repeat_row)
        page.add(gd)

        self.date_row.connect("notify::enable-expansion", self._on_date_toggled)
        self.time_row.connect("notify::enable-expansion", self._on_time_toggled)

        # Organize
        go = Adw.PreferencesGroup(title="Organize")
        self.list_row = Adw.ComboRow(title="List", model=Gtk.StringList.new([lst["name"] for lst in self.store.lists]))
        ids = [lst["id"] for lst in self.store.lists]
        self.list_row.set_selected(ids.index(item["list_id"]) if item["list_id"] in ids else 0)
        go.add(self.list_row)
        self.priority_row = Adw.ComboRow(title="Priority", model=Gtk.StringList.new(core.PRIORITIES))
        self.priority_row.set_selected(item["priority"])
        go.add(self.priority_row)
        self.flag_row = Adw.SwitchRow(title="Flagged", active=item["flagged"])
        go.add(self.flag_row)
        page.add(go)

        gc = Adw.PreferencesGroup()
        self.claude_row = Adw.ButtonRow(title="Send to Claude Code", start_icon_name="utilities-terminal-symbolic")
        self.claude_row.connect("activated", self._on_claude)
        gc.add(self.claude_row)
        page.add(gc)
        self.list_row.connect("notify::selected", lambda *_: self._sync_claude())
        self.claude_group = gc
        self._sync_claude()

        gx = Adw.PreferencesGroup()
        delete = Adw.ButtonRow(title="Delete Reminder", start_icon_name="user-trash-symbolic",
                               css_classes=["destructive-action"])
        delete.connect("activated", self._on_delete)
        gx.add(delete)
        page.add(gx)

        self._ready = True
        self._sync_subtitles()
        self.connect("closed", self._apply)

    # -- helpers

    def _default_time(self):
        now = dt.datetime.now()
        return (now + dt.timedelta(hours=1)).replace(minute=0).time()

    @staticmethod
    def _two_digits(spin):
        spin.set_text(f"{int(spin.get_value()):02d}")
        return True

    def _set_calendar(self, d):
        gd = GLib.DateTime.new_local(d.year, d.month, d.day, 0, 0, 0)
        if hasattr(self.calendar, "set_date"):
            self.calendar.set_date(gd)
        else:
            self.calendar.select_day(gd)

    def _get_calendar(self):
        g = self.calendar.get_date()
        return dt.date(g.get_year(), g.get_month(), g.get_day_of_month())

    def _get_time(self):
        h = int(self.hour.get_value())
        if self.ampm is not None:
            h = h % 12 + (12 if self.ampm.get_selected() == 1 else 0)
        return dt.time(h, int(self.minute.get_value()))

    def _quick_date(self, _b, delta):
        today = dt.date.today()
        d = today + dt.timedelta(days=delta) if delta is not None else today + dt.timedelta(days=7 - today.weekday())
        self._set_calendar(d)
        self._sync_subtitles()

    def _on_date_toggled(self, *_):
        if not self.date_row.get_enable_expansion():
            self.time_row.set_enable_expansion(False)
        self._sync_subtitles()

    def _on_time_toggled(self, *_):
        if self.time_row.get_enable_expansion() and not self.date_row.get_enable_expansion():
            self._set_calendar(dt.date.today())
            self.date_row.set_enable_expansion(True)
            self.date_row.set_expanded(False)
        self._sync_subtitles()

    def _sync_subtitles(self):
        if not self._ready:
            return
        today = dt.date.today()
        has_date = self.date_row.get_enable_expansion()
        self.date_row.set_subtitle(core.friendly_date(self._get_calendar(), today) if has_date else "")
        self.time_row.set_subtitle(core.format_time(self._get_time(), use_24h())
                                   if self.time_row.get_enable_expansion() else "")
        self.repeat_row.set_sensitive(has_date)

    # -- save

    def _sync_claude(self):
        lst = self.store.lists[min(self.list_row.get_selected(), len(self.store.lists) - 1)]
        self.claude_group.set_visible(bool(lst.get("folder")))
        if lst.get("folder"):
            self.claude_group.set_description(f"Opens a terminal in {core.short_path(lst['folder'])} "
                                              "and runs Claude Code with this to-do as the prompt.")

    def _on_claude(self, *_):
        self.close()        # saves edits first, so Claude gets the latest text
        self.win.send_to_claude([self.item_id])

    def _on_delete(self, *_):
        self._deleted = True
        self.close()
        self.win.delete_item(self.item_id)

    def _apply(self, *_):
        if self._deleted:
            return
        item = self.store.get_item(self.item_id)
        if not item:
            return
        buf = self.notes.get_buffer()
        has_date = self.date_row.get_enable_expansion()
        has_time = has_date and self.time_row.get_enable_expansion()
        changes = {
            "title": self.title_row.get_text().strip() or item["title"],
            "notes": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).rstrip(),
            "due_date": self._get_calendar().isoformat() if has_date else None,
            "due_time": self._get_time().strftime("%H:%M") if has_time else None,
            "repeat": core.REPEATS[self.repeat_row.get_selected()][0] if has_date else "none",
            "list_id": self.store.lists[min(self.list_row.get_selected(), len(self.store.lists) - 1)]["id"],
            "priority": self.priority_row.get_selected(),
            "flagged": self.flag_row.get_active(),
        }
        changes = {k: v for k, v in changes.items() if item.get(k) != v}
        if changes:
            if "due_date" in changes or "due_time" in changes:
                self.win.app.withdraw(self.item_id)
            self.store.update_item(self.item_id, **changes)


# ===================================================================== preferences


class PrefsDialog(Adw.PreferencesDialog):
    def __init__(self, app):
        super().__init__(title="Preferences")
        self.app = app
        s = app.store
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")

        g = Adw.PreferencesGroup(title="Notifications")
        bg = Adw.SwitchRow(title="Run in Background",
                           subtitle="Keep running after the window is closed and start at login, "
                                    "so reminders always notify on time",
                           active=s.settings["background"])
        bg.connect("notify::active", self._on_background)
        g.add(bg)
        hour = Adw.SpinRow.new_with_range(0, 23, 1)
        hour.set_title("Alert Time for All-Day Reminders")
        hour.set_subtitle("Hour of the day to notify when a reminder has a date but no time")
        hour.set_value(s.settings["default_hour"])
        hour.connect("notify::value", lambda r, _p: s.set_setting("default_hour", int(r.get_value())))
        g.add(hour)
        snooze = Adw.SpinRow.new_with_range(1, 240, 5)
        snooze.set_title("Snooze Length")
        snooze.set_subtitle("Minutes")
        snooze.set_value(s.settings["snooze_minutes"])
        snooze.connect("notify::value", lambda r, _p: s.set_setting("snooze_minutes", int(r.get_value())))
        g.add(snooze)
        sound = Adw.SwitchRow(title="Play a Sound", active=s.settings["sound"])
        sound.connect("notify::active", lambda r, _p: s.set_setting("sound", r.get_active()))
        g.add(sound)
        test = Adw.ButtonRow(title="Send a Test Notification")
        test.connect("activated", lambda *_: app.send_test_notification())
        g.add(test)
        page.add(g)

        g2 = Adw.PreferencesGroup(title="Adding Reminders")
        parse = Adw.SwitchRow(title="Understand Dates in Text",
                              subtitle="“Pay rent on 1st dec”, “call mom tomorrow 6pm”, “gym every monday 7am”, "
                                       "“!!” for priority, “#flag”, “#ListName”",
                              active=s.settings["smart_parse"])
        parse.connect("notify::active", lambda r, _p: s.set_setting("smart_parse", r.get_active()))
        g2.add(parse)
        page.add(g2)

        g3 = Adw.PreferencesGroup(title="Data")
        where = Adw.ActionRow(title="Saved In", subtitle=DATA_FILE, subtitle_selectable=True)
        open_btn = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                              tooltip_text="Open Folder")
        def open_folder(*_):
            os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
            Gtk.FileLauncher.new(Gio.File.new_for_path(os.path.dirname(DATA_FILE))).launch(self.get_root(), None)
        open_btn.connect("clicked", open_folder)
        where.add_suffix(open_btn)
        g3.add(where)
        page.add(g3)
        self.add(page)

    def _on_background(self, row, _p):
        on = row.get_active()
        self.app.store.set_setting("background", on)
        self.app._apply_background(on)


def main():
    app = RemindersApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
