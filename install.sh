#!/usr/bin/env bash
# Ideas installer: adds a launcher and app-grid entry for the current user.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
APP_ID=io.github.voidrlm.Ideas

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

chmod +x "$here/ideas"
mkdir -p ~/.local/bin ~/.local/share/applications ~/.local/share/icons/hicolor/scalable/apps
ln -sf "$here/ideas" ~/.local/bin/ideas
ln -sf "$here/$APP_ID.svg" ~/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg
sed "s|^Exec=ideas|Exec=$HOME/.local/bin/ideas|" "$here/$APP_ID.desktop" > ~/.local/share/applications/$APP_ID.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
gtk-update-icon-cache -q -t ~/.local/share/icons/hicolor 2>/dev/null || true
# Remove the older "Reminders" install of this app, if any.
rm -f ~/.local/bin/reminders ~/.local/share/applications/io.github.voidrlm.Reminders.desktop \
      ~/.local/share/icons/hicolor/scalable/apps/io.github.voidrlm.Reminders.svg \
      ~/.config/autostart/io.github.voidrlm.Reminders.desktop
echo "Installed. Open “Ideas” from the app grid, or run: ideas"
echo "Add from a terminal or a keyboard shortcut with:  ideas --add \"Try a dark theme !!\""
