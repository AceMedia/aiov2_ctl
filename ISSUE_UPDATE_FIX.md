# Update Fix

This was caused by a recent history rewrite on our end — apologies for the inconvenience!

Your local repository diverged from the remote, which prevents the normal fast-forward update.

## Quick Fix

The installed `aiov2_ctl` has the old update code, so you need to reset the repo and reinstall directly:

```bash
cd ~/aiov2_ctl
git fetch origin
git reset --hard origin/main
chmod +x aiov2_ctl.py
sudo ./aiov2_ctl.py --install
```

After that, `aiov2_ctl --update` will work normally going forward.

## What Happened

We force-pushed a fix to the main branch, which rewrote the commit history. If you had already pulled the previous version, your local branch no longer matched the remote — causing the "Cannot fast-forward to multiple branches" error.

The latest version also includes a fix to make updates more robust going forward.
