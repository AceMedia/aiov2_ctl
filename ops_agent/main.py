import argparse
import traceback

from . import bootstrap, companions, power, wifi


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-prompts", action="store_true",
                        help="keep every rail as it is, with no questions on the way in or out "
                             "(used when swapping with the native app)")
    parser.add_argument("--upconverter", metavar="MHZ",
                        help="set and remember an upconverter offset: off, ham-it-up (125 MHz) or a number of MHz")
    args = parser.parse_args()
    if args.upconverter is not None:
        from .sdr import parse_upconverter_arg, save_upconverter_mhz
        offset = parse_upconverter_arg(args.upconverter)
        if offset is None:
            parser.error("--upconverter takes off, ham-it-up or a number of MHz")
        save_upconverter_mhz(offset)
        print(f"Upconverter offset saved: {'off' if not offset else f'+{offset:g} MHz'}")

    power.set_debug(args.debug)

    status = power.get_aio_status()
    enabled = power.startup_enabled(status) if args.no_prompts else power.prompt_for_power(status)

    found = companions.get_companions()
    line = companions.summary(found)
    if line:
        power.log(line)
    if found.get("usb_rail") == "off" and not found.get("devices"):
        power.debug("USB rail is off, so no companions can show up")

    bootstrap.ensure_runtime_dependencies()
    bootstrap._apply_default_meshtastic_tcp(enabled)
    bootstrap.check_lora_dependencies(enabled)

    try:
        from .ui import OpsApp
        app = OpsApp(enabled)
        app.companions = found
        app.wifi_radios = wifi.get_radios()
        app.run()
    except Exception as e:
        print("\n\n" + "=" * 50)
        print("APPLICATION CRASH")
        print("=" * 50)
        print(f"Error: {e}")
        print(f"Type: {type(e).__name__}")
        traceback.print_exc()
        print("=" * 50)
        print("\nPress Enter to continue...")
        input()

    if not args.no_prompts:
        power.prompt_for_shutdown(enabled)
