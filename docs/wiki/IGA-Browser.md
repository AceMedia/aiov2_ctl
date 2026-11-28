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

## The top row

All of IGA's controls sit in one slim row, 28 pixels high. The pages are the app's own folder tabs, drawn like the
sub-tabs in Settings: the open page in the panel colour with the feature colour's stripe along its top, joined to
the page below; the others a little lower, tucked under it:

- **Back**, **Forward** and **Reload** (Stop while a page loads), as small pixel icons at the left;
- the open pages, each as a tab with the site's own icon (its favicon) and title. With many pages open the other
  tabs shrink to the favicon alone;
- the **current page's tab is the address bar**. It shows a padlock for a secure (https) page, an open padlock for
  one that is not secure (http), or the app's own mark for its start page, then the favicon, then the address
  with the site's name in the text colour and the rest of the address fainter. Hover over the padlock to see what it
  means. Click the tab, or press Ctrl+L (or Enter when the tabs have the keyboard), and the same tab opens across
  the whole row with the full address selected; type, then Enter. Escape, or clicking elsewhere, folds it back;
- **New page** (+), **Bookmark** and the **menu** at the right.

Every icon has a tooltip saying what it does and its key. A page without a favicon gets a small one in the app's
style. Favicons come from the site itself, as part of loading the page; IGA never asks another service for them.

The menu, the bookmarks list, naming a bookmark, downloads, find in page and messages such as the six-page limit
open as a small panel under the row, made of the app's own buttons and boxes. Nothing pops up over the app.

## Keys

| Key | What it does |
| --- | --- |
| Ctrl+L | Open the current page's tab into the address field |
| Enter (in the address field) | Open the address |
| Ctrl+T | New page |
| Ctrl+W | Close page |
| Ctrl+Tab, Ctrl+Shift+Tab | Next or previous IGA page (from the app's row of tabs they switch the app's tabs, as before) |
| Ctrl+R | Reload |
| Ctrl+F | Find in page |
| Ctrl+plus, Ctrl+minus, Ctrl+0 | Zoom in, out, back to normal |
| Alt+Left, Alt+Right | Back, Forward |
| f, F11, the full-screen button | Full screen (see below) |
| Escape | One step back out (see below) |

While a web page or the address field has the keyboard, the app's single-letter and number shortcuts do nothing,
so you can type. Inside a web page Tab moves between the page's links and boxes.

**Getting out: Escape, one step per press.** In this order, the first that applies:

1. an open panel (the menu, find, a download question...) closes;
2. the address field folds back into the current page's tab;
3. a page that is loading stops;
4. in full screen, one full-screen state back;
5. from a page, the keyboard moves to the current page's tab;
6. from the current page's tab (or any other IGA control), the keyboard moves to the app's row of tabs. IGA stays
   the selected tab; there the letters, numbers and Tab switch tabs again.

From the row of tabs, a further Escape is the app's own, as on every tab: it leaves full screen, then goes to the
first tab. IGA itself never changes which tab is selected.

## Full screen

On the IGA tab the app's full-screen control (the button at the top right, `f`, or Y on the gamepad) steps through
three states:

1. **Normal**: as on every tab.
2. **Browser full screen**: the window fills the screen and the app's own row of tabs and status bar are hidden, so
   IGA's row and the page fill it.
3. **Page full screen**: IGA's row is hidden too; only the web page shows.

Pressing the control again from page full screen goes back to normal. **F11** and **Escape** step back one state
at a time. Rest the pointer on the top edge of the screen and a small strip slides in at the top right, with
**Back one step** and **Leave full screen** (and, in page full screen, the page's address); it goes when the pointer
moves away. Ctrl+L in page full screen brings the row back just long enough to type an address.

A page that asks for full screen itself (a video) uses page full screen, and returns to where you were when it is
done. Choosing another tab of the app brings the app's tabs and status bar back. On every other tab the full-screen
control works exactly as before.

## What it looks like

IGA's own controls are the app's ordinary buttons, boxes and tabs, so they follow the app's theme (light, dark or
the system's), high contrast, the feature colour and the pixel fonts. The icons are drawn in the
app's pixel style in the button text colour. The pages that belong to the app (the start
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

Downloads always ask where to save: a panel asks "Save example.pdf to ~/Downloads?" with **Save**, **Choose
folder** (a list of your folders, in the app's own list) and **Cancel**. While you choose, the file is written into
IGA's own private folder (`pending/`) and moved where you said once it has finished; a cancelled download is
removed. The name is made safe: no folders, no leading dot, and a number added when a file of that name is already
there.

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

