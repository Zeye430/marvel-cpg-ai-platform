"""Owner execution policy (owner_execution_policy.v1) and exposure-ledger setup.

The policy records a *real* human approval (name, time, message reference)
supplied by the operator; this code never invents one. Program-resolved
identities (run, ledger, bundle) are recorded later as program resolution,
not as something the owner reviewed in advance.
"""

import fcntl
from contextlib import contextmanager
from pathlib import Path

from data_platform.test_exposure import initialize_test_exposure_ledger, read_ledger

from . import config
from .contracts import Module2Error, code_hashes, config_sha256, parse_time, read_json, sha256_file, utc_now, write_json

RISKS = (
    "complete_label_selection_bias: metrics use rows with seven observed future days only",
    "segment_and_current_status_results_may_miss_targets",
    "static_simulated_v3_data_differs_from_real_business",
    "calibration_does_not_guarantee_reliable_probabilities",
    "known_historical_june_exploration: June results are a disclosed holdout_confirmation, not an untouched test",
)


def create_policy(path, *, human_authority, workflow, allowed_output_roots, ledger_path, known_history,
                  prior_exposures, synthetic=False):
    """Write the policy once. ``human_authority`` must come from the real approver."""
    for key in ("approver", "approved_at", "message", "message_reference"):
        if not isinstance(human_authority.get(key), str) or not human_authority[key].strip():
            raise Module2Error(f"Human authority field required (never generated): {key}")
    parse_time(human_authority["approved_at"])
    plan = workflow["plan"].to_dict()
    policy = {
        "version": config.VERSIONS["policy"], "synthetic": bool(synthetic),
        "policy_id": f"policy-{sha256_file(workflow['path'])[:12]}-{config_sha256()[:12]}",
        "human_authority": dict(human_authority),
        "execution_scope": {
            "dataset_hash": plan["source"]["dataset_hash"], "source_build_id": plan["source"]["source_build_id"],
            "plan_sha256": plan["plan_sha256"], "workflow_run": workflow["path"], "workflow_sha256": workflow["sha256"],
            "feature_version": config.FEATURE_VERSION, "cohort": config.COHORT, "label_version": config.LABEL_VERSION,
            "config_sha256": config_sha256(), "rules": config.frozen_rules(),
            # Code frozen at transcription; a bundle fitted by different code cannot use this policy.
            "code_identity": code_hashes(),
            "allowed_output_roots": [str(Path(r).resolve()) for r in allowed_output_roots],
            "max_primary_holdout_evaluations": 1, "exposure_modes": ["holdout_confirmation"],
            "confirmation_windows": [plan["test"]],
            "verification_replay": "Identical release/evidence/source/window with an explicit reason only",
            "ledger": {"path": str(Path(ledger_path).resolve()), "initialization_permitted": True},
            "known_history": known_history, "prior_exposures": prior_exposures,
            "risk_acknowledgements": list(RISKS),
        },
        "not_authorized": ["production deployment", "model acceptance", "Git commit/push/PR/merge",
                           "cloud access", "future snapshots or unlimited retraining"],
        "transcribed_at": utc_now(),
    }
    write_json(path, policy, exclusive=True)
    return policy


def check_policy(path, workflow, output_root):
    policy = read_json(path, "owner execution policy")
    human, scope = policy.get("human_authority", {}), policy.get("execution_scope", {})
    if policy.get("version") != config.VERSIONS["policy"]:
        raise Module2Error("Unsupported execution policy")
    for key in ("approver", "approved_at", "message", "message_reference"):
        if not isinstance(human.get(key), str) or not human[key].strip():
            raise Module2Error("Execution policy lacks genuine human authority")
    plan = workflow["plan"].to_dict()
    if (scope.get("dataset_hash") != plan["source"]["dataset_hash"] or scope.get("plan_sha256") != plan["plan_sha256"]
            or scope.get("workflow_sha256") != workflow["sha256"] or scope.get("config_sha256") != config_sha256()):
        raise Module2Error("Execution policy does not cover this workflow/plan/rule set")
    if scope.get("code_identity") != code_hashes():
        raise Module2Error("Execution policy does not bind the current frozen code identity")
    root = Path(output_root).resolve()
    if not any(root.is_relative_to(Path(r)) for r in scope.get("allowed_output_roots", [])):
        raise Module2Error("Output root is outside the policy's authorized roots")
    return policy


BINDING_VERSION = "module2.ledger_binding.v1"


def binding_path(ledger):
    """Durable initialization receipt kept beside, not inside, the replaceable ledger file."""
    ledger = Path(ledger)
    return ledger.with_name(ledger.name + ".binding.json")


