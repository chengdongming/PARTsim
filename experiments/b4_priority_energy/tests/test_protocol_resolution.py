import hashlib
import json
import math
import re
import shutil
import tempfile
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
B4_DIR = TEST_DIR.parent
REPO_ROOT = TEST_DIR.parents[2]
RESOLUTION_PATH = B4_DIR / "protocol_resolution_v1.json"
MARKDOWN_PATH = B4_DIR / "protocol_resolution_v1.md"
FROZEN_DOC_PATH = REPO_ROOT / "docs" / "experiments" / (
    "ASAP_BLOCK_B4_priority_energy_v5_2_frozen.md"
)
SYSTEM_TEMPLATE_PATH = REPO_ROOT / "v9_3_b4_priority_energy_system_template.yml"

# External compatibility anchors permitted by B4-PE-I4A-0-R2.
FROZEN_DOC_SHA256 = (
    "5e168664d9ce2062bf2418d2280195124c08b1311d1de1e280d20822965c0581"
)
SYSTEM_TEMPLATE_SHA256 = (
    "a64181bf9fda8155c5b0b8b0451a160d6c44c2c8fae188a974640a4d2b243510"
)
PHASE_ALGORITHM_ANCHORS = {
    "pilot": [
        "ASAP-BLOCK",
        "ASAP-NONBLOCK",
        "ASAP-SYNC",
        "ALAP-BLOCK",
        "ST-BLOCK",
    ],
    "formal_main": [
        "ASAP-BLOCK",
        "ASAP-NONBLOCK",
        "ASAP-SYNC",
        "ALAP-BLOCK",
        "ALAP-NONBLOCK",
        "ALAP-SYNC",
        "ST-BLOCK",
        "ST-NONBLOCK",
        "ST-SYNC",
    ],
    "negative_control": [
        "ASAP-BLOCK",
        "ASAP-NONBLOCK",
        "ASAP-SYNC",
        "ALAP-BLOCK",
        "ALAP-NONBLOCK",
        "ALAP-SYNC",
        "ST-BLOCK",
        "ST-NONBLOCK",
        "ST-SYNC",
    ],
}
PHASE_COUNT_ANCHORS = {
    "pilot": 2400,
    "formal_main": 18000,
    "negative_control": 5400,
}
EXPECTED_DOCUMENT_IDENTITY_MIGRATION = {
    "authorization_scope": "B4-PE R2/master integration",
    "authorized": True,
    "current_sha256": FROZEN_DOC_SHA256,
    "document_path": (
        "docs/experiments/ASAP_BLOCK_B4_priority_energy_v5_2_frozen.md"
    ),
    "exact_reason": "removed two trailing spaces from line 2",
    "freeze_status": "candidate",
    "master_integration_commit": (
        "46ac0ece34eacbd5178e292c16de961359a5c440"
    ),
    "migration_scope": "R2/master integration only",
    "previous_identity_commit": (
        "8b09e37483eb2df6ce22621761f06433f2519663"
    ),
    "previous_sha256": (
        "0fee308839f2097664a63a21f8806128c868b1016fab2712e67892356961be52"
    ),
    "scientific_contract_change": False,
    "semantic_change": False,
    "silent_changes_forbidden": True,
    "source_pr": 58,
}

# Compatibility anchors only. Derivation remains entirely JSON-driven below.
GOLDEN_VECTORS = {
    "pilot_taskset_seed": 1979506832282504405,
    "pilot_taskset_id": (
        "ts-76ef159067fe346e01596b1443f4f14d7dc6e3c0689360ea71328623288da5c6"
    ),
    "pilot_random_source_seed": 4039868445231694038,
    "pilot_source_id": (
        "src-dc2baf44e8076f4b5e42482a53140678a70583cb402fe225347ffe1dba62b060"
    ),
    "pilot_case_id": (
        "case-3a8b02d0a0e0d8fefc8dd1617ac5bd86ed8bc75f464963789856c060b13f0e90"
    ),
    "formal_taskset_seed": 8216105298971168251,
    "formal_taskset_id": (
        "ts-af51b3735401b7855bb8d4d0b1e373a2ea0ab50e6ee9abf2b7bd30b5de29534b"
    ),
}


class ContractError(ValueError):
    pass


class CompatibilityError(ContractError):
    pass


class DuplicateIdentityError(ContractError):
    pass


class SeedCollisionError(ContractError):
    pass


class IDCollisionError(ContractError):
    pass


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact_json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def formula_product(phase_config):
    return math.prod(item["factor"] for item in phase_config["formula"])


def _require(condition, message, error_type=ContractError):
    if not condition:
        raise error_type(message)


def _hash_name(name):
    return name.lower().replace("-", "")


def _hash_digest_size(name):
    try:
        return hashlib.new(_hash_name(name)).digest_size
    except (TypeError, ValueError) as exc:
        raise ContractError(f"unsupported hash: {name}") from exc


def _hash_material(name, material):
    try:
        digest = hashlib.new(_hash_name(name))
    except (TypeError, ValueError) as exc:
        raise ContractError(f"unsupported hash: {name}") from exc
    digest.update(material)
    return digest


def key_schemas(contract):
    return {
        name: fields
        for name, fields in contract["reuse_dimensions"].items()
        if name.endswith("_key")
    }


