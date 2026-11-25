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
  can send all open to-dos at once. While Claude works the to-do shows **Claude is working…**;
  when Claude finishes its reply (a `Stop` hook passed with `--settings`) it is marked done, with
  Undo in the toast. If Claude Code is closed before it finishes, the to-do stays open. Sessions
  that finish while Ideas is closed are picked up the next time it opens.
- **Tasks from Microsoft Teams:** the button at the top of the sidebar (`Ctrl+T`) reads your unread
  Teams chats and adds the things you've been asked to do to a **Teams** list, with who asked and
  the original message in the notes. Chats are **not** marked as read. See below.
- **Commit and push:** a list whose folder has uncommitted (or unpushed) work shows a push button
  in the sidebar. It lists what changed, then commits everything with a message written by
  [`smart-commit`](#commit-and-push) and pushes. A folder that holds several repositories (say a
  backend and a frontend) is handled in one go.
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

The Claude Code and Teams options need the `claude` command; sending to Claude Code also needs a
terminal (Ptyxis, GNOME Terminal, Console, kitty, foot, WezTerm, Alacritty, Konsole or xterm).

## Teams

No Azure app or admin consent is involved: Ideas drives a private Chromium (Playwright) that is
signed in to Teams web, the way you would be in a browser. The first check sets this up by itself
(a virtualenv with Playwright in `~/.local/share/ideas/teams-venv`, the sign-in in
`teams-profile` next to it) and asks you to sign in once in a window; after that it runs unseen.

- **Nothing in Teams changes.** Normally Teams' own code doesn't even run: the chats are read with
  plain GET requests from a blank page. When the sign-in needs renewing, Teams is loaded briefly
  with every request that could change anything refused. Ideas keeps its own bookmark of how far
  it has looked in each chat, so a message is only considered once while staying unread in Teams.
- **Claude Code decides what is a task.** The new unread messages (with a little of the
  conversation around them) are passed to `claude -p`, with no tools, which returns the to-dos.
  This uses your Claude Code sign-in; nothing else leaves the machine.
- Run it by hand to see what it does: `echo '{}' | python3 ideas_teams.py --data-dir
  ~/.local/share/ideas --debug --dry-run` prints the chats it would look at.

## Commit and push

The push button runs the bundled `smart-commit -a -y` in each repository with uncommitted changes
(it stages everything and writes the message from the diff, in the repository's own commit
style), then `git push` (setting the upstream for a new branch). A commit whose push fails stays
committed; the button remains and only pushes next time.

`smart-commit` is a standalone script and works from a terminal too: `./smart-commit --help`.

## Data

Everything is stored in `~/.local/share/ideas/data.json`, saved after every change. Attached
images are kept next to it in `images/`; images no to-do uses any more are removed the next time
the app starts. Set `IDEAS_DATA=/path/to/file.json` to use another file.

Data from the app's earlier name, Reminders (`~/.local/share/reminders`), is moved over
automatically the first time Ideas starts.
