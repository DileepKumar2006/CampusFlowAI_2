import unittest
from tests.helpers import *
from campusflow import nlp

class NLP(unittest.TestCase):
    def test_extraction_scenario(self):
        e = nlp.extract(SCENARIO)
        self.assertEqual((e["room"], e["equipment"], e["affected_people"], e["event"], e["deadline_time"]), ("B204", ["Projector"], 60, "presentation", "14:00"))
    def test_missing_fields_are_none_not_guessed(self):
        e = nlp.extract("The projector is broken")
        self.assertIsNone(e["room"]); self.assertIsNone(e["affected_people"]); self.assertIsNone(e["deadline_time"])
    def test_time_formats(self):
        self.assertEqual(nlp.extract("class at 9:30 am in A101")["deadline_time"], "09:30")
        self.assertEqual(nlp.extract("exam at 12 pm")["deadline_time"], "12:00")
        self.assertEqual(nlp.extract("meeting 16:45")["deadline_time"], "16:45")
    def test_room_patterns(self):
        self.assertEqual(nlp.extract("projector in b204 broken")["room"], "B204")
        self.assertEqual(nlp.extract("room B-204 is hot")["room"], "B204")
        self.assertEqual(nlp.extract("Z999 has no power")["room"], "Z999")          # unknown rooms are surfaced, then rejected by verification
        self.assertIsNone(nlp.extract("we need a 100 students hall")["room"])      # 'a 100' must not become room A100
    def test_ac_is_word_bounded(self):
        self.assertNotIn("AC", nlp.extract("the back door is stuck")["equipment"])
        self.assertIn("AC", nlp.extract("AC not cooling in D101")["equipment"])
    def test_classification(self):
        for text, cat in [(SCENARIO, "Equipment Failure"), ("sparks coming from the socket in A101", "Safety"),
                          ("lights left on all night in Block C wasting electricity", "Energy"),
                          ("I need a room for 50 students", "Classroom Request"), ("where is the library?", "Information"), ("hello there", "Other")]:
            self.assertEqual(nlp.classify(text)["category"], cat, text)
    def test_priority_scenario_is_documented_60_P2(self):
        e = nlp.extract(SCENARIO); p = nlp.priority("Equipment Failure", e)
        self.assertEqual((p["score"], p["priority"]), (60, "P2"))
        self.assertEqual(sum(f["points"] for f in p["factors"]), 60)
    def test_priority_rules(self):
        self.assertEqual(nlp.priority("Safety", nlp.extract("fire in A101"))["priority"], "P1")
        self.assertEqual(nlp.priority("Equipment Failure", nlp.extract("mic broken"))["priority"], "P4")  # 15: confirmed fault, no other signals
        big = nlp.priority("Equipment Failure", nlp.extract("projector broken, lecture at 9 am for 150 students urgent"))
        self.assertEqual(big["priority"], "P1")  # 15+20+10+20+25 = 90
    def test_priority_thresholds(self):
        self.assertEqual(nlp.level_for(70)[0], "P1"); self.assertEqual(nlp.level_for(69)[0], "P2")
        self.assertEqual(nlp.level_for(50)[0], "P2"); self.assertEqual(nlp.level_for(49)[0], "P3")
        self.assertEqual(nlp.level_for(14)[0], "P5")
if __name__ == "__main__": unittest.main()