def validate_contract_structure(contract):
    required_top = {
        "canonicalization",
        "fail_closed",
        "frozen_document_sha256",
        "id_derivation",
        "identity_protocol",
        "phase_algorithms",
        "phase_counts",
        "reuse_dimensions",
        "schema_version",
        "seed_derivation",
        "source_contract",
        "system_template_sha256",
    }
    _require(required_top <= set(contract), "required top-level field missing")
    _require(
        type(contract["schema_version"]) is int and contract["schema_version"] > 0,
        "invalid schema_version",
    )

    algorithms = contract["phase_algorithms"]
    counts = contract["phase_counts"]
    _require(isinstance(algorithms, dict) and algorithms, "phases must be non-empty")
    _require(len(algorithms) == 3, "exactly three phases required")
    _require(set(algorithms) == set(counts), "phase/count names mismatch")
    for phase, names in algorithms.items():
        _require(isinstance(phase, str) and phase, "invalid phase name")
        _require(
            isinstance(names, list)
            and names
            and all(isinstance(name, str) and name for name in names),
            f"{phase} algorithms invalid",
        )
        _require(len(names) == len(set(names)), f"{phase} algorithms duplicate")
        config = counts[phase]
        _require(type(config["expected"]) is int, f"{phase} expected invalid")
        _require(isinstance(config["formula"], list) and config["formula"], "formula")
        dimensions = []
        for item in config["formula"]:
            _require(isinstance(item["dimension"], str), "dimension name invalid")
            _require(type(item["factor"]) is int and item["factor"] > 0, "factor")
            dimensions.append(item["dimension"])
        _require(len(dimensions) == len(set(dimensions)), "duplicate phase dimension")
        _require(formula_product(config) == config["expected"], "phase count mismatch")

    schemas = key_schemas(contract)
    _require(schemas, "no key schemas")
    _require(
        {"taskset_key", "source_key", "case_key"} <= set(schemas),
        "required identity key schema missing",
    )
    for name, fields in schemas.items():
        _require(
            isinstance(fields, list)
            and fields
            and all(isinstance(field, str) and field for field in fields),
            f"{name} must be a non-empty string list",
        )
        _require(len(fields) == len(set(fields)), f"{name} contains duplicate fields")

    canonical = contract["canonicalization"]
    _require(isinstance(canonical["encoding"], str), "encoding missing")
    "".encode(canonical["encoding"])
    _require(
        isinstance(canonical["separators"], list)
        and len(canonical["separators"]) == 2
        and all(isinstance(value, str) for value in canonical["separators"]),
        "invalid separators",
    )
    _require(type(canonical["ensure_ascii"]) is bool, "ensure_ascii invalid")
    _require(isinstance(canonical["object_keys"], str), "object key policy invalid")
    _require(type(canonical["reject_unknown_key_fields"]) is bool, "unknown policy")
    _require(
        type(canonical["forbid_binary64_numbers"]) is bool,
        "binary64 policy invalid",
    )
    _require(
        isinstance(canonical["booleans_as_integers"], str),
        "boolean policy invalid",
    )
    for list_name in (
        "decimal_string_fields",
        "integer_fields",
        "forbidden_identity_fields",
    ):
        values = canonical[list_name]
        _require(
            isinstance(values, list)
            and all(isinstance(value, str) for value in values)
            and len(values) == len(set(values)),
            f"{list_name} invalid",
        )
    re.compile(canonical["decimal_string_pattern"])

    seed_domains = []
    seed_required = {
        "domain",
        "hash",
        "digest_slice",
        "byte_order",
        "mask_hex",
        "result_bits",
        "zero_allowed",
        "collision_policy",
    }
    seed_rules = contract["seed_derivation"]
    _require(isinstance(seed_rules, dict) and seed_rules, "seed rules invalid")
    for kind, rule in seed_rules.items():
        _require(isinstance(kind, str) and kind, "seed kind invalid")
        _require(isinstance(rule, dict), f"{kind} seed rule invalid")
        _require(f"{kind}_key" in schemas, f"missing schema for {kind} seed")
        _require(seed_required <= set(rule), f"{kind} seed field missing")
        _require(isinstance(rule["domain"], str) and rule["domain"], "seed domain")
        seed_domains.append(rule["domain"])
        digest_size = _hash_digest_size(rule["hash"])
        digest_slice = rule["digest_slice"]
        _require(
            set(digest_slice) >= {"start_byte", "length_bytes"},
            "digest slice field missing",
        )
        start = digest_slice["start_byte"]
        length = digest_slice["length_bytes"]
        _require(type(start) is int and type(length) is int, "digest slice type")
        _require(start >= 0 and length > 0 and start + length <= digest_size, "digest slice out of range")
        _require(rule["byte_order"] in {"big", "little"}, "invalid byte order")
        _require(type(rule["result_bits"]) is int and rule["result_bits"] > 0, "result bits")
        try:
            mask = int(rule["mask_hex"], 16)
        except (TypeError, ValueError) as exc:
            raise ContractError("invalid seed mask") from exc
        _require(mask == (1 << rule["result_bits"]) - 1, "mask/result_bits mismatch")
        _require(rule["result_bits"] <= length * 8, "mask exceeds digest slice")
        _require(type(rule["zero_allowed"]) is bool, "zero policy invalid")
        _require(rule["collision_policy"] == "fail_closed", "seed collision policy")
    _require(
        "source" in seed_rules
        and "deterministic_source_seed" in seed_rules["source"]
        and seed_rules["source"]["deterministic_source_seed"] is None,
        "deterministic source seed",
    )
    _require(len(seed_domains) == len(set(seed_domains)), "duplicate seed domain")

    id_domains = []
    id_prefixes = []
    id_required = {
        "domain",
        "hash",
        "id_prefix",
        "digest_hex_length",
        "use_full_hexdigest",
        "collision_policy",
    }
    _require(
        isinstance(contract["id_derivation"], dict) and contract["id_derivation"],
        "ID rules invalid",
    )
    for kind, rule in contract["id_derivation"].items():
        _require(id_required <= set(rule), f"{kind} ID field missing")
        key_name = f"{kind}_key"
        _require(key_name in schemas, f"missing schema for {kind} identity")
        _require(isinstance(rule["domain"], str) and rule["domain"], "ID domain")
        _require(isinstance(rule["id_prefix"], str) and rule["id_prefix"], "ID prefix")
        id_domains.append(rule["domain"])
        id_prefixes.append(rule["id_prefix"])
        digest_size = _hash_digest_size(rule["hash"])
        _require(type(rule["digest_hex_length"]) is int, "ID length type")
        _require(type(rule["use_full_hexdigest"]) is bool, "full digest flag")
        if rule["use_full_hexdigest"]:
            _require(
                rule["digest_hex_length"] == digest_size * 2,
                "full digest length mismatch",
            )
        else:
            _require(
                0 < rule["digest_hex_length"] <= digest_size * 2,
                "truncated digest length invalid",
            )
        _require(rule["collision_policy"] == "fail_closed", "ID collision policy")
    _require(len(id_domains) == len(set(id_domains)), "duplicate ID domain")
    _require(len(id_prefixes) == len(set(id_prefixes)), "duplicate ID prefix")
    _require(
        len(seed_domains + id_domains) == len(set(seed_domains + id_domains)),
        "duplicate identity domain",
    )

    fail_closed = contract["fail_closed"]
    policy_names = (
        "duplicate_canonical_key",
        "different_canonical_keys_same_seed",
        "different_canonical_keys_same_id",
    )
    for name in policy_names:
        _require(fail_closed.get(name) in {"error", "fail_closed"}, f"{name} policy")
    return contract


