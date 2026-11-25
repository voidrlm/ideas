#!/usr/bin/env bash
# Reminders installer: adds a launcher and app-grid entry for the current user.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
APP_ID=io.github.voidrlm.Reminders

if ! python3 - <<'PY' 2>/dev/null
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 8)
PY
then
    echo "Missing dependencies: PyGObject, GTK 4 and libadwaita 1.8+ are required." >&2
    echo "  Fedora: sudo dnf install python3-gobject gtk4 libadwaita" >&2
    exit 1
fi

chmod +x "$here/reminders"
mkdir -p ~/.local/bin ~/.local/share/applications ~/.local/share/icons/hicolor/scalable/apps
ln -sf "$here/reminders" ~/.local/bin/reminders
ln -sf "$here/$APP_ID.svg" ~/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg
sed "s|^Exec=reminders|Exec=$HOME/.local/bin/reminders|" "$here/$APP_ID.desktop" > ~/.local/share/applications/$APP_ID.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
gtk-update-icon-cache -q -t ~/.local/share/icons/hicolor 2>/dev/null || true
echo "Installed. Open “Reminders” from the app grid, or run: reminders"
echo "Add from a terminal or a keyboard shortcut with:  reminders --add \"Call mom tomorrow 6pm\""
