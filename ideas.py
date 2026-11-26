"""Ideas: simple lists of ideas and to-dos for the GNOME desktop.

Lists of to-dos with notes and images.  A list can be linked to a project folder; its
to-dos (with their images) can then be sent to Claude Code in that folder.
Everything lives in one JSON file (~/.local/share/ideas/data.json).
"""

import glob
import json
import os
import shlex
import shutil
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402

import ideas_core as core  # noqa: E402
import ideas_git as gitx  # noqa: E402

APP_ID = "io.github.voidrlm.Ideas"
APP_NAME = "Ideas"
VERSION = "2.0"

DATA_FILE = os.environ.get("IDEAS_DATA") or os.path.join(GLib.get_user_data_dir(), "ideas", "data.json")
# The app used to be called "Reminders": its data folder is moved over on first start, and the
# login entry it used for notifications is removed.
OLD_DATA_DIR = os.path.join(GLib.get_user_data_dir(), "reminders")
OLD_AUTOSTART_FILE = os.path.join(GLib.get_user_config_dir(), "autostart", "io.github.voidrlm.Reminders.desktop")
TEAMS_SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "ideas_teams.py")
# Where to-dos are picked up from: name, helper script and its arguments.
SOURCES = {
    "teams": ("Teams", TEAMS_SCRIPT, []),
}


def migrate_old_data():
    new_dir = os.path.dirname(DATA_FILE)
    if os.environ.get("IDEAS_DATA") or os.path.exists(DATA_FILE):
        return
    if os.path.exists(os.path.join(OLD_DATA_DIR, "data.json")) and not os.path.exists(new_dir):
        try:
            os.rename(OLD_DATA_DIR, new_dir)
        except OSError as e:
            print("Couldn't move old Reminders data:", e, file=sys.stderr)


def build_css():
    css = """
    .dot { min-width: 12px; min-height: 12px; border-radius: 99px; }
    .sidebar-count { font-feature-settings: "tnum"; opacity: 0.6; }
    .section-heading { margin: 6px 4px 0 4px; }
    .item-title.done { text-decoration-line: line-through; opacity: 0.5; }
    .item-meta { font-size: 0.88em; }
    .priority-mark { font-weight: 800; color: var(--warning-color); }
    .notes-view { padding: 10px; background: none; }
    .notes-frame { border-radius: 12px; }
    .color-choice { min-width: 26px; min-height: 26px; padding: 0; border-radius: 99px; }
    .color-choice:checked { box-shadow: 0 0 0 2px var(--window-bg-color), 0 0 0 4px var(--window-fg-color); }
    row.item-row { padding: 0; }
    .thumb { border-radius: 8px; }
    .thumb-more { font-size: 0.8em; font-weight: bold; }
    .image-remove { min-width: 24px; min-height: 24px; padding: 0; margin: 4px; }
    .image-tile { padding: 0; border-radius: 10px; }
    .agent-btn { min-width: 34px; min-height: 34px; padding: 0; }
    .agent-btn.sent { background-color: transparent; color: inherit; opacity: 0.55; }
    .agent-btn.sent:hover { background-color: alpha(currentColor, 0.1); opacity: 1; }
    .launch-box { margin: 0 2px; }
    .git-btn { min-width: 26px; min-height: 26px; padding: 0; margin: -4px 0; color: var(--accent-color); }
    .git-files { font-family: monospace; font-size: 0.9em; }
    """
    for key, agent in AGENTS.items():
        c = agent["color"]
        css += f"""
        .agent-{key} {{ color: {c}; background-color: alpha({c}, 0.14); }}
        .agent-{key}:hover {{ background-color: alpha({c}, 0.26); }}
        .agent-{key}:active {{ background-color: alpha({c}, 0.36); }}
        .working-{key} {{ color: {c}; }}
        """
    for key, agent in LAUNCHERS.items():
        if agent.get("color"):
            css += f".launch-{key} {{ color: {agent['color']}; }}\n"
    for name, hexc in core.COLORS:
        css += f"""
        .dot.{name}, .color-choice.{name} {{ background-color: {hexc}; }}
        checkbutton.list-{name} check:checked {{ background-color: {hexc}; color: white; }}
        checkbutton.list-{name} check {{ border-color: alpha({hexc}, 0.7); }}
        """
    return css


def spawn(argv, cwd=None):
    """Start a program without waiting for it.  Raises GLib.Error if it can't start.

    (GLib.spawn_async's Python override is broken in this PyGObject, so use Gio.Subprocess.)
    """
    launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
    if cwd:
        launcher.set_cwd(cwd)
    proc = launcher.spawnv(argv)
    proc.wait_async(None, lambda p, res: p.wait_finish(res))   # reap it when it exits


# ---------------------------------------------------------------- Claude Code


# The coding agents a to-do can be sent to.  Each runs in a terminal in the list's folder.
AGENTS = {
    "claude": {"name": "Claude Code", "icon": "claude-symbolic", "color": "#d97757"},
    "codex": {"name": "Codex", "icon": "codex-symbolic", "color": "#10a37f"},
    # Claude Code, with OpenRouter as the model provider (any OpenRouter model: IDEAS_OPENROUTER_MODEL)
    "openrouter": {"name": "OpenRouter", "icon": "openrouter-symbolic", "color": "#6467f2"},
}
OPENROUTER_URL = "https://openrouter.ai/api"

# What the header buttons can open in a list's folder: the agents above, plus ones that to-dos
# can't be sent to (yet).  In the order the buttons are shown.
LAUNCHERS = {
    "claude": AGENTS["claude"],
    "opencode": {"name": "OpenCode", "icon": "opencode-symbolic", "color": None},
    "codex": AGENTS["codex"],
    "openrouter": AGENTS["openrouter"],
}


def find_program(name, extra=()):
    """A program on PATH or in the usual per-user install places (apps started from the desktop
    often don't have ~/.local/bin and friends on PATH)."""
    home = os.path.expanduser("~")
    places = [shutil.which(name), *extra,
              *(os.path.join(home, d, name) for d in (".local/bin", ".npm-global/bin", ".bun/bin", ".volta/bin",
                                                      ".cargo/bin", "bin")),
              *sorted(glob.glob(os.path.join(home, ".nvm/versions/node/*/bin", name)), reverse=True),
              os.path.join("/usr/local/bin", name)]
    return next((p for p in places if p and os.access(p, os.X_OK)), None)


def find_claude():
    return find_program("claude", [os.path.expanduser("~/.claude/local/claude")])


def find_codex():
    return find_program("codex")


def find_opencode():
    return find_program("opencode", [os.path.expanduser("~/.opencode/bin/opencode")])


def detect_launchers(agents):
    """{launcher: program path} for what can be opened in a folder.  agents: detect_agents()."""
    found = dict(agents)
    opencode = find_opencode()
    if opencode:
        found["opencode"] = opencode
    return {key: found[key] for key in LAUNCHERS if key in found}