def canonical_json(contract, key_name, key):
    schemas = key_schemas(contract)
    _require(key_name in schemas, f"unknown key schema: {key_name}")
    expected = schemas[key_name]
    _require(isinstance(key, dict), "identity key must be an object")
    canonical = contract["canonicalization"]
    if canonical["reject_unknown_key_fields"]:
        _require(set(key) == set(expected), "identity fields mismatch")
    else:
        _require(set(expected) <= set(key), "identity fields mismatch")
    decimal_fields = set(canonical["decimal_string_fields"])
    integer_fields = set(canonical["integer_fields"])
    decimal_pattern = re.compile(canonical["decimal_string_pattern"])
    for field, value in key.items():
        if (
            isinstance(value, bool)
            and canonical["booleans_as_integers"] == "forbidden"
        ):
            raise ContractError(f"bool is forbidden in identity field: {field}")
        if isinstance(value, float) and canonical["forbid_binary64_numbers"]:
            raise ContractError(f"binary64 is forbidden in identity field: {field}")
        if field in decimal_fields:
            _require(
                isinstance(value, str) and decimal_pattern.fullmatch(value),
                f"decimal identity field must be a string: {field}",
            )
        elif field in integer_fields:
            _require(type(value) is int, f"integer identity field has wrong type: {field}")
        else:
            _require(isinstance(value, str), f"identity field must be a string: {field}")
    return json.dumps(
        key,
        sort_keys=canonical["object_keys"] == "lexicographic",
        separators=tuple(canonical["separators"]),
        ensure_ascii=canonical["ensure_ascii"],
    ).encode(canonical["encoding"])


def key_from_record(contract, key_name, record):
    fields = key_schemas(contract)[key_name]
    try:
        key = {field: record[field] for field in fields}
    except KeyError as exc:
        raise CompatibilityError(f"compatibility record missing field: {exc.args[0]}") from exc
    canonical_json(contract, key_name, key)
    return key


def semantic_record(
    contract,
    phase="pilot",
    utilization="0.3",
    replicate_index=1,
    lambda_E="0.70",
    rho_E="1",
    algorithm=None,
):
    source = contract["source_contract"]
    pools = contract["reuse_dimensions"]["phase_taskset_pool"]
    return {
        "identity_protocol": contract["identity_protocol"],
        "taskset_pool": pools[phase],
        "utilization": utilization,
        "replicate_index": replicate_index,
        "phase": phase,
        "lambda_E": lambda_E,
        "rho_E": rho_E,
        "algorithm": algorithm or contract["phase_algorithms"][phase][0],
        "source_profile": source["source_profile"],
        "horizon_ms": source["horizon_ms"],
        "rho_reference": source["rho_reference"],
        "E0_rule": source["E0_rule"],
        "Emax_rule": source["Emax_rule"],
        "alpha_rule": source["alpha_rule"],
    }


def taskset_key(
    taskset_pool="pilot", utilization="0.3", replicate_index=1, contract=None
):
    contract = RESOLUTION if contract is None else contract
    record = semantic_record(contract, utilization=utilization, replicate_index=replicate_index)
    record["taskset_pool"] = taskset_pool
    return key_from_record(contract, "taskset_key", record)


