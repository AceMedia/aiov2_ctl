import subprocess
import threading
import time
from math import atan2, cos, degrees, radians, sin, sqrt

from rich.markup import escape

from . import bootstrap, flipper, handoff, power, wifi
from .bootstrap import App, ComposeResult, Container, Footer, Header, Horizontal, Panel, Static, Text, Vertical
from .gps import GPS
from .lora import LoRa
from .preferences import export_telemetry_snapshot, load_preferences, save_preferences
from .power import debug, log
from .sdr import SDR, level_bar

class GPSWidget(Static):
    """GPS display widget with 3-column layout"""
    
    def __init__(self, gps_obj, enabled):
        super().__init__()
        self.gps_obj = gps_obj
        self.enabled = enabled
        self.gps_data = None
    
    def update_data(self, gps_data):
        self.gps_data = gps_data
        self.refresh()

    def _format_coord(self, value, digits=5):
        if value is None:
            return "-"
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return str(value)

    def _format_metric(self, value, digits=1, suffix=""):
        if value is None:
            return "-"
        try:
            return f"{float(value):.{digits}f}{suffix}"
        except (TypeError, ValueError):
            return f"{value}{suffix}"

    def _sky_plot_lines(self):
        width = 15
        height = 9
        grid = [[" " for _ in range(width)] for _ in range(height)]
        cx = width // 2
        cy = height // 2
        radius = min(cx, cy)

        for y in range(height):
            for x in range(width):
                dx = x - cx
                dy = y - cy
                dist = (dx * dx + dy * dy) ** 0.5
                if abs(dist - radius) < 0.45:
                    grid[y][x] = "·"
                elif abs(dist - (radius / 2)) < 0.4:
                    grid[y][x] = "·"

        grid[0][cx] = "N"
        grid[height - 1][cx] = "S"
        grid[cy][0] = "W"
        grid[cy][width - 1] = "E"
        grid[cy][cx] = "+"

        satellites = list(self.gps_obj.sky_data) if self.gps_obj else []
        visible = len(satellites)
        used = sum(1 for sat in satellites if sat.get("used"))

        for sat in satellites:
            az = sat.get("az")
            el = sat.get("el")
            if az is None or el is None:
                continue
            try:
                az = float(az)
                el = float(el)
            except (TypeError, ValueError):
                continue
            ring = max(0.0, min(1.0, (90.0 - el) / 90.0))
            plot_radius = ring * max(1.0, radius - 1)
            x = int(round(cx + sin(radians(az)) * plot_radius))
            y = int(round(cy - cos(radians(az)) * plot_radius))
            if 0 <= x < width and 0 <= y < height:
                grid[y][x] = "●" if sat.get("used") else "○"

        lines = ["".join(row) for row in grid]
        lines.append(f"Vis:{visible} Used:{used}")
        return lines
    
    def render(self):
        if not self.enabled:
            return Panel("GPS OFF", title="GPS", border_style="dim")
        
        # Get status for title
        if self.gps_obj:
            status = self.gps_obj.get_status()
        else:
            status = "gpsd init failed"
        
        # Column 1: Position data
        if self.gps_data:
            fix = {0: "NO FIX", 1: "NO FIX", 2: "2D", 3: "3D"}.get(self.gps_data.get("mode"), "?")
            hdop = self.gps_obj.sky_quality.get("hdop") if self.gps_obj else None
            position = (
                f"Fix: {fix}\n"
                f"Lat: {self._format_coord(self.gps_data.get('lat'))}\n"
                f"Lon: {self._format_coord(self.gps_data.get('lon'))}\n"
                f"Alt: {self._format_coord(self.gps_data.get('alt'), 1)}\n"
                f"Spd: {self._format_metric(self.gps_data.get('speed'), 1, 'm/s')}\n"
                f"Trk: {self._format_metric(self.gps_data.get('track'), 0, '°')}\n"
                f"HAcc: {self._format_metric(self.gps_data.get('epx'), 1, 'm')}\n"
                f"VAcc: {self._format_metric(self.gps_data.get('epv'), 1, 'm')}\n"
                f"HDOP: {self._format_metric(hdop, 1)}\n"
                f"UTC: {self.gps_data.get('time') or '-'}"
            )
        else:
            position = "Waiting for fix..."
        
        # Column 2: Satellites visualization
        satellites = "\n".join(self._sky_plot_lines())
        
        # Format as 2 columns
        content = f"[bold cyan]Position[/bold cyan]                   [bold cyan]Satellites[/bold cyan]\n"
        content += f"{position.split(chr(10))[0]:<30} {satellites.split(chr(10))[0]:<30}\n"
        
        # Add remaining lines
        position_lines = position.split("\n")
        satellites_lines = satellites.split("\n")
        max_lines = max(len(position_lines), len(satellites_lines))
        
        for i in range(1, max_lines):
            p = position_lines[i] if i < len(position_lines) else ""
            sat = satellites_lines[i] if i < len(satellites_lines) else ""
            content += f"{p:<30} {sat:<30}\n"
        
        return Panel(Text.from_markup(content), title=f"GPS - {status}", border_style="green")


class SDRWidget(Static):
    """SDR display widget with status + spectrum waterfall"""
    
    def __init__(self, sdr_obj, enabled, ops_app=None):
        super().__init__()
        self.sdr_obj = sdr_obj
        self.enabled = enabled
        self.ops_app = ops_app
    
    def render(self):
        if not self.enabled:
            return Panel("SDR OFF", title="SDR", border_style="dim")
        
        if not self.sdr_obj:
            return Panel("SDR powered (init failed)", title="SDR", border_style="red")
        
        # Calculate available width (accounting for panel borders and padding)
        available_width = max(40, self.size.width - 4) if self.size.width > 0 else 70
        
        s_meter = ""
        if self.sdr_obj.s_meter_db is not None:
            s_units = int((self.sdr_obj.s_meter_db + 127) / 6)
            s_units = max(0, min(9, s_units))
            filled = "▮" * s_units
            empty = "▯" * (9 - s_units)
            s_meter = f"  [green]{filled}{empty}[/green] S{s_units}"

        span = self.sdr_obj.sample_rate / 1e6
        if_bw_khz = self.sdr_obj.if_bw_khz
        preset = self.sdr_obj.current_preset()
        channel = self.sdr_obj.current_channel()
        active_profile = self.sdr_obj.active_band_profile()
        title_digits = active_profile.get("freq_digits", 2) if active_profile else 2
        preset_label = ""
        if self.sdr_obj.band_profile_name:
            preset_label = f" | {self.sdr_obj.band_profile_name}"
        elif preset and abs(self.sdr_obj.freq_mhz - preset[1]) <= 0.25:
            preset_label = f" | {preset[0]}"
        channel_label = ""
        if channel:
            plan = active_profile
            plan_name = plan.get("channel_plan") if plan else None
            if plan_name:
                channel_label = f" | {plan_name} Ch {channel['channel']}"
        toggle_bits = [
            f"Aud {'ON' if self.sdr_obj.audio_enabled else 'OFF'}",
            f"Bias {'ON' if self.sdr_obj.bias_t_enabled else 'OFF'}",
            f"Sq {'AUTO' if self.ops_app and self.ops_app.auto_squelch_enabled else 'OFF'}",
        ]
        toggle_label = " | " + " ".join(toggle_bits)
        if self.sdr_obj.upconverter_mhz:
            toggle_label += f" | Upconv +{self.sdr_obj.upconverter_mhz:g}"
        title = (
            f"SDR | {self.sdr_obj.modulation} | {self.sdr_obj.freq_mhz:.{title_digits}f} MHz | "
            f"IF {if_bw_khz} kHz | Span {span:.1f} MHz{preset_label}{channel_label}{toggle_label}{s_meter}"
        )
        
        if self.sdr_obj.spectrum:
            # Calculate dB range for title
            min_db = min(self.sdr_obj.spectrum)
            max_db = max(self.sdr_obj.spectrum)
            
            # Show single spectrum with overview range and green position indicator
            spectrum = self.sdr_obj.get_overview_display(
                width=available_width,
                height=10,
                bandwidth_mhz=self.ops_app.sdr_bandwidth if self.ops_app else 1.0,
            )
            
            peak_lines = []
            for peak in self.sdr_obj.strongest_peaks(limit=3):
                label = f" {peak['preset']}" if peak.get("preset") else ""
                peak_lines.append(
                    f"[bold cyan]{peak['freq_mhz']:.2f}[/bold cyan] MHz "
                    f"[green]{peak['db']:.1f} dB[/green]{escape(label)}"
                )
            if peak_lines:
                spectrum += "\n[bold yellow]Top peaks:[/bold yellow] " + " | ".join(peak_lines)

            station_lines = []
            for candidate in self.sdr_obj.station_scan_candidates(limit=4):
                tolerance = candidate.get("tune_tolerance_mhz", 0.05)
                marker = ">" if abs(candidate["freq_mhz"] - self.sdr_obj.freq_mhz) <= tolerance else " "
                label = f" {candidate['preset']}" if candidate.get("preset") else ""
                if candidate.get("channel") is not None:
                    plan = candidate.get("plan")
                    label += f" {plan or 'CB'} Ch{candidate['channel']}"
                digits = candidate.get("freq_digits", 2)
                station_lines.append(
                    f"{marker}[cyan]{candidate['freq_mhz']:.{digits}f}[/cyan]"
                    f" [green]{candidate['db']:.1f}[/green]{escape(label)}"
                )
            if station_lines:
                band_label = self.sdr_obj.active_band_profile()
                band_name = band_label["name"] if band_label else "Band"
                spectrum += f"\n[bold magenta]{escape(band_name)} scan:[/bold magenta] " + " | ".join(station_lines)

            spectrum += "\n" + self._level_line()

            body = Text.from_markup(spectrum)
            combined = body
        else:
            if not self.sdr_obj.active:
                msg = escape(self.sdr_obj.get_status())
            elif self.sdr_obj.link_state != "ok" and self.sdr_obj.last_error:
                msg = f"[bold red]{escape(self.sdr_obj.last_error)}[/bold red]"
                if self.sdr_obj.link_state == "retrying":
                    msg += "\nRetrying..."
                else:
                    msg += "\nPress a tune key (Up or Down) to try again"
            elif not self.sdr_obj.panadapter_proc:
                msg = "SDR not started"
            elif self.sdr_obj.panadapter_proc and not self.sdr_obj.initial_fft_done:
                msg = "Acquiring spectrum..."
            else:
                msg = "Waiting for data..."
            combined = Text.from_markup(msg)

        if self.sdr_obj.audio_error:
            combined = Text.from_markup(f"[red]{escape(self.sdr_obj.audio_error)}[/red]\n") + combined
        problem = self.sdr_obj.link_state != "ok" or not self.sdr_obj.active
        return Panel(combined, title=title, border_style="red" if problem else "cyan")

    def _level_line(self):
        """One line: signal above noise bar, squelch state, audio state."""
        sdr = self.sdr_obj
        snr = sdr.snr_db()
        if snr is None:
            return "[bold]Level:[/bold] --"
        if sdr.squelch_level > 0:
            squelch = "[green]open[/green]" if sdr.squelch_open else "[dim]shut[/dim]"
            squelch = f" | Sq {sdr.squelch_level} {squelch}"
        else:
            squelch = " | Sq off"
        return (
            f"[bold]Level:[/bold] [green]{level_bar(snr)}[/green] "
            f"{snr:+.0f} dB over noise{squelch}"
        )