def login_shell_facts():
    """What a login shell knows that the desktop may not: where codex is, and whether an
    OpenRouter key is set (only whether: the key itself isn't read here).  Slow-ish: run it in the background."""
    try:
        import subprocess
        r = subprocess.run(["bash", "-lc", 'command -v codex; echo "--"; [ -n "$OPENROUTER_API_KEY" ] && echo key'],
                           capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
        before, _, after = r.stdout.partition("--")
        codex = before.strip().splitlines()[-1] if before.strip() else None
        return {"codex": codex if codex and os.access(codex, os.X_OK) else None, "openrouter_key": "key" in after}
    except Exception:
        return {"codex": None, "openrouter_key": False}


def detect_agents(shell=None):
    """{agent: program path} for the agents that can be used.  shell: login_shell_facts(), if known."""
    shell = shell or {}
    found = {}
    claude = find_claude()
    if claude:
        found["claude"] = claude
    codex = find_codex() or shell.get("codex")
    if codex:
        found["codex"] = codex
    if claude and (os.environ.get("OPENROUTER_API_KEY") or shell.get("openrouter_key")):
        found["openrouter"] = claude
    return found


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

# Runs in the terminal: cd into the folder, set up OpenRouter if asked, run the agent, note when it
# exits (also if the window is closed), then stay in a shell in that folder instead of closing.
# The OpenRouter key is taken from the environment here, so it never appears on a command line.
TERMINAL_SCRIPT = (
    'cd "$1" || exit 1; m="$2"; mode="$3"; shift 3; '
    'if [ "$mode" = openrouter ]; then '
    '  if [ -z "$OPENROUTER_API_KEY" ]; then echo "OPENROUTER_API_KEY is not set in this shell."; '
    '  else export ANTHROPIC_BASE_URL=' + OPENROUTER_URL + ' ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY" ANTHROPIC_API_KEY=""; '
    '    [ -n "$IDEAS_OPENROUTER_MODEL" ] && export ANTHROPIC_MODEL="$IDEAS_OPENROUTER_MODEL" '
    'ANTHROPIC_DEFAULT_SONNET_MODEL="$IDEAS_OPENROUTER_MODEL" ANTHROPIC_DEFAULT_OPUS_MODEL="$IDEAS_OPENROUTER_MODEL" '
    'ANTHROPIC_DEFAULT_HAIKU_MODEL="$IDEAS_OPENROUTER_MODEL" ANTHROPIC_SMALL_FAST_MODEL="$IDEAS_OPENROUTER_MODEL"; fi; '
    'fi; '
    'trap \'[ -n "$m" ] && touch "$m"; exit 129\' HUP TERM; '
    '"$@"; [ -n "$m" ] && touch "$m"; trap - HUP TERM; exec "${SHELL:-bash}" -l'
)


def agent_argv(agent, program, prompt, extra_dirs=(), images=(), marker=None):
    """The command line that starts `agent` on `prompt`.

    marker: path prefix; "<marker>.done" is touched when the agent finishes its turn.
    prompt None: just open the agent, with nothing to do yet.
    """
    if prompt is None:
        return [program]
    done = marker + ".done" if marker else None
    if agent == "codex":
        argv = [program]
        if done:    # Codex runs `notify` after each turn, with a JSON description as the last argument
            argv += ["-c", "notify=" + json.dumps(["bash", "-c", 'touch "$0"', done])]
        if images:
            argv += ["--image", ",".join(images)]
        return argv + ["--", prompt]
    argv = [program]        # Claude Code (directly, or through OpenRouter)
    if done:
        hook = {"type": "command", "command": "touch " + shlex.quote(done)}
        argv += ["--settings", json.dumps({"hooks": {"Stop": [{"hooks": [hook]}]}})]
    for d in extra_dirs:
        argv += ["--add-dir", d]
    return argv + ["--", prompt]      # "--" ends options: --add-dir takes several values, and prompts may start with "-"


def launch_agent(agent, folder, prompt, extra_dirs=(), images=(), marker=None, agents=None):
    """Open a terminal in `folder` running `agent` with `prompt` (None: just open it).  Returns an error or None.

    extra_dirs (where attached images live) let the agent read them; images are also attached
    directly where the agent supports it.  marker: path prefix; "<marker>.done" is touched when the
    agent finishes its turn and "<marker>.exit" when the command ends, so the app can tell how it went.
    """
    name = LAUNCHERS[agent]["name"]
    if not folder or not os.path.isdir(folder):
        return "The linked folder doesn't exist any more"
    program = (agents if agents is not None else detect_launchers(detect_agents())).get(agent)
    if not program:
        return {"claude": "Claude Code (the “claude” command) isn't installed",
                "codex": "Codex (the “codex” command) isn't installed",
                "opencode": "OpenCode (the “opencode” command) isn't installed",
                "openrouter": "OpenRouter needs Claude Code and OPENROUTER_API_KEY"}[agent]
    term = next(((shutil.which(t), args) for t, args in TERMINALS if shutil.which(t)), None)
    if not term:
        return "No terminal app found"
    argv = agent_argv(agent, program, prompt, extra_dirs, images, marker)
    exit_marker = marker + ".exit" if marker else ""
    mode = "openrouter" if agent == "openrouter" else "-"
    cmd = [term[0], *term[1], "bash", "-lc", TERMINAL_SCRIPT, f"ideas-{agent}", folder, exit_marker, mode, *argv]
    try:
        spawn(cmd, cwd=folder)
    except GLib.Error as e:
        return f"Couldn't open the terminal for {name}: {e.message}"
    return None


# ---------------------------------------------------------------- Microsoft Teams


class SourceCheck:
    """One run of a helper (ideas_teams.py): on_event(dict) for each line it prints, then on_done()."""

    def __init__(self, source, request, login, on_event, on_done):
        self.on_event, self.on_done = on_event, on_done
        _label, script, args = SOURCES[source]
        argv = [sys.executable, script, *args, "--data-dir", os.path.dirname(DATA_FILE)] + (["--login"] if login else [])
        flags = Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        self.proc = Gio.SubprocessLauncher.new(flags).spawnv(argv)       # raises GLib.Error
        stdin = self.proc.get_stdin_pipe()
        data = GLib.Bytes.new(json.dumps(request).encode())
        stdin.write_bytes_async(data, GLib.PRIORITY_DEFAULT, None, lambda s, res: (self._finish_write(s, res)))
        self.lines = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
        self._read()

    @staticmethod
    def _finish_write(stream, res):
        try:
            stream.write_bytes_finish(res)
        except GLib.Error:
            pass
        stream.close_async(GLib.PRIORITY_DEFAULT, None, None)

    def _read(self):
        self.lines.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line)

    def _on_line(self, stream, res):
        try:
            line, _n = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:                    # the helper has finished
            self.proc.wait_async(None, lambda p, r: p.wait_finish(r))
            on_done, self.on_done, self.on_event = self.on_done, None, None
            if on_done:
                on_done()
            return
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if isinstance(event, dict) and self.on_event:
            self.on_event(event)
        self._read()

    def cancel(self):
        self.on_done = self.on_event = None
        self.proc.force_exit()


# ---------------------------------------------------------------- images

_thumb_cache = {}


