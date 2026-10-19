"""Data-layer acceptance: context time travel, labels, serving and publication."""

import json
import shutil
import unittest
from dataclasses import replace
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
import duckdb

from data_platform.access import DataUnavailable, ServingReader
from data_platform.context_features import build_oos_context_features
from data_platform.feature_handoff import export_feature_input
from data_platform.feature_runner import build_features
from data_platform.ingestion import digest
from data_platform.labels import build_labels
from data_platform.pipeline import build
from data_platform.quality import Quality
from data_platform.serving import build_serving,publish_serving,rollback_serving
from data_platform.workflow import build_all
import test_data_platform as source_fixtures


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=source_fixtures.PlatformTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.root=self.fixture.root
        self.config=replace(self.fixture.config,include_kpi_marts=True)

    def parent(self):
        record=build(self.config)
        return Path(record["database"]).parent,record

    def features(self,parent):
        return build_features(parent,self.config.as_of,self.root/"features",memory_limit="256MB",threads=1)

    def test_full_feature_build_handles_overlap_missing_promo_and_audit(self):
        parent,source=self.parent()
        result=self.features(parent)
        with duckdb.connect(str(Path(result["directory"])/result["database"]),read_only=True) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM gold.features_oos_daily").fetchone()[0],7)
            self.assertEqual(con.execute("SELECT eligible_promotion_count,has_promotion_overlap,observed_promo_discount_pct,consumer_promo_data_status FROM gold.features_oos_daily WHERE scoring_date='2025-01-02'").fetchone(),(2,True,None,"Unknown"))
            self.assertEqual(con.execute("SELECT last_audit_id,days_since_last_audit,pending_order_count FROM gold.features_oos_daily WHERE scoring_date='2025-01-07'").fetchone(),("A1",6,1))
        self.assertEqual(digest(parent/"build.duckdb"),source["database_sha256"])

    def test_late_old_audit_never_replaces_newer_business_evidence(self):
        parent,_=self.parent()
        projected=export_feature_input(parent,self.root/"inputs",self.config.as_of,include_context=True)
        working=self.root/"context.duckdb"
        shutil.copyfile(Path(projected["directory"])/projected["database"],working)
        with duckdb.connect(str(working)) as con:
            con.execute("""INSERT INTO feature_input.execution_audits
                SELECT * REPLACE ('A2' AS audit_id,DATE '2025-01-04' AS date,TIMESTAMP '2025-01-06 09:00:00' AS source_known_at)
                FROM feature_input.execution_audits LIMIT 1""")
            con.execute("""INSERT INTO feature_input.execution_audits
                SELECT * REPLACE ('A3' AS audit_id,DATE '2025-01-05' AS date,TIMESTAMP '2025-01-05 09:00:00' AS source_known_at)
                FROM feature_input.execution_audits LIMIT 1""")
            build_oos_context_features(con,self.config.as_of,Quality(con,"context"))
            self.assertEqual(con.execute("SELECT last_audit_id FROM gold.features_oos_context WHERE scoring_date='2025-01-06'").fetchone()[0],"A3")
            self.assertEqual(con.execute("SELECT last_audit_id FROM gold.features_oos_context WHERE scoring_date='2025-01-04'").fetchone()[0],"A1")

    def test_context_future_mutation_and_truncation(self):
        parent,_=self.parent()
        projection=export_feature_input(parent,self.root/"inputs",self.config.as_of,include_context=True)
        results=[]
        for mode in ("full","changed","truncated"):
            path=self.root/(mode+".duckdb")
            shutil.copyfile(Path(projection["directory"])/projection["database"],path)
            with duckdb.connect(str(path)) as con:
                if mode=="changed":
                    con.execute("UPDATE feature_input.consumer_promotions SET promo_discount_pct=99 WHERE date>'2025-01-04'")
                    con.execute("""INSERT INTO feature_input.execution_audits SELECT * REPLACE ('FUT' AS audit_id,
                        DATE '2025-01-06' AS date,TIMESTAMP '2025-01-06' AS source_known_at) FROM feature_input.execution_audits LIMIT 1""")
                if mode=="truncated":
                    for table in ("daily_state","consumer_promotions","execution_audits","promotion_calendar"):
                        con.execute(f"DELETE FROM feature_input.{table} WHERE date>'2025-01-04'")
                build_oos_context_features(con,date(2025,1,4) if mode=="truncated" else self.config.as_of,Quality(con,mode))
                results.append(con.execute("SELECT * FROM gold.features_oos_context WHERE scoring_date<='2025-01-04' ORDER BY scoring_date").fetchall())
        self.assertEqual(results[0],results[1]);self.assertEqual(results[0],results[2])

    def test_complete_labels_partial_positive_and_immaturity(self):
        inventory=100;rows=[]
        for offset in range(25):
            day=date(2025,1,1)+timedelta(days=offset)
            receipt=10 if offset==1 else 5 if offset==3 else 0
            sold=inventory if offset==19 else 1 if offset<19 else 0
            inventory=inventory+receipt-sold
            if offset not in (2,17):
                rows.append(self.fixture.sellout(str(day),sold,inventory,receipt,"D1" if offset==1 else "D2" if offset==3 else ""))
        self.fixture.rows["fact_sell_out"]=rows
        self.fixture.write_sources()
        self.config=replace(self.config,as_of=date(2025,1,25))
        parent,_=self.parent()
        record=build_labels(parent,self.config.as_of,self.root/"labels",memory_limit="256MB",threads=1)
        with duckdb.connect(str(Path(record["directory"])/record["database"]),read_only=True) as con:
            values=dict(con.execute("SELECT scoring_date,target_oos_7d FROM evaluation.labels_oos_next7").fetchall())
            self.assertIsNone(values[date(2025,1,1)])
            self.assertEqual(values[date(2025,1,4)],0)
            self.assertIsNone(values[date(2025,1,15)])
            self.assertEqual(values[date(2025,1,18)],1)
            self.assertIsNone(values[date(2025,1,25)])
            self.assertEqual(con.execute("SELECT label_source,confirmed_observed_oos FROM evaluation.labels_oos_next7 WHERE scoring_date='2025-01-15'").fetchone(),("partial_window_confirmed_positive",True))
            self.assertEqual(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='gold'").fetchone()[0],0)

    def policy(self,version="test-v1"):
        path=self.root/(version+".json")
        path.write_text(json.dumps({"version":version,"assignments":[{"principal_id":"alice","store_id":"S1","role":"sales"}]}))
        return path

    def test_serving_gold_only_scoped_reads_and_unknown_models(self):
        parent,_=self.parent();features=self.features(parent)
        serving=build_serving(parent,features["directory"],self.root/"serving",access_policy=self.policy(),memory_limit="256MB",threads=1)
        reader=ServingReader(serving["directory"])
        self.assertEqual(len(reader.read("store_sku_daily","alice")),7)
        self.assertEqual(reader.read("negotiation_context","alice")[0]["policy_status"],"Requires review")
        self.assertAlmostEqual(reader.read("negotiation_context","alice")[0]["static_reference_margin_pct"],50)
        with self.assertRaises(PermissionError):reader.read("store_day","unknown")
        with self.assertRaises(ValueError):reader.read("store_day; DROP TABLE gold.dim_stores","alice")
        with self.assertRaises(ValueError):reader.read("store_day","alice",limit=10001)
        with self.assertRaises(DataUnavailable):reader.read("oos_predictions","alice")
        with duckdb.connect(str(Path(serving["directory"])/serving["database"]),read_only=True) as con:
            schemas={r[0] for r in con.execute("SELECT DISTINCT table_schema FROM information_schema.tables").fetchall()}
            self.assertTrue(schemas.issubset({"gold","serving","meta"}))
            self.assertEqual(con.execute("SELECT inventory_observed,inventory_data_status FROM serving.vw_store_sku_daily WHERE date='2025-01-02'").fetchone(),(None,"Unknown"))

    def test_unconfigured_scope_denies_all_and_no_silent_admin(self):
        parent,_=self.parent();features=self.features(parent)
        serving=build_serving(parent,features["directory"],self.root/"serving")
        with self.assertRaises(PermissionError):ServingReader(serving["directory"]).read("products","admin")

    def test_publish_rollback_and_tamper_protection(self):
        parent,_=self.parent();features=self.features(parent);root=self.root/"serving"
        first=build_serving(parent,features["directory"],root,access_policy=self.policy())
        p1=publish_serving(root,first["directory"])
        second=build_serving(parent,features["directory"],root,access_policy=self.policy("test-v2"))
        p2=publish_serving(root,second["directory"])
        rolled=rollback_serving(root,p1["publication_id"])
        self.assertEqual(rolled["artifact_id"],first["artifact_id"])
        self.assertEqual(rolled["previous_publication_id"],p2["publication_id"])
        pointer=(root/"published/serving.json").read_bytes()
        with duckdb.connect(str(Path(second["directory"])/second["database"])) as con:
            con.execute("UPDATE gold.dim_products SET list_price=999")
        with self.assertRaisesRegex(ValueError,"integrity"):
            rollback_serving(root,p2["publication_id"])
        self.assertEqual((root/"published/serving.json").read_bytes(),pointer)

    def test_feature_failure_has_terminal_report_and_no_serving_publish(self):
        parent,_=self.parent()
        with patch('data_platform.feature_runner.build_oos_context_features',side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):self.features(parent)
        runs=list((self.root/"features/builds").glob('*/run.json'))
        self.assertEqual(json.loads(runs[0].read_text())["status"],"Failed")

    def test_end_to_end_build_all_and_labels_do_not_leak_into_serving(self):
        record=build_all(self.config.as_of,self.root/"delivery",source_dir=self.config.source_dir,
                         snapshot_root=self.config.snapshot_root,access_policy=self.policy(),threads=1,memory_limit="256MB",publish=True)
        self.assertEqual(record["status"],"Accepted")
        self.assertTrue(record["components"]["labels"]["restricted"])
        self.assertEqual(record["components"]["features"]["rows"],7)
        self.assertTrue(record["publication"]["application_ready"])

    def test_complete_features_match_truncated_history_and_replay(self):
        parent,_=self.parent()
        full=self.features(parent)
        early=build_features(parent,date(2025,1,4),self.root/"early",memory_limit="256MB",threads=1)
        with duckdb.connect(str(Path(full["directory"])/full["database"]),read_only=True) as con:
            expected=con.execute("SELECT * FROM gold.features_oos_daily WHERE scoring_date<='2025-01-04' ORDER BY scoring_date").fetchall()
        with duckdb.connect(str(Path(early["directory"])/early["database"]),read_only=True) as con:
            self.assertEqual(expected,con.execute("SELECT * FROM gold.features_oos_daily ORDER BY scoring_date").fetchall())

    def test_account_filtering_with_two_real_fixture_stores(self):
        self.fixture.rows["dim_stores"].append({**self.fixture.rows["dim_stores"][0],"store_id":"S2","store_name":"Other store"})
        for table in ("fact_sell_in","fact_sell_out","fact_deliveries","fact_execution_audits"):
            for original in list(self.fixture.rows[table]):
                row={**original,"store_id":"S2"}
                for key in ("transaction_id","delivery_id","audit_id","restock_delivery_id"):
                    if row.get(key):row[key]+="-S2"
                self.fixture.rows[table].append(row)
        self.fixture.write_sources()
        parent,_=self.parent();features=self.features(parent)
        serving=build_serving(parent,features["directory"],self.root/"serving",access_policy=self.policy())
        reader=ServingReader(serving["directory"])
        self.assertEqual({r["store_id"] for r in reader.read("store_sku_daily","alice")},{"S1"})
        self.assertEqual({r["store_id"] for r in reader.read("stores","alice")},{"S1"})
        with self.assertRaises(PermissionError):reader.read("stores","alice' OR 1=1 --")

    def test_zero_reference_price_has_no_invented_margin(self):
        self.fixture.rows["dim_products"][0]["list_price"]=0
        self.fixture.rows["fact_sell_in"][0].update(gross_sales=0,net_sales=0,discount_pct=0,extra_discount_pct=0)
        self.fixture.write_sources()
        parent,_=self.parent();features=self.features(parent)
        serving=build_serving(parent,features["directory"],self.root/"serving",access_policy=self.policy())
        row=ServingReader(serving["directory"]).read("negotiation_context","alice")[0]
        self.assertIsNone(row["static_reference_margin_pct"])
        self.assertIsNone(row["historical_static_cost_margin_pct"])

    def test_june_labels_and_output_inside_input_are_rejected(self):
        parent,_=self.parent()
        with self.assertRaisesRegex(ValueError,"June"):
            build_labels(parent,date(2026,6,1),self.root/"labels")
        with self.assertRaisesRegex(ValueError,"inside an input"):
            build_features(parent,self.config.as_of,parent/"features")

    def test_workflow_failure_cannot_replace_previous_publication(self):
        kwargs=dict(source_dir=self.config.source_dir,snapshot_root=self.config.snapshot_root,
                    access_policy=self.policy(),threads=1,memory_limit="256MB",publish=True)
        root=self.root/"delivery"
        build_all(self.config.as_of,root,**kwargs)
        pointer=root/"serving/published/serving.json"
        before=pointer.read_bytes()
        with patch('data_platform.workflow.build_labels',side_effect=RuntimeError('label fixture failure')):
            with self.assertRaises(RuntimeError):build_all(self.config.as_of,root,**kwargs)
        self.assertEqual(pointer.read_bytes(),before)
        reports=[json.loads(p.read_text()) for p in (root/"runs").glob('*/run.json')]
        self.assertTrue(any(r["status"]=="Failed" for r in reports))


if __name__=="__main__":unittest.main()
