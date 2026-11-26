import os
import site
import subprocess
import sys
import time

from .power import disable_feature, enable_feature, get_key

App = None
ComposeResult = None
Header = None
Footer = None
Static = None
Container = None
Vertical = None
Horizontal = None
Text = None
Panel = None
np = None
meshtastic = None

def _ensure_system_site_packages():
    """Ensure system site-packages are visible even if user site shadows them."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"

    if os.environ.get("PYTHONNOUSERSITE"):
        user_site = site.getusersitepackages()
        if user_site in sys.path:
            sys.path.remove(user_site)

    candidates = [
        f"/usr/local/lib/python{version}/dist-packages",
        f"/usr/lib/python{version}/dist-packages",
    ]

    for path in candidates:
        if os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)


# Optional Meshtastic TCP mode (e.g. meshtasticd on localhost)
MESHTASTIC_HOST = os.environ.get("MESHTASTIC_HOST")
try:
    MESHTASTIC_PORT = int(os.environ.get("MESHTASTIC_PORT", "4403"))
except ValueError:
    MESHTASTIC_PORT = 4403

MESHTASTIC_AVAILABLE = False


def _ensure_meshtastic_import():
    global meshtastic, MESHTASTIC_AVAILABLE

    try:
        import meshtastic as meshtastic_module
        import meshtastic.serial_interface  # noqa: F401
    except ImportError:
        meshtastic = None
        MESHTASTIC_AVAILABLE = False
        return False

    meshtastic = meshtastic_module
    MESHTASTIC_AVAILABLE = True
    return True


def _create_tcp_interface(host, port):
    """Create Meshtastic TCP interface across version variants."""
    from meshtastic import tcp_interface

    attempts = [
        {"hostname": host, "port": port},
        {"host": host, "port": port},
        {"ip": host, "port": port},
    ]

    last_error = None

    for kwargs in attempts:
        try:
            return tcp_interface.TCPInterface(**kwargs)
        except TypeError as e:
            last_error = e

    try:
        return tcp_interface.TCPInterface(host, port)
    except TypeError as e:
        last_error = e

    try:
        return tcp_interface.TCPInterface(host)
    except TypeError as e:
        last_error = e

    raise last_error


def _apply_default_meshtastic_tcp(enabled):
    """Apply default Meshtastic TCP env vars when LORA is enabled."""
    if not enabled.get("LORA"):
        return

    if os.environ.get("MESHTASTIC_HOST"):
        return

    os.environ["MESHTASTIC_HOST"] = "localhost"
    os.environ.setdefault("MESHTASTIC_PORT", "4403")

    global MESHTASTIC_HOST, MESHTASTIC_PORT
    MESHTASTIC_HOST = os.environ.get("MESHTASTIC_HOST")
    try:
        MESHTASTIC_PORT = int(os.environ.get("MESHTASTIC_PORT", "4403"))
    except ValueError:
        MESHTASTIC_PORT = 4403

def _restart_program():
    print("Restarting ops.py...\n")
    time.sleep(1)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def _ensure_textual_imports():
    global App, ComposeResult, Header, Footer, Static, Container, Vertical, Horizontal, Text, Panel

    try:
        from textual.app import App as textual_app, ComposeResult as compose_result
        from textual.widgets import Header as textual_header, Footer as textual_footer, Static as textual_static
        from textual.containers import Container as textual_container, Vertical as textual_vertical, Horizontal as textual_horizontal
        from rich.text import Text as rich_text
        from rich.panel import Panel as rich_panel
    except ImportError as e:
        print("\n" + "=" * 60)
        print("Textual TUI Library Required")
        print("=" * 60)
        print("The python3-textual package is installed but not importable.")
        print("This happens when hardware packages create user site-packages")
        print("that interfere with system packages.\n")
        print(f"Technical details: {e}\n")
        print("Install textual now to fix this? [y/n]: ", end="", flush=True)
        response = input().strip().lower()

        if response == "y":
            print("\nStep 1: Fixing directory permissions...")
            print("Running: sudo chown -R $USER:$USER ~/.pygpsclient")
            print("(You may be prompted for your password)\n")
            try:
                username = subprocess.check_output(["whoami"], text=True).strip()
                chown_result = subprocess.run(
                    ["sudo", "chown", "-R", f"{username}:{username}", os.path.expanduser("~/.pygpsclient")],
                    text=True,
                    capture_output=True,
                )
                if chown_result.returncode == 0:
                    print("✔ Permissions fixed\n")
                else:
                    print("⚠ Could not fix permissions (directory may not exist yet)\n")

                print("Step 2: Installing textual...")
                print("Running: pip3 install --break-system-packages textual\n")
                result = subprocess.run(["pip3", "install", "--break-system-packages", "textual"], text=True)
                if result.returncode == 0:
                    print("\n✔ textual installed successfully!")
                    _restart_program()
                print("\n✘ Installation failed.")
            except Exception as ex:
                print(f"\n✘ Installation error: {ex}")

        print("\nManual fix:")
        print("  sudo chown -R $USER:$USER ~/.pygpsclient")
        print("  pip3 install --break-system-packages textual\n")
        sys.exit(1)

    App = textual_app
    ComposeResult = compose_result
    Header = textual_header
    Footer = textual_footer
    Static = textual_static
    Container = textual_container
    Vertical = textual_vertical
    Horizontal = textual_horizontal
    Text = rich_text
    Panel = rich_panel


def _ensure_numpy_import():
    global np

    try:
        import numpy as numpy_module
    except ImportError as e:
        print("\n" + "=" * 60)
        print("NumPy Required")
        print("=" * 60)
        print("The 'numpy' package is required for SDR panadapter FFT rendering.\n")
        print(f"Technical details: {e}\n")
        print("Install numpy now to fix this? [y/n]: ", end="", flush=True)
        response = input().strip().lower()

        if response == "y":
            print("\nInstalling numpy...")
            print("Running: pip3 install --break-system-packages numpy\n")
            try:
                result = subprocess.run(["pip3", "install", "--break-system-packages", "numpy"], text=True)
                if result.returncode == 0:
                    print("\n✔ numpy installed successfully!")
                    _restart_program()
                print("\n✘ Installation failed.")
            except Exception as ex:
                print(f"\n✘ Installation error: {ex}")

        print("\nManual fix:")
        print("  pip3 install --break-system-packages numpy\n")
        sys.exit(1)

    np = numpy_module


def ensure_runtime_dependencies():
    _ensure_system_site_packages()
    _ensure_meshtastic_import()
    _ensure_textual_imports()
    _ensure_numpy_import()

def check_lora_dependencies(enabled):
    """Check if meshtastic library is installed and device is configured"""
    if not enabled.get("LORA"):
        return  # LORA not enabled, nothing to check
    
    # Check if library is installed
    if not MESHTASTIC_AVAILABLE:
        print("\n" + "="*50)
        print("LoRa Dependency Required")
        print("="*50)
        print("LORA is enabled but 'meshtastic' Python library is not installed.")
        print("This library is required for Meshtastic mesh networking.\n")
        print("Install it now? [y/n]: ", end='', flush=True)
        
        # Get single keypress
        while True:
            key = get_key().lower()
            if key in ['y', 'n']:
                print(key)  # Echo the choice
                break
        
        if key == 'y':
            print("\nInstalling meshtastic library...")
            print("Running: sudo pip3 install --break-system-packages --ignore-installed packaging meshtastic")
            print("(You may be prompted for your password)\n")
            
            try:
                result = subprocess.run(
                    ["sudo", "pip3", "install", "--break-system-packages", "--ignore-installed", "packaging", "meshtastic"],
                    text=True
                )
                
                if result.returncode == 0:
                    print("\n✔ meshtastic installed successfully!")
                    print("Note: Please restart ops.py for changes to take effect.\n")
                else:
                    print("\n✘ Installation failed. Please install manually:")
                    print("  sudo pip3 install --break-system-packages meshtastic\n")
            except Exception as e:
                print(f"\n✘ Installation error: {e}")
                print("Please install manually: sudo pip3 install --break-system-packages meshtastic\n")
        else:
            print("\nSkipping installation.")
            print("LoRa features will not be available until meshtastic is installed.\n")
        
        print("Press Enter to continue...")
        input()
        return
    
    # If using TCP mode, skip serial configuration
    if MESHTASTIC_HOST:
        print("\n" + "="*50)
        print("LoRa TCP Mode")
        print("="*50)
        print(f"Using TCP interface: {MESHTASTIC_HOST}:{MESHTASTIC_PORT}")
        print("Skipping serial configuration checks.\n")
        return

    # Library is installed, check if device needs configuration
    print("\n" + "="*50)
    print("LoRa Device Configuration")
    print("="*50)
    print("Checking if Meshtastic device is configured...")

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    sys_paths = [
        f"/usr/local/lib/python{version}/dist-packages",
        f"/usr/lib/python{version}/dist-packages",
    ]
    env["PYTHONPATH"] = ":".join(sys_paths + [env.get("PYTHONPATH", "")]).strip(":")
    
    # Test if device responds quickly
    test_result = subprocess.run(
        [
            "timeout",
            "3",
            sys.executable,
            "-c",
            "import meshtastic.serial_interface; meshtastic.serial_interface.SerialInterface()",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    
    if test_result.returncode == 0:
        print("✔ Device already configured and responding!\n")
        return
    
    # Device not responding, offer to power cycle first
    print("\n⚠ Device not responding (may need power cycle + configuration)")
    print("\nPower cycle LoRa device now? [y/n]: ", end='', flush=True)
    
    while True:
        key = get_key().lower()
        if key in ['y', 'n']:
            print(key)
            break
    
    if key == 'y':
        print("\nPower cycling LoRa device...")
        disable_feature("LORA")
        print("→ LoRa OFF (waiting 3 seconds...)")
        time.sleep(3)
        enable_feature("LORA")
        print("→ LoRa ON (waiting 5 seconds for enumeration...)")
        time.sleep(5)
        print("✔ Power cycle complete\n")
    
    print("Meshtastic devices require regional frequency configuration.")
    print("Configure region now? [y/n]: ", end='', flush=True)
    
    while True:
        key = get_key().lower()
        if key in ['y', 'n']:
            print(key)
            break
    
    if key != 'y':
        print("\nSkipping configuration.")
        print("Note: LoRa may not work until device is configured.\n")
        print("Press Enter to continue...")
        input()
        return
    
    # Show region options
    print("\n" + "="*50)
    print("Select Your Region")
    print("="*50)
    print("1) US          - United States (915 MHz)")
    print("2) EU_868      - Europe (868 MHz)")
    print("3) EU_433      - Europe (433 MHz)")
    print("4) AU          - Australia (915 MHz)")
    print("5) CN          - China (470 MHz)")
    print("6) JP          - Japan (920 MHz)")
    print("7) Skip        - Configure manually later")
    print("\nEnter choice [1-7]: ", end='', flush=True)
    
    region_choice = input().strip()
    
    region_map = {
        '1': 'US',
        '2': 'EU_868',
        '3': 'EU_433',
        '4': 'AU',
        '5': 'CN',
        '6': 'JP'
    }
    
    if region_choice == '7':
        print("\nSkipping configuration. You can configure manually with:")
        print("  meshtastic --set lora.region US")
        print("\nPress Enter to continue...")
        input()
        return
    
    region = region_map.get(region_choice)
    if not region:
        print(f"\n✘ Invalid choice: {region_choice}")
        print("Skipping configuration.\n")
        print("Press Enter to continue...")
        input()
        return
    
    # Apply configuration using Python API (with timeout protection)
    print(f"\nConfiguring device for region: {region}")
    print("Attempting quick connection test (15 second timeout)...\n")
    
    # Try Python API with subprocess timeout wrapper
    config_success = False
    
    # Create a test script that will timeout if device hangs
    test_script = f'''
import sys
import meshtastic.serial_interface
try:
    print("Connecting to device...", flush=True)
    iface = meshtastic.serial_interface.SerialInterface(devPath='/dev/ttyACM0')
    print("Connected! Setting region...", flush=True)
    iface.localNode.setRegion("{region}")
    print("SUCCESS", flush=True)
    iface.close()
    sys.exit(0)
except Exception as e:
    print(f"ERROR: {{e}}", flush=True)
    sys.exit(1)
'''
    
    try:
        result = subprocess.run(
            [sys.executable, "-c", test_script],
            capture_output=True,
            text=True,
            timeout=15,  # 15 second timeout
            env=env,
        )
        
        if "SUCCESS" in result.stdout:
            print(f"✔ Region set to {region} via Python API")
            config_success = True
        else:
            print(f"⚠ Python API connection failed or timed out")
            if result.stderr:
                print(f"Details: {result.stderr[:150]}")
            
    except subprocess.TimeoutExpired:
        print("✘ Device not responding (connection timeout after 15s)")
    
    if config_success:
        print("\nDevice is now configured!")
        print("LoRa should work in ops.py.\n")
    else:
        print("\n" + "="*50)
        print("ALTERNATIVE CONFIGURATION METHOD")
        print("="*50)
        print("The device is not responding to configuration attempts.")
        print("This is a known issue with some Meshtastic firmware.")
        print("\nTry configuring via Web GUI:")
        print("  1. Visit: http://client.meshtastic.org")
        print("  2. Click 'Connect' and select the serial device")
        print(f"  3. Go to Radio Configuration → LoRa")
        print(f"  4. Set Region to: {region}")
        print("  5. Click 'Save'\n")
        print("Or flash new firmware:")
        print("  https://flasher.meshtastic.org\n")
    
    print("Press Enter to continue...")
    input()
