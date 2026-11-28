"""The honest picture of movement on the Sense page: lit links, their fading trail, the strips and the words."""
import inspect
import itertools
import math
import re
import unittest

from ops_gui import room3d as r, sense as s, sense_dsp as d, style

# Signal strength from one receive chain cannot know any of these, so no sentence may use them.
WHO = re.compile(r"\b(person|persons|people|someone|somebody|anyone|human|humans|body|bodies|intruder|man|woman)\b", re.I)
WHERE = re.compile(r"\b(north|south|east|west|left|right|behind|ahead|front|back|corner|metres?|meters?|feet|"
                   r"degrees|bearing|angle|position|located|location|standing|walking|here|there)\b", re.I)


def every_room_line():
    """Every sentence the line over the room can say, over all states, link counts, scores and doubts."""
    names = ("ap one", "ap two", "ap three")
    out = []
    for state in ("calibrating", "quiet", "recent", "movement"):
        for count in (1, 2, 3):
            for scores in itertools.product((0.0, 4.0, 12.0), repeat=count):
                dbs = dict(zip(names, scores))
                for spans in ((None, None), (14.0, 3.0), (200.0, 400.0)):
                    for k in range(count + 1):
                        out.append(s.room_line(state, dbs, None, 5.0, spans[0], spans[1], unsure=names[:k]))
    out.append(s.room_line("movement", {"wlan0": 9.0}, {"wlan0": "the wlan0 access point"}, 5.0, 3.0))
    out.append(s.room_line("quiet", {}))
    return out