class LoRaWidget(Static):
    """LoRa display widget"""
    VIEW_MODES = ["radar", "alerts", "checkins", "lost", "detail"]
    
    def __init__(self, lora_obj, enabled, gps_obj=None):
        super().__init__()
        self.lora_obj = lora_obj
        self.enabled = enabled
        self.gps_obj = gps_obj
        self.view_mode = "radar"
        self.selected_node_id = None
        self.last_rendered_node_ids = []
        self._render_node_history = {}

    def cycle_view(self, step=1):
        current = self.VIEW_MODES.index(self.view_mode) if self.view_mode in self.VIEW_MODES else 0
        self.view_mode = self.VIEW_MODES[(current + step) % len(self.VIEW_MODES)]
        self.refresh()
        return self.view_mode

    def cycle_selected_node(self, step=1):
        if not self.last_rendered_node_ids:
            return None
        if self.selected_node_id not in self.last_rendered_node_ids:
            self.selected_node_id = self.last_rendered_node_ids[0]
        current = self.last_rendered_node_ids.index(self.selected_node_id)
        self.selected_node_id = self.last_rendered_node_ids[(current + step) % len(self.last_rendered_node_ids)]
        self.refresh()
        return self.selected_node_id

    def _view_label(self):
        return {
            "radar": "Radar",
            "alerts": "Alerts",
            "checkins": "Check-ins",
            "lost": "Lost",
            "detail": "Detail",
        }.get(self.view_mode, "Radar")

    def _get_position(self, info):
        if not isinstance(info, dict):
            return None
        pos = info.get("position") or {}
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is None or lon is None:
            return None
        try:
            return (float(lat), float(lon))
        except (TypeError, ValueError):
            return None

    def _haversine_km(self, lat1, lon1, lat2, lon2):
        from math import radians, sin, cos, sqrt, asin

        r = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        c = 2 * asin(sqrt(a))
        return r * c

    def _format_age(self, seconds):
        if seconds is None:
            return None
        from .columns import age_words
        return age_words(seconds)

    def _bearing_deg(self, lat1, lon1, lat2, lon2):
        lat1_r = radians(lat1)
        lat2_r = radians(lat2)
        dlon_r = radians(lon2 - lon1)
        y = sin(dlon_r) * cos(lat2_r)
        x = cos(lat1_r) * sin(lat2_r) - sin(lat1_r) * cos(lat2_r) * cos(dlon_r)
        return (degrees(atan2(y, x)) + 360.0) % 360.0

    def _bearing_cardinal(self, bearing):
        points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return points[int((bearing + 22.5) // 45) % len(points)]

    def _last_heard_seconds(self, info):
        if not isinstance(info, dict):
            return None

        last_heard = info.get("lastHeard") or info.get("lastHeardTime")
        if last_heard is None:
            position = info.get("position")
            if isinstance(position, dict):
                last_heard = position.get("time")

        if last_heard is None:
            return None

        try:
            value = float(last_heard)
        except (TypeError, ValueError):
            return None

        if value > 1_000_000_000_000:
            value /= 1000.0

        return max(0.0, time.time() - value)

    def _last_heard_age(self, info):
        return self._format_age(self._last_heard_seconds(info))

    def _extract_node_metrics(self, info):
        if not isinstance(info, dict):
            return {}

        metrics = {}
        candidates = [info]
        for key in ("deviceMetrics", "powerMetrics", "localStats"):
            candidate = info.get(key)
            if isinstance(candidate, dict):
                candidates.append(candidate)

        for candidate in candidates:
            for key in ("batteryLevel", "voltage", "channelUtilization", "airUtilTx"):
                value = candidate.get(key)
                if value is not None and key not in metrics:
                    metrics[key] = value

        return metrics

    def _activity_tag(self, activity):
        if activity.get("sent"):
            return "TX"
        return {
            "text": "TXT",
            "position": "POS",
            "telemetry": "TEL",
            "user": "ID",
            "event": "EVT",
        }.get(activity.get("kind"), "RX")

    def _format_activity_line(self, activity, col_width):
        tag = self._activity_tag(activity)
        from_str = str(activity.get("from", "?"))[:10]
        channel = activity.get("channel")
        channel_str = f"ch{channel}" if channel is not None else "ch?"
        summary = str(activity.get("summary", ""))[: max(8, col_width - 30)]
        quality = []
        if activity.get("rx_rssi") is not None:
            quality.append(f"{activity['rx_rssi']}dBm")
        if activity.get("rx_snr") is not None:
            quality.append(f"{activity['rx_snr']}dB")
        suffix = f" [dim]{escape(' '.join(quality))}[/dim]" if quality else ""
        return (
            f"[cyan]{escape(str(activity.get('time', '--:--:--')))}[/cyan] "
            f"[yellow]{escape(tag)}[/yellow] "
            f"[dim]{escape(channel_str)}[/dim] "
            f"[green]{escape(from_str)}[/green] "
            f"{escape(summary)}{suffix}"
        )

    def _format_node_line(self, short, distance, metrics, age):
        parts = [short]
        if distance is not None:
            if distance >= 1:
                parts.append(f"{distance:.1f}km")
            else:
                parts.append(f"{distance * 1000:.0f}m")

        battery = metrics.get("batteryLevel")
        if battery is not None:
            parts.append(f"{battery}%")

        channel_use = metrics.get("channelUtilization")
        if channel_use is not None:
            parts.append(f"Ch{channel_use}%")

        if age:
            parts.append(age)

        return " • ".join(parts)

    def _trail_summary(self, node_id):
        history = self._render_node_history.get(str(node_id), [])
        if len(history) < 2:
            return None
        total_km = 0.0
        for start, end in zip(history, history[1:]):
            total_km += self._haversine_km(start["lat"], start["lon"], end["lat"], end["lon"])
        if total_km >= 1:
            return f"trk{len(history)}/{total_km:.1f}km"
        return f"trk{len(history)}/{total_km * 1000:.0f}m"

    def _relative_summary(self, local_ctx, lat, lon):
        if not local_ctx or lat is None or lon is None:
            return None, None
        distance = self._haversine_km(local_ctx["lat"], local_ctx["lon"], lat, lon)
        bearing = self._bearing_deg(local_ctx["lat"], local_ctx["lon"], lat, lon)
        return distance, bearing

    def _local_position_context(self, mesh_self_pos):
        if self.gps_obj and isinstance(self.gps_obj.last_tpv, dict):
            tpv = self.gps_obj.last_tpv
            if (tpv.get("mode") or 0) >= 2 and tpv.get("lat") is not None and tpv.get("lon") is not None:
                return {
                    "lat": float(tpv["lat"]),
                    "lon": float(tpv["lon"]),
                    "track": tpv.get("track"),
                    "speed": tpv.get("speed"),
                    "source": "GPS",
                }
        if mesh_self_pos:
            return {
                "lat": mesh_self_pos[0],
                "lon": mesh_self_pos[1],
                "track": None,
                "speed": None,
                "source": "Mesh",
            }
        return None

    def _movement_summary(self, node_id):
        history = self._render_node_history.get(str(node_id), [])
        if len(history) < 2:
            return None
        start = history[-2]
        end = history[-1]
        delta_s = max(1.0, end["time"] - start["time"])
        distance_km = self._haversine_km(start["lat"], start["lon"], end["lat"], end["lon"])
        if distance_km < 0.01:
            return "still"
        bearing = self._bearing_deg(start["lat"], start["lon"], end["lat"], end["lon"])
        speed_ms = (distance_km * 1000.0) / delta_s
        return f"{self._bearing_cardinal(bearing)} {speed_ms:.1f}m/s"

    def _link_hint(self, metrics, distance):
        if distance is None:
            return None
        rssi = metrics.get("rxRssi")
        snr = metrics.get("rxSnr")
        if rssi is None and snr is None:
            return None
        if distance <= 1.0 and ((rssi is not None and rssi < -112) or (snr is not None and snr < 1)):
            return "weak-link"
        if distance >= 8.0 and ((rssi is not None and rssi > -100) or (snr is not None and snr > 6)):
            return "strong-link"
        return None

    def _lost_summary(self, node_id, info):
        if not isinstance(info, dict):
            return None
        age = self._last_heard_seconds(info)
        if age is None or age < 900:
            return None
        pos = self._get_position(info)
        pieces = [self._format_age(age)]
        if pos:
            pieces.append(f"{pos[0]:.4f},{pos[1]:.4f}")
        movement = self._movement_summary(node_id)
        if movement:
            pieces.append(movement)
        metrics = self._extract_node_metrics(info)
        link_metrics = info.get("linkMetrics") if isinstance(info, dict) else None
        if isinstance(link_metrics, dict):
            metrics.update(link_metrics)
        if metrics.get("rxRssi") is not None:
            pieces.append(f"{metrics['rxRssi']}dBm")
        if metrics.get("rxSnr") is not None:
            pieces.append(f"{metrics['rxSnr']}dB")
        return " • ".join(pieces)

    def _format_radar_line(self, short, distance, bearing, metrics, age, movement, trail=None, link_hint=None):
        parts = [short]
        if distance is not None:
            if distance >= 1:
                parts.append(f"{distance:.1f}km")
            else:
                parts.append(f"{distance * 1000:.0f}m")
        if bearing is not None:
            parts.append(f"{bearing:.0f}°{self._bearing_cardinal(bearing)}")
        if movement:
            parts.append(movement)
        if trail:
            parts.append(trail)
        if link_hint:
            parts.append(link_hint)

        battery = metrics.get("batteryLevel")
        if battery is not None:
            parts.append(f"{battery}%")

        voltage = metrics.get("voltage")
        if voltage is not None:
            try:
                parts.append(f"{float(voltage):.1f}V")
            except (TypeError, ValueError):
                parts.append(f"{voltage}V")

        if age:
            parts.append(age)

        return " • ".join(parts)

    def _format_alert_line(self, alert):
        return f"{alert['time']} {alert['message']}"

    def _format_checkin_line(self, checkin, local_ctx):
        rel_bits = []
        distance, bearing = self._relative_summary(local_ctx, checkin.get("lat"), checkin.get("lon"))
        if distance is not None:
            if distance >= 1:
                rel_bits.append(f"{distance:.1f}km")
            else:
                rel_bits.append(f"{distance * 1000:.0f}m")
        if bearing is not None:
            rel_bits.append(f"{bearing:.0f}°{self._bearing_cardinal(bearing)}")
        note = f" {checkin['note']}" if checkin.get("note") else ""
        rel = f" {' '.join(rel_bits)}" if rel_bits else ""
        who = "Me" if checkin.get("sent") else checkin.get("from", "?")
        channel = checkin.get("channel")
        channel_str = f" ch{channel}" if channel is not None else ""
        return (
            f"{checkin.get('time', '--:--:--')}{channel_str} {who} "
            f"{checkin.get('lat', 0.0):.4f},{checkin.get('lon', 0.0):.4f}"
            f"{rel}{note[:18]}"
        )

    def _format_active_channels(self, snapshot):
        active_channels = snapshot.get("active_channels") or []
        selected_channel = snapshot.get("selected_channel", 0)
        best_channel = snapshot.get("best_channel")
        if not active_channels:
            return f"[dim]Selected ch{selected_channel} (waiting for traffic)[/dim]"
        labels = []
        for item in active_channels:
            age = ""
            last_seen = item.get("last_seen")
            if last_seen:
                age_value = self._format_age(time.time() - last_seen)
                if age_value:
                    age = f" {age_value}"
            label = f"ch{item['channel']} {int(item.get('score', 0))}* {item['unique_nodes']}n {item['rx_count']}r/{item['tx_count']}t{age}"
            if item["channel"] == best_channel:
                label = f"[cyan]{label}[/cyan]"
            if item["channel"] == selected_channel:
                label = f"[green]{label}*[/green]"
            labels.append(label)
        return "[dim]Active:[/dim] " + " ".join(labels)

    def _format_channel_mode(self, snapshot):
        selected_channel = snapshot.get("selected_channel", 0)
        best_channel = snapshot.get("best_channel")
        auto_follow = snapshot.get("auto_follow_channel")
        channel_locked = snapshot.get("channel_locked")
        if channel_locked:
            return f"[dim]Mode:[/dim] [yellow]locked[/yellow] ch{selected_channel}"
        if auto_follow:
            suffix = " [green](best)[/green]" if best_channel == selected_channel else ""
            return f"[dim]Mode:[/dim] [cyan]follow[/cyan] ch{selected_channel}{suffix}"
        return f"[dim]Mode:[/dim] manual ch{selected_channel}"

    def _format_scout_summary(self, snapshot):
        active_channels = snapshot.get("active_channels") or []
        selected_channel = snapshot.get("selected_channel", 0)
        if not active_channels:
            return f"[dim]Scout:[/dim] waiting for active mesh traffic"
        best = active_channels[0]
        reasons = []
        if best.get("unique_nodes"):
            reasons.append(f"{best['unique_nodes']} nodes")
        if best.get("checkin_count"):
            reasons.append(f"{best['checkin_count']} check-ins")
        elif best.get("text_count"):
            reasons.append(f"{best['text_count']} texts")
        telemetry_total = best.get("telemetry_count", 0) + best.get("position_count", 0)
        if telemetry_total:
            reasons.append(f"{telemetry_total} telemetry")
        if best.get("avg_snr") is not None:
            reasons.append(f"{best['avg_snr']:.1f}dB SNR")
        elif best.get("avg_rssi") is not None:
            reasons.append(f"{best['avg_rssi']:.0f}dBm")
        summary = f"[dim]Scout:[/dim] best now [cyan]ch{best['channel']}[/cyan]"
        if best["channel"] == selected_channel:
            summary += " [green](selected)[/green]"
        if reasons:
            summary += " - " + ", ".join(reasons[:3])
        return summary

    def _format_detail_lines(self, node):
        lines = []
        lines.append(f"[bold cyan]{escape(node['short'])}[/bold cyan]")
        if node.get("line"):
            lines.append(escape(node["line"]))
        pos = node.get("position")
        if pos:
            lines.append(f"[dim]{pos[0]:.5f}, {pos[1]:.5f}[/dim]")
        metrics = node.get("metrics", {})
        metric_bits = []
        if metrics.get("rxRssi") is not None:
            metric_bits.append(f"RSSI {metrics['rxRssi']}dBm")
        if metrics.get("rxSnr") is not None:
            metric_bits.append(f"SNR {metrics['rxSnr']}dB")
        if metrics.get("batteryLevel") is not None:
            metric_bits.append(f"Bat {metrics['batteryLevel']}%")
        if metrics.get("voltage") is not None:
            metric_bits.append(f"V {metrics['voltage']}")
        if metric_bits:
            lines.append(" • ".join(metric_bits))
        trail = self._trail_summary(node["id"])
        if trail:
            lines.append(f"Trail {trail}")
        lost = node.get("lost")
        if lost:
            lines.append(f"[magenta]Lost:[/magenta] {escape(lost)}")
        return lines
    
    def render(self):
        if not self.enabled:
            return Panel("LoRa OFF", title="LoRa", border_style="dim")

        snapshot = self.lora_obj.snapshot() if self.lora_obj else None
        self._render_node_history = snapshot.get("node_history", {}) if snapshot else {}
        
        # Get status for title
        if self.lora_obj:
            status = self.lora_obj.get_status()
        else:
            status = "init failed"
        
        if not snapshot or not snapshot["active"]:
            if snapshot and snapshot["connecting"]:
                content = f"[cyan]{snapshot['connection_status']}[/cyan]\n\nPlease wait..."
            elif not bootstrap.MESHTASTIC_AVAILABLE:
                content = "[yellow]Not available[/yellow]\n\nInstall: pip3 install --break-system-packages meshtastic"
            else:
                conflict_lines = []
                if snapshot and snapshot["conflict_processes"]:
                    labels = ", ".join(
                        f"{item['name']}:{item['pid']}"
                        for item in snapshot["conflict_processes"][:3]
                    )
                    conflict_lines.append(f"[red]Serial busy:[/red] {escape(labels)}")
                if not conflict_lines:
                    conflict_lines.append("Check USB connection")
                content = (
                    "[yellow]Not connected[/yellow]\n\n"
                    + "\n".join(conflict_lines)
                    + "\n[dim]Tried: /dev/ttyACM0, ttyUSB0, ttyACM1[/dim]"
                )
            return Panel(Text.from_markup(content), title=f"LoRa - {status}", border_style="yellow")

        # Build activity view (left) and nodes view (right)
        width = self.size.width or 80
        col_width = max(26, (width - 6) // 2)

        selected_channel = snapshot.get("selected_channel", 0)
        filtered_activities = [
            activity for activity in snapshot["activities"] if activity.get("channel", 0) == selected_channel
        ]
        activity_lines = [
            f"[bold cyan]Activity[/bold cyan] [dim]ch{selected_channel}[/dim]",
            self._format_channel_mode(snapshot),
            self._format_scout_summary(snapshot),
            self._format_active_channels(snapshot),
        ]
        if filtered_activities:
            for activity in filtered_activities[-8:]:
                activity_lines.append(self._format_activity_line(activity, col_width))
        else:
            activity_lines += [
                f"[dim]No traffic on ch{selected_channel} yet[/dim]",
                "Switch with Y / Shift+Y",
                "Waiting for traffic...",
                "[dim]Press T to send on selected ch[/dim]",
            ]

        nodes_lines = []
        nodes = []
        self_pos = None
        self_name = None
        local_ctx = None

        if snapshot["node_info"]:
            self_id = snapshot.get("self_id")
            if self_id is not None:
                self_info = snapshot["node_info"].get(self_id)
                if isinstance(self_info, dict):
                    user = self_info.get("user", {})
                    self_name = user.get("shortName") or user.get("longName")
                    self_pos = self._get_position(self_info)

            local_ctx = self._local_position_context(self_pos)

            for node_id, info in snapshot["node_info"].items():
                if self_id is not None and str(node_id) == str(self_id):
                    continue
                user = info.get("user", {}) if isinstance(info, dict) else {}
                short = user.get("shortName") or user.get("longName") or str(node_id)

                distance = None
                bearing = None
                node_pos = self._get_position(info)
                if local_ctx and node_pos:
                    distance = self._haversine_km(local_ctx["lat"], local_ctx["lon"], node_pos[0], node_pos[1])
                    bearing = self._bearing_deg(local_ctx["lat"], local_ctx["lon"], node_pos[0], node_pos[1])
                metrics = self._extract_node_metrics(info)
                link_metrics = info.get("linkMetrics") if isinstance(info, dict) else None
                if isinstance(link_metrics, dict):
                    metrics.update(link_metrics)
                age = self._last_heard_age(info)
                movement = self._movement_summary(node_id)
                trail = self._trail_summary(node_id)
                link_hint = self._link_hint(metrics, distance)
                age_seconds = self._last_heard_seconds(info)
                lost = self._lost_summary(node_id, info)
                nodes.append(
                    {
                        "id": str(node_id),
                        "short": short,
                        "position": node_pos,
                        "metrics": metrics,
                        "distance": distance,
                        "age_seconds": age_seconds if age_seconds is not None else 1e12,
                        "lost": lost,
                        "line": self._format_radar_line(short, distance, bearing, metrics, age, movement, trail=trail, link_hint=link_hint),
                    }
                )

        mode_label = self._view_label()
        if self.view_mode == "radar":
            nodes_lines.append(
                f"[bold cyan]{mode_label}[/bold cyan] [dim]({len(snapshot['node_info'])})[/dim] "
                f"[dim]Ring {snapshot['proximity_radius_km']:.2f}km[/dim]"
            )
        elif self.view_mode == "alerts":
            nodes_lines.append(
                f"[bold cyan]{mode_label}[/bold cyan] [dim]({len(snapshot['recent_alerts'])})[/dim] "
                f"[dim]Ring {snapshot['proximity_radius_km']:.2f}km[/dim]"
            )
        elif self.view_mode == "checkins":
            nodes_lines.append(
                f"[bold cyan]{mode_label}[/bold cyan] [dim](ch{selected_channel})[/dim]"
            )
        elif self.view_mode == "lost":
            lost_count = len([node for node in nodes if node["lost"]])
            nodes_lines.append(f"[bold cyan]{mode_label}[/bold cyan] [dim]({lost_count})[/dim]")
        else:
            nodes_lines.append(f"[bold cyan]{mode_label}[/bold cyan] [dim](ch{selected_channel})[/dim]")

        if self_name:
            nodes_lines.append(f"[green]You:[/green] {escape(self_name)}")
        local_ctx = local_ctx or self._local_position_context(self_pos)
        if local_ctx:
            extras = []
            if local_ctx.get("track") is not None:
                try:
                    extras.append(f"{float(local_ctx['track']):.0f}°")
                except (TypeError, ValueError):
                    pass
            if local_ctx.get("speed") is not None:
                try:
                    extras.append(f"{float(local_ctx['speed']):.1f}m/s")
                except (TypeError, ValueError):
                    pass
            extra_str = f" {' '.join(extras)}" if extras else ""
            nodes_lines.append(
                f"[dim]{local_ctx['source']} {local_ctx['lat']:.5f}, {local_ctx['lon']:.5f}{extra_str}[/dim]"
            )
        if snapshot["last_rx_summary"]:
            nodes_lines.append(f"[dim]Last:[/dim] {escape(snapshot['last_rx_summary'][: max(8, col_width - 8)])}")
        if snapshot["last_tx_status"]:
            tx_style = "green" if snapshot["last_tx_ok"] else "red"
            tx_age = ""
            if snapshot["last_tx_time"]:
                tx_age_value = self._format_age(time.time() - snapshot["last_tx_time"])
                if tx_age_value:
                    tx_age = f" [dim]{tx_age_value}[/dim]"
            nodes_lines.append(
                f"[{tx_style}]TX:[/{tx_style}] "
                f"{escape(snapshot['last_tx_status'][: max(8, col_width - 8)])}{tx_age}"
            )

        nodes.sort(key=lambda item: (item["distance"] is None, item["distance"] if item["distance"] is not None else 1e12, item["age_seconds"]))
        self.last_rendered_node_ids = [node["id"] for node in nodes]
        if self.selected_node_id not in self.last_rendered_node_ids and self.last_rendered_node_ids:
            self.selected_node_id = self.last_rendered_node_ids[0]
        stale_nodes = [node for node in nodes if node["lost"]]

        if self.view_mode == "radar":
            if snapshot["recent_alerts"]:
                alert = snapshot["recent_alerts"][-1]
                nodes_lines.append(f"[magenta]Alert:[/magenta] {escape(alert['message'][: max(8, col_width - 8)])}")
            recent_checkins = [
                checkin for checkin in snapshot["recent_checkins"] if checkin.get("channel", 0) == selected_channel
            ]
            if recent_checkins:
                nodes_lines.append(
                    f"[cyan]Check-in:[/cyan] "
                    f"{escape(self._format_checkin_line(recent_checkins[-1], local_ctx)[: max(8, col_width - 10)])}"
                )
            nodes_lines += [escape(node["line"]) for node in nodes[:8]] if nodes else ["[dim]No nodes yet[/dim]"]
            if stale_nodes:
                nodes_lines.append("[bold magenta]Lost[/bold magenta]")
                nodes_lines.extend(escape(node["lost"][: max(8, col_width - 2)]) for node in stale_nodes[:2])
        elif self.view_mode == "alerts":
            if snapshot["recent_alerts"]:
                nodes_lines.extend(
                    escape(self._format_alert_line(alert)[: max(8, col_width - 2)])
                    for alert in reversed(snapshot["recent_alerts"][-6:])
                )
            else:
                nodes_lines.append("[dim]No alerts yet[/dim]")
            recent_checkins = [
                checkin for checkin in snapshot["recent_checkins"] if checkin.get("channel", 0) == selected_channel
            ]
            if recent_checkins:
                nodes_lines.append("[bold cyan]Recent check-ins[/bold cyan]")
                nodes_lines.extend(
                    escape(self._format_checkin_line(checkin, local_ctx)[: max(8, col_width - 2)])
                    for checkin in reversed(recent_checkins[-3:])
                )
        elif self.view_mode == "checkins":
            checkins = [
                checkin for checkin in snapshot["recent_checkins"] if checkin.get("channel", 0) == selected_channel
            ]
            if checkins:
                nodes_lines.extend(
                    escape(self._format_checkin_line(checkin, local_ctx)[: max(8, col_width - 2)])
                    for checkin in reversed(checkins[-6:])
                )
            else:
                nodes_lines.append(f"[dim]No ch{selected_channel} check-ins yet[/dim]")
        elif self.view_mode == "lost":
            if stale_nodes:
                nodes_lines.extend(escape(node["lost"][: max(8, col_width - 2)]) for node in stale_nodes[:6])
            else:
                nodes_lines.append("[dim]No lost nodes[/dim]")
        else:
            selected = next((node for node in nodes if node["id"] == self.selected_node_id), None)
            if selected:
                nodes_lines.extend(self._format_detail_lines(selected))
            else:
                nodes_lines.append("[dim]No node selected[/dim]")

        max_lines = max(len(activity_lines), len(nodes_lines))
        activity_lines += [""] * (max_lines - len(activity_lines))
        nodes_lines += [""] * (max_lines - len(nodes_lines))

        from .columns import two_columns
        content = two_columns(activity_lines, nodes_lines, col_width, Text, right_width=width - col_width - 9)
        mode_hint = "lock" if snapshot.get("channel_locked") else "follow" if snapshot.get("auto_follow_channel") else "manual"
        return Panel(
            content,
            title=f"LoRa Mesh - {status} | ch{selected_channel} | {mode_hint} | {mode_label}",
            border_style="yellow",
        )


class StatusWidget(Static):
    """Status/Event log widget"""

    def __init__(self, ops_app=None):
        super().__init__()
        self.ops_app = ops_app

    def render(self):
        content = "\n".join(power.status_log) or "No events"
        title = "Status / Events"
        if self.ops_app and getattr(self.ops_app, "status_focus_mode", False):
            title += " | Focus"
        return Panel(content, title=title, border_style="green")


class FlipperWidget(Static):
    """Optional Flipper Zero panel. Only created when a Flipper is plugged in."""

    def __init__(self):
        super().__init__()
        self.status = {}
        self.busy = False
        self.live = False
        self.frame_text = ""
        self.screen_note = ""

    def refresh_status(self):
        if self.busy or self.live:
            return
        self.busy = True

        def work():
            try:
                self.status = flipper.get_status()
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()

    def toggle_screen(self):
        """Watch the Flipper's display. Read only. One reader at a time on the port."""
        if self.live:
            self.live = False
            return
        if self.busy:
            return
        self.live = True
        self.screen_note = "Reading the screen..."

        def work():
            try:
                while self.live:
                    frame = flipper.get_screen()
                    if frame:
                        self.frame_text, self.screen_note = frame, ""
                    else:
                        self.screen_note = "No screen. Is the Flipper out of the USB-UART Bridge app?"
                        self.live = False
                    time.sleep(0.3)
            finally:
                self.live = False

        threading.Thread(target=work, daemon=True).start()

    def render(self):
        if self.live or self.screen_note:
            body = self.frame_text if self.frame_text and self.live else ""
            lines = ([body] if body else []) + ([self.screen_note] if self.screen_note else [])
            return Panel("\n".join(lines), title="Flipper screen | i stop", border_style="cyan")
        lines = ["Reading..."] if self.busy and not self.status else flipper.panel_lines(self.status)
        return Panel("\n".join(lines), title="Flipper Zero | k refresh | i screen", border_style="cyan")


class WifiWidget(Static):
    """Optional Wi-Fi scan panel. Only created when an extra radio is present."""

    def __init__(self, radios):
        super().__init__()
        self.radios = radios
        self.scan = {}
        self.busy = False

    def refresh_scan(self):
        if self.busy:
            return
        self.busy = True

        def work():
            try:
                self.scan = wifi.get_scan()
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()

    def render(self):
        radios = self.scan.get("radios") or self.radios
        lines = wifi.panel_lines(self.scan, radios)
        if self.busy:
            lines.append("Scanning...")
        return Panel("\n".join(lines), title="Wi-Fi | l scan", border_style="magenta")


class DebugWidget(Static):
    """Debug log widget"""
    
    def render(self):
        # Show most recent entries, filling available height
        content = "\n".join(power.debug_log[-50:]) if power.debug_log else "Debug enabled"
        return Panel(content, title="Debug Log", border_style="magenta")


class SafeFooter(Footer):
    """Footer wrapper that supports show_bindings across Textual versions."""

    def __init__(self, *args, show_bindings=True, **kwargs):
        try:
            super().__init__(*args, show_bindings=show_bindings, **kwargs)
        except TypeError:
            super().__init__(*args, **kwargs)
            if hasattr(self, "show_bindings"):
                self.show_bindings = show_bindings


# -----------------------------
# Main App
# -----------------------------

class OpsApp(App):
    """uConsole AIO v2 Ops Panel"""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("o", "toggle_status_focus", "Status"),
    ]

    SDR_BINDINGS = [
        ("a", "toggle_audio", "Audio"),
        ("slash", "cycle_modulation", "Mod"),
        (".", "next_band_profile", "Band +"),
        (",", "prev_band_profile", "Band -"),
        ("c", "next_channel", "Chan +"),
        ("shift+c", "prev_channel", "Chan -"),
        ("p", "next_preset", "Preset +"),
        ("shift+p", "prev_preset", "Preset -"),
        ("n", "next_station", "Station +"),
        ("shift+n", "prev_station", "Station -"),
        ("up", "tune_up", "Freq +1"),
        ("down", "tune_down", "Freq -1"),
        ("left", "fine_tune_down", "Fine -"),
        ("right", "fine_tune_up", "Fine +"),
        ("[", "bw_down", "BW -0.1"),
        ("]", "bw_up", "BW +0.1"),
        ("f", "scan_forward", "Scan →"),
        ("shift+f", "scan_backward", "Scan ←"),
        ("s", "toggle_auto_squelch", "AutoSq"),
        ("v", "audio_gain_up", "Gain +"),
        ("shift+v", "audio_gain_down", "Gain -"),
        ("/", "cycle_modulation", "Mod"),
        ("m", "mute", "Mute"),
        ("b", "toggle_bias_t", "Bias-T"),
        ("e", "enter_frequency", "Freq"),
        ("h", "toggle_upconverter", "Upconv"),
        ("u", "cb_channels", "CB"),
        ("escape", "stop_seek", "Stop"),
    ]

    LORA_BINDINGS = [
        ("t", "send_lora_message", "LoRa TX"),
        ("g", "send_gps_checkin", "GPS TX"),
        ("j", "jump_best_lora_channel", "Best"),
        ("y", "next_lora_channel", "MeshCh +"),
        ("shift+y", "prev_lora_channel", "MeshCh -"),
        ("z", "toggle_follow_lora_channel", "Follow"),
        ("shift+z", "toggle_lock_lora_channel", "Lock"),
        ("r", "increase_lora_ring", "Ring +"),
        ("shift+r", "decrease_lora_ring", "Ring -"),
        ("w", "next_lora_view", "View +"),
        ("shift+w", "prev_lora_view", "View -"),
        ("d", "next_lora_node", "Node +"),
        ("shift+d", "prev_lora_node", "Node -"),
        ("x", "export_lora_history", "Export"),
    ]
    
    CSS = """
    Screen {
        layout: vertical;
    }
    
    #panels {
        layout: horizontal;
        height: 1fr;
    }
    
    #left_panel {
        width: 3fr;
        layout: vertical;
        height: 100%;
    }
    
    #debug_panel {
        width: 1fr;
        height: 100%;
    }
    
    GPSWidget {
        height: 14;
    }
    
    SDRWidget {
        height: 16;
    }
    
    LoRaWidget {
        height: 12;
    }
    
    StatusWidget {
        height: 1fr;
    }
    
    DebugWidget {
        height: 100%;
    }
    """

    def __init__(self, enabled):
        self.gps = None
        self.sdr = None
        self.lora = None
        self.preferences = load_preferences()
        self.status_focus_mode = bool(self.preferences.get("ui", {}).get("status_focus_mode", False))
        self.enabled = enabled
        self.gps_widget = None
        self.sdr_widget = None
        self.lora_widget = None
        self.status_widget = None
        self.debug_widget = None
        self.debug_panel = None
        self.left_panel = None
        self.sdr_bandwidth = 1.0  # 1 MHz - visible scan window
        self.auto_squelch_enabled = False  # Off by default
        self.scan_seek_last_update = 0
        super().__init__()
        self._register_feature_bindings()

    def _apply_lora_preferences(self):
        lora_prefs = self.preferences.get("lora", {})
        if self.lora_widget:
            view = lora_prefs.get("view_mode")
            if view in getattr(self.lora_widget, "VIEW_MODES", ()):
                self.lora_widget.view_mode = view
            selected_node_id = lora_prefs.get("selected_node_id")
            if selected_node_id:
                self.lora_widget.selected_node_id = str(selected_node_id)
        if self.lora:
            selected_channel = lora_prefs.get("selected_channel")
            auto_follow_channel = lora_prefs.get("auto_follow_channel")
            channel_locked = lora_prefs.get("channel_locked")
            radius = lora_prefs.get("proximity_radius_km")
            try:
                radius = float(radius)
            except (TypeError, ValueError):
                radius = None
            with self.lora.lock:
                if selected_channel is not None:
                    self.lora.selected_channel = self.lora._normalize_channel(selected_channel)
                if auto_follow_channel is not None:
                    self.lora.auto_follow_channel = bool(auto_follow_channel)
                if channel_locked is not None:
                    self.lora.channel_locked = bool(channel_locked)
                if radius is not None and radius > 0:
                    self.lora.proximity_radius_km = radius
                    self.lora.proximity_state = {}
                alerts = lora_prefs.get("recent_alerts")
                if isinstance(alerts, list):
                    self.lora.recent_alerts = alerts[-self.lora.max_alerts:]
                checkins = lora_prefs.get("recent_checkins")
                if isinstance(checkins, list):
                    self.lora.recent_checkins = checkins[-self.lora.max_checkins:]

    def _save_lora_preferences(self):
        lora_prefs = self.preferences.setdefault("lora", {})
        snapshot = self.lora.snapshot() if self.lora else None
        if self.lora_widget:
            lora_prefs["view_mode"] = self.lora_widget.view_mode
            lora_prefs["selected_node_id"] = self.lora_widget.selected_node_id
        if snapshot:
            lora_prefs["selected_channel"] = snapshot["selected_channel"]
            lora_prefs["auto_follow_channel"] = snapshot["auto_follow_channel"]
            lora_prefs["channel_locked"] = snapshot["channel_locked"]
            lora_prefs["proximity_radius_km"] = snapshot["proximity_radius_km"]
            lora_prefs["recent_alerts"] = snapshot["recent_alerts"]
            lora_prefs["recent_checkins"] = snapshot["recent_checkins"]
        save_preferences(self.preferences)

    def _save_ui_preferences(self):
        ui_prefs = self.preferences.setdefault("ui", {})
        ui_prefs["status_focus_mode"] = self.status_focus_mode
        save_preferences(self.preferences)

    def _set_widget_display(self, widget, visible):
        if not widget:
            return
        widget.styles.display = "block" if visible else "none"

    def _apply_status_focus_layout(self):
        visible = not self.status_focus_mode
        self._set_widget_display(self.gps_widget, visible)
        self._set_widget_display(self.sdr_widget, visible)
        self._set_widget_display(self.lora_widget, visible)
        if self.debug_panel:
            self._set_widget_display(self.debug_panel, visible)
        if self.left_panel:
            self.left_panel.styles.width = "1fr" if self.status_focus_mode else "3fr"
        if self.status_widget:
            self.status_widget.styles.height = "1fr"
            self.status_widget.refresh()

    @classmethod
    def _bindings_for_enabled(cls, enabled):
        bindings = list(cls.BINDINGS)
        if handoff.has_display():
            bindings.append(("shift+g", "open_app", "Open app"))
        if enabled.get("SDR"):
            bindings.extend(cls.SDR_BINDINGS)
        if enabled.get("LORA"):
            bindings.extend(cls.LORA_BINDINGS)
        return handoff.with_shift_aliases(bindings)

    def _register_feature_bindings(self):
        for key, action, description in self._bindings_for_enabled(self.enabled):
            if action == "quit":
                continue
            self.bind(key, action, description=description, show=True)

    def _kick_sdr_on_start(self):
        """Workaround: some boots don't produce FFT until first retune.
        Do a quick left then right (net zero) to kick rtl_sdr/FFT."""
        if not (self.sdr and self.sdr.active):
            return

        base = self.sdr.freq_mhz

        # Only do this if we haven't got any FFT yet.
        if self.sdr.initial_fft_done or self.sdr.spectrum:
            return

        debug("SDR: startup kick (left then right)")

        # Step left (no recenter so the view doesn't jump)
        self.sdr.request_tune(base - 0.1, recenter=False)

        # Step right back shortly after
        def step_back():
            # Use current base in case something changed (but aim for net-zero).
            target = base
            self.sdr.request_tune(target, recenter=False)

        # Textual timer: run very soon after
        self.set_timer(0.15, step_back)

    def _start_sdr_after_mount(self):
        if not (self.sdr and self.sdr.active):
            return

        self.sdr_bandwidth = self.sdr.if_bw_khz / 1000.0
        self.sdr.bandwidth_mhz = self.sdr_bandwidth

        # Enable bias-t only while the app is running
        self.sdr.set_bias_t(True)

        # Start SDR scan after UI is live
        start_freq = self.sdr.startup_frequency()
        self.sdr.apply_startup_profile(start_freq)
        self.sdr.start_scan(start_freq, bandwidth=self.sdr_bandwidth, recenter=True)
        self.sdr.finish_startup_tune()
        self.sdr.prime_spectrum()

        # Kick the spectrum on init (left then right)
        self.set_timer(0.25, self._kick_sdr_on_start)

    def _restart_meshtasticd(self):
        if not self.enabled.get("LORA"):
            return
        try:
            subprocess.run(
                ["sudo", "systemctl", "restart", "meshtasticd"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            debug("LoRa: meshtasticd restarted")
            log("LoRa: meshtasticd restarted")
        except Exception as e:
            debug(f"LoRa: meshtasticd restart failed: {e}")
            log("LoRa: meshtasticd restart failed")
    
    def _flipper_present(self):
        return flipper.has_flipper(getattr(self, "companions", {}))

    def _wifi_present(self):
        return wifi.has_extra_radio(getattr(self, "wifi_radios", {}))

    def action_scan_wifi(self):
        widget = getattr(self, "wifi_widget", None)
        if widget:
            widget.refresh_scan()

    def action_flipper_screen(self):
        widget = getattr(self, "flipper_widget", None)
        if widget:
            widget.toggle_screen()

    def action_refresh_flipper(self):
        widget = getattr(self, "flipper_widget", None)
        if widget:
            widget.refresh_status()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        
        if power.DEBUG:
            with Horizontal(id="panels"):
                with Vertical(id="left_panel"):
                    if self.enabled.get("GPS"):
                        self.gps_widget = GPSWidget(self.gps, True)
                        yield self.gps_widget
                    
                    if self.enabled.get("SDR"):
                        self.sdr_widget = SDRWidget(self.sdr, True, self)
                        yield self.sdr_widget
                    
                    if self.enabled.get("LORA"):
                        self.lora_widget = LoRaWidget(self.lora, True, self.gps)
                        self._apply_lora_preferences()
                        yield self.lora_widget
                    
                    if self._flipper_present():
                        self.flipper_widget = FlipperWidget()
                        yield self.flipper_widget

                    if self._wifi_present():
                        self.wifi_widget = WifiWidget(self.wifi_radios.get("radios"))
                        yield self.wifi_widget

                    self.status_widget = StatusWidget(self)
                    yield self.status_widget
                
                with Container(id="debug_panel"):
                    self.debug_widget = DebugWidget()
                    yield self.debug_widget
        else:
            if self.enabled.get("GPS"):
                self.gps_widget = GPSWidget(self.gps, True)
                yield self.gps_widget
            
            if self.enabled.get("SDR"):
                self.sdr_widget = SDRWidget(self.sdr, True, self)
                yield self.sdr_widget
            
            if self.enabled.get("LORA"):
                self.lora_widget = LoRaWidget(self.lora, True, self.gps)
                self._apply_lora_preferences()
                yield self.lora_widget
            
            if self._flipper_present():
                self.flipper_widget = FlipperWidget()
                yield self.flipper_widget

            if self._wifi_present():
                self.wifi_widget = WifiWidget(self.wifi_radios.get("radios"))
                yield self.wifi_widget

            self.status_widget = StatusWidget(self)
            yield self.status_widget
        
        yield SafeFooter(show_bindings=True)
    
    def on_mount(self):
        """Initialize hardware on mount"""
        # The Flipper key and panel exist only when one is plugged in.
        if self._flipper_present() and getattr(self, "flipper_widget", None):
            self.bind("k", "refresh_flipper", description="Flipper")
            self.bind("i", "flipper_screen", description="Flipper screen")
            self.flipper_widget.refresh_status()
        # The Wi-Fi key and panel exist only with an extra radio. No auto scan.
        if self._wifi_present() and getattr(self, "wifi_widget", None):
            self.bind("l", "scan_wifi", description="Wi-Fi scan")
        if power.DEBUG:
            try:
                self.debug_panel = self.query_one("#debug_panel", Container)
            except Exception:
                self.debug_panel = None
            try:
                self.left_panel = self.query_one("#left_panel", Vertical)
            except Exception:
                self.left_panel = None

        # Initialize GPS if enabled
        if self.enabled.get("GPS"):
            self.gps = GPS()
            if self.gps_widget:
                self.gps_widget.gps_obj = self.gps
            if self.lora_widget:
                self.lora_widget.gps_obj = self.gps
        
        # Initialize SDR if enabled
        if self.enabled.get("SDR"):
            self.sdr = SDR()
            if self.sdr and self.sdr.active:
                # Delay SDR start to let Textual mount/render
                self.set_timer(0.4, self._start_sdr_after_mount)
            if self.sdr_widget:
                self.sdr_widget.sdr_obj = self.sdr
        
        # Initialize LoRa if enabled (non-blocking)
        if self.enabled.get("LORA"):
            try:
                self._restart_meshtasticd()
                debug("Creating LoRa object...")
                self.lora = LoRa()
                self.lora.persist_callback = self._save_lora_preferences
                self._apply_lora_preferences()
                if self.lora_widget:
                    self.lora_widget.lora_obj = self.lora
                
                # Start connection in background after a short delay
                def connect_lora():
                    time.sleep(1)  # Let UI render first
                    if self.lora:
                        self.lora.connect()
                
                import threading
                lora_thread = threading.Thread(target=connect_lora, daemon=True)
                lora_thread.start()
                debug("LoRa connection started in background")
            except Exception as e:
                debug(f"LoRa initialization failed with exception: {e}")
                log(f"LoRa: Initialization error - {e}")
                self.lora = None
        
        log("uConsole ops panel started")
        self.title = "OpsApp"
        self._apply_status_focus_layout()
        
        # Start update timer
        self.set_interval(0.1, self.update_data)

    def _require(self, feature):
        return self.enabled.get(feature, False)

    def on_key(self, event):
        """Log all key presses to debug"""
        debug(f"Key: {event.key}")
        if getattr(event, "character", None) == "/" or event.key == "slash":
            self.action_cycle_modulation()
            event.stop()
            return
        if self.sdr and self.sdr.seek_active:
            if event.key not in ("f", "F", "shift+f"):
                self.sdr.seek_active = False
                log("Scan canceled")

    def action_toggle_status_focus(self):
        """Toggle a status-focused layout that hides the other panels."""
        self.status_focus_mode = not self.status_focus_mode
        self._apply_status_focus_layout()
        self._save_ui_preferences()
        log(f"Status focus: {'ON' if self.status_focus_mode else 'OFF'}")

    def update_data(self):
        """Update all widgets with fresh data"""
        # Update GPS
        if self.gps and self.gps_widget:
            gps_data = self.gps.read()
            if gps_data:
                self.gps_widget.update_data(gps_data)
            else:
                self.gps_widget.refresh()

        # Update SDR
        if self.sdr:
            self.sdr.apply_pending_tune()
            self.sdr.read_panadapter()
            if self.sdr_widget and self.sdr.dirty:
                self.sdr_widget.refresh()
                self.sdr.dirty = False

            if self.sdr.seek_active:
                self._scan_seek_step()

        # Update LoRa
        if self.lora:
            self.lora.refresh_nodes()
            local_ctx = self._current_local_position()
            if local_ctx:
                self.lora.update_proximity(local_ctx, self._distance_km)
        if self.lora_widget:
            self.lora_widget.refresh()

        # Update Status
        if self.status_widget:
            self.status_widget.refresh()

        # Update Debug
        if self.debug_widget:
            self.debug_widget.refresh()

    def _distance_km(self, lat1, lon1, lat2, lon2):
        r = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return r * (2 * atan2(sqrt(a), sqrt(max(0.0, 1 - a))))

    def _current_local_position(self):
        if self.gps and isinstance(self.gps.last_tpv, dict):
            tpv = self.gps.last_tpv
            if (tpv.get("mode") or 0) >= 2 and tpv.get("lat") is not None and tpv.get("lon") is not None:
                return {
                    "lat": float(tpv["lat"]),
                    "lon": float(tpv["lon"]),
                    "alt": tpv.get("alt"),
                    "track": tpv.get("track"),
                    "speed": tpv.get("speed"),
                    "source": "GPS",
                }
        if self.lora:
            snapshot = self.lora.snapshot()
            self_id = snapshot.get("self_id")
            if self_id is not None:
                info = snapshot["node_info"].get(self_id)
                if isinstance(info, dict):
                    pos = info.get("position") or {}
                    lat = pos.get("latitude")
                    lon = pos.get("longitude")
                    if lat is not None and lon is not None:
                        return {
                            "lat": float(lat),
                            "lon": float(lon),
                            "alt": pos.get("altitude"),
                            "track": None,
                            "speed": None,
                            "source": "Mesh",
                        }
        return None
    
    def action_toggle_audio(self):
        """Toggle FM audio demodulation"""
        if not self._require("SDR"):
            return
        debug(f"action_toggle_audio called | audio_enabled={self.sdr.audio_enabled if self.sdr else False}")
        if self.sdr and self.sdr.active:
            if self.sdr.audio_enabled:
                self.sdr.stop_audio()
            else:
                self.sdr.start_audio()
            log(f"SDR audio: {'ON' if self.sdr.audio_enabled else 'OFF'}")

    def _tune_preset(self, step):
        if not self._require("SDR"):
            return
        if not (self.sdr and self.sdr.active):
            return
        preset = self.sdr.tune_to_preset(step)
        if not preset:
            return
        name, freq = preset
        self.sdr_bandwidth = self.sdr.if_bw_khz / 1000.0
        self.sdr.bandwidth_mhz = self.sdr_bandwidth
        log(f"Preset: {name} {freq:.1f} MHz")

    def _tune_band_profile(self, step):
        if not self._require("SDR"):
            return
        if not (self.sdr and self.sdr.active):
            return
        profile = self.sdr.tune_to_band_profile(step)
        if not profile:
            return
        self.sdr_bandwidth = self.sdr.if_bw_khz / 1000.0
        self.sdr.bandwidth_mhz = self.sdr_bandwidth
        digits = profile.get("freq_digits", 3)
        log(
            f"Band: {profile['name']} {profile['freq_mhz']:.{digits}f} MHz "
            f"{profile['modulation']} {profile['if_bw_khz']} kHz"
        )

    def _tune_station_candidate(self, step):
        if not self._require("SDR"):
            return
        if not (self.sdr and self.sdr.active):
            return
        candidate = self.sdr.tune_to_station_candidate(step)
        if not candidate:
            profile = self.sdr.active_band_profile()
            band_name = profile["name"] if profile else "current band"
            log(f"No {band_name} scan candidates yet")
            return
        self.sdr_bandwidth = self.sdr.if_bw_khz / 1000.0
        self.sdr.bandwidth_mhz = self.sdr_bandwidth
        label = f" {candidate['preset']}" if candidate.get("preset") else ""
        if candidate.get("channel") is not None:
            label += f" {candidate.get('plan', 'CB')} Ch{candidate['channel']}"
        digits = candidate.get("freq_digits", 2)
        band_name = candidate.get("band", "Band")
        log(f"{band_name}: {candidate['freq_mhz']:.{digits}f} MHz{label}")

    def _tune_channel(self, step):
        if not self._require("SDR"):
            return
        if not (self.sdr and self.sdr.active):
            return
        selected = self.sdr.tune_to_channel(step)
        if not selected:
            profile = self.sdr.active_band_profile()
            band_name = profile["name"] if profile else "current band"
            log(f"No channel plan for {band_name}")
            return
        self.sdr_bandwidth = self.sdr.if_bw_khz / 1000.0
        self.sdr.bandwidth_mhz = self.sdr_bandwidth
        log(
            f"{selected['band']}: {selected['plan']} Ch{selected['channel']} "
            f"{selected['freq_mhz']:.5f} MHz"
        )

    def action_next_preset(self):
        """Tune to the next FM preset."""
        self._tune_preset(1)

    def action_prev_preset(self):
        """Tune to the previous FM preset."""
        self._tune_preset(-1)

    def action_next_band_profile(self):
        """Tune to the next SDR band profile."""
        self._tune_band_profile(1)

    def action_prev_band_profile(self):
        """Tune to the previous SDR band profile."""
        self._tune_band_profile(-1)

    def action_next_channel(self):
        """Tune to the next channel in the active channel plan."""
        self._tune_channel(1)

    def action_prev_channel(self):
        """Tune to the previous channel in the active channel plan."""
        self._tune_channel(-1)

    def action_next_station(self):
        """Tune to the next detected FM station candidate."""
        self._tune_station_candidate(1)

    def action_prev_station(self):
        """Tune to the previous detected FM station candidate."""
        self._tune_station_candidate(-1)

    def action_audio_gain_up(self):
        """Increase SDR audio gain"""
        if not self._require("SDR"):
            return
        if self.sdr and self.sdr.active:
            self.sdr.audio_gain = min(49, self.sdr.audio_gain + 5)
            log(f"SDR audio gain: {self.sdr.audio_gain} dB")
            debug(f"Audio gain increased to {self.sdr.audio_gain} dB")

    def action_audio_gain_down(self):
        """Decrease SDR audio gain"""
        if not self._require("SDR"):
            return
        if self.sdr and self.sdr.active:
            self.sdr.audio_gain = max(0, self.sdr.audio_gain - 5)
            log(f"SDR audio gain: {self.sdr.audio_gain} dB")
            debug(f"Audio gain decreased to {self.sdr.audio_gain} dB")

    def action_cycle_modulation(self):
        if not self._require("SDR"):
            return
        if self.sdr and self.sdr.active:
            next_index = (self.sdr.mod_index + 1) % len(self.sdr.modulations)
            self.sdr.band_profile_name = None
            self.sdr.set_modulation(self.sdr.modulations[next_index])
            self.sdr_bandwidth = self.sdr.if_bw_khz / 1000.0
            self.sdr.bandwidth_mhz = self.sdr_bandwidth
            self.sdr.dirty = True
            log(f"Modulation: {self.sdr.modulation}")
    
    def action_tune_up(self):
        """Increase SDR frequency by 1 MHz"""
        if not self._require("SDR"):
            return
        debug(f"action_tune_up called")
        if self.sdr and self.sdr.active:
            new_freq = (self.sdr.pending_freq if self.sdr.pending_freq is not None else self.sdr.freq_mhz) + 1.0
            self.sdr.request_tune(new_freq, recenter=True)
    
    def action_tune_down(self):
        """Decrease SDR frequency by 1 MHz"""
        if not self._require("SDR"):
            return
        debug(f"action_tune_down called")
        if self.sdr and self.sdr.active:
            new_freq = (self.sdr.pending_freq if self.sdr.pending_freq is not None else self.sdr.freq_mhz) - 1.0
            self.sdr.request_tune(new_freq, recenter=True)
    
    def action_bw_up(self):
        """Increase SDR bandwidth"""
        if not self._require("SDR"):
            return
        debug(f"action_bw_up called")
        if self.sdr and self.sdr.active:
            step = 5 if self.sdr.modulation in ("FM", "WFM") else 1
            self.sdr.if_bw_khz = min(self.sdr.if_bw_khz + step, 250)
            self.sdr_bandwidth = self.sdr.if_bw_khz / 1000.0
            self.sdr.bandwidth_mhz = self.sdr_bandwidth
            self.sdr._update_audio_filter()
            self.sdr._audio_fir_state = None
            self.sdr.dirty = True
            log(f"IF BW: {self.sdr.if_bw_khz} kHz")
            debug(f"IF BW increased to {self.sdr.if_bw_khz} kHz")
    
    def action_bw_down(self):
        """Decrease SDR bandwidth"""
        if not self._require("SDR"):
            return
        debug(f"action_bw_down called")
        if self.sdr and self.sdr.active:
            step = 5 if self.sdr.modulation in ("FM", "WFM") else 1
            self.sdr.if_bw_khz = max(self.sdr.if_bw_khz - step, 4)
            self.sdr_bandwidth = self.sdr.if_bw_khz / 1000.0
            self.sdr.bandwidth_mhz = self.sdr_bandwidth
            self.sdr._update_audio_filter()
            self.sdr._audio_fir_state = None
            self.sdr.dirty = True
            log(f"IF BW: {self.sdr.if_bw_khz} kHz")
            debug(f"IF BW decreased to {self.sdr.if_bw_khz} kHz")
    
    def action_fine_tune_up(self):
        """Increase SDR frequency by a mode-appropriate fine-tune step."""
        if not self._require("SDR"):
            return
        debug(f"action_fine_tune_up called")
        if self.sdr and self.sdr.active:
            step = self.sdr.fine_tune_step_mhz()
            new_freq = (self.sdr.pending_freq if self.sdr.pending_freq is not None else self.sdr.freq_mhz) + step
            self.sdr.request_tune(new_freq, recenter=True)
    
    def action_fine_tune_down(self):
        """Decrease SDR frequency by a mode-appropriate fine-tune step."""
        if not self._require("SDR"):
            return
        debug(f"action_fine_tune_down called")
        if self.sdr and self.sdr.active:
            step = self.sdr.fine_tune_step_mhz()
            new_freq = (self.sdr.pending_freq if self.sdr.pending_freq is not None else self.sdr.freq_mhz) - step
            self.sdr.request_tune(new_freq, recenter=True)
    
    def action_toggle_auto_squelch(self):
        """Toggle automatic squelch on/off"""
        if not self._require("SDR"):
            return
        debug(f"action_toggle_auto_squelch called")
        if self.sdr and self.sdr.active:
            self.auto_squelch_enabled = not self.auto_squelch_enabled
            
            if self.auto_squelch_enabled:
                # Enable auto squelch at moderate level
                self.sdr.squelch_level = 30
                log(f"Auto-squelch: ON (level {self.sdr.squelch_level})")
                debug(f"Auto-squelch enabled at level {self.sdr.squelch_level}")
            else:
                # Disable squelch - hear all noise
                self.sdr.squelch_level = 0
                log("Auto-squelch: OFF (hearing all noise)")
                debug("Auto-squelch disabled")
            self.sdr.dirty = True

    def action_toggle_upconverter(self):
        """Switch an upconverter (the Nooelec Ham It Up) on or off. Frequencies stay the real ones."""
        if not self._require("SDR"):
            return
        if not (self.sdr and self.sdr.active):
            return
        from .sdr import HAM_IT_UP_MHZ
        new = 0.0 if self.sdr.upconverter_mhz else HAM_IT_UP_MHZ
        if self.sdr.set_upconverter(new):
            log("Upconverter off: normal tuning, 24 MHz and up" if not new else
                f"Upconverter +{new:g} MHz: tune the real HF frequency. If it is powered from the dongle, turn Bias-T on (b)")

    def action_toggle_bias_t(self):
        """Toggle RTL-SDR bias-t on/off"""
        if not self._require("SDR"):
            return
        debug("action_toggle_bias_t called")
        if self.sdr and self.sdr.active:
            desired = not self.sdr.bias_t_enabled
            if not self.sdr.set_bias_t(desired):
                if self.sdr.last_bias_t_error:
                    log(f"SDR bias-t error: {self.sdr.last_bias_t_error[:60]}")
    
    def action_scan_forward(self):
        """Seek forward through the band to the next strong signal. Esc stops it."""
        self._start_seek(1)

    def action_scan_backward(self):
        """Seek backward through the band to the previous strong signal. Esc stops it."""
        self._start_seek(-1)

    def _start_seek(self, direction):
        if not self._require("SDR"):
            return
        if self.sdr and self.sdr.active and self.sdr.spectrum:
            self.sdr.seek_begin(direction)
            log(f"Seeking {'up' if direction > 0 else 'down'} (Esc stops)")
            self._scan_seek_step()

    def action_stop_seek(self):
        if self.sdr and self.sdr.seek_active:
            self.sdr.stop_seek()
            log("Seek stopped")

    def action_cb_channels(self):
        """One key to CB. First press: your last CB plan. Again: UK 27/81, CEPT, 11m AM."""
        if not self._require("SDR"):
            return
        profile = self.sdr.cycle_cb() if self.sdr else None
        if profile:
            log(f"CB: {profile['name']} (c/C changes channel, e then ch19 jumps to a channel)")

    def action_enter_frequency(self):
        """Type a frequency (98.5, 145.5m, 27185k) or a CB channel (ch19)."""
        if not self._require("SDR"):
            return
        if not (self.sdr and self.sdr.active):
            return
        from textual.containers import Vertical
        from textual.screen import ModalScreen
        from textual.widgets import Input, Label

        class FrequencyPrompt(ModalScreen):
            DEFAULT_CSS = """
            FrequencyPrompt { align: center middle; }
            #freq_prompt { width: 60; height: auto; border: round green; padding: 1 2; background: $surface; }
            """
            BINDINGS = [("escape", "cancel", "Cancel")]

            def compose(self):
                with Vertical(id="freq_prompt"):
                    yield Label("Frequency in MHz (98.5, 145.5m, 27185k) or CB channel (ch19). Enter to tune, Esc to cancel.")
                    yield Input(placeholder="e.g. 145.5 or ch19", id="freq_input")

            def on_input_submitted(self, event):
                self.dismiss(event.value)

            def action_cancel(self):
                self.dismiss(None)

        self.push_screen(FrequencyPrompt(), self._frequency_entered)

    def _frequency_entered(self, value):
        if not value or not self.sdr:
            return
        tuned = self.sdr.tune_to_frequency(value)
        if tuned is None:
            log(f"Could not tune to '{value[:20]}'")
        else:
            log(f"Tuned to {tuned:.5f} MHz")

    def _scan_seek_step(self):
        if not self.sdr:
            return
        message = self.sdr.seek_step()
        if message:
            log(message)

    def action_mute(self):
        """Quick mute/unmute audio"""
        if not self._require("SDR"):
            return
        debug(f"action_mute called")
        if self.sdr and self.sdr.active:
            if self.sdr.audio_enabled:
                self.sdr.stop_audio()
                log("Audio: MUTED")
            else:
                self.sdr.start_audio()
                log("Audio: UNMUTED")
    
    def action_send_lora_message(self):
        """Send a LoRa message (T key)"""
        if not self._require("LORA"):
            return
        debug("action_send_lora_message called")
        
        if not self.lora or not self.lora.active:
            log("LoRa: Not connected - cannot send")
            return
        
        # Temporarily suspend TUI to get input safely
        with self.suspend():
            print("\n" + "="*50)
            print("LoRa Mesh - Send Message")
            print("="*50)
            print(f"Sending on channel: {self.lora.selected_channel}")
            try:
                message = input("Enter message (or blank to cancel): ")
            except EOFError:
                message = ""
            print()
        
        if message.strip():
            self.lora.send_message(message)
        else:
            log("LoRa: Message cancelled")

    def action_send_gps_checkin(self):
        """Send a GPS-tagged mesh check-in."""
        if not self._require("LORA"):
            return
        if not self.lora or not self.lora.active:
            log("LoRa: Not connected - cannot send check-in")
            return
        location = self._current_local_position()
        if not location:
            log("LoRa: No local GPS position for check-in")
            return

        note = ""
        with self.suspend():
            print("\n" + "=" * 50)
            print("LoRa Mesh - GPS Check-in")
            print("=" * 50)
            print(f"Sending on channel: {self.lora.selected_channel}")
            print(f"Lat {location['lat']:.5f}  Lon {location['lon']:.5f}")
            try:
                note = input("Optional note (blank for none): ").strip()
            except EOFError:
                note = ""
            print()

        message = self.lora.format_checkin_message(location, note)
        if not message:
            log("LoRa: Check-in build failed")
            return
        self.lora.send_message(message)

    def action_increase_lora_ring(self):
        """Increase the mesh proximity/geofence radius."""
        if not self._require("LORA"):
            return
        if not self.lora:
            return
        radius = self.lora.adjust_proximity_radius(1)
        self._save_lora_preferences()
        self.log_lora_ring(radius)

    def _set_lora_channel(self, step):
        if not self._require("LORA"):
            return
        if not self.lora:
            return
        snapshot = self.lora.snapshot()
        active_channels = snapshot.get("active_channels") or []
        if len(active_channels) == 1:
            channel = active_channels[0]["channel"]
            if channel == 0:
                log("LoRa: only active mesh channel seen is ch0 (primary)")
            else:
                log(f"LoRa: only active mesh channel seen is ch{channel}")
            return
        selected = self.lora.cycle_active_channel(step)
        if selected is None:
            log("LoRa: No active mesh channels seen yet")
            return
        self._save_lora_preferences()
        if snapshot.get("auto_follow_channel") and not snapshot.get("channel_locked"):
            log(f"LoRa: locked manual channel ch{selected}")
            return
        log(f"LoRa channel: ch{selected}")

    def action_jump_best_lora_channel(self):
        """Jump directly to the scout's best current mesh channel."""
        if not self._require("LORA"):
            return
        if not self.lora:
            return
        selected = self.lora.jump_to_best_channel()
        if selected is None:
            log("LoRa: No best mesh channel yet")
            return
        self._save_lora_preferences()
        log(f"LoRa best channel: ch{selected}")

    def action_next_lora_channel(self):
        """Switch to the next active mesh channel."""
        self._set_lora_channel(1)

    def action_prev_lora_channel(self):
        """Switch to the previous active mesh channel."""
        self._set_lora_channel(-1)

    def action_toggle_follow_lora_channel(self):
        """Toggle auto-follow of the scout's best mesh channel."""
        if not self._require("LORA"):
            return
        if not self.lora:
            return
        enabled, selected = self.lora.toggle_auto_follow_channel()
        self._save_lora_preferences()
        if enabled:
            log(f"LoRa follow: ON ch{selected}")
        else:
            log("LoRa follow: OFF")

    def action_toggle_lock_lora_channel(self):
        """Toggle lock on the currently selected mesh channel."""
        if not self._require("LORA"):
            return
        if not self.lora:
            return
        locked, selected = self.lora.toggle_channel_lock()
        self._save_lora_preferences()
        if locked:
            log(f"LoRa lock: ch{selected}")
        else:
            log("LoRa lock: OFF")

    def action_decrease_lora_ring(self):
        """Decrease the mesh proximity/geofence radius."""
        if not self._require("LORA"):
            return
        if not self.lora:
            return
        radius = self.lora.adjust_proximity_radius(-1)
        self._save_lora_preferences()
        self.log_lora_ring(radius)

    def log_lora_ring(self, radius):
        if radius is None:
            return
        if radius >= 1:
            log(f"LoRa ring: {radius:.2f} km")
        else:
            log(f"LoRa ring: {radius * 1000:.0f} m")

    def action_next_lora_view(self):
        """Switch to the next LoRa telemetry view."""
        if not self._require("LORA"):
            return
        if not self.lora_widget:
            return
        self.lora_widget.cycle_view(1)
        self._save_lora_preferences()
        log(f"LoRa view: {self.lora_widget._view_label()}")

    def action_prev_lora_view(self):
        """Switch to the previous LoRa telemetry view."""
        if not self._require("LORA"):
            return
        if not self.lora_widget:
            return
        self.lora_widget.cycle_view(-1)
        self._save_lora_preferences()
        log(f"LoRa view: {self.lora_widget._view_label()}")

    def action_next_lora_node(self):
        """Select the next node in LoRa detail mode."""
        if not self._require("LORA"):
            return
        if not self.lora_widget:
            return
        selected = self.lora_widget.cycle_selected_node(1)
        if selected:
            self._save_lora_preferences()
            log(f"LoRa node: {selected}")

    def action_prev_lora_node(self):
        """Select the previous node in LoRa detail mode."""
        if not self._require("LORA"):
            return
        if not self.lora_widget:
            return
        selected = self.lora_widget.cycle_selected_node(-1)
        if selected:
            self._save_lora_preferences()
            log(f"LoRa node: {selected}")

    def action_export_lora_history(self):
        """Export the current LoRa telemetry snapshot to a JSON file."""
        if not self._require("LORA"):
            return
        if not self.lora:
            log("LoRa: No telemetry available to export")
            return

        snapshot = self.lora.snapshot()
        if not any(
            snapshot.get(key)
            for key in ("activities", "recent_alerts", "recent_checkins", "node_info", "node_history")
        ):
            log("LoRa: No telemetry history to export yet")
            return

        path = export_telemetry_snapshot(snapshot, self._current_local_position())
        if not path:
            log("LoRa: Telemetry export failed")
            return
        log(f"LoRa: Exported telemetry to {path}")
    
    def action_open_app(self):
        """Swap to the native app: release the radios, start the app a moment later, and quit
        without the rail shutdown questions so nothing is powered off on the way."""
        if not handoff.has_display():
            log("No desktop here, so there is no app to open")
            return
        if self.sdr and self.sdr.audio_enabled:
            self.sdr.stop_audio()
        if self.sdr:
            self.sdr.stop_scan()
            self.sdr.set_bias_t(False)
        if not handoff.launch_detached(handoff.delayed(handoff.app_command(handoff.install_dir()))):
            log("Could not start the app")
            return
        self.swap_to_app = True
        self.exit()

    def action_quit(self):
        """Override quit to prompt for hardware shutdown"""
        debug("action_quit called")
        
        # Stop audio if running
        if self.sdr and self.sdr.audio_enabled:
            self.sdr.stop_audio()

        # Stop SDR processes and disable bias-t
        if self.sdr:
            self.sdr.stop_scan()
            self.sdr.set_bias_t(False)
        
        # Exit the app - shutdown prompt will happen in main()
        self.exit()


# -----------------------------
# UI
# -----------------------------

def build_layout(enabled, gps_data, gps_obj, sdr_obj):
    """Legacy function - not used in Textual version"""
    pass
