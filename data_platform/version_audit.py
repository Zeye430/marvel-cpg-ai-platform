"""Compare active local sources and Git/LFS identities without loading outcomes."""

import csv
import hashlib
import subprocess
from pathlib import Path

from .ingestion import digest, json_hash, load_contract

ROOT = Path(__file__).resolve().parents[1]


def audit_data(source_dir=None, contract_path=None, git_ref="origin/main"):
    source_dir = Path(source_dir or ROOT / "data/raw")
    contract = load_contract(contract_path or ROOT / "data_platform/contracts/v3/sources.json")
    revision = subprocess.run(["git", "rev-parse", "--verify", git_ref], cwd=ROOT,
                              capture_output=True, text=True)
    ref_commit = revision.stdout.strip() if revision.returncode == 0 else None
    rows, hashes = [], {}
    for source in contract["sources"]:
        name = source["filename"]
        path = source_dir / name
        local_hash, header = None, None
        if path.is_file():
            local_hash = digest(path)
            hashes[name] = local_hash
            with path.open(newline="", encoding="utf-8") as handle:
                header = next(csv.reader(handle), [])
        blob = subprocess.run(["git", "show", f"{git_ref}:new_marvel_data/{name}"],
                              cwd=ROOT, capture_output=True)
        remote_hash, storage = None, "not_tracked_at_ref"
        if blob.returncode == 0:
            if blob.stdout.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                remote_hash = next((line.split("oid sha256:", 1)[1]
                                    for line in blob.stdout.decode().splitlines()
                                    if line.startswith("oid sha256:")), None)
                storage = "git_lfs_content_oid"
            else:
                remote_hash = hashlib.sha256(blob.stdout).hexdigest()
                storage = "git_blob_content_hash"
        archive = source_dir / "_archive/v2_2026-10-09" / name
        archive_hash = digest(archive) if archive.is_file() else None
        rows.append({
            "filename": name, "active_sha256": local_hash,
            "v3_reference_sha256": source["reference_sha256"],
            "active_matches_v3_reference": local_hash == source["reference_sha256"],
            "active_header_matches_contract": header == [c["name"] for c in source["columns"]],
            "git_source_path": "new_marvel_data/" + name, "git_content_sha256": remote_hash,
            "git_storage": storage,
            "git_content_matches_v3": remote_hash == source["reference_sha256"],
            "git_content_matches_v2_archive": bool(remote_hash and archive_hash and remote_hash == archive_hash),
        })
    return {
        "active_directory": str(source_dir.resolve()), "contract_version": contract["contract_version"],
        "active_dataset_hash": json_hash(sorted(hashes.items())) if len(hashes) == 8 else None,
        "active_matches_authorized_v3": all(r["active_matches_v3_reference"] for r in rows),
        "git_ref": git_ref, "git_ref_commit": ref_commit,
        "ref_freshness_note": "Uses the locally fetched Git ref; fetch origin before claiming current GitHub state.",
        "raw_files_note": "data/raw is ignored; Git checkout does not update or provision these inputs.",
        "files": rows,
    }
