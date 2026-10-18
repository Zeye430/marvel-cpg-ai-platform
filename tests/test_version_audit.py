"""An LFS pointer's content OID, not the pointer text hash, identifies source data."""

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from data_platform.version_audit import ROOT,audit_data


class VersionAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw=Path(self.temp.name)
        self.contract=json.loads((ROOT/'data_platform/contracts/v3/sources.json').read_text())
        self.git_ids={}
        for source in self.contract['sources']:
            path=self.raw/source['filename']
            with path.open('w',newline='') as handle:
                writer=csv.writer(handle)
                writer.writerow([c['name'] for c in source['columns']])
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            source['reference_sha256']=sha
            self.git_ids[source['filename']]=sha
        archive=self.raw/'_archive/v2_2026-10-09'
        archive.mkdir(parents=True)
        (archive/'fact_sell_out.csv').write_text('old_header\nold_data\n')
        self.git_ids['fact_sell_out.csv']=hashlib.sha256((archive/'fact_sell_out.csv').read_bytes()).hexdigest()

    def fake_git(self,args,**kwargs):
        if args[1]=='rev-parse':
            return SimpleNamespace(returncode=0,stdout='fetched-commit\n')
        name=args[2].rsplit('/',1)[1]
        pointer=f"version https://git-lfs.github.com/spec/v1\noid sha256:{self.git_ids[name]}\nsize 123\n".encode()
        return SimpleNamespace(returncode=0,stdout=pointer)

    def audit(self):
        with patch('data_platform.version_audit.load_contract',return_value=self.contract),patch('data_platform.version_audit.subprocess.run',side_effect=self.fake_git):
            return audit_data(source_dir=self.raw)

    def test_active_v3_and_git_v2_can_coexist(self):
        result=self.audit()
        self.assertTrue(result['active_matches_authorized_v3'])
        row=next(r for r in result['files'] if r['filename']=='fact_sell_out.csv')
        self.assertTrue(row['git_content_matches_v2_archive'])
        self.assertFalse(row['git_content_matches_v3'])
        self.assertEqual(row['git_content_sha256'],self.git_ids['fact_sell_out.csv'])
        self.assertEqual(result['git_ref_commit'],'fetched-commit')

    def test_changed_active_header_cannot_claim_authorized_v3(self):
        path=self.raw/'fact_sell_out.csv'
        path.write_text(path.read_text().replace('restock_units','wrong_units'))
        result=self.audit()
        self.assertFalse(result['active_matches_authorized_v3'])
        row=next(r for r in result['files'] if r['filename']=='fact_sell_out.csv')
        self.assertFalse(row['active_header_matches_contract'])


if __name__=='__main__':
    unittest.main()
