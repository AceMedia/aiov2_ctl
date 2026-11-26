import json
import socket
import time

from .power import debug, log

class GPS:
    def __init__(self):
        self.sock = None
        self.buffer = ""
        self.connected = False
        self.last_message_time = None
        self.message_count = 0
        self.sky_sats = 0
        self.sky_data = []
        self.sky_quality = {}
        self.last_tpv = None
        self.connection_time = time.time()
        
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect(("127.0.0.1", 2947))
            self.sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
            self.sock.setblocking(False)
            self.connected = True
            debug("gpsd socket connected")
            log("gpsd socket connected on 127.0.0.1:2947")
        except Exception as e:
            debug(f"gpsd connect failed: {e}")
            log(f"gpsd connection failed: {e}")
            self.sock = None

    def get_status(self):
        """Return diagnostic status string"""
        if not self.connected:
            return "gpsd socket unavailable"
        
        elapsed = time.time() - self.connection_time
        if self.message_count == 0:
            if elapsed > 10:
                return f"gpsd connected but no data for {int(elapsed)}s (device not bound?)"
            else:
                return "gpsd connected, waiting for data..."
        
        if self.last_message_time:
            since_last = time.time() - self.last_message_time
            if since_last > 5:
                return f"gpsd data stalled ({int(since_last)}s ago, {self.message_count} msgs total)"
        
        return f"gpsd receiving data ({self.message_count} msgs, {self.sky_sats} sats)"

    def read(self):
        if not self.sock:
            return None

        try:
            data = self.sock.recv(4096).decode(errors="ignore")
        except BlockingIOError:
            return None
        except Exception as e:
            debug(f"gpsd read error: {e}")
            return None

        if not data:
            return None

        self.buffer += data
        if "\n" not in self.buffer:
            return None

        complete, self.buffer = self.buffer.rsplit("\n", 1)

        tpv_result = None
        
        for line in complete.splitlines():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_class = msg.get("class")
            
            # Count any JSON message as activity
            if msg_class:
                self.message_count += 1
                self.last_message_time = time.time()
            
            # Track satellite visibility from SKY messages
            if msg_class == "SKY":
                sats = msg.get("satellites", [])
                self.sky_data = []
                for sat in sats:
                    self.sky_data.append(
                        {
                            "prn": sat.get("PRN") or sat.get("gnssid") or sat.get("svid"),
                            "az": sat.get("az"),
                            "el": sat.get("el"),
                            "ss": sat.get("ss"),
                            "used": bool(sat.get("used", False)),
                        }
                    )
                self.sky_sats = len([s for s in sats if s.get("used", False)])
                self.sky_quality = {
                    "visible": len(sats),
                    "used": self.sky_sats,
                    "hdop": msg.get("hdop"),
                    "vdop": msg.get("vdop"),
                    "pdop": msg.get("pdop"),
                    "gdop": msg.get("gdop"),
                    "tdop": msg.get("tdop"),
                }
                debug(f"GPS SKY: {len(sats)} visible, {self.sky_sats} used")
                
            # Return TPV data for display
            if msg_class == "TPV":
                debug("GPS TPV update")
                tpv_result = {
                    "lat": msg.get("lat"),
                    "lon": msg.get("lon"),
                    "alt": msg.get("alt"),
                    "mode": msg.get("mode"),
                    "time": msg.get("time"),
                    "speed": msg.get("speed"),
                    "track": msg.get("track"),
                    "climb": msg.get("climb"),
                    "epx": msg.get("epx"),
                    "epy": msg.get("epy"),
                    "epv": msg.get("epv"),
                    "eps": msg.get("eps"),
                    "epc": msg.get("epc"),
                }
                self.last_tpv = tpv_result
        
        return tpv_result