def thumb_texture(path, size):
    """A square, center-cropped texture of the image, cached by file and size."""
    try:
        key = (path, size, os.path.getmtime(path))
    except OSError:
        return None
    tex = _thumb_cache.get(key)
    if tex is None:
        try:
            _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
            target = size * 2  # sharp on HiDPI
            scale = min(1.0, max(target / max(w, 1), target / max(h, 1)))
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max(1, int(w * scale)), max(1, int(h * scale)), False)
            pb = pb.apply_embedded_orientation() or pb
            side = min(pb.get_width(), pb.get_height())
            pb = pb.new_subpixbuf((pb.get_width() - side) // 2, (pb.get_height() - side) // 2, side, side).copy()
        except (GLib.Error, TypeError):
            return None
        fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
        tex = Gdk.MemoryTexture.new(pb.get_width(), pb.get_height(), fmt, pb.read_pixel_bytes(), pb.get_rowstride())
        _thumb_cache[key] = tex
    return tex


def thumb_picture(path, size):
    """A size×size rounded thumbnail (Gtk.Image keeps an exact size, unlike Gtk.Picture)."""
    tex = thumb_texture(path, size)
    img = Gtk.Image(pixel_size=size, css_classes=["thumb"], valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
    img.set_overflow(Gtk.Overflow.HIDDEN)
    if tex:
        img.set_from_paintable(tex)
    else:
        img.set_from_icon_name("image-missing-symbolic")
    return img


def save_texture(store, texture):
    name = store.new_image_name(".png")
    texture.save_to_png(store.image_path(name))
    return name


def import_image_file(store, gfile):
    """Copy an image file into the store; returns its new name, or None if it isn't an image."""
    path = gfile.get_path()
    ext = os.path.splitext(gfile.get_basename() or "")[1].lower()
    try:
        if ext in core.IMAGE_EXTS:
            name = store.new_image_name(".jpg" if ext == ".jpeg" else ext)
            data = gfile.load_bytes(None)[0].get_data()
            with open(store.image_path(name), "wb") as f:
                f.write(data)
            return name
        if not path:
            return None
        pb = GdkPixbuf.Pixbuf.new_from_file(path)       # HEIC, BMP, TIFF, AVIF… → PNG
        pb = pb.apply_embedded_orientation() or pb
        name = store.new_image_name(".png")
        pb.savev(store.image_path(name), "png", [], [])
        return name
    except (GLib.Error, OSError):
        return None


def import_value(store, value):
    """Names of images saved from a dropped/pasted Gdk.Texture or Gdk.FileList."""
    if isinstance(value, Gdk.Texture):
        return [save_texture(store, value)]
    if isinstance(value, Gdk.FileList):
        return [n for n in (import_image_file(store, f) for f in value.get_files()) if n]
    return []


def clipboard_has_image(clipboard):
    """True when Ctrl+V should attach an image instead of pasting text."""
    fmts = clipboard.get_formats()
    if fmts.contain_gtype(Gdk.FileList.__gtype__):
        return True
    # Office apps put a picture of copied text on the clipboard too; prefer the text then.
    return fmts.contain_gtype(Gdk.Texture.__gtype__) and not fmts.contain_mime_type("text/plain")


def read_clipboard_images(store, clipboard, done):
    """Read pasted images and call done(names)."""
    fmts = clipboard.get_formats()
    if fmts.contain_gtype(Gdk.FileList.__gtype__):
        def got_files(cb, res):
            try:
                value = cb.read_value_finish(res)
            except GLib.Error:
                value = None
            done(import_value(store, value) if value is not None else [])
        clipboard.read_value_async(Gdk.FileList.__gtype__, GLib.PRIORITY_DEFAULT, None, got_files)
    else:
        def got_texture(cb, res):
            try:
                tex = cb.read_texture_finish(res)
            except GLib.Error:
                tex = None
            done([save_texture(store, tex)] if tex else [])
        clipboard.read_texture_async(None, got_texture)


def image_drop_target(on_drop):
    """A drop target accepting image files and raw images; on_drop(value, x, y) -> bool."""
    target = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
    target.set_gtypes([Gdk.FileList.__gtype__, Gdk.Texture.__gtype__])
    target.connect("drop", lambda _t, value, x, y: on_drop(value, x, y))
    return target


def open_image(path, parent):
    Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(parent, None)


# ---------------------------------------------------------------- small widgets


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


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def join_names(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def agent_name(item):
    """Name of the agent a to-do was last sent to."""
    return AGENTS.get(item.get("agent") or "claude", AGENTS["claude"])["name"]


def in_background(work, done):
    """Run work() off the main thread, then done(result) back on it."""
    def run():
        try:
            result = work()
        except Exception as e:          # report it instead of dying silently in a thread
            result = e
        GLib.idle_add(lambda: done(result) and False)
    threading.Thread(target=run, daemon=True).start()


# ===================================================================== application


class IdeasApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.store = None
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            os.path.join(os.path.dirname(os.path.realpath(core.__file__)), "icons"))   # agent icons
        provider = Gtk.CssProvider()
        provider.load_from_string(build_css())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        migrate_old_data()
        self.store = core.Store(DATA_FILE)
        self.agents = detect_agents()       # {agent: program}; refined by a login shell below
        self.launchers = detect_launchers(self.agents)      # what the header buttons can open
        in_background(login_shell_facts, self._got_shell_facts)
        self._watch_claude_runs()
        try:
            os.remove(OLD_AUTOSTART_FILE)
        except OSError:
            pass

        def add(name, cb, accels=None):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

        add("about", lambda *a: self._show_about())
        add("shortcuts", lambda *a: self._show_shortcuts(), accels=["<Control>question"])
        add("quit", lambda *a: self.quit(), accels=["<Control>q"])
        self.set_accels_for_action("window.close", ["<Control>w"])

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        if "--add" in args:
            text = " ".join(args[args.index("--add") + 1:]).strip()
            if text:
                self.quick_add(text, None)
            return 0
        if "--background" in args:      # left over from an old login entry: nothing to do
            return 0
        self.activate()
        if "--new" in args and self.window:
            self.window.activate_action("win.new-item", None)
        return 0

    def do_activate(self):
        # GTK 4 doesn't emit "destroy" on close, so also check the window is still ours.
        if self.window is None or self.window not in self.get_windows():
            self.window = MainWindow(self)
        self.window.present()
        self.check_claude_runs()        # sessions that finished while Ideas was closed

    def _got_shell_facts(self, facts):
        agents = detect_agents(facts if isinstance(facts, dict) else None)
        if agents != self.agents:
            self.agents = agents
            self.launchers = detect_launchers(agents)
            if self.window in self.get_windows():
                self.window.refresh()

    # -- agent sessions finishing

    def _watch_claude_runs(self):
        self._runs_check_queued = False
        os.makedirs(self.store.claude_runs_dir, exist_ok=True)
        self._runs_monitor = Gio.File.new_for_path(self.store.claude_runs_dir).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self._runs_monitor.connect("changed", lambda *_: self._queue_claude_check())

    def _queue_claude_check(self):
        if not self._runs_check_queued:
            self._runs_check_queued = True
            GLib.timeout_add(200, self.check_claude_runs)

    def check_claude_runs(self):
        self._runs_check_queued = False
        finished, abandoned = self.store.finish_claude_runs()
        win = self.window if self.window in self.get_windows() else None
        if win and finished:
            ids = [it["id"] for it in finished]

            def undo():
                for iid in ids:
                    self.store.set_done(iid, False)
            what = f"“{finished[0]['title']}”" if len(finished) == 1 else plural(len(finished), "to-do")
            win.toast(f"{agent_name(finished[0])} finished {what}: marked done", undo)
        if win and abandoned:
            what = f"“{abandoned[0]['title']}”" if len(abandoned) == 1 else plural(len(abandoned), "to-do")
            win.toast(f"{agent_name(abandoned[0])} closed before finishing {what}, so it's still open")
        return False

    def window_closed(self, win):
        if self.window is win:
            self.window = None

    def quick_add(self, text, list_id, images=()):
        """Add a to-do typed in the add box (or --add); "!!" sets priority, "#List" picks the list."""
        s = self.store
        p = core.parse_quick(text, [lst["name"] for lst in s.lists])
        title = p["title"]
        if not title and images:
            title = "Image" if len(images) == 1 else "Images"
        if p["list_name"]:
            list_id = s.list_by_name(p["list_name"])["id"]
        return s.add_item(title, list_id, priority=p["priority"], images=list(images))

    def _show_shortcuts(self):
        self.activate()
        d = Adw.ShortcutsDialog()
        for title, items in (
            ("To-dos", [("New to-do", "<Control>n"), ("Search", "<Control>f"),
                        ("Show or hide completed", "<Control>h"), ("Paste an image", "<Control>v"),
                        ("Check Teams for tasks", "<Control>t")]),
            ("General", [("New list", "<Control><Shift>n"), ("Keyboard shortcuts", "<Control>question"),
                         ("Close window", "<Control>w"), ("Quit", "<Control>q")]),
        ):
            sec = Adw.ShortcutsSection(title=title)
            for label, accel in items:
                sec.add(Adw.ShortcutsItem.new(label, accel))
            d.add(sec)
        d.present(self.window)

    def _show_about(self):
        Adw.AboutDialog(
            application_name=APP_NAME, application_icon=APP_ID, version=VERSION, developer_name="Arjun",
            comments="Lists of ideas and to-dos with notes and images. Send a to-do to Claude Code, "
                     "Codex or OpenRouter, commit and push a list's project folder, and pick up "
                     "tasks from unread Teams chats.",
            website="https://github.com/voidrlm/ideas",
        ).present(self.window)


# ===================================================================== main window


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        st = app.store.settings
        super().__init__(application=app, title=APP_NAME,
                         default_width=st.get("window_width", 900), default_height=st.get("window_height", 640))
        if st.get("window_maximized"):
            self.maximize()
        self.set_size_request(360, 400)
        self.app = app
        self.store = app.store
        self.list_id = st.get("last_list") if self.store.get_list(st.get("last_list")) else self.store.lists[0]["id"]
        self._building = False
        self.pending_images = []
        self.checks = {}                # source -> the SourceCheck running for it
        self.check_status = {}          # source -> what that check is doing
        self.git_state = {}             # list id -> repositories with something to commit or push
        self.git_busy = set()           # list ids being committed and pushed right now
        self._git_checking = self._git_again = self._closed = False

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=220, max_sidebar_width=280)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self._build_actions()
        self._build_sidebar()
        self._build_content()

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.scroller.add_controller(image_drop_target(self._on_drop_list))
        self.quick_box.add_controller(image_drop_target(lambda v, x, y: self._stage_images(import_value(self.store, v))))

        self.store.connect(self._on_store_changed)
        self.connect("close-request", self._on_close_request)
        # Uncommitted work shows up when you come back to the window, and every half minute.
        self.connect("notify::is-active", lambda *_: self.is_active() and self.refresh_git())
        self._git_timer = GLib.timeout_add_seconds(30, lambda: (self.is_active() and self.refresh_git(), True)[1])
        self.refresh()
        self.refresh_git()
        self.split.set_show_content(True)

    def _on_close_request(self, _win):
        self._closed = True
        GLib.source_remove(self._git_timer)
        for check in self.checks.values():
            check.cancel()
        self.checks.clear()
        self._clear_pending()
        st = self.store.settings
        st["window_maximized"] = self.is_maximized()
        if not self.is_maximized():
            st["window_width"], st["window_height"] = self.get_width(), self.get_height()
        st["last_list"] = self.list_id
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

        add("new-item", lambda *a: self._focus_quick_add(), accels=["<Control>n"])
        add("new-list", lambda *a: self.edit_list_dialog(None), accels=["<Control><Shift>n", "<Control>l"])
        add("search", lambda *a: self.search_bar.set_search_mode(True), accels=["<Control>f"])
        add("edit-list", lambda a, v: self.edit_list_dialog(v.get_string()), "s")
        add("delete-list", lambda a, v: self.delete_list(v.get_string()), "s")
        add("move-list-up", lambda a, v: self.store.move_list(v.get_string(), -1), "s")
        add("move-list-down", lambda a, v: self.store.move_list(v.get_string(), 1), "s")
        add("clear-completed", lambda *a: self.clear_completed())
        add("item-open", lambda a, v: self.open_item(v.get_string()), "s")
        add("item-delete", lambda a, v: self.delete_item(v.get_string()), "s")
        # target "<agent>:<id>"
        add("item-agent", lambda a, v: self._send_target(v.get_string(), whole_list=False), "s")
        add("list-agent", lambda a, v: self._send_target(v.get_string(), whole_list=True), "s")
        add("open-agent", lambda a, v: self.open_agent(v.get_string()), "s")
        add("list-push", lambda a, v: self.push_list(v.get_string()), "s")
        add("teams-check", lambda *a: self.check_source("teams"), accels=["<Control>t"])
        add("teams-login", lambda *a: self.check_source("teams", login=True))
        show = Gio.SimpleAction.new_stateful("show-completed", None,
                                             GLib.Variant("b", self.store.settings["show_completed"]))
        show.connect("change-state", self._on_show_completed)
        self.add_action(show)
        self.app.set_accels_for_action("win.show-completed", ["<Control>h"])

    def _send_target(self, target, whole_list):
        """An item-agent / list-agent action's target is "<agent>:<item or list id>"."""
        agent, _, oid = target.partition(":")
        if agent not in AGENTS or not oid:
            return
        if whole_list:
            self.send_list_to_agent(oid, agent)
        else:
            self.send_to_agent([oid], agent=agent)

    def _on_show_completed(self, action, value):
        action.set_state(value)
        self.store.set_setting("show_completed", value.get_boolean())

    # -- sidebar

    def _build_sidebar(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("New List", "win.new-list")
        teams = Gio.Menu()
        teams.append("Check Teams for Tasks", "win.teams-check")
        teams.append("Sign In to Teams…", "win.teams-login")
        menu.append_section(None, teams)
        sec = Gio.Menu()
        sec.append("Keyboard Shortcuts", "app.shortcuts")
        sec.append("About Ideas", "app.about")
        menu.append_section(None, sec)
        hb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main Menu",
                                   primary=True))
        self.teams_stack = Gtk.Stack()
        self.teams_stack.add_named(Gtk.Image(icon_name="chat-message-new-symbolic"), "idle")
        self.teams_stack.add_named(Adw.Spinner(), "busy")
        self.teams_btn = Gtk.Button(child=self.teams_stack, action_name="win.teams-check")
        hb.pack_start(self.teams_btn)
        self._sync_busy()
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
        self.split.set_sidebar(Adw.NavigationPage(title="Lists", child=tv))

    def _rebuild_sidebar(self):
        counts = self.store.open_counts()
        self._building = True
        self.sidebar_list.remove_all()
        selected = None
        for lst in self.store.lists:
            row = Gtk.ListBoxRow()
            row.list_id = lst["id"]
            box = Gtk.Box(spacing=12, margin_top=4, margin_bottom=4)
            box.append(color_dot(lst["color"], 14))
            box.append(Gtk.Label(label=lst["name"], xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            if lst.get("folder"):
                box.append(Gtk.Image(icon_name="folder-symbolic", pixel_size=12, css_classes=["dim-label"],
                                     tooltip_text=core.short_path(lst["folder"])))
            if lst["id"] in self.git_busy:
                box.append(Gtk.Button(child=Adw.Spinner(), css_classes=["flat", "circular", "git-btn"], sensitive=False,
                                      valign=Gtk.Align.CENTER, tooltip_text="Committing and pushing…"))
            elif self.git_state.get(lst["id"]):
                push = Gtk.Button(icon_name="send-to-symbolic", css_classes=["flat", "circular", "git-btn"],
                                  valign=Gtk.Align.CENTER, tooltip_text=self._git_summary(self.git_state[lst["id"]]))
                push.connect("clicked", lambda _b, lid=lst["id"]: self.push_list(lid))
                box.append(push)
            n = counts.get(lst["id"], 0)
            box.append(Gtk.Label(label=str(n) if n else "", css_classes=["sidebar-count"]))
            row.set_child(box)
            self.sidebar_list.append(row)
            if lst["id"] == self.list_id:
                selected = row
        if selected is None:
            self.list_id = self.store.lists[0]["id"]
            selected = self.sidebar_list.get_row_at_index(0)
        self.sidebar_list.select_row(selected)
        self._building = False

    def _on_sidebar_selected(self, _lb, row):
        if self._building or row is None:
            return
        if row.list_id != self.list_id:
            self.list_id = row.list_id
            if self.search_bar.get_search_mode():
                self.search_bar.set_search_mode(False)
            self.refresh(sidebar=False, scroll_top=True)

    def _on_sidebar_right_click(self, gesture, _n, x, y):
        row = self.sidebar_list.get_row_at_y(int(y))
        if not row:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.list_popover.set_menu_model(self._list_menu(row.list_id))
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
            for key in self.app.agents:
                cc.append(f"Send Open To-dos to {AGENTS[key]['name']}", f"win.list-agent::{key}:{list_id}")
            if self.git_state.get(list_id) and list_id not in self.git_busy:
                cc.append("Commit and Push Changes…", f"win.list-push::{list_id}")
            menu.append_section(None, cc)
        menu.append("Edit List…", f"win.edit-list::{list_id}")
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
        self.launch_box = Gtk.Box(css_classes=["launch-box"], visible=False)   # filled by _render_launchers
        self._launch_shown = None
        hb.pack_start(self.launch_box)
        self.window_title = Adw.WindowTitle()
        hb.set_title_widget(self.window_title)
        tv.add_top_bar(hb)

        self.search_bar = Gtk.SearchBar(show_close_button=False)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search all lists", hexpand=True)
        self.search_bar.set_child(Adw.Clamp(maximum_size=720, child=self.search_entry))
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.search_bar.bind_property("search-mode-enabled", self.search_btn, "active",
                                      GObject.BindingFlags.BIDIRECTIONAL)
        self.search_entry.connect("search-changed", lambda *_: self.refresh(sidebar=False, scroll_top=True))
        self.search_bar.connect("notify::search-mode-enabled",
                                lambda *_: not self.search_bar.get_search_mode() and self.search_entry.set_text(""))
        tv.add_top_bar(self.search_bar)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.quick_entry = Gtk.Entry(placeholder_text="Add an idea or to-do…  (paste an image with Ctrl+V)",
                                     primary_icon_name="list-add-symbolic", hexpand=True)
        self.quick_entry.connect("activate", self._on_quick_add)
        qbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, margin_bottom=6,
                       margin_start=12, margin_end=12)
        qbox.append(self.quick_entry)
        self.pending_box = Gtk.Box(spacing=8, margin_top=6, margin_start=6, visible=False)
        qbox.append(self.pending_box)
        self.quick_box = Adw.Clamp(maximum_size=720, child=qbox)
        body.append(self.quick_box)

        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.items_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6,
                                 margin_bottom=24, margin_start=12, margin_end=12)
        self.empty = Adw.StatusPage(vexpand=True)
        self.content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.add_named(Adw.Clamp(maximum_size=720, child=self.items_box), "items")
        self.content_stack.add_named(self.empty, "empty")
        self.scroller.set_child(self.content_stack)
        body.append(self.scroller)

        self.item_popover = Gtk.PopoverMenu(has_arrow=False, halign=Gtk.Align.START)
        self.item_popover.set_parent(self.items_box)

        tv.set_content(body)
        self.content_page = Adw.NavigationPage(title=APP_NAME, child=tv)
        self.split.set_content(self.content_page)

    def _focus_quick_add(self):
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)
        self.split.set_show_content(True)
        self.quick_entry.grab_focus()

    def _on_quick_add(self, entry):
        text = entry.get_text().strip()
        if not text and not self.pending_images:
            return
        item = self.app.quick_add(text, self.list_id, self.pending_images)
        self._clear_pending(discard=False)
        entry.set_text("")
        if item["list_id"] != self.list_id:     # "#OtherList" sent it elsewhere
            self.toast(f"Added to {self.store.get_list(item['list_id'])['name']}")

    # -- pasting and dropping images

    def _on_key_pressed(self, _ctl, keyval, _code, state):
        if keyval not in (Gdk.KEY_v, Gdk.KEY_V) or not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        clipboard = self.get_clipboard()
        if not clipboard_has_image(clipboard):
            return False                     # ordinary text paste
        dialog = self.get_visible_dialog()
        if isinstance(dialog, ItemDialog):
            read_clipboard_images(self.store, clipboard, dialog.attach)
            return True
        if dialog is not None:
            return False
        row = self._focused_item_row()
        if row is not None:
            item_id = row.item_id
            read_clipboard_images(self.store, clipboard, lambda names: self._attach(item_id, names))
        else:
            read_clipboard_images(self.store, clipboard, self._stage_images)
        return True

    def _focused_item_row(self):
        w = self.get_focus()
        while w is not None:
            if isinstance(w, Gtk.ListBoxRow) and hasattr(w, "item_id"):
                return w
            if w is self.quick_entry:
                return None
            w = w.get_parent()
        return None

    def _attach(self, item_id, names):
        if not names:
            self.toast("No image found to attach")
            return
        self.store.add_images(item_id, names)
        self.toast(f"Attached {plural(len(names), 'image')} to “{self.store.get_item(item_id)['title']}”")

    def _on_drop_list(self, value, x, y):
        w = self.scroller.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and not (isinstance(w, Gtk.ListBoxRow) and hasattr(w, "item_id")):
            w = w.get_parent()
        names = import_value(self.store, value)
        if w is not None:
            self._attach(w.item_id, names)
        else:
            self._stage_images(names)
        return bool(names)

    def _stage_images(self, names):
        """Images pasted into the add box wait there until the to-do is added."""
        if not names:
            self.toast("No image found to attach")
            return
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)
        self.pending_images += names
        self._sync_pending()
        self.split.set_show_content(True)
        self.quick_entry.grab_focus()

    def _clear_pending(self, discard=True):
        if discard:
            for n in self.pending_images:
                self.store.discard_image_file(n)
        self.pending_images = []
        self._sync_pending()

    def _sync_pending(self):
        box = self.pending_box
        while box.get_first_child():
            box.remove(box.get_first_child())
        for n in self.pending_images:
            box.append(thumb_picture(self.store.image_path(n), 36))
        if self.pending_images:
            box.append(Gtk.Label(label=plural(len(self.pending_images), "image") + " — type a title and press Enter",
                                 css_classes=["dim-label"]))
            x = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"],
                           tooltip_text="Remove images", valign=Gtk.Align.CENTER)
            x.connect("clicked", lambda *_: self._clear_pending())
            box.append(x)
        box.set_visible(bool(self.pending_images))

    # -- rendering

    def _on_store_changed(self):
        self.refresh()

    def refresh(self, sidebar=True, scroll_top=False):
        if sidebar:
            self._rebuild_sidebar()
        adj = self.scroller.get_vadjustment()
        pos = 0 if scroll_top else adj.get_value()
        self._render_items()
        GLib.idle_add(lambda: adj.set_value(pos) and False)

    def _render_items(self):
        search = self.search_entry.get_text().strip()
        lst = self.store.get_list(self.list_id)
        title = f"Search: {search}" if search else lst["name"]
        self.content_page.set_title(title)
        self.window_title.set_title(title)
        self.window_title.set_subtitle(core.short_path(lst["folder"]) if lst.get("folder") and not search else "")
        self.quick_box.set_visible(not search)
        self._render_launchers(None if search else lst.get("folder"))

        menu = Gio.Menu()
        menu.append("Show Completed", "win.show-completed")
        menu.append("Clear Completed…", "win.clear-completed")
        menu.append_section(None, self._list_menu(self.list_id))
        self.view_menu_btn.set_menu_model(menu)

        child = self.items_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            if child is not self.item_popover:
                self.items_box.remove(child)
            child = nxt

        sections = self.store.sections(self.list_id, self.store.settings["show_completed"], search)
        if not sections:
            self.content_stack.set_visible_child_name("empty")
            if search:
                self.empty.set_icon_name("system-search-symbolic")
                self.empty.set_title("No Results")
                self.empty.set_description("No to-dos match your search.")
            else:
                done = sum(1 for it in self.store.items if it["list_id"] == self.list_id and it["done"])
                self.empty.set_icon_name("checkbox-checked-symbolic")
                self.empty.set_title("All Done" if done else "No To-dos Yet")
                self.empty.set_description("Add one above, or paste a screenshot with Ctrl+V.")
            return
        self.content_stack.set_visible_child_name("items")
        for heading, items in sections:
            if heading:
                self.items_box.append(Gtk.Label(label=heading, xalign=0, css_classes=["heading", "section-heading"]))
            lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
            lb.connect("row-activated", lambda _lb, row: self.open_item(row.item_id))
            for it in items:
                lb.append(self._item_row(it, show_list=bool(search)))
            self.items_box.append(lb)

    def _item_row(self, it, show_list):
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
            title_box.append(Gtk.Label(label="!" * it["priority"], css_classes=["priority-mark"],
                                       valign=Gtk.Align.START, tooltip_text=core.PRIORITIES[it["priority"]] + " priority"))
        title_box.append(Gtk.Label(label=it["title"], xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                   hexpand=True, css_classes=["item-title"] + (["done"] if it["done"] else [])))
        text.append(title_box)

        meta = Gtk.Box(spacing=8, css_classes=["item-meta"])
        agent = it.get("agent") if it.get("agent") in AGENTS else "claude"
        if it.get("claude_run") and not it["done"]:
            sent = Gtk.Box(spacing=4, css_classes=[f"working-{agent}"],
                           tooltip_text=f"{AGENTS[agent]['name']} is working on this; it's marked done when it finishes")
            sent.append(Gtk.Image(icon_name=AGENTS[agent]["icon"], pixel_size=12))
            sent.append(Adw.Spinner(width_request=12, height_request=12))
            meta.append(sent)
        elif it.get("claude_sent_at"):
            meta.append(Gtk.Image(icon_name=AGENTS[agent]["icon"], pixel_size=12, css_classes=["dim-label"],
                                  tooltip_text=f"Sent to {AGENTS[agent]['name']}"))
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

        if it["images"]:
            tb = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER, tooltip_text=plural(len(it["images"]), "image"))
            tb.append(thumb_picture(self.store.image_path(it["images"][0]), 40))
            if len(it["images"]) > 1:
                tb.append(Gtk.Label(label=f"+{len(it['images']) - 1}", css_classes=["thumb-more", "dim-label"]))
            box.append(tb)
        if lst.get("folder") and not it["done"]:
            buttons = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            for key in self.app.agents:
                name = AGENTS[key]["name"]
                sent = bool(it.get("claude_sent_at")) and key == agent
                label = f"Send to {name} again" if sent else f"Send to {name}"
                if key == "openrouter":
                    label += " (Claude Code with an OpenRouter model)"
                send = Gtk.Button(icon_name=AGENTS[key]["icon"], valign=Gtk.Align.CENTER,
                                  css_classes=["circular", "agent-btn"] + (["sent"] if sent else [f"agent-{key}"]),
                                  tooltip_text=label + f" — opens in {core.short_path(lst['folder'])}"
                                               + (" with its images" if it["images"] else ""))
                send.update_property([Gtk.AccessibleProperty.LABEL], [label])
                send.set_action_name("win.item-agent")
                send.set_action_target_value(GLib.Variant("s", f"{key}:{it['id']}"))
                buttons.append(send)
            if buttons.get_first_child():
                box.append(buttons)
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
            for key in self.app.agents:
                menu.append(f"Send to {AGENTS[key]['name']}", f"win.item-agent::{key}:{iid}")
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
            self.store.set_done(item_id, done)
            if done:
                self.toast(f"Completed “{title}”", lambda: self.store.set_done(item_id, False))
            return False
        # Let the tick animation play before the row moves away.
        GLib.timeout_add(350 if done else 0, apply)

    def toast(self, text, undo=None, action=None):
        """action: (button label, callback) for something other than Undo."""
        if undo:
            action = ("Undo", undo)
        t = Adw.Toast(title=text, timeout=10 if action and not undo else 5)
        t.set_use_markup(False)
        if action:
            t.set_button_label(action[0])
            t.connect("button-clicked", lambda *_: action[1]())
        self.toasts.add_toast(t)

    # -- git: commit and push a list's folder

    @staticmethod
    def _git_summary(repos):
        parts = []
        for st in repos:
            what = [plural(len(st["files"]), "uncommitted change")] if st["files"] else []
            if st["ahead"]:
                what.append(plural(st["ahead"], "unpushed commit"))
            parts.append(f"{st['name']}: {' and '.join(what)}")
        return "Commit and push\n" + "\n".join(parts)

    def refresh_git(self):
        """Look (in the background) for uncommitted or unpushed work in the lists' folders."""
        if self._git_checking:
            self._git_again = True
            return
        folders = {lst["id"]: lst["folder"] for lst in self.store.lists if lst.get("folder")}
        if not folders:
            if self.git_state:
                self.git_state = {}
                self._rebuild_sidebar()
            return
        self._git_checking = True

        def done(state):
            self._git_checking = False
            if self._closed:
                return
            if isinstance(state, dict) and state != self.git_state:
                self.git_state = state
                self._rebuild_sidebar()
            if self._git_again:
                self._git_again = False
                self.refresh_git()
        in_background(lambda: {lid: st for lid, st in ((lid, gitx.folder_status(f)) for lid, f in folders.items()) if st}, done)

    def push_list(self, list_id):
        """Commit everything uncommitted under the list's folder and push it, after a look at what that is."""
        lst = self.store.get_list(list_id)
        if not lst or not lst.get("folder") or list_id in self.git_busy:
            return
        folder = lst["folder"]

        def checked(repos):
            if self._closed:
                return
            if not isinstance(repos, list) or not repos:
                self.toast(f"Nothing to commit or push in {core.short_path(folder)}")
                self.refresh_git()
                return
            smart = gitx.find_smart_commit()
            if any(st["files"] for st in repos) and not smart:
                self.toast("smart-commit is missing from the Ideas folder, so no commit message can be written")
                return
            self._confirm_push(list_id, repos, smart)
        in_background(lambda: gitx.folder_status(folder), checked)

    def _confirm_push(self, list_id, repos, smart):
        commits = any(st["files"] for st in repos)
        names = join_names(st["name"] for st in repos)
        d = Adw.AlertDialog(heading="Commit and Push?" if commits else "Push?",
                            body=(f"Everything below is committed in {names} with a message written from the changes, "
                                  "then pushed." if commits else f"Pushes the commits waiting in {names}."))
        lines = []
        for st in repos:
            lines.append(f"{st['name']} · {st['branch'] or 'no branch'}"
                         + (f" · {plural(st['ahead'], 'commit')} to push" if st["ahead"] else ""))
            lines += [f"  {code:<2} {path}" for code, path in st["files"][:12]]
            if len(st["files"]) > 12:
                lines.append(f"  … and {len(st['files']) - 12} more")
        label = Gtk.Label(label="\n".join(lines), xalign=0, yalign=0, selectable=False, css_classes=["git-files"],
                          ellipsize=Pango.EllipsizeMode.MIDDLE, margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
        d.set_extra_child(Gtk.ScrolledWindow(child=label, css_classes=["card"], hscrollbar_policy=Gtk.PolicyType.NEVER,
                                             propagate_natural_height=True, max_content_height=260, min_content_width=360))
        d.add_response("cancel", "Cancel")
        d.add_response("push", "Commit and Push" if commits else "Push")
        d.set_response_appearance("push", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("push")
        d.set_close_response("cancel")
        d.connect("response", lambda _d, resp: resp == "push" and self._do_push(list_id, repos, smart))
        d.present(self)

    def _do_push(self, list_id, repos, smart):
        self.git_busy.add(list_id)
        self._rebuild_sidebar()

        def done(results):
            self.git_busy.discard(list_id)
            if self._closed:
                return
            self._rebuild_sidebar()
            self.refresh_git()
            if not isinstance(results, list):
                self.toast(f"Commit and push failed: {results}")
                return
            failed = [r for r in results if r["error"]]
            if failed:
                d = Adw.AlertDialog(heading="Not Everything Was Pushed",
                                    body="\n\n".join(f"{r['name']}: {r['error']}" for r in failed))
                d.add_response("ok", "OK")
                d.present(self)
            ok = [r for r in results if r["pushed"]]
            if len(ok) == 1 and ok[0]["committed"]:
                self.toast(f"Pushed {ok[0]['name']}: {ok[0]['committed']}")
            elif ok:
                self.toast(("Committed and pushed " if any(r["committed"] for r in ok) else "Pushed ")
                           + join_names(r["name"] for r in ok))
        in_background(lambda: [gitx.commit_and_push(st["path"], smart) for st in repos], done)

    # -- Microsoft Teams

    def _sync_busy(self):
        """The header button spins while any check runs; its tooltip says what they are doing."""
        self.teams_stack.set_visible_child_name("busy" if self.checks else "idle")
        status = "\n".join(self.check_status.get(src) or f"Checking {SOURCES[src][0]}…" for src in self.checks)
        self.teams_btn.set_tooltip_text(status or "Check Teams for Tasks (Ctrl+T)\n"
                                        "Reads your unread chats without marking them as read")

    def check_source(self, source, login=False):
        """Look through unread Teams chats for things to do and add them to that source's list."""
        if source in self.checks:
            return
        label = SOURCES[source][0]
        got = {"answer": False}

        def on_event(ev):
            kind = ev.get("event")
            if kind == "status":
                self.check_status[source] = ev.get("text")
                self._sync_busy()
                if ev.get("toast"):
                    self.toast(ev.get("text", ""))
            elif kind == "login":
                got["answer"] = True
                self.toast(f"Sign in to {label} to check it for tasks",
                           action=("Sign In", lambda: self.check_source(source, login=True)))
            elif kind == "error":
                got["answer"] = True
                self.toast(ev.get("text") or f"{label} check failed")
            elif kind == "result":
                got["answer"] = True
                self._source_result(source, ev)

        def on_done():
            self.checks.pop(source, None)
            self.check_status.pop(source, None)
            self._sync_busy()
            if not got["answer"]:
                self.toast(f"The {label} check stopped unexpectedly")

        try:
            self.checks[source] = SourceCheck(source, self.store.teams_request(), login, on_event, on_done)
        except GLib.Error as e:
            self.toast(f"Couldn't start the {label} check: {e.message}")
            return
        self.check_status[source] = f"Checking {label}…"
        self._sync_busy()

    def _source_result(self, source, ev):
        label = SOURCES[source][0]
        added = self.store.add_teams_tasks(ev.get("tasks"), ev.get("checked"))
        what = "new Teams message"
        if not added:
            n = ev.get("messages") or 0
            self.toast(f"No tasks for you in {plural(n, what)}" if n else f"Nothing new in {label}")
            return
        ids = [it["id"] for it in added]
        self.list_id = added[0]["list_id"]          # show the list they went to
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)
        self.refresh(scroll_top=True)
        self.toast(f"Added {plural(len(added), 'to-do')} from {label}", lambda: self.store.delete_items(ids))

    def _render_launchers(self, folder):
        """The header buttons that open a coding agent in the list's folder."""
        shown = (folder, tuple(self.app.launchers)) if folder else None
        if shown == self._launch_shown:
            return
        self._launch_shown = shown
        while (child := self.launch_box.get_first_child()):
            self.launch_box.remove(child)
        self.launch_box.set_visible(bool(shown and self.app.launchers))
        if not shown:
            return
        # One split button: the main part opens Claude Code, the arrow lists every agent.
        where = core.short_path(folder)
        default = "claude" if "claude" in self.app.launchers else next(iter(self.app.launchers))
        tip = f"Open {LAUNCHERS[default]['name']} in {where}"
        icon = Gtk.Image(icon_name=LAUNCHERS[default]["icon"], css_classes=[f"launch-{default}"])
        if len(self.app.launchers) == 1:
            btn = Gtk.Button(child=icon, css_classes=["flat"], tooltip_text=tip)
        else:
            menu = Gio.Menu()
            for key in self.app.launchers:
                item = Gio.MenuItem.new(LAUNCHERS[key]["name"], None)
                item.set_action_and_target_value("win.open-agent", GLib.Variant("s", key))
                menu.append_item(item)
            btn = Adw.SplitButton(child=icon, menu_model=menu, tooltip_text=tip,
                                  dropdown_tooltip=f"Open Another Agent in {where}")
        btn.update_property([Gtk.AccessibleProperty.LABEL], [tip])
        btn.set_action_name("win.open-agent")
        btn.set_action_target_value(GLib.Variant("s", default))
        self.launch_box.append(btn)

    def open_agent(self, agent):
        """Open a coding agent in the current list's folder, with nothing to do yet."""
        lst = self.store.get_list(self.list_id)
        if agent not in LAUNCHERS or not lst:
            return
        if not lst.get("folder"):
            self.toast("Link a folder to this list first (Edit List…)")
            return
        try:
            err = launch_agent(agent, lst["folder"], None, agents=self.app.launchers)
        except Exception as e:      # never fail silently: say what went wrong
            err = f"Couldn't start {LAUNCHERS[agent]['name']}: {e}"
        if err:
            self.toast(err)

    def send_to_agent(self, item_ids, list_id=None, agent="claude"):
        items = [it for it in (self.store.get_item(i) for i in item_ids) if it]
        if not items:
            return
        name = AGENTS[agent]["name"]
        lst = self.store.get_list(list_id or items[0]["list_id"])
        if not lst or not lst.get("folder"):
            self.toast("Link a folder to this list first (Edit List…)")
            return
        images = [self.store.image_path(n) for it in items for n in it["images"]]
        run = core.new_id()
        try:
            os.makedirs(self.store.claude_runs_dir, exist_ok=True)
            err = launch_agent(agent, lst["folder"], core.claude_prompt(items, lst["name"], self.store.image_path),
                               [self.store.images_dir] if images else (), images,
                               marker=os.path.join(self.store.claude_runs_dir, run), agents=self.app.agents)
        except Exception as e:      # never fail silently: say what went wrong
            err = f"Couldn't start {name}: {e}"
        if err:
            self.toast(err)
            return
        # They're marked done when the agent finishes (see Application.check_claude_runs).
        self.store.start_claude_run([it["id"] for it in items], run, agent)
        what = f"“{items[0]['title']}”" if len(items) == 1 else plural(len(items), "to-do")
        self.toast(f"Sent {what} to {name} in {os.path.basename(lst['folder'].rstrip(os.sep))}. "
                   "It's marked done when it finishes.")

    def send_list_to_agent(self, list_id, agent="claude"):
        ids = [it["id"] for _h, its in self.store.sections(list_id) for it in its if not it["done"]]
        if not ids:
            self.toast("No open to-dos in this list")
            return
        self.send_to_agent(ids, list_id, agent)

    def delete_item(self, item_id):
        it = self.store.get_item(item_id)
        if not it:
            return
        removed = self.store.delete_items([item_id])
        self.toast(f"Deleted “{it['title']}”", lambda: self.store.restore_items(removed))

    def open_item(self, item_id):
        it = self.store.get_item(item_id)
        if it:
            ItemDialog(self, it).present(self)
        return False

    def clear_completed(self):
        ids = [it["id"] for it in self.store.items if it["done"] and it["list_id"] == self.list_id]
        if not ids:
            self.toast("No completed to-dos to clear")
            return
        name = self.store.get_list(self.list_id)["name"]
        d = Adw.AlertDialog(heading="Clear Completed?", body=f"Delete {plural(len(ids), 'completed to-do')} in “{name}”.")
        d.add_response("cancel", "Cancel")
        d.add_response("clear", "Clear")
        d.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, resp):
            if resp == "clear":
                removed = self.store.delete_items(ids)
                self.toast(f"Cleared {plural(len(ids), 'to-do')}", lambda: self.store.restore_items(removed))
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
            fbtn.set_child(Adw.ButtonContent(icon_name="folder-symbolic",
                                             label=(os.path.basename(f.rstrip(os.sep)) or f) if f else "Link a Folder…"))
            fbtn.set_tooltip_text(core.short_path(f) if f else
                                  "Link a project folder to send this list's to-dos to Claude Code")
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
                self.list_id = new["id"]
                self.refresh(scroll_top=True)
                self.split.set_show_content(True)
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
                            body=f"This also deletes its {plural(n, 'to-do')}." if n else "The list is empty.")
        d.add_response("cancel", "Cancel")
        d.add_response("delete", "Delete")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, resp):
            if resp != "delete":
                return
            saved = self.store.delete_list(list_id)
            if saved:
                self.toast(f"Deleted “{lst['name']}”", lambda: self.store.restore_list(saved))
        d.connect("response", done)
        d.present(self)


