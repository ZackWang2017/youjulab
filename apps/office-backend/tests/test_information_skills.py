"""资讯技能文件可被办公编排层读取。"""
from __future__ import annotations

import sys
import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from information_skills import KEYWORD_SKILLS, load_skill
import information_skills
from information.repository import information_repository as repo
from information.service.fetch_service import today


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


class LocalInformationSkillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'OFFICE_SQLITE_PATH': str(Path(self.temp.name)/'office.sqlite')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.group = repo.create_group('alice', 'News', 'finance_api')
        self.item = repo.insert_item({'owner_id': 'alice', 'group_id': self.group['id'], 'source_kind': 'finance_api',
                                     'source_name': 'Source', 'title': 'Revenue +10%', 'fetched_date': today()})

    async def test_impact_writes_only_selected_local_items(self):
        async def result(*args):
            yield {'type': 'result', 'raw': json.dumps({'items': [
                {'id': self.item['id'], 'impact': '利好'}, {'id': 9999, 'impact': 'invented'}]}), 'thinking': ''}
        with patch.object(information_skills, '_complete', result):
            events = [event async for event in information_skills.iter_impact_events('alice', self.group['id'])]
        self.assertEqual(len(events[-1]['items']), 1)
        self.assertEqual(repo.get_item('alice', self.item['id'])['impact'], '利好')
        self.assertIsNone(repo.get_item('bob', self.item['id']))

    async def test_analysis_checks_local_owner_before_model(self):
        with patch.object(information_skills, '_complete') as model:
            with self.assertRaisesRegex(ValueError, '资讯不存在'):
                _ = [event async for event in information_skills.iter_analyze_events('bob', self.item['id'])]
        model.assert_not_called()

    async def test_review_persists_locally(self):
        async def result(*args):
            yield {'type': 'result', 'raw': json.dumps({'content': 'Review', 'sentiment': '利好', 'items': []}), 'thinking': ''}
        with patch.object(information_skills, '_complete', result):
            events = [event async for event in information_skills.iter_review_events('alice', self.group['id'])]
        self.assertEqual(events[-1]['review']['content'], 'Review')
        self.assertEqual(len(repo.list_reviews('alice', self.group['id'])), 1)
        self.assertEqual(repo.list_reviews('bob', self.group['id']), [])
