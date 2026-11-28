# IGA browser

IGA is a small web browser that lives in its own tab of the [Ops App](Ops-App). It has an address bar, Back,
Forward and Reload, up to six open pages, bookmarks, find in page, zoom and downloads. It is named after the
ninja training region.

It is optional and off by default. Until you install it, the app shows nothing of it: no tab, no setting, no
start-menu entry.

It uses Qt WebEngine (the Chromium engine, through PyQt6), the same engine as a full desktop browser, so ordinary
web sites work. That engine is not small: see [Memory](#memory) before you install it on a small Raspberry Pi.

## Installing and removing

In the app: **Settings > Features**, add **IGA browser**. From the command line, run the installer and pick the
feature called `browser`. Either way the installer:

- installs the package `python3-pyqt6.qtwebengine`;
- writes an empty switch file, `~/.local/share/aiov2_ctl/iga/enabled` (the folder is readable only by you);
- adds a start-menu entry, **IGA browser**, that opens the app straight on the IGA tab
  (`python3 -m ops_gui --tab IGA`).

Restart the app to see the tab. Removing the feature takes away the switch file and the start-menu entry. Your
bookmarks and browser data stay in `~/.local/share/aiov2_ctl/iga` until you delete that folder, and the package
stays installed, as with every feature.

The engine is not loaded when the app starts, nor when the tab is made: only the first time you open the IGA tab.
If the package is missing or broken, the tab says so and gives the command that fixes it:

```
sudo apt install python3-pyqt6.qtwebengine
```

## Keys

| Key | What it does |
| --- | --- |
| Ctrl+L | Go to the address bar |
| Enter (in the address bar) | Open the address |
| Ctrl+T | New page |
| Ctrl+W | Close page |
| Ctrl+Tab, Ctrl+Shift+Tab | Next or previous page |
| Ctrl+R | Reload |
| Ctrl+F | Find in page |
| Ctrl+plus, Ctrl+minus, Ctrl+0 | Zoom in, out, back to normal |
| Alt+Left, Alt+Right | Back, Forward |
| Escape | One step back out (see below) |

While a web page or the address bar has the keyboard, the app's single-letter and number shortcuts do nothing,
so you can type. Inside a web page Tab moves between the page's links and boxes.

**Getting out of a page: Escape twice.** Escape goes back one step at a time, as it does everywhere in the app:

1. it closes the find bar, if it is open;
2. from a page that is loading it stops the loading;
3. from a page it moves you to the address bar;
4. from the address bar it moves you to the app's row of tabs, where the letters, numbers and Tab switch tabs
   again.

The other controls (Back, Forward, Reload, Bookmark and the Menu) are reached with the D-pad highlight like the
buttons on every other tab. The Menu holds New page, Close page, Start page, Bookmarks, Downloads, Find in page,
Zoom and **Clear data now**.

## What it looks like

IGA's own controls are the app's ordinary buttons, boxes and tabs, so they follow the app's theme (light, dark or
the system's), high contrast, the feature colour and the pixel fonts. The pages that belong to the app (the start
page, the "that is not an address" page and the page shown when a site cannot be loaded) are drawn in the app's
colours too and are redrawn when you change the theme.

Web pages keep their own look: IGA never changes another site's styles. The engine's own bits that sit over a page
are left as the engine draws them: the right-click menu on a page, the scroll bars inside a page and the boxes a
page's JavaScript opens.

## The address bar

- An address with `http://` or `https://` opens as it is.
- A site's name, with or without a path or a port (`example.org`, `example.org/news`), opens with `https://`.
  `localhost`, `.local` names and addresses on your own network (such as `192.168.0.10`) open with `http://`.
- `file://`, `javascript:`, `data:`, `chrome:`, `view-source:` and other kinds of address are refused from the
  address bar, with a line saying why.
- Anything else is not an address. By default it is **not sent anywhere**; the page says
  "That is not an address. Choose a search engine in Settings > IGA to search from the address bar."

In **Settings > IGA** you can choose **No search (addresses only)** (the default), **DuckDuckGo**, or **Custom**: a
search address with `%s` where the words go, such as `https://example.org/search?q=%s`. With a search engine
chosen, what you type goes to that engine and nowhere else.

## What is kept on the device, and where

Everything is in `~/.local/share/aiov2_ctl/iga`, a folder only you can read:

| What | Where | Notes |
| --- | --- | --- |
| The switch | `enabled` | Written by the installer. |
| Bookmarks | `bookmarks.json` | Readable only by you. A damaged file is treated as empty and left alone until you add a bookmark. |
| Visited pages | `history.json` | **Off by default.** Only when **Remember visited pages** is on: up to 500 pages. **Clear history** empties it. |
| Cookies (signed-in sessions), site storage, cache | `profile/` | Kept between runs, as in any browser, so sites keep you signed in. |

**Clear data now** (in the Menu) removes cookies (which is what keeps you signed in to sites), site storage, the cache and the history at once; bookmarks are
kept. **Clear everything when the app closes** (Settings > IGA, off by default) does the same each time the app
closes.

Downloads always ask where to save, starting in `~/Downloads`. The name offered is made safe: no folders, no
leading dot, and a number added when a file of that name is already there.

## What it sends

IGA loads only what you ask for. It sends nothing of its own: no usage reports, no start page from the internet
(the start page is built by the app from your bookmarks), and no spelling, translation or search service unless you
choose a search engine. The engine is told not to fetch ahead (no DNS prefetching, no Chromium background
networking), to keep WebRTC to public interfaces only, and to let the app's own pages reach neither the network nor
your files.

The sites you visit can still do what web sites do: load things from other sites, set cookies and run scripts.

## What it refuses

- A page asking for your location, camera, microphone, notifications or screen is refused, without a question,
  in this first version.
- Pages cannot go full screen.
- Links that open a new window open as a new IGA page instead, up to six. With six open, IGA says "Six pages is
  the most IGA keeps open, to save memory. Close one first."
- Chromium's sandbox stays on. Web security is never switched off.

## Memory

Measured on a uConsole with a CM5: the engine takes about **220 MB** with a blank page, and about **350 MB** with
an ordinary page open. Each further page adds to that. It is fine on a CM4 or CM5 with 2 GB or more; it would be a
poor fit for a 512 MB Pi.

## Not there yet

This is the first version. Not in it:

- a repository of its own, and builds for Windows or macOS;
- ad and tracker blocking;
- reader mode;
- extensions;
- sync between devices;
- printing;
- per-site permissions (everything that asks is refused for now).

