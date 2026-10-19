"""Bounded table reads for already-authenticated principals, never arbitrary SQL."""

import duckdb
from .artifacts import verified_artifact
from .serving import VIEWS, ESTIMATED_VIEWS


class DataUnavailable(RuntimeError):
    pass


class ServingReader:
    def __init__(self, artifact_dir):
        self.record,self.database,_=verified_artifact(artifact_dir,kind="serving")

    def read(self,name,principal_id,*,start=None,end=None,limit=1000,allow_candidate_estimates=False):
        if name not in VIEWS or type(limit) is not int or not 1<=limit<=10000:
            raise ValueError("Unknown view or invalid row limit")
        relation,date_column,scoped=VIEWS[name]
        with duckdb.connect(str(self.database),read_only=True) as con:
            # Authentication is the application's responsibility. Principal IDs
            # must come from a verified session, never from an LLM/user query.
            if not con.execute("SELECT count(*) FROM gold.account_scope WHERE principal_id=?",[principal_id]).fetchone()[0]:
                raise PermissionError("No configured account scope")
            if name in ESTIMATED_VIEWS:
                if self.record.get("estimate_views_status") != "Candidate_not_approved":
                    raise DataUnavailable("Candidate estimate views are not present in this publication")
                if allow_candidate_estimates is not True:
                    raise DataUnavailable("Candidate estimates require explicit opt-in; not operationally approved")
            if name in {"oos_predictions","visit_priority"} and self.record["model_outputs_status"]!="Available":
                raise DataUnavailable("No released model outputs; missing predictions are not zero risk")
            predicates=[];params=[]
            if scoped:
                predicates.append("store_id IN (SELECT store_id FROM gold.account_scope WHERE principal_id=?)")
                params.append(principal_id)
            for bound,operator in ((start,">="),(end,"<=")):
                if bound is not None:
                    if not date_column:
                        raise ValueError("This reference view has no date filter")
                    predicates.append(f"{date_column}{operator}?");params.append(bound)
            sql=f"SELECT * FROM serving.vw_{name}"
            if predicates:
                sql+=" WHERE "+" AND ".join(predicates)
            cursor=con.execute(sql+" ORDER BY ALL LIMIT ?",params+[limit])
            names=[c[0] for c in cursor.description]
            return [dict(zip(names,row)) for row in cursor.fetchall()]

    def schema(self):
        with duckdb.connect(str(self.database),read_only=True) as con:
            available={r[0] for r in con.execute("SELECT table_name FROM information_schema.views WHERE table_schema='serving'").fetchall()}
            return {name:{"columns":[{"name":r[0],"type":r[1]} for r in con.execute(f"DESCRIBE serving.vw_{name}").fetchall()],
                          "store_scoped":definition[2],"date_column":definition[1],
                          "estimate_release_status":"Candidate_not_approved" if name in ESTIMATED_VIEWS else "Not an estimate view"}
                    for name,definition in VIEWS.items() if "vw_"+name in available}
