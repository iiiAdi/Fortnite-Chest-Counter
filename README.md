# Fortnite-Chest-Counter

A chest counter with friends for the pirate token!

A small always-on-top overlay for Windows that counts the chests you open. You press a button each time you open one, and your season total goes to a live friends leaderboard.

> Not affiliated with or endorsed by Epic Games. The app never reads, injects into, or changes Fortnite. It only listens for your own hotkeys and mouse buttons. Use it at your own risk.

## What it shows

- **Found chests**: chests in your current match (reset with a new game)
- **Current Session chests**: chests since you opened the app
- **Season**: your total for the season, saved between sessions

Your progress is saved automatically in `%APPDATA%\ChestCounter`.

## Install

1. Go to the **Releases** page of this repository and download `chest_counter.exe`.
2. Run it. Windows may warn that the app is unrecognized, because it isn't code-signed. Click **More info > Run anyway**.
3. Set Fortnite to **Windowed Fullscreen**, otherwise the overlay can't show on top of the game.

## Controls

| Action | Control |
| --- | --- |
| Add a chest | Mouse side button (upper) |
| Remove a chest (undo) | Mouse side button (lower) |
| New game | Numpad `-` |
| Reset season total | `Ctrl+Shift+F9`, pressed twice within 3 seconds |
| Lock / unlock the overlay | `Ctrl+Shift+F10` |
| Quit | `Ctrl+Shift+F12` |

The overlay starts **locked**, so mouse clicks go straight through it to the game. Unlock it to drag it somewhere else or to right-click it for the menu, then lock it again.

If your mouse's upper and lower side buttons are the other way round, swap `MOUSE_ADD_BUTTON` and `MOUSE_UNDO_BUTTON` at the top of `chest_counter.py`. If your mouse software remaps those buttons to keyboard keys or macros, set them back to the default Back and Forward actions.

## Friends leaderboard

1. The person running the group sends you a **leaderboard link**.
2. On first launch, a window asks for that link and your name. Paste the link, type your name, and press **Save**. It shows "Connected!" when it works.
3. Your season total is sent automatically. Open the same link in a browser, on your phone too, to see the ranking live.
4. A green dot next to a name means their app is online right now.

You can reopen the settings any time: unlock the overlay, right-click it, and choose **Leaderboard settings...**.

When the group owner starts a new season, everyone's season total resets automatically the next time the app syncs.

The counts are typed in by hand, so the leaderboard is built on trust between friends.

## Running your own group (for the owner)

The leaderboard runs for free on Google Apps Script, with no server needed.

1. Go to [script.google.com](https://script.google.com), create a **New project**, and paste in the contents of `Code.gs`.
2. Change `SECRET` to a long random string. Never publish your real secret.
3. Click **Deploy > New deployment**, choose **Web app**, set *Execute as* to **Me** and *Who has access* to **Anyone**, then deploy and copy the `/exec` URL.
4. Your leaderboard link is `<that URL>?key=<your SECRET>`. Send it only to your friends, since anyone with the link can join.
5. Optional: set `DOWNLOAD_URL` in `Code.gs` to this repository's releases page to show a **Download the app** button on the leaderboard.

To start a new season, open the script project, choose the function `newSeason` in the toolbar, and press **Run**.

If you edit `Code.gs` later, publish a new version under **Deploy > Manage deployments**, or the old code keeps running.

## Build it yourself

Python 3 on Windows, no extra packages needed:

```
pip install pyinstaller
pyinstaller --onefile --noconsole chest_counter.py
```

The result is `dist\chest_counter.exe`.
