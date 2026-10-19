"""End-to-end local data delivery, without model/UI/cloud development."""

import uuid
from dataclasses import replace
from pathlib import Path
from .feature_runner import build_features
from .labels import build_labels
from .pipeline import BuildConfig, ROOT, atomic_json, build, now, writer_lock
from .serving import build_serving, publish_serving
from .artifacts import require_separate_root
from .time_policy import DEVELOPMENT_CUTOFF


def build_all(as_of,output_root=None,*,source_dir=None,snapshot_root=None,access_policy=None,
              assume_complete_snapshot=False,memory_limit="1GB",threads=2,publish=False):
    root=Path(output_root or ROOT/"outputs/v3/platform/delivery").resolve()
    require_separate_root(root,source_dir or BuildConfig().source_dir)
    with writer_lock(root/".workflow.lock"):
        run_id=now().strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:12]
        run_dir=root/"runs"/run_id
        run_dir.mkdir(parents=True)
        record={"run_id":run_id,"as_of":str(as_of),"status":"Running","started_at":str(now()),"components":{}}
        atomic_json(run_dir/"run.json",record)
        try:
            config=replace(BuildConfig(),as_of=as_of,output_root=root/"base",include_kpi_marts=True,
                           memory_limit=memory_limit,threads=threads)
            if source_dir: config=replace(config,source_dir=Path(source_dir))
            if snapshot_root: config=replace(config,snapshot_root=Path(snapshot_root))
            parent=build(config)
            source_build=Path(parent["database"]).parent
            record["components"]["base"]=parent
            atomic_json(run_dir/"run.json",record)
            development_as_of=min(as_of,DEVELOPMENT_CUTOFF)
            features=build_features(source_build,development_as_of,root/"features",assume_complete_snapshot=assume_complete_snapshot,memory_limit=memory_limit,threads=threads)
            record["components"]["features"]=features
            atomic_json(run_dir/"run.json",record)
            record["components"]["labels"]=build_labels(source_build,development_as_of,root/"evaluation/labels",memory_limit=memory_limit,threads=threads)
            scoring_features = features if as_of == development_as_of else build_features(
                source_build,as_of,root/"scoring/features",purpose="scoring",
                assume_complete_snapshot=assume_complete_snapshot,memory_limit=memory_limit,threads=threads)
            record["components"]["scoring_features"]=scoring_features
            serving=build_serving(source_build,scoring_features["directory"],root/"serving",access_policy=access_policy,memory_limit=memory_limit,threads=threads)
            record["components"]["serving"]=serving
            # No publication until every required data-layer step succeeds.
            if publish:
                record["publication"]=publish_serving(root/"serving",serving["directory"])
            record.update(status="Accepted",dataset_hash=parent["dataset_hash"],development_as_of=str(development_as_of),
                          june_test_status="Locked; no test labels or model evaluation in build-all")
        except (Exception,KeyboardInterrupt) as error:
            record.update(status="Interrupted" if isinstance(error,KeyboardInterrupt) else "Failed",error=f"{type(error).__name__}: {error}")
            raise
        finally:
            record["finished_at"]=str(now())
            atomic_json(run_dir/"run.json",record)
    return record
