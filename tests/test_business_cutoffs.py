"""Business June freshness, isolated May development and evidence-gated June test."""

import json
import math
import unittest
from dataclasses import replace
from datetime import date,timedelta
from pathlib import Path

import duckdb

from data_platform.access import ServingReader,DataUnavailable
from data_platform.artifacts import verified_artifact
from data_platform.feature_handoff import export_feature_input
from data_platform.feature_runner import build_features
from data_platform.ingestion import digest
from data_platform.labels import build_labels
from data_platform.pipeline import BuildConfig,build
from data_platform.test_release import authorize_test_release
from data_platform.workflow import build_all
import test_data_platform as fixtures


def evidence(root,dataset_hash):
    model=root/"synthetic-model.bin";model.write_bytes(b"synthetic fixture only, not a trained model")
    report={"status":"Passed","fold_id":"may_validation","outcome_end":"2026-05-31",
            "label_policy":"complete_window","dataset_hash":dataset_hash,
            "model_version":"synthetic-v1","model_sha256":digest(model),
            "feature_version":"oos_features.v1-complete","threshold_policy_version":"synthetic-threshold-v1",
            "metrics":{"precision":.8,"recall":.72,"roc_auc":.85}}
    report_file=root/"synthetic-validation.json";report_file.write_text(json.dumps(report))
    record={**{k:report[k] for k in ("dataset_hash","model_version","model_sha256","feature_version","threshold_policy_version")},
            "release_version":"oos_test_release.v1","status":"Approved","approved_by":"synthetic-test-owner",
            "model_frozen_at":"2026-10-19T01:00:00+00:00","approved_at":"2026-10-19T02:00:00+00:00",
            "model_file":model.name,"validation_report_file":report_file.name,
            "validation_report_sha256":digest(report_file)}
    release=root/"synthetic-test-release.json";release.write_text(json.dumps(record))
    return release,record,report_file,report


class BusinessCutoffTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PlatformTests();self.addCleanup(self.fixture.doCleanups);self.fixture.setUp()
        self.root=self.fixture.root

    def june_sources(self):
        current=date(2026,5,1);inventory=3000
        while current<=date(2026,6,30):
            refill=7 if current==date(2026,6,2) else 0
            inventory+=refill-1
            if current!=date(2026,6,10):
                self.fixture.rows['fact_sell_out'].append(self.fixture.sellout(str(current),1,inventory,refill,"D-FUTURE" if refill else ""))
            current+=timedelta(days=1)
        self.fixture.write_sources()
        policy=self.root/"scope.json"
        policy.write_text(json.dumps({"version":"synthetic-june-v1","assignments":[{"principal_id":"alice","store_id":"S1","role":"sales"}]}))
        return policy

    def test_business_and_development_have_different_boundaries(self):
        self.assertEqual(BuildConfig().as_of,date(2026,6,30))
        self.june_sources()
        result=build(replace(self.fixture.config,as_of=date(2026,6,30),include_kpi_marts=True))
        parent=Path(result["database"]).parent
        self.assertEqual(verified_artifact(parent)[2],date(2026,6,30))
        with self.assertRaisesRegex(ValueError,"June"):
            export_feature_input(parent,self.root/"bad-dev",date(2026,6,1))
        projected=export_feature_input(parent,self.root/"good-dev",date(2026,5,31))
        self.assertEqual(projected["purpose"],"development")
        with self.assertRaisesRegex(ValueError,"available"):
            build(replace(self.fixture.config,as_of=date(2026,7,1)))
        with self.assertRaisesRegex(ValueError,"2026-06-30"):
            build_features(parent,date(2026,7,1),self.root/"bad-score",purpose="scoring")

    def test_latest_workflow_dual_views_and_no_june_test_side_effects(self):
        policy=self.june_sources()
        r=build_all(date(2026,6,30),self.root/"delivery",source_dir=self.fixture.config.source_dir,
                    snapshot_root=self.fixture.config.snapshot_root,access_policy=policy,
                    threads=1,memory_limit="256MB",publish=True)
        c=r["components"]
        self.assertEqual(c["features"]["as_of"],"2026-05-31")
        self.assertEqual(c["labels"]["as_of"],"2026-05-31")
        self.assertEqual(c["scoring_features"]["as_of"],"2026-06-30")
        self.assertEqual(c["scoring_features"]["purpose"],"scoring")
        self.assertEqual(c["serving"]["as_of"],"2026-06-30")
        dev_db=Path(c['features']['directory'])/'features.duckdb'
        score_db=Path(c['scoring_features']['directory'])/'features.duckdb'
        with duckdb.connect(str(dev_db),read_only=True) as dev,duckdb.connect(str(score_db),read_only=True) as score:
            sql="SELECT * FROM gold.features_oos_daily WHERE scoring_date<='2026-05-31' ORDER BY scoring_date"
            self.assertEqual(dev.execute(sql).fetchall(),score.execute(sql).fetchall())
        reader=ServingReader(c["serving"]["directory"])
        self.assertEqual(reader.read("negotiation_context","alice")[0]["as_of_date"],date(2026,6,30))
        reported=reader.read("store_sku_daily","alice",start="2026-06-10",end="2026-06-10")[0]
        self.assertIsNone(reported['inventory_observed'])
        self.assertEqual(reported['inventory_data_status'],"Unknown")
        with self.assertRaises(DataUnavailable):reader.read("estimated_store_day","alice")
        candidate=reader.read("estimated_store_sku_daily","alice",start="2026-06-10",end="2026-06-10",allow_candidate_estimates=True)[0]
        self.assertEqual(candidate['inventory_data_status'],"Estimated")
        self.assertIsNone(candidate['inventory_observed'])
        self.assertEqual(candidate['inventory_resolved'],candidate['inventory_estimated'])
        observed=reader.read("estimated_store_sku_daily","alice",start="2026-06-11",end="2026-06-11",allow_candidate_estimates=True)[0]
        self.assertEqual(observed['inventory_data_status'],"Observed")
        self.assertEqual(observed['inventory_resolved'],observed['inventory_observed'])
        self.assertIsNone(observed['inventory_estimated'])
        with self.assertRaises(PermissionError):reader.read("estimated_store_day","bob",allow_candidate_estimates=True)
        for name in ("estimated_store_day","estimated_commercial_period"):
            self.assertTrue(reader.read(name,"alice",allow_candidate_estimates=True))
        parent=Path(c['base']['database']).parent
        with self.assertRaisesRegex(ValueError,"release"):
            build_labels(parent,date(2026,6,30),self.root/"blocked-test",purpose="test")
        self.assertFalse((self.root/"blocked-test").exists())
        with duckdb.connect(str(Path(c['labels']['directory'])/'labels.duckdb'),read_only=True) as labels:
            self.assertEqual(labels.execute('SELECT max(scoring_date),max(evaluation_as_of) FROM evaluation.labels_oos_next7').fetchone(),(date(2026,5,31),date(2026,5,31)))
        approval,_,_,_=evidence(self.root,c['base']['dataset_hash'])
        released=build_labels(parent,date(2026,6,30),self.root/"synthetic-released-test",purpose="test",test_release=approval)
        self.assertEqual(verified_artifact(released['directory'],kind='labels')[0]['purpose'],'test')
        with duckdb.connect(str(Path(released['directory'])/'labels.duckdb'),read_only=True) as con:
            self.assertEqual(con.execute('SELECT min(scoring_date),max(scoring_date),max(scoring_date) FILTER(WHERE target_oos_7d IS NOT NULL) FROM evaluation.labels_oos_next7').fetchone(),(date(2026,6,1),date(2026,6,30),date(2026,6,23)))
            self.assertEqual(con.execute("SELECT count(target_oos_7d) FROM evaluation.labels_oos_next7 WHERE scoring_date>='2026-06-24'").fetchone()[0],0)
            self.assertEqual(con.execute('SELECT DISTINCT partition FROM evaluation.fold_samples').fetchall(),[('test',)])

    def test_test_release_rejects_unfrozen_mismatched_and_failed_evidence(self):
        release,record,report_file,report=evidence(self.root,"synthetic-dataset")
        self.assertEqual(authorize_test_release(release,"synthetic-dataset")['model_version'],'synthetic-v1')
        with self.assertRaises(ValueError):authorize_test_release(release,"other-dataset")
        for value in (.74,float('nan'),True):
            report['metrics']['precision']=value;report_file.write_text(json.dumps(report))
            record['validation_report_sha256']=digest(report_file);release.write_text(json.dumps(record))
            with self.assertRaises(ValueError):authorize_test_release(release,"synthetic-dataset")
        report['metrics']['precision']=.8;report_file.write_text(json.dumps(report))
        record['validation_report_sha256']=digest(report_file)
        for frozen in ('2026-10-20T00:00:00+00:00','2026-10-18T00:00:00'):
            release.write_text(json.dumps({**record,'model_frozen_at':frozen}))
            with self.assertRaises(ValueError):authorize_test_release(release,"synthetic-dataset")
        release.write_text(json.dumps({**record,'model_sha256':'tampered'}))
        with self.assertRaises(ValueError):authorize_test_release(release,"synthetic-dataset")


if __name__=='__main__':unittest.main()