def taskset_key_for_phase(
    phase, utilization="0.3", replicate_index=1, contract=None
):
    contract = RESOLUTION if contract is None else contract
    record = semantic_record(
        contract, phase=phase, utilization=utilization, replicate_index=replicate_index
    )
    return key_from_record(contract, "taskset_key", record)


def source_key(taskset_id_value, lambda_E="0.70", contract=None):
    contract = RESOLUTION if contract is None else contract
    record = semantic_record(contract, lambda_E=lambda_E)
    record["taskset_id"] = taskset_id_value
    return key_from_record(contract, "source_key", record)


def case_key(
    taskset_id_value,
    source_id_value,
    algorithm="ASAP-BLOCK",
    phase="pilot",
    rho_E="1",
    contract=None,
):
    contract = RESOLUTION if contract is None else contract
    _require(phase in contract["phase_algorithms"], "unknown phase")
    _require(
        algorithm in contract["phase_algorithms"][phase],
        "unknown or disallowed algorithm",
    )
    record = semantic_record(
        contract, phase=phase, rho_E=rho_E, algorithm=algorithm
    )
    record.update(taskset_id=taskset_id_value, source_id=source_id_value)
    return key_from_record(contract, "case_key", record)


def derive_seed(contract, seed_kind, key):
    _require(seed_kind in contract["seed_derivation"], "unknown seed kind")
    rule = contract["seed_derivation"][seed_kind]
    key_name = f"{seed_kind}_key"
    encoding = contract["canonicalization"]["encoding"]
    material = rule["domain"].encode(encoding) + canonical_json(contract, key_name, key)
    digest = _hash_material(rule["hash"], material).digest()
    start = rule["digest_slice"]["start_byte"]
    length = rule["digest_slice"]["length_bytes"]
    selected = digest[start : start + length]
    value = int.from_bytes(selected, rule["byte_order"])
    return value & int(rule["mask_hex"], 16)


def derive_taskset_seed(key, contract=None):
    contract = RESOLUTION if contract is None else contract
    return derive_seed(contract, "taskset", key)


def derive_source_seed(key, random_source=False, contract=None):
    contract = RESOLUTION if contract is None else contract
    rule = contract["seed_derivation"]["source"]
    if not random_source:
        return rule["deterministic_source_seed"]
    return derive_seed(contract, "source", key)


def derive_id(contract, identity_kind, key):
    _require(identity_kind in contract["id_derivation"], "unknown identity kind")
    rule = contract["id_derivation"][identity_kind]
    key_name = f"{identity_kind}_key"
    encoding = contract["canonicalization"]["encoding"]
    material = rule["domain"].encode(encoding) + canonical_json(contract, key_name, key)
    digest = _hash_material(rule["hash"], material).hexdigest()
    selected = digest if rule["use_full_hexdigest"] else digest[: rule["digest_hex_length"]]
    _require(len(selected) == rule["digest_hex_length"], "ID digest length mismatch")
    return rule["id_prefix"] + selected


def identity_id(identity_kind, key, contract=None):
    contract = RESOLUTION if contract is None else contract
    return derive_id(contract, identity_kind, key)


def identity_bundle(contract, record):
    task_key = key_from_record(contract, "taskset_key", record)
    task_seed = derive_seed(contract, "taskset", task_key)
    task_id = derive_id(contract, "taskset", task_key)
    with_task = dict(record, taskset_id=task_id)
    src_key = key_from_record(contract, "source_key", with_task)
    random_source_seed = derive_seed(contract, "source", src_key)
    source_id = derive_id(contract, "source", src_key)
    with_source = dict(with_task, source_id=source_id)
    c_key = key_from_record(contract, "case_key", with_source)
    case_id = derive_id(contract, "case", c_key)
    return {
        "taskset_key": task_key,
        "taskset_seed": task_seed,
        "taskset_id": task_id,
        "source_key": src_key,
        "random_source_seed": random_source_seed,
        "source_id": source_id,
        "case_key": c_key,
        "case_id": case_id,
    }


def compatibility_vectors(contract):
    pilot = identity_bundle(contract, semantic_record(contract))
    formal = identity_bundle(
        contract,
        semantic_record(
            contract,
            phase="formal_main",
            utilization="0.4",
            replicate_index=37,
            lambda_E="0.85",
            rho_E="2",
        ),
    )
    return {
        "pilot_taskset_seed": pilot["taskset_seed"],
        "pilot_taskset_id": pilot["taskset_id"],
        "pilot_random_source_seed": pilot["random_source_seed"],
        "pilot_source_id": pilot["source_id"],
        "pilot_case_id": pilot["case_id"],
        "formal_taskset_seed": formal["taskset_seed"],
        "formal_taskset_id": formal["taskset_id"],
    }


def contract_projection(contract):
    fail_closed = contract["fail_closed"]
    collision_names = (
        "duplicate_canonical_key",
        "different_canonical_keys_same_seed",
        "different_canonical_keys_same_id",
    )
    projection = {
        "canonicalization": contract["canonicalization"],
        "phase_algorithms": contract["phase_algorithms"],
        "phase_counts": contract["phase_counts"],
        "key_schemas": key_schemas(contract),
        "phase_taskset_pool": contract["reuse_dimensions"]["phase_taskset_pool"],
        "seed_derivation": contract["seed_derivation"],
        "id_derivation": contract["id_derivation"],
        "collision_policies": {name: fail_closed[name] for name in collision_names},
        "source_contract": contract["source_contract"],
    }
    return [f"{name}={compact_json(value)}" for name, value in projection.items()]


