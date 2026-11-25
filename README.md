# Reminders

A to-do list and reminders app for GNOME, built with GTK 4 and libadwaita.

- **Smart lists:** Today, Scheduled, All, Flagged and Completed, plus your own colored lists.
- **Quick add with natural dates:** type `Call mom tomorrow 6pm`, `Pay rent on 1st dec`,
  `Gym every monday 7am`, `Take meds in 20 min`, `!!` for priority, `#flag`, `#ListName`.
  A preview shows what was understood before you press Enter.
- **Notifications on time**, with *Mark as Done* and *Snooze* buttons. Reminders missed while
  the computer was off are shown when it starts again.
- **Repeats:** daily, weekdays, weekly, monthly and yearly. Completing one schedules the next.
- **Folder-linked lists → Claude Code:** link a project folder to a list (Edit List…). Each
  to-do then gets a **Send to Claude Code** button that opens a terminal in that folder and
  runs `claude` with the to-do (title and notes) as the prompt. The list menu can send all
  open to-dos at once.
- Search, notes, undo for deletes and completions, keyboard shortcuts (`Ctrl+?`).

## Install

Requires Python 3, PyGObject, GTK 4 and libadwaita 1.8 or newer.

```sh
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita
./install.sh
```

This adds `reminders` to `~/.local/bin` and an entry to the app grid.

## Usage

```sh
reminders                                  # open the window
reminders --add "Pay rent on 1st dec"      # add a reminder without opening the window
reminders --new                            # open with the add box focused
```

By default the app keeps running in the background after you close its window, and starts at
login, so reminders can notify on time. Turn this off in Preferences → *Run in Background*.
**Quit** from the menu (`Ctrl+Q`) stops reminders until the app is opened again.

The Claude Code option needs the `claude` command and a terminal (Ptyxis, GNOME Terminal,
Console, kitty, foot, WezTerm, Alacritty, Konsole or xterm).

## Data

Everything is stored in `~/.local/share/reminders/data.json`, saved after every change.
Set `REMINDERS_DATA=/path/to/file.json` to use another file.