class WordsTests(unittest.TestCase):
    def test_no_sentence_says_who_or_what_is_moving(self):
        for text in every_room_line() + [s.STRIP_CAPTION, s.LINK_CAPTION, s.PULSE_TIP, *s.RADAR_CAPTION,
                                         *s.ABOVE_CAPTION]:
            self.assertIsNone(WHO.search(text), text)
            self.assertNotRegex(text.lower(), r"detected|\d+ (thing|object|mover)s?")

    def test_no_sentence_gives_a_direction_or_a_position(self):
        for text in every_room_line():
            self.assertIsNone(WHERE.search(text), text)

    def test_where_is_only_ever_the_path_to_a_sender(self):
        for text in every_room_line():
            if "varying more than" in text:
                self.assertRegex(text, r"signal on the paths? to ", text)
                self.assertIn("is one cause", text)
            self.assertNotRegex(text.lower(), r"something is moving|someone|a person", text)

    def test_one_link_moving_names_its_path_and_how_long(self):
        self.assertEqual(s.room_line("movement", {"a": 9.0}, None, 5.0, moving_for=14.2),
                         "The signal on the path to a is varying more than its baseline, for 14 s. Movement near a path is one cause; interference and the transmitter changing power are others.")

    def test_the_connected_link_is_named_by_its_access_point(self):
        self.assertEqual(s.room_line("movement", {"wlan0": 9.0}, {"wlan0": "the wlan0 access point"}, 5.0, 3.0),
                         "The signal on the path to the wlan0 access point is varying more than its baseline, for 3 s. Movement near a path is one cause; interference and the transmitter changing power are others.")

    def test_several_links_are_counted_and_the_strongest_named(self):
        text = s.room_line("movement", {"a": 7.0, "b": 12.0, "c": 1.0}, None, 5.0, moving_for=14.0)
        self.assertEqual(text, "Varying on 2 of 3 links, strongest on the path to b, for 14 s. "
                               "The signal on the paths to a and b is varying more than its baseline. Movement near a path is one cause; interference and the transmitter changing power are others.")

    def test_one_of_several_links_is_counted_too(self):
        text = s.room_line("movement", {"a": 7.0, "b": 1.0, "c": 1.0}, None, 5.0, moving_for=100.0)
        self.assertEqual(text, "Varying on 1 of 3 links, for 1 min. The signal on the path to a is varying more than its baseline. Movement near a path is one cause; interference and the transmitter changing power are others.")

    def test_movement_with_no_link_over_the_line_still_names_the_strongest(self):
        text = s.room_line("movement", {"a": 4.0, "b": 2.0}, None, 5.0, moving_for=5.0)
        self.assertIn("Varying on 1 of 2 links", text)
        self.assertIn("the path to a is varying", text)

    def test_a_still_room_says_for_how_long(self):
        self.assertEqual(s.room_line("quiet", {"a": 0.0}, still_for=200.0), "Steady for 3 min.")
        self.assertEqual(s.room_line("quiet", {"a": 0.0}), "Steady since the room was learned.")
        self.assertEqual(s.room_line("recent", {"a": 0.0}, still_for=8.0),
                         "Steady now. The signal last varied above its baseline 8 s ago.")

    def test_learning_says_nothing_about_movement(self):
        self.assertEqual(s.room_line("calibrating", {"a": 30.0}, moving_for=5.0),
                         "Learning the room. Nothing can be said about the signal yet.")

    def test_a_link_with_too_few_readings_is_never_called_quiet(self):
        text = s.room_line("quiet", {"a": 0.0, "b": 0.0}, still_for=60.0, unsure=["b"])
        self.assertEqual(text, "Steady for 60 s. Too few readings on the path to b to say whether it is quiet.")
        self.assertEqual(s.room_line("quiet", {"a": 0.0, "b": 0.0}, still_for=60.0, unsure=["a", "b"]),
                         "Too few readings to say whether the signal is varying.")

    def test_a_link_with_too_few_readings_is_not_counted_as_moving(self):
        text = s.room_line("movement", {"a": 20.0, "b": 8.0}, None, 5.0, moving_for=4.0, unsure=["a"])
        self.assertIn("Varying on 1 of 2 links", text)
        self.assertIn("the path to b is varying", text)
        self.assertIn("Too few readings on the path to a", text)

    def test_spans_are_seconds_then_minutes(self):
        self.assertEqual([s.span_text(v) for v in (0, 14.9, 89, 90, 600, None, -3)],
                         ["0 s", "14 s", "89 s", "1 min", "10 min", "0 s", "0 s"])

    def test_paths_are_joined_in_plain_english(self):
        self.assertEqual(s.paths_to(["a"]), "the path to a")
        self.assertEqual(s.paths_to(["a", "b"]), "the paths to a and b")
        self.assertEqual(s.paths_to(["a", "b", "c"]), "the paths to a, b and c")

    def test_the_old_caption_about_distance_and_direction_is_still_drawn(self):
        self.assertIn('"nearer = stronger signal, direction unknown"', inspect.getsource(s))

    def test_the_captions_say_a_lit_line_is_not_a_place(self):
        self.assertIn("does not show a place", s.LINK_CAPTION)
        self.assertIn("not a picture", s.PULSE_TIP)

    def test_the_history_line_is_only_a_count(self):
        log = s.ActivityLog()
        self.assertEqual(log.history_text(0.0), "")
        log.update("movement", 10.0)
        log.update("quiet", 20.0)
        self.assertEqual(log.history_text(30.0), "Last 10 minutes: 1 stretch of variation, 10s in all.")
        self.assertIn(log.history_text(30.0), log.text(30.0))


