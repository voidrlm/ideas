# Ideas

Simple lists of ideas and to-dos for GNOME, built with GTK 4 and libadwaita — with images and a
one-click hand-off to Claude Code.

- **Lists** with colors. Tick to-dos off; show or clear completed ones per list. Search all lists.
- **Quick add:** type and press Enter. `!`, `!!`, `!!!` set the priority; `#ListName` files it in
  another list.
- **Notes and images:** paste screenshots or copied images/files with `Ctrl+V` (into the add box,
  onto a selected to-do, or in its details), drag and drop them, or add them from a folder.
- **Folder-linked lists → Claude Code:** link a project folder to a list (Edit List…). Each to-do
  then gets a **Send to Claude Code** button that opens a terminal in that folder and runs
  `claude` with the to-do as the prompt. Its images go too: their paths are in the prompt and the
  image folder is passed with `--add-dir`, so Claude can open them without asking. The list menu
  can send all open to-dos at once. Sent to-dos are marked done (Undo in the toast reopens them).
- Undo for deletes and completions, keyboard shortcuts (`Ctrl+?`).

## Install

Requires Python 3, PyGObject, GTK 4 and libadwaita 1.8 or newer.

```sh
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita
./install.sh
```

This adds `ideas` to `~/.local/bin` and an entry to the app grid.

## Usage

```sh
ideas                                 # open the window
ideas --add "Try a dark theme !!"     # add a to-do without opening the window
ideas --new                           # open with the add box focused
```

The Claude Code option needs the `claude` command and a terminal (Ptyxis, GNOME Terminal,
Console, kitty, foot, WezTerm, Alacritty, Konsole or xterm).

## Data

Everything is stored in `~/.local/share/ideas/data.json`, saved after every change. Attached
images are kept next to it in `images/`; images no to-do uses any more are removed the next time
the app starts. Set `IDEAS_DATA=/path/to/file.json` to use another file.

Data from the app's earlier name, Reminders (`~/.local/share/reminders`), is moved over
automatically the first time Ideas starts.
