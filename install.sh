#!/usr/bin/env bash
# Ideas installer — lists of ideas and to-dos for GNOME, with a one-click hand-off to Claude Code.
#
#   Install or update:  curl -fsSL https://raw.githubusercontent.com/voidrlm/ideas/main/install.sh | bash
#   From a checkout:    ./install.sh          (runs the app from this folder)
#   Remove:             ./install.sh --uninstall
#
# Installs for the current user only (~/.local); asks for your password just to add system packages.
set -euo pipefail

main() {
APP=ideas
NAME="Ideas"
APP_ID=io.github.voidrlm.Ideas
REPO=voidrlm/ideas
ADW_MIN=1.8
DEPS_DNF=(python3-gobject gtk4 libadwaita git)
DEPS_APT=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gdkpixbuf-2.0 gir1.2-graphene-1.0 git)
DEPS_PACMAN=(python-gobject gtk4 libadwaita git)
GENERIC_DEPS="PyGObject, GTK 4, libadwaita"
DISTROS="Fedora 43+, Ubuntu 25.10+, Arch Linux, or another distribution from late 2025 or newer"
DATA_NOTE="~/.local/share/ideas"
EXTRA_USAGE=""
extra_option() { return 1; }
extra_deps() { :; }
check_optional() { :; }
after_install() { :; }

# ---------------------------------------------------------------------------------------------
# Everything below is the same for every app.

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

DEPS=1 UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --no-deps) DEPS=0 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)
            echo "Usage: install.sh [--no-deps] [--uninstall] $EXTRA_USAGE"
            echo "  --no-deps     don't install system packages, only the app"
            echo "  --uninstall   remove $NAME (your settings and data are kept)"
            exit 0 ;;
        *) extra_option "$arg" || die "Unknown option: $arg (see --help)" ;;
    esac
done

BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
OPT="$HOME/.local/opt/$APP"

refresh_caches() {
    update-desktop-database "$APPS" 2>/dev/null || true
    gtk-update-icon-cache -q -t "${ICONS%/scalable/apps}" 2>/dev/null || true
}

if [ "$UNINSTALL" = 1 ]; then
    rm -f "$BIN/$APP" "$APPS/$APP_ID.desktop" "$ICONS/$APP_ID.svg"
    rm -rf "$OPT"
    refresh_caches
    say "$NAME was removed."
    echo "Your settings and data were kept: $DATA_NOTE"
    exit 0
fi

[ "$(id -u)" != 0 ] || die "Run this as your normal user, not with sudo. It asks for your password when it needs it."

# Where the app comes from: this folder when it is a checkout, otherwise the latest release.
here=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    [ -f "$here/$APP_ID.desktop" ] || here=""
fi
if [ -z "$here" ]; then
    command -v curl >/dev/null || die "curl is needed to download $NAME."
    tag="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null |
           sed -n 's/^ *"tag_name": *"\([^"]*\)".*/\1/p' | head -1 || true)"
    if [ -n "$tag" ]; then url="https://github.com/$REPO/archive/refs/tags/$tag.tar.gz"
    else tag="main"; url="https://github.com/$REPO/archive/refs/heads/main.tar.gz"; fi
    say "Downloading $NAME ($tag)…"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL "$url" | tar -xz -C "$tmp" --strip-components=1
    rm -rf "$OPT"
    mkdir -p "$(dirname "$OPT")"
    mv "$tmp" "$OPT"
    trap - EXIT
    here="$OPT"
fi

# System packages.
if command -v dnf >/dev/null; then PM=dnf; PKGS=("${DEPS_DNF[@]}"); INSTALL=(sudo dnf install -y)
elif command -v apt-get >/dev/null; then PM=apt; PKGS=("${DEPS_APT[@]}"); INSTALL=(sudo apt-get install -y)
elif command -v pacman >/dev/null; then PM=pacman; PKGS=("${DEPS_PACMAN[@]}"); INSTALL=(sudo pacman -S --needed --noconfirm)
else PM=""; PKGS=(); fi

missing=()
if [ "$DEPS" = 1 ] && [ -n "$PM" ]; then
    case "$PM" in
        dnf) for p in "${PKGS[@]}"; do rpm -q --whatprovides "$p" >/dev/null 2>&1 || missing+=("$p"); done ;;
        apt) for p in "${PKGS[@]}"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'ok installed' || missing+=("$p"); done ;;
        pacman) for p in "${PKGS[@]}"; do pacman -Q "$p" >/dev/null 2>&1 || missing+=("$p"); done ;;
    esac
fi
if [ ${#missing[@]} -gt 0 ]; then
    say "Installing system packages: ${missing[*]}"
    [ "$PM" = apt ] && sudo apt-get update -qq || true
    if ! "${INSTALL[@]}" "${missing[@]}"; then
        warn "Installing them together failed; trying one at a time."
        for p in "${missing[@]}"; do "${INSTALL[@]}" "$p" >/dev/null 2>&1 || warn "  not available: $p"; done
    fi
fi
extra_deps

# Check that what the app needs is really there.
if ! python3 - "$ADW_MIN" <<'PY'
import sys
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw
need = tuple(int(x) for x in sys.argv[1].split("."))
have = (Adw.get_major_version(), Adw.get_minor_version())
if have < need:
    sys.exit(f"libadwaita {'.'.join(map(str, have))} is too old: {sys.argv[1]} or newer is needed.")
PY
then
    warn ""
    warn "$NAME needs Python 3 with PyGObject, GTK 4 and libadwaita $ADW_MIN or newer."
    warn "That means $DISTROS."
    [ -n "$PM" ] || warn "Install them with your package manager: $GENERIC_DEPS"
    exit 1
fi
check_optional

# Launcher, app-grid entry and icon.
chmod +x "$here/$APP"
mkdir -p "$BIN" "$APPS" "$ICONS"
ln -sf "$here/$APP" "$BIN/$APP"
install -m644 "$here/$APP_ID.svg" "$ICONS/$APP_ID.svg"
sed "s|^Exec=[^ ]*|Exec=$BIN/$APP|" "$here/$APP_ID.desktop" > "$APPS/$APP_ID.desktop"
refresh_caches
after_install

echo
say "✓ $NAME is installed. Open it from your app grid, or run: $APP"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  (add $BIN to your PATH to run '$APP' from a terminal)";; esac
[ "$here" = "$OPT" ] && echo "  To update, run the same command again. To remove: curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | bash -s -- --uninstall"
exit 0
}

main "$@"
