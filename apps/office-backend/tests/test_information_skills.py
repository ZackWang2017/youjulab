"""资讯技能文件可被办公编排层读取。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from information_skills import KEYWORD_SKILLS, load_skill


class InformationSkillTests(unittest.TestCase):
    def test_keyword_skills_exist(self):
        for name in KEYWORD_SKILLS.values():
            text = load_skill(name)
            self.assertIn("keywords", text)

    def test_analysis_and_review_skills_exist(self):
        self.assertIn("summary", load_skill("item-analysis"))
        self.assertIn("sentiment", load_skill("daily-review"))
        self.assertIn("items", load_skill("daily-review"))
        self.assertIn("impact", load_skill("item-impact"))


if __name__ == "__main__":
    unittest.main()
