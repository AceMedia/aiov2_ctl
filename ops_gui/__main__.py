import sys

from . import launchers

code = launchers.command(sys.argv[1:])
if code is not None:
    sys.exit(code)

from .app import main

sys.exit(main())