def validate_compatibility(contract, markdown_path=MARKDOWN_PATH):
    _require(
        contract["frozen_document_sha256"] == FROZEN_DOC_SHA256,
        "frozen document SHA compatibility drift",
        CompatibilityError,
    )
    _require(
        contract["system_template_sha256"] == SYSTEM_TEMPLATE_SHA256,
        "system template SHA compatibility drift",
        CompatibilityError,
    )
    _require(
        contract["phase_algorithms"] == PHASE_ALGORITHM_ANCHORS,
        "phase algorithm compatibility drift",
        CompatibilityError,
    )
    actual_counts = {
        phase: config["expected"] for phase, config in contract["phase_counts"].items()
    }
    _require(
        actual_counts == PHASE_COUNT_ANCHORS,
        "phase count compatibility drift",
        CompatibilityError,
    )
    try:
        actual_vectors = compatibility_vectors(contract)
    except ContractError as exc:
        raise CompatibilityError(f"golden vector derivation failed: {exc}") from exc
    _require(
        actual_vectors == GOLDEN_VECTORS,
        "golden vector compatibility drift",
        CompatibilityError,
    )
    markdown = Path(markdown_path).read_text(encoding="utf-8")
    for line in contract_projection(contract):
        _require(
            f"\n{line}\n" in markdown,
            "Markdown projection compatibility drift",
            CompatibilityError,
        )
    return contract


def load_contract(path=RESOLUTION_PATH, require_compatibility=True):
    contract = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_contract_structure(contract)
    if require_compatibility:
        validate_compatibility(contract)
    return contract


RESOLUTION = load_contract()


def sample_record(algorithm="ASAP-BLOCK", output_root="/tmp/b4-pe-a"):
    record = semantic_record(RESOLUTION, algorithm=algorithm)
    record["output_root"] = output_root
    return record


def identity_from_record(record, contract=None):
    contract = RESOLUTION if contract is None else contract
    bundle = identity_bundle(contract, record)
    return bundle["taskset_id"], bundle["source_id"], bundle["case_id"]


def check_registry(contract, entries, key_name, value_kind):
    policies = contract["fail_closed"]
    _require(policies["duplicate_canonical_key"] in {"error", "fail_closed"}, "duplicate policy")
    _require(
        policies["different_canonical_keys_same_seed"] in {"error", "fail_closed"},
        "seed collision policy",
    )
    _require(
        policies["different_canonical_keys_same_id"] in {"error", "fail_closed"},
        "ID collision policy",
    )
    seen_keys = set()
    seen_values = {}
    for semantic_key, derived_value in entries:
        canonical = canonical_json(contract, key_name, semantic_key)
        if canonical in seen_keys:
            raise DuplicateIdentityError("duplicate canonical key")
        if derived_value in seen_values and seen_values[derived_value] != canonical:
            if value_kind == "seed":
                raise SeedCollisionError("seed collision between different canonical keys")
            raise IDCollisionError("ID collision between different canonical keys")
        seen_keys.add(canonical)
        seen_values[derived_value] = canonical