class LevelTests(unittest.TestCase):
    def test_activity_steps_are_quiet_subtle_and_strong(self):
        self.assertEqual([s.activity_level(v) for v in (None, 0, 2.9, 3.0, 9.9, 10.0, 40)], [0, 0, 0, 1, 1, 2, 2])
        self.assertEqual(len(s.ACTIVITY_WORDS), 3)
        self.assertEqual(d.QUIET_DB, 3.0)

    def test_a_link_needs_enough_readings_to_be_trusted(self):
        self.assertFalse(s.link_sure(None))
        self.assertFalse(s.link_sure(0.39))
        self.assertTrue(s.link_sure(0.4))
        self.assertTrue(s.link_sure(1.0))

    def test_a_link_that_has_gone_silent_reads_as_unreliable(self):
        e = s.Engine()
        for i in range(100):
            e.add("a", i / 5.0, -60.0, native=5.0)
        e.step(20.0)
        self.assertGreater(e.link_reliability()["a"], 0.9)
        e.step(40.0)                                           # twenty seconds with nothing from it
        self.assertEqual(e.link_reliability()["a"], 0.0)
        self.assertGreater(e.reliability(), 0.9)               # the old measure cannot see that

    def test_the_state_colour_can_be_read_on_both_themes(self):
        self.assertEqual(s.state_colour("quiet", "dark"), style.good_colour("dark"))
        self.assertEqual(s.state_colour("quiet", "light"), style.good_colour("light"))
        self.assertEqual(s.state_colour("calibrating", "light"), style.MUTED["light"])
        for state in s.STATE_WORDS:
            for mode in ("dark", "light"):
                self.assertRegex(s.state_colour(state, mode), r"^#[0-9a-f]{6}$")
            # on the light theme's white card every state colour is dark enough to read
            light = s.state_colour(state, "light")
            self.assertLess(sum(int(light[i:i + 2], 16) for i in (1, 3, 5)), 3 * 150, (state, light))

    def test_the_pulse_grows_with_motion_and_beats_only_on_new_readings(self):
        self.assertEqual(s.pulse_rings(0.0, 0), [])
        self.assertEqual(s.pulse_rings(None, 3), [])
        self.assertEqual([len(s.pulse_rings(v, 0)) for v in (0.2, 0.5, 1.0, 7.0)], [1, 2, 3, 3])
        self.assertEqual(s.pulse_rings(1.0, 4), s.pulse_rings(1.0, 4))       # the same reading draws the same thing
        self.assertNotEqual(s.pulse_rings(1.0, 4), s.pulse_rings(1.0, 5))
        for tick in range(4):
            self.assertTrue(all(0 < f <= 1.0 for f in s.pulse_rings(1.0, tick)))


class LinkHistoryTests(unittest.TestCase):
    def test_a_disturbed_link_keeps_a_fading_trail_for_a_minute(self):
        h = s.LinkHistory()
        h.push(0.0, {"a": 12.0, "b": 0.0})
        for t in range(1, 61):
            h.push(float(t), {"a": 0.0, "b": 0.0})
        self.assertEqual(h.trail("a", 0.0), 12.0)
        self.assertAlmostEqual(h.trail("a", 30.0), 6.0)
        self.assertEqual(h.trail("a", 60.0), 0.0)
        self.assertEqual(h.trail("b", 30.0), 0.0)
        self.assertEqual(h.trail("never heard", 30.0), 0.0)

    def test_untrusted_scores_leave_no_trail(self):
        h = s.LinkHistory()
        h.push(0.0, {"a": 15.0}, {"a": False})
        self.assertEqual(h.trail("a", 1.0), 0.0)

    def test_old_readings_are_dropped(self):
        h = s.LinkHistory()
        for t in range(400):
            h.push(float(t), {"a": 1.0})
        self.assertEqual(len(h.rows["a"]), int(s.STRIP_SECONDS))

    def test_the_strip_runs_oldest_first_and_keeps_the_strongest_in_each_cell(self):
        h = s.LinkHistory()
        for t in range(30):
            h.push(float(t), {"a": 12.0 if t == 4 else 5.0 if t == 28 else 0.0})
        strip = h.strip("a", 29.0)
        self.assertEqual(len(strip), s.STRIP_COLUMNS)
        self.assertEqual(strip[:-10], [None] * (s.STRIP_COLUMNS - 10))       # nothing heard before it started
        self.assertEqual(strip[-1], 1)                                       # the subtle wobble a second ago
        self.assertEqual(strip[-9], 2)                                       # the strong one 25 seconds ago
        self.assertEqual(strip[-5], 0)

    def test_a_cell_is_marked_not_sure_only_when_nothing_in_it_can_be_trusted(self):
        h = s.LinkHistory()
        h.push(0.0, {"a": 0.0}, {"a": False})
        h.push(1.0, {"a": 0.0}, {"a": False})
        self.assertEqual(h.strip("a", 1.0)[-1], s.UNSURE)
        h.push(2.0, {"a": 0.0}, {"a": True})
        self.assertEqual(h.strip("a", 2.0)[-1], 0)
        self.assertEqual(h.strip("nobody", 2.0), [None] * s.STRIP_COLUMNS)

    def test_it_keeps_scores_and_nothing_about_what_caused_them(self):
        h = s.LinkHistory()
        h.push(0.0, {"a": 3.0})
        self.assertEqual(h.rows, {"a": [(0.0, 3.0, True)]})