@contextmanager
def _ledger_lock(ledger):
    # Blocking lock on the same file the shared reservation layer uses, so parallel
    # first-setup attempts serialize and all observe one ledger identity.
    lock = ledger.with_name(ledger.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read_ledger(ledger):
    try:
        return read_ledger(ledger)
    except ValueError as error:
        raise Module2Error(f"Exposure ledger unusable ({error}); manual reconciliation/restore required, "
                           "it is never rebuilt automatically") from error


def _read_binding(path, ledger):
    binding = read_json(path, "ledger binding receipt")
    if (binding.get("version") != BINDING_VERSION or binding.get("ledger_path") != str(ledger.resolve())
            or not isinstance(binding.get("ledger_id"), str) or not isinstance(binding.get("header_hash"), str)
            or not isinstance(binding.get("observed"), dict)):
        raise Module2Error("Ledger binding receipt is malformed or names another ledger; manual reconciliation required")
    return binding


def _check_bound(binding, header, entries):
    if header["ledger_id"] != binding["ledger_id"] or header["entry_hash"] != binding["header_hash"]:
        raise Module2Error("Exposure ledger is not the bound ledger (replaced or re-created); manual restore required")
    observed = binding["observed"]
    chain = [header, *entries]
    sequence = observed.get("sequence")
    if (not isinstance(sequence, int) or isinstance(sequence, bool) or sequence >= len(chain)
            or chain[sequence]["entry_hash"] != observed.get("entry_hash")):
        raise Module2Error("Exposure ledger is missing entries already observed (truncated or rolled back); "
                           "manual restore required")


def _observed(entries, header):
    tip = entries[-1] if entries else header
    return {"sequence": tip["sequence"], "entry_hash": tip["entry_hash"], "recorded_at": utc_now()}


def ensure_ledger(policy, policy_path):
    """Bind the policy's fixed ledger path once; afterwards only verify and reuse.

    First setup (no ledger, no binding receipt, initialization permitted) creates the
    ledger and a durable receipt. Once bound, a missing, moved, replaced, truncated or
    malformed ledger fails closed before any release or truth access.
    """
    ledger = Path(policy["execution_scope"]["ledger"]["path"])
    receipt = binding_path(ledger)
    with _ledger_lock(ledger):
        binding = _read_binding(receipt, ledger) if receipt.exists() or receipt.is_symlink() else None
        if ledger.exists() or ledger.is_symlink():
            header, entries = _read_ledger(ledger)
            if binding is not None:
                _check_bound(binding, header, entries)
                return header
            # A valid ledger initialized before receipts existed: adopt it as found.
            write_json(receipt, {
                "version": BINDING_VERSION, "ledger_path": str(ledger.resolve()), "ledger_id": header["ledger_id"],
                "header_hash": header["entry_hash"], "bound_by": "adopted_existing_ledger",
                "bound_at": utc_now(), "policy_id": policy["policy_id"], "policy_sha256": sha256_file(policy_path),
                "observed": _observed(entries, header)}, exclusive=True)
            return header
        if binding is not None:
            raise Module2Error("Bound exposure ledger is missing; it is never re-created automatically "
                               "(restore the original file or reconcile manually)")
        if not policy["execution_scope"]["ledger"].get("initialization_permitted"):
            raise Module2Error("Ledger missing and initialization not permitted")
        header = initialize_test_exposure_ledger(
            ledger, initialized_by="Module 2 delivery program (engineer-run executor), not the owner personally",
            initialized_at=utc_now(), history_note=policy["execution_scope"]["known_history"],
            prior_exposures=policy["execution_scope"]["prior_exposures"],
            authorized_under={"policy_id": policy["policy_id"], "policy_sha256": sha256_file(policy_path)})
        write_json(receipt, {
            "version": BINDING_VERSION, "ledger_path": str(ledger.resolve()), "ledger_id": header["ledger_id"],
            "header_hash": header["entry_hash"], "bound_by": "initialized", "bound_at": utc_now(),
            "policy_id": policy["policy_id"], "policy_sha256": sha256_file(policy_path),
            "observed": _observed([], header)}, exclusive=True)
        return header


def record_ledger_tip(policy):
    """After an append, advance the receipt's observed tip so later truncation is detected."""
    ledger = Path(policy["execution_scope"]["ledger"]["path"])
    receipt = binding_path(ledger)
    with _ledger_lock(ledger):
        binding = _read_binding(receipt, ledger)
        header, entries = _read_ledger(ledger)
        _check_bound(binding, header, entries)
        write_json(receipt, dict(binding, observed=_observed(entries, header)))