class ProtocolResolutionTests(unittest.TestCase):
    def _mutated_contract(self, mutate):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        copied = Path(temp_dir.name) / "protocol_resolution_v1.json"
        shutil.copy2(RESOLUTION_PATH, copied)
        changed = json.loads(copied.read_text(encoding="utf-8"))
        mutate(changed)
        copied.write_text(
            json.dumps(changed, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return copied, changed

    def _assert_structural_invalid(self, mutate, message):
        copied, _ = self._mutated_contract(mutate)
        with self.assertRaisesRegex(ContractError, message):
            load_contract(copied, require_compatibility=False)

    def _assert_compatibility_drift(self, mutate, message):
        copied, changed = self._mutated_contract(mutate)
        validate_contract_structure(changed)
        baseline = {
            "contract_fingerprint": hashlib.sha256(
                compact_json(RESOLUTION).encode("utf-8")
            ).hexdigest(),
            "projection": contract_projection(RESOLUTION),
            "vectors": compatibility_vectors(RESOLUTION),
        }
        try:
            changed_vectors = compatibility_vectors(changed)
        except ContractError as exc:
            changed_vectors = {"derivation_error": str(exc)}
        changed_fingerprint = {
            "contract_fingerprint": hashlib.sha256(
                compact_json(changed).encode("utf-8")
            ).hexdigest(),
            "projection": contract_projection(changed),
            "vectors": changed_vectors,
        }
        self.assertNotEqual(baseline, changed_fingerprint)
        with self.assertRaisesRegex(CompatibilityError, message):
            load_contract(copied, require_compatibility=True)

    def test_frozen_document_sha(self):
        self.assertEqual(file_sha256(FROZEN_DOC_PATH), FROZEN_DOC_SHA256)

    def test_authorized_document_identity_migration_is_exact(self):
        self.assertEqual(
            RESOLUTION["identity_migrations"],
            [EXPECTED_DOCUMENT_IDENTITY_MIGRATION],
        )

    def test_system_template_sha(self):
        self.assertEqual(file_sha256(SYSTEM_TEMPLATE_PATH), SYSTEM_TEMPLATE_SHA256)

    def test_phase_algorithm_anchors(self):
        self.assertEqual(RESOLUTION["phase_algorithms"], PHASE_ALGORITHM_ANCHORS)

    def test_phase_count_anchors_and_products(self):
        actual = {
            phase: config["expected"]
            for phase, config in RESOLUTION["phase_counts"].items()
        }
        self.assertEqual(actual, PHASE_COUNT_ANCHORS)
        for phase, config in RESOLUTION["phase_counts"].items():
            self.assertEqual(formula_product(config), config["expected"], phase)

    def test_contract_structure_is_valid(self):
        self.assertIs(validate_contract_structure(RESOLUTION), RESOLUTION)

    def test_contract_compatibility_is_valid(self):
        self.assertIs(validate_compatibility(RESOLUTION), RESOLUTION)

    def test_reference_derivation_uses_discovered_json_kinds(self):
        for seed_kind in RESOLUTION["seed_derivation"]:
            key_name = f"{seed_kind}_key"
            self.assertIn(key_name, key_schemas(RESOLUTION))
        for identity_kind in RESOLUTION["id_derivation"]:
            key_name = f"{identity_kind}_key"
            self.assertIn(key_name, key_schemas(RESOLUTION))

    def test_golden_vectors_are_compatibility_anchors(self):
        self.assertEqual(compatibility_vectors(RESOLUTION), GOLDEN_VECTORS)

    def test_formal_and_negative_reuse_key_seed_id(self):
        formal_record = semantic_record(
            RESOLUTION, phase="formal_main", utilization="0.4", replicate_index=37
        )
        negative_record = semantic_record(
            RESOLUTION, phase="negative_control", utilization="0.4", replicate_index=37
        )
        formal_key = key_from_record(RESOLUTION, "taskset_key", formal_record)
        negative_key = key_from_record(RESOLUTION, "taskset_key", negative_record)
        self.assertEqual(formal_key, negative_key)
        self.assertEqual(formal_key["taskset_pool"], "formal")
        self.assertEqual(derive_seed(RESOLUTION, "taskset", formal_key), 8216105298971168251)
        self.assertEqual(
            derive_seed(RESOLUTION, "taskset", formal_key),
            derive_seed(RESOLUTION, "taskset", negative_key),
        )
        self.assertEqual(
            derive_id(RESOLUTION, "taskset", formal_key),
            derive_id(RESOLUTION, "taskset", negative_key),
        )

    def test_pilot_uses_independent_taskset_pool(self):
        self.assertNotEqual(
            taskset_key_for_phase("pilot", "0.4", 1),
            taskset_key_for_phase("formal_main", "0.4", 1),
        )

    def test_float_rejected_for_all_decimal_dimensions(self):
        ts_id = identity_id("taskset", taskset_key())
        src_id = identity_id("source", source_key(ts_id))
        calls = (
            lambda: taskset_key(utilization=0.2),
            lambda: source_key(ts_id, lambda_E=0.5),
            lambda: case_key(ts_id, src_id, rho_E=1.0),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaisesRegex(ContractError, "binary64 is forbidden"):
                    call()

    def test_int_and_bool_rejected_for_decimal_dimensions(self):
        ts_id = identity_id("taskset", taskset_key())
        src_id = identity_id("source", source_key(ts_id))
        for value in (2, True, False):
            with self.subTest(value=value):
                for call in (
                    lambda value=value: taskset_key(utilization=value),
                    lambda value=value: source_key(ts_id, lambda_E=value),
                    lambda value=value: case_key(ts_id, src_id, rho_E=value),
                ):
                    with self.assertRaises(ContractError):
                        call()

    def test_integer_dimensions_require_real_int_not_bool(self):
        with self.assertRaisesRegex(ContractError, "bool is forbidden"):
            taskset_key(replicate_index=True)
        ts_id = identity_id("taskset", taskset_key())
        key = source_key(ts_id)
        key["horizon_ms"] = False
        with self.assertRaisesRegex(ContractError, "bool is forbidden"):
            canonical_json(RESOLUTION, "source_key", key)

    def test_integer_dimensions_reject_float_and_string(self):
        for value in (1.0, "1"):
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    taskset_key(replicate_index=value)
        ts_id = identity_id("taskset", taskset_key())
        for value in (30000.0, "30000"):
            key = source_key(ts_id)
            key["horizon_ms"] = value
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    canonical_json(RESOLUTION, "source_key", key)

    def test_decimal_lexemes_remain_distinct(self):
        self.assertNotEqual(
            identity_id("taskset", taskset_key(utilization="0.2")),
            identity_id("taskset", taskset_key(utilization="0.20")),
        )

    def test_canonical_key_input_order_is_stable(self):
        key = taskset_key()
        self.assertEqual(
            canonical_json(RESOLUTION, "taskset_key", key),
            canonical_json(
                RESOLUTION, "taskset_key", dict(reversed(list(key.items())))
            ),
        )

    def _assert_unknown_fields(self, key_name, legal_key, injected_fields):
        for field, value in injected_fields:
            changed = dict(legal_key)
            changed[field] = value
            with self.subTest(key_name=key_name, field=field):
                with self.assertRaisesRegex(ContractError, "identity fields mismatch"):
                    canonical_json(RESOLUTION, key_name, changed)

    def test_taskset_unknown_fields_table(self):
        self._assert_unknown_fields(
            "taskset_key",
            taskset_key(),
            (
                ("unexpected_dimension", "x"),
                ("absolute_path", "/tmp/taskset.json"),
                ("timestamp", "now"),
                ("pid", 123),
                ("execution_order", 1),
            ),
        )

    def test_source_unknown_fields_table(self):
        ts_id = identity_id("taskset", taskset_key())
        self._assert_unknown_fields(
            "source_key",
            source_key(ts_id),
            (
                ("unexpected_dimension", "x"),
                ("trace_path", "/tmp/input.csv"),
                ("output_root", "/tmp/output"),
                ("timestamp", "now"),
                ("pid", 123),
                ("execution_order", 1),
            ),
        )

    def test_case_unknown_fields_table(self):
        ts_id = identity_id("taskset", taskset_key())
        src_id = identity_id("source", source_key(ts_id))
        self._assert_unknown_fields(
            "case_key",
            case_key(ts_id, src_id),
            (
                ("unexpected_dimension", "x"),
                ("output_root", "/tmp/output"),
                ("trace_path", "/tmp/input.csv"),
                ("timestamp", "now"),
                ("pid", 123),
                ("execution_order", 1),
                ("algorithm_order", 1),
            ),
        )

    def test_deterministic_source_seed_is_null(self):
        ts_id = identity_id("taskset", taskset_key())
        self.assertIsNone(derive_source_seed(source_key(ts_id)))

    def test_random_source_seed_is_json_driven(self):
        ts_id = identity_id("taskset", taskset_key())
        key = source_key(ts_id)
        self.assertEqual(
            derive_source_seed(key, random_source=True),
            GOLDEN_VECTORS["pilot_random_source_seed"],
        )

    def test_algorithm_changes_only_case_identity(self):
        first = identity_from_record(sample_record("ASAP-BLOCK"))
        second = identity_from_record(sample_record("ASAP-NONBLOCK"))
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1], second[1])
        self.assertNotEqual(first[2], second[2])

    def test_output_root_does_not_change_identity(self):
        self.assertEqual(
            identity_from_record(sample_record(output_root="/tmp/a")),
            identity_from_record(sample_record(output_root="/var/tmp/b")),
        )

    def test_algorithm_order_does_not_change_case_set(self):
        algorithms = RESOLUTION["phase_algorithms"]["pilot"]
        forward = {identity_from_record(sample_record(name))[2] for name in algorithms}
        reverse = {
            identity_from_record(sample_record(name))[2]
            for name in reversed(algorithms)
        }
        self.assertEqual(forward, reverse)

    def test_phase_changes_only_case_identity(self):
        ts_key = taskset_key_for_phase("formal_main", "0.4", 37)
        ts_id = identity_id("taskset", ts_key)
        src_key = source_key(ts_id, "0.85")
        src_id = identity_id("source", src_key)
        formal = case_key(ts_id, src_id, "ASAP-BLOCK", "formal_main", "1")
        negative = case_key(ts_id, src_id, "ASAP-BLOCK", "negative_control", "1")
        self.assertEqual(identity_id("taskset", ts_key), ts_id)
        self.assertEqual(identity_id("source", src_key), src_id)
        self.assertNotEqual(identity_id("case", formal), identity_id("case", negative))

    def test_ids_use_contract_full_digest_lengths(self):
        bundle = identity_bundle(RESOLUTION, semantic_record(RESOLUTION))
        for kind in RESOLUTION["id_derivation"]:
            identity = bundle[f"{kind}_id"]
            rule = RESOLUTION["id_derivation"][kind]
            self.assertTrue(identity.startswith(rule["id_prefix"]))
            digest = identity[len(rule["id_prefix"]) :]
            self.assertEqual(len(digest), rule["digest_hex_length"])
            self.assertRegex(digest, r"^[0-9a-f]+$")

    def test_duplicate_same_key_has_distinct_error(self):
        key = taskset_key()
        with self.assertRaisesRegex(DuplicateIdentityError, "duplicate canonical key"):
            check_registry(
                RESOLUTION, [(key, 1), (dict(key), 1)], "taskset_key", "seed"
            )

    def test_seed_collision_uses_different_keys(self):
        first = taskset_key(replicate_index=1)
        second = taskset_key(replicate_index=2)
        with self.assertRaisesRegex(SeedCollisionError, "seed collision"):
            check_registry(
                RESOLUTION, [(first, 7), (second, 7)], "taskset_key", "seed"
            )

    def test_id_collision_uses_different_keys(self):
        first = taskset_key(replicate_index=1)
        second = taskset_key(replicate_index=2)
        with self.assertRaisesRegex(IDCollisionError, "ID collision"):
            check_registry(
                RESOLUTION, [(first, "same"), (second, "same")], "taskset_key", "id"
            )

    def test_unknown_algorithm_fails_closed(self):
        ts_id = identity_id("taskset", taskset_key())
        src_id = identity_id("source", source_key(ts_id))
        with self.assertRaisesRegex(ContractError, "unknown or disallowed"):
            case_key(ts_id, src_id, algorithm="UNKNOWN")

    def test_markdown_is_dynamic_json_projection(self):
        markdown = MARKDOWN_PATH.read_text(encoding="utf-8")
        for line in contract_projection(RESOLUTION):
            with self.subTest(section=line.split("=", 1)[0]):
                self.assertIn(f"\n{line}\n", markdown)

    def test_structure_rejects_digest_slice_out_of_bounds(self):
        self._assert_structural_invalid(
            lambda data: data["seed_derivation"]["source"]["digest_slice"].update(
                start_byte=31, length_bytes=2
            ),
            "digest slice out of range",
        )

    def test_structure_rejects_missing_seed_field(self):
        self._assert_structural_invalid(
            lambda data: data["seed_derivation"]["taskset"].pop("hash"),
            "seed field missing",
        )

    def test_structure_rejects_non_null_deterministic_source_seed(self):
        self._assert_structural_invalid(
            lambda data: data["seed_derivation"]["source"].__setitem__(
                "deterministic_source_seed", 0
            ),
            "deterministic source seed",
        )

    def test_structure_rejects_duplicate_id_prefix(self):
        self._assert_structural_invalid(
            lambda data: data["id_derivation"]["source"].__setitem__(
                "id_prefix", data["id_derivation"]["taskset"]["id_prefix"]
            ),
            "duplicate ID prefix",
        )

    def test_structure_rejects_duplicate_identity_domain(self):
        self._assert_structural_invalid(
            lambda data: data["id_derivation"]["source"].__setitem__(
                "domain", data["id_derivation"]["taskset"]["domain"]
            ),
            "duplicate ID domain",
        )

    def test_structure_rejects_count_product_mismatch(self):
        self._assert_structural_invalid(
            lambda data: data["phase_counts"]["pilot"]["formula"][0].__setitem__(
                "factor", data["phase_counts"]["pilot"]["formula"][0]["factor"] + 1
            ),
            "phase count mismatch",
        )

    def test_structure_rejects_duplicate_key_field(self):
        self._assert_structural_invalid(
            lambda data: data["reuse_dimensions"]["taskset_key"].append(
                data["reuse_dimensions"]["taskset_key"][0]
            ),
            "contains duplicate fields",
        )

    def test_drift_taskset_seed_domain_changes_vector(self):
        self._assert_compatibility_drift(
            lambda data: data["seed_derivation"]["taskset"].__setitem__(
                "domain", "B4-PE/DRIFTED-SEED/v2\n"
            ),
            "golden vector compatibility drift",
        )

    def test_drift_source_seed_slice_changes_vector(self):
        self._assert_compatibility_drift(
            lambda data: data["seed_derivation"]["source"]["digest_slice"].__setitem__(
                "start_byte", 1
            ),
            "golden vector compatibility drift",
        )

    def test_drift_byte_order_changes_vector(self):
        self._assert_compatibility_drift(
            lambda data: data["seed_derivation"]["taskset"].__setitem__(
                "byte_order", "little"
            ),
            "golden vector compatibility drift",
        )

    def test_drift_mask_changes_vector(self):
        def mutate(data):
            rule = data["seed_derivation"]["taskset"]
            rule["result_bits"] = 31
            rule["mask_hex"] = "0x7fffffff"

        self._assert_compatibility_drift(mutate, "golden vector compatibility drift")

    def test_drift_taskset_id_prefix_changes_vector(self):
        self._assert_compatibility_drift(
            lambda data: data["id_derivation"]["taskset"].__setitem__(
                "id_prefix", "task-"
            ),
            "golden vector compatibility drift",
        )

    def test_drift_case_id_domain_changes_vector(self):
        self._assert_compatibility_drift(
            lambda data: data["id_derivation"]["case"].__setitem__(
                "domain", "B4-PE/DRIFTED-CASE/v2\n"
            ),
            "golden vector compatibility drift",
        )

    def test_drift_taskset_key_schema_changes_vector(self):
        def mutate(data):
            fields = data["reuse_dimensions"]["taskset_key"]
            fields[fields.index("replicate_index")] = "phase"

        self._assert_compatibility_drift(mutate, "golden vector compatibility drift")

    def test_drift_canonicalization_changes_markdown_projection(self):
        self._assert_compatibility_drift(
            lambda data: data["canonicalization"].__setitem__("ensure_ascii", True),
            "Markdown projection compatibility drift",
        )

    def test_drift_pilot_algorithm_uses_external_anchor(self):
        self._assert_compatibility_drift(
            lambda data: data["phase_algorithms"]["pilot"].__setitem__(0, "DRIFTED"),
            "phase algorithm compatibility drift",
        )

    def test_drift_frozen_sha_uses_external_anchor(self):
        self._assert_compatibility_drift(
            lambda data: data.__setitem__("frozen_document_sha256", "0" * 64),
            "frozen document SHA compatibility drift",
        )

    def test_no_removed_second_truth_identifiers(self):
        source = Path(__file__).read_text(encoding="utf-8")
        for removed_name in (
            "EXPECTED_" + "KEY_SHAPES",
            "IDENTITY_" + "KIND_TOKENS",
        ):
            self.assertNotIn(removed_name, source)
        for copied_domain in (
            "B4-PE/" + "TASKSET-SEED/v1" + chr(92) + "n",
            "B4-PE/" + "SOURCE-SEED/v1" + chr(92) + "n",
        ):
            self.assertNotIn(copied_domain, source)


if __name__ == "__main__":
    unittest.main()