class LinkDrawingTests(unittest.TestCase):
    def post(self, db=12.0, sure=True, x=1.0, z=0.0):
        return {"name": "a", "label": "a", "db": db, "x": x, "z": z, "height": 0.5, "sure": sure, "trail": 0.0}

    def test_the_trail_fades_evenly_and_is_gone_after_a_minute(self):
        self.assertEqual(r.trail_db([], 10.0), 0.0)
        self.assertEqual(r.trail_db([(10.0, 8.0)], 10.0), 8.0)
        self.assertAlmostEqual(r.trail_db([(0.0, 8.0)], 15.0), 6.0)
        self.assertEqual(r.trail_db([(0.0, 8.0)], r.TRAIL_SECONDS), 0.0)
        self.assertAlmostEqual(r.trail_db([(0.0, 20.0), (50.0, 4.0)], 55.0), max(20.0 * 5 / 60, 4.0 * 55 / 60))
        self.assertEqual(r.trail_db([(20.0, 9.0)], 10.0), 0.0)              # a reading from the future counts for nothing
        self.assertEqual(r.trail_db([(0.0, None), (0.0, -4.0)], 1.0), 0.0)

    def test_a_link_with_too_few_readings_is_dashed_not_quiet(self):
        look = r.link_look(0.0, 0.0, sure=False)
        self.assertEqual((look["kind"], look["dashed"], look["width"]), ("unsure", True, 1))
        self.assertEqual(r.link_look(18.0, 18.0, sure=False)["kind"], "unsure")
        self.assertFalse(r.link_look(0.0)["dashed"])

    def test_a_link_gets_wider_the_more_it_is_disturbed(self):
        widths = [r.link_look(db)["width"] for db in (0, 3, 6, 10, 16, 99)]
        self.assertEqual(widths, sorted(widths))
        self.assertEqual(widths[0], 1)
        self.assertEqual(widths[-1], r.WIDTHS[-1])
        self.assertEqual([r.link_look(db)["kind"] for db in (0, 2.9, 3, 20)], ["quiet", "quiet", "active", "active"])

    def test_a_link_that_was_disturbed_a_moment_ago_keeps_its_colour(self):
        look = r.link_look(0.5, trail=9.0)
        self.assertEqual((look["kind"], look["shown"], look["width"]), ("trail", 9.0, 2))
        self.assertEqual(r.link_look(0.5, trail=2.0)["kind"], "quiet")
        self.assertEqual(r.link_look(12.0, trail=4.0)["shown"], 12.0)
        self.assertEqual(r.link_look(None, None)["kind"], "quiet")

    def test_ripples_only_run_on_a_disturbed_trusted_link(self):
        self.assertEqual(r.ripple_bars(self.post(db=0.0), 3), [])
        self.assertEqual(r.ripple_bars(self.post(sure=False), 3), [])
        self.assertEqual(r.ripple_bars(self.post(x=0.0, z=0.0), 3), [])
        self.assertEqual([len(r.ripple_bars(self.post(db=v), 0)) for v in (3, 6, 10, 16)], [3, 4, 5, 6])

    def test_ripples_cover_the_whole_link_evenly_never_one_spot(self):
        post = self.post(x=0.6, z=0.8)
        for tick in range(r.RIPPLE_STEPS):
            bars = r.ripple_bars(post, tick)
            mids = [math.hypot((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in bars]
            gaps = [round(y - x, 9) for x, y in zip(mids, mids[1:])]
            self.assertEqual(len(set(gaps)), 1)                             # evenly spaced
            self.assertGreater(max(mids) - min(mids), 0.5)                  # along most of the link's length of 1
            self.assertLessEqual(max(mids), 1.0 + 1e-9)
            self.assertGreater(min(mids), 0.0)
            for a, b in bars:                                               # each bar lies across the link
                self.assertAlmostEqual((b[0] - a[0]) * 0.6 + (b[1] - a[1]) * 0.8, 0.0)

    def test_ripples_step_towards_the_uconsole_once_a_reading_and_not_in_between(self):
        post = self.post()
        first = lambda tick: r.ripple_bars(post, tick)[0][0][0]
        self.assertEqual(r.ripple_bars(post, 5), r.ripple_bars(post, 5))    # no clock in it
        self.assertLess(first(1), first(0))
        self.assertLess(first(2), first(1))
        self.assertEqual(r.ripple_bars(post, 0), r.ripple_bars(post, r.RIPPLE_STEPS))

    def test_posts_carry_whether_they_can_be_trusted_and_their_trail(self):
        posts = r.layout([{"name": "a", "db": 2.0, "dbm": -60.0, "sure": False, "trail": 7.0},
                          {"name": "b", "db": 2.0, "dbm": -60.0}])
        self.assertEqual([(p["sure"], p["trail"]) for p in posts], [(False, 7.0), (True, 0.0)])

    def test_an_untrusted_post_shows_a_question_mark_instead_of_a_score(self):
        self.assertEqual(r.post_label("ap aa:bb:cc", 6.4), "ap aa:bb:cc +6")
        self.assertEqual(r.post_label("ap aa:bb:cc", 6.4, sure=False), "ap aa:bb:cc ?")
        self.assertEqual(r.post_label("a very long network name", 0.0), "g network name +0")


class ThemeTests(unittest.TestCase):
    @staticmethod
    def light(colour):
        return sum(int(colour[i:i + 2], 16) for i in (1, 3, 5)) / 3

    def test_the_floor_is_light_on_the_light_theme_and_dark_on_the_dark_one(self):
        self.assertGreater(self.light(r.colours("light")["floor"]), 200)
        self.assertLess(self.light(r.colours("dark")["floor"]), 60)
        self.assertEqual(r.colours("anything else"), r.colours("dark"))

    def test_the_room_takes_its_colours_from_the_theme(self):
        for mode in ("dark", "light"):
            col = r.colours(mode)
            self.assertEqual(col["text"], style.TOKENS[mode]["text"])
            self.assertEqual(col["label_bg"], style.TOKENS[mode]["card"])
            self.assertEqual(col["caption"], style.MUTED[mode])
            self.assertEqual(col["unsure"], style.MUTED[mode])
            self.assertEqual(col["device"], style.TOKENS[mode]["accent"])
            for value in col.values():
                self.assertRegex(value, r"^#[0-9a-fA-F]{6}$")

    def test_lines_and_words_stand_out_from_the_floor_on_both_themes(self):
        for mode in ("dark", "light"):
            col = r.colours(mode)
            floor = self.light(col["floor"])
            for key in ("grid", "text", "caption", "quiet", "unsure"):
                self.assertGreater(abs(self.light(col[key]) - floor), 12, (mode, key))
            self.assertGreater(abs(self.light(col["text"]) - self.light(col["label_bg"])), 150)

    def test_a_quiet_link_is_green_and_an_unknown_one_is_grey(self):
        for mode in ("dark", "light"):
            quiet, unsure = r.colours(mode)["quiet"], r.colours(mode)["unsure"]
            self.assertGreater(int(quiet[3:5], 16), int(quiet[1:3], 16))
            self.assertNotEqual(quiet, unsure)

    def test_a_still_post_is_pale_on_the_light_floor_and_the_hot_end_is_the_same(self):
        self.assertEqual(r.heat_colour(0), r.HEAT[0][1])
        self.assertEqual(r.heat_colour(0, "dark"), r.heat_colour(0))
        self.assertGreater(self.light(r.heat_colour(0, "light")), 150)
        self.assertEqual(r.heat_colour(99, "light"), r.heat_colour(99, "dark"))
        for _v, colour in r.HEAT_LIGHT:                                    # still no blues
            self.assertGreaterEqual(int(colour[1:3], 16), int(colour[5:7], 16))

    def test_strip_cells_have_a_colour_for_every_code(self):
        for mode in ("dark", "light"):
            col = r.colours(mode)
            self.assertEqual(r.strip_colour(None, mode), col["floor"])
            self.assertEqual(r.strip_colour(s.UNSURE, mode), col["unsure"])
            self.assertEqual(r.strip_colour(0, mode), col["quiet"])
            shades = [r.strip_colour(code, mode) for code in (None, s.UNSURE, 0, 1, 2)]
            self.assertEqual(len(set(shades)), 5)


class CheapTests(unittest.TestCase):
    def test_the_room_and_strips_have_no_timer_of_their_own(self):
        src = inspect.getsource(s)
        room = src[src.index("    class Room3D(QWidget):"):src.index("    class Radar(QWidget):")]
        self.assertNotIn("QTimer", room)
        self.assertNotIn("time.time", room)

    def test_nothing_new_is_listened_to(self):
        self.assertEqual(s.air_args(6), ["sudo", "-n", "aiov2_ctl", "--wifi-listen", "--iface", "wlan1", "--channel",
                                         "6", "--seconds", "3600", "--json"])


def page_strings():
    """Every piece of text written into the Sense modules as a string (the docstrings, which are notes for whoever
    reads the code and never reach the screen, are left out), so a test can read all the page could ever show."""
    import ast
    from ops_gui import radar
    found = []
    for module in (s, r, d, radar):
        tree = ast.parse(inspect.getsource(module))
        notes = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    notes.add(id(first.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in notes:
                found.append(node.value)
    return found


class WholePageTests(unittest.TestCase):
    BANNED = re.compile(r"\b(fall|falls|fell|fallen|falling|person|persons|people|someone|somebody|human|humans)\b|"
                        r"between you", re.I)
    # Words that would put what was sensed somewhere. One receive chain cannot know any of them.
    PLACED = re.compile(r"\b(north|south|east|west|left|right(?! now)|behind|ahead|in front|bearing|degrees|"
                        r"(metres?|meters?|feet|foot|yards?|m) away)\b", re.I)
    # These may only be written to say that they are NOT known.
    DENIED = re.compile(r"\b(direction|position|angle|place|map)\b", re.I)
    DENIAL = re.compile(r"direction unknown|position on screen means nothing|does not show a place|not a map", re.I)

    def test_the_search_really_reads_the_page(self):
        strings = page_strings()
        self.assertIn("nearer = stronger signal, direction unknown", strings)
        self.assertIn("Start watching", strings)
        self.assertTrue(any("on a path it takes is varying more than its baseline" in t for t in strings))
        self.assertIsNotNone(self.BANNED.search("Possible fall"))
        self.assertIsNotNone(self.BANNED.search("moving between you and the access point"))

    def test_no_text_on_the_page_names_a_fall_or_a_person_or_places_movement_between_you_and_anything(self):
        for text in page_strings():
            self.assertIsNone(self.BANNED.search(text), text)

    def test_no_text_on_the_page_gives_a_direction_or_a_distance_away(self):
        self.assertIsNotNone(self.PLACED.search("3 metres away"))
        self.assertIsNotNone(self.PLACED.search("Movement to the left"))
        self.assertIsNotNone(self.PLACED.search("on the right"))
        self.assertIsNone(self.PLACED.search("nothing moving right now"))     # a time, not a side
        for text in page_strings():
            self.assertIsNone(self.PLACED.search(text), text)

    def test_direction_and_position_are_only_ever_named_to_say_they_are_not_known(self):
        named = [t for t in page_strings() if self.DENIED.search(t)]
        self.assertGreaterEqual(len(named), 4)                           # the three views and the strip note
        for text in named:
            self.assertIsNotNone(self.DENIAL.search(text), text)

    def test_the_search_reads_the_captions_of_the_flat_views_too(self):
        strings = page_strings()
        for part in s.RADAR_CAPTION + s.ABOVE_CAPTION:
            self.assertIn(part, strings)
            self.assertIsNone(self.BANNED.search(part), part)

    def test_nothing_the_page_builds_does_either(self):
        log = s.ActivityLog()
        built = every_room_line() + [s.STRIP_CAPTION, s.LINK_CAPTION, s.PULSE_TIP, log.text(0.0),
                                     *s.RADAR_CAPTION, *s.ABOVE_CAPTION,
                                     s.waiting_text("ap one", 6), s.source_title("air:6:aa:bb:cc:dd:ee:ff")]
        for kind in ("movement_start", "movement_end", "presence_start", "presence_end", "learned",
                     "sharp_then_still", "anything else"):
            built.append(s.event_text(kind, 0))
        for state, when in (("quiet", 1.0), ("movement", 5.0), ("recent", 9.0), ("quiet", 300.0)):
            log.update(state, when)
            built += [log.text(when + 1, {"ap one": 9.0, "ap two": 1.0}), log.history_text(when + 1)]
        built += [" ".join(pair) for pair in (s.air_blocker(False, {}), s.air_blocker(True, {s.AIR_IFACE: -50.0}))]
        for text in built:
            self.assertIsNone(self.BANNED.search(text), text)
            self.assertIsNone(self.PLACED.search(text), text)

    def test_the_fall_guess_is_gone(self):
        self.assertFalse(hasattr(d, "FallWatch"))
        self.assertFalse(hasattr(s.Engine(), "fall"))
        self.assertFalse(hasattr(s.ActivityLog(), "falls"))
        for module in (s, d):
            self.assertNotIn("fall_suspect", inspect.getsource(module))

    def test_a_sharp_change_is_only_a_line_in_the_log_never_a_notification(self):
        src = inspect.getsource(s)
        self.assertEqual(src.count("win.notify_user("), 1)                 # only the start of movement

    def test_the_floor_is_never_shaded_as_if_it_were_a_place(self):
        for name in ("cells", "seg_distance", "ring_points", "SIGMA"):
            self.assertFalse(hasattr(r, name), name)
        src = inspect.getsource(s)
        room = src[src.index("    class Room3D(QWidget):"):src.index("    class Strips(QWidget):")]
        self.assertNotIn("self.cells", room)
        self.assertEqual(room.count('p.setBrush(QColor(col["floor"]))'), 1)   # the one colour a tile is ever given

    def test_disturbance_is_drawn_on_the_link_line_itself(self):
        src = inspect.getsource(s)
        room = src[src.index("    class Room3D(QWidget):"):src.index("    class Strips(QWidget):")]
        self.assertIn("room3d.link_look(", room)
        self.assertIn('p.drawLine(middle, P(q["x"], 0, q["z"])[0])', room)


if __name__ == "__main__":
    unittest.main()
