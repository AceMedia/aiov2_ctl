# Uninstall

`aiov2_ctl --uninstall` removes what this tool installed, and nothing else.

It never:

- removes a package (`gpsd`, `meshtasticd`, PyQt6 and the rest stay, since other things may use them);
- switches a rail or stops or starts a radio service;
- deletes your saved rail settings, or the ops app's logs and settings.

Removing the ops app or the ops console closes it if it is running and removes its start-menu shortcut. Their files are part of this tool's checkout and stay there.

If an older install left the app in a folder of its own, `~/ops-agentic`, that folder is removed only if it holds nothing of yours: no changed files, nothing unpushed, nothing stashed.

## See what it would do

```bash
aiov2_ctl --uninstall --dry-run
```

[![The uninstall plan](img/install/cli-02-uninstall-plan.webp)](img/install/cli-02-uninstall-plan.webp)

## Remove everything

```bash
sudo aiov2_ctl --uninstall
```

It shows the plan and asks first:

[![Uninstall asking before it removes anything](img/install/cli-03-uninstall-question.webp)](img/install/cli-03-uninstall-question.webp)

A running tray and ops app are closed before their files are removed. The result lists each feature:

[![The result of an uninstall](img/install/cli-04-uninstall-result.webp)](img/install/cli-04-uninstall-result.webp)

## Remove one feature

```bash
sudo aiov2_ctl --uninstall --only flipper
```

Whatever cannot work without the feature you name is removed with it (removing `sdr` also removes `adsb`; removing `tray` also removes `autostart`).

In the installer window, untick the feature and press "See the plan". The plan lists what will be removed first, and the button reads "Remove and install".

[![One feature unticked in the installer window](img/install/gui-03-one-feature-unticked.webp)](img/install/gui-03-one-feature-unticked.webp)

[![The plan when a feature is being removed](img/install/gui-04-plan-to-remove.webp)](img/install/gui-04-plan-to-remove.webp)

[![The installer window after removing a feature](img/install/gui-06-removed.webp)](img/install/gui-06-removed.webp)

## Putting it back

Run the installer again: see [Installation](Installation). The tray's login entry is not part of the usual set, so after a full uninstall and reinstall add it again with `aiov2_ctl --autostart`.

## What is left behind, on purpose

- `/usr/local/share/aiov2_ctl/config.json`: your saved rail settings.
- `~/.config/aiov2_ctl`, `~/.config/ops_gui`, `~/.local/state/ops_gui`: your settings and logs.
- The checkout in `~/aiov2_ctl-ace`, which holds the program and the ops app's files. Delete it yourself if you want it gone.
- Every package.