# ===================================================================== to-do details


class ItemDialog(Adw.Dialog):
    def __init__(self, win, item):
        super().__init__(title="Details", content_width=480, content_height=640)
        self.win = win
        self.store = win.store
        self.item_id = item["id"]
        self._deleted = False

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
        self.notes.set_size_request(-1, 90)
        gn.add(Gtk.Frame(child=self.notes, css_classes=["card", "notes-frame"]))
        page.add(gn)

        self.images_group = Adw.PreferencesGroup(
            title="Images", description="Paste with Ctrl+V, drop image files here, or add from a folder.")
        add_img = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
                             tooltip_text="Add Images…")
        add_img.connect("clicked", self._pick_images)
        self.images_group.set_header_suffix(add_img)
        self.images_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=8,
                                       max_children_per_line=4, min_children_per_line=2, homogeneous=True)
        self.images_group.add(self.images_flow)
        page.add(self.images_group)
        tv.add_controller(image_drop_target(lambda v, x, y: bool(self.attach(import_value(self.store, v))) or True))

        go = Adw.PreferencesGroup(title="Organize")
        self.list_row = Adw.ComboRow(title="List", model=Gtk.StringList.new([lst["name"] for lst in self.store.lists]))
        ids = [lst["id"] for lst in self.store.lists]
        self.list_row.set_selected(ids.index(item["list_id"]) if item["list_id"] in ids else 0)
        go.add(self.list_row)
        self.priority_row = Adw.ComboRow(title="Priority", model=Gtk.StringList.new(core.PRIORITIES))
        self.priority_row.set_selected(item["priority"])
        go.add(self.priority_row)
        page.add(go)

        self.claude_group = Adw.PreferencesGroup()
        for key in win.app.agents:
            agent_row = Adw.ButtonRow(title=f"Send to {AGENTS[key]['name']}", start_icon_name=AGENTS[key]["icon"])
            agent_row.connect("activated", self._on_agent, key)
            self.claude_group.add(agent_row)
        page.add(self.claude_group)
        self.list_row.connect("notify::selected", lambda *_: self._sync_claude())

        if item.get("teams_url"):
            gt = Adw.PreferencesGroup(description="Opening the message in Teams marks that chat as read.")
            teams_row = Adw.ButtonRow(title="Open in Teams", start_icon_name="chat-message-new-symbolic")
            teams_row.connect("activated", lambda *_: Gtk.UriLauncher.new(item["teams_url"]).launch(self.win, None, None))
            gt.add(teams_row)
            page.add(gt)

        gx = Adw.PreferencesGroup()
        delete = Adw.ButtonRow(title="Delete To-do", start_icon_name="user-trash-symbolic",
                               css_classes=["destructive-action"])
        delete.connect("activated", self._on_delete)
        gx.add(delete)
        page.add(gx)

        self._refresh_images()
        self._sync_claude()
        self.connect("closed", self._apply)

    # -- images

    def attach(self, names):
        if names:
            self.store.add_images(self.item_id, names)
            self._refresh_images()
        else:
            self.win.toast("No image found to attach")
        return names

    def _refresh_images(self):
        flow = self.images_flow
        while flow.get_first_child():
            flow.remove(flow.get_first_child())
        item = self.store.get_item(self.item_id)
        names = item["images"] if item else []
        for n in names:
            path = self.store.image_path(n)
            tile = Gtk.Button(css_classes=["flat", "image-tile"], tooltip_text="Open image",
                              child=thumb_picture(path, 96))
            tile.connect("clicked", lambda *_a, p=path: open_image(p, self.win))
            overlay = Gtk.Overlay(child=tile)
            rm = Gtk.Button(icon_name="window-close-symbolic", css_classes=["osd", "circular", "image-remove"],
                            halign=Gtk.Align.END, valign=Gtk.Align.START, tooltip_text="Remove image")
            rm.connect("clicked", lambda *_a, name=n: self._remove_image(name))
            overlay.add_overlay(rm)
            flow.append(overlay)
        flow.set_visible(bool(names))

    def _remove_image(self, name):
        self.store.remove_image(self.item_id, name)
        self._refresh_images()

    def _pick_images(self, *_):
        filt = Gtk.FileFilter(name="Images")
        filt.add_pixbuf_formats()
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        fd = Gtk.FileDialog(title="Add Images", filters=filters, default_filter=filt)

        def picked(dlg, res):
            try:
                files = dlg.open_multiple_finish(res)
            except GLib.Error:
                return
            self.attach([n for n in (import_image_file(self.store, files.get_item(i))
                                     for i in range(files.get_n_items())) if n])
        fd.open_multiple(self.win, None, picked)

    # -- Claude Code

    def _selected_list(self):
        return self.store.lists[min(self.list_row.get_selected(), len(self.store.lists) - 1)]

    def _sync_claude(self):
        lst = self._selected_list()
        self.claude_group.set_visible(bool(lst.get("folder")) and bool(self.win.app.agents))
        if lst.get("folder"):
            names = join_names(AGENTS[k]["name"] for k in self.win.app.agents) if self.win.app.agents else ""
            self.claude_group.set_description(f"Opens a terminal in {core.short_path(lst['folder'])} and runs "
                                              f"{names.replace(' and ', ' or ')} with this to-do and its images as "
                                              "the prompt. It's marked done when it finishes.")

    def _on_agent(self, _row, agent):
        self.close()        # saves edits first, so the agent gets the latest text
        self.win.send_to_agent([self.item_id], agent=agent)

    # -- save

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
        changes = {
            "title": self.title_row.get_text().strip() or item["title"],
            "notes": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).rstrip(),
            "list_id": self._selected_list()["id"],
            "priority": self.priority_row.get_selected(),
        }
        changes = {k: v for k, v in changes.items() if item.get(k) != v}
        if changes:
            self.store.update_item(self.item_id, **changes)


def main():
    return IdeasApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
