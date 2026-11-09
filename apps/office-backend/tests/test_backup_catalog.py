from pathlib import Path
import tempfile
import unittest
from backup_catalog import command


class BackupCatalogTests(unittest.TestCase):
    def test_sequence_is_monotonic_and_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'office.sqlite'
            key={'userKey':'user-a','deviceId':'device-a'}
            first=command(db,'reserve',{**key,'id':'one'})
            second=command(db,'reserve',{**key,'id':'two'})
            self.assertEqual((first['version'],second['version']),(1,2))
            self.assertEqual(command(db,'list',{'userKey':'other','deviceId':'device-a'}),[])
            command(db,'save',{**first,'state':'upload_failed','path':'preserved'})
            self.assertEqual(command(db,'list',key)[1]['path'],'preserved')
            with self.assertRaises(ValueError):command(db,'save',{**first,'userKey':'other'})
            with self.assertRaises(ValueError):command(db,'save',{**first,'version':100})
