"""Manifest self-hash, entries and the staged lifecycle (synthetic fixtures only)."""

import pytest
from evals.contracts.canonical import ContractError, encode_json_object, encode_jsonl, sha256_hex
from evals.contracts.constants import Label
from evals.contracts.manifest import (
    Manifest,
    ManifestEntry,
    ManifestVersions,
    Role,
    Stage,
    build_manifest,
    compute_manifest_sha256,
    decode_manifest,
    encode_manifest,
    entry_for,
    stage_roles,
    validate_chain,
    validate_transition,
    verify_entry,
)
from evals.contracts.models import (
    AuthoringAttestation,
    AuthoringPurpose,
    ConstraintStatus,
    DraftedBy,
    ExtractionStatus,
    ExtractionTarget,
    GoldField,
    LabelChange,
    LabelChangeKind,
    Partition,
    PartitionAssignment,
    ProvenanceEntry,
    QueryExtraction,
    ReviewReason,
    Round,
    SourceHit,
    SourceSystem,
)
from evals.contracts.review_sets import (
    build_adjudication_queue,
    build_consistency_sample,
    derive_final_judgements,
)
from golden_support import (
    HANDLE,
    T0,
    after_delay,
    eligibility,
    gold,
    judgement,
    pool_for,
    pool_report,
    provenance_bundle,
    query,
)
from pydantic import ValidationError

from ecommerce_search.filtering.policy import FilterOperator

PRODUCTS = ["SYN-LAP-0001", "SYN-LAP-0002", "SYN-LAP-0003", "SYN-LAP-0004", "SYN-LAP-0005"]
VERSIONS = ManifestVersions(
    rubric="rubric-1",
    pool_policy="pool-1",
    blind_order="blind-1",
    consistency_sample="cs-1",
    parser="qu-1",
    lexicon="1",
    filter_policy="fp-1",
    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    embedding_revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    reranker_model="cross-encoder/ms-marco-MiniLM-L6-v2",
    reranker_revision="233902d25c440f23af6f7d6e94d2946bac0bee0a",
)
PATHS = {role: f"v1/{role.value}.jsonl" for role in Role} | {
    Role.CONSISTENCY_SAMPLE: "v1/consistency_sample.json",
    Role.AUTHORING_ATTESTATION: "v1/authoring_attestation.json",
    Role.POOL_REPORT: "v1/pool_report.json",
    Role.PROVENANCE: "v1/provenance.json",
}


def _files(catalog) -> dict[Role, bytes]:
    q = query("gq-p01", "fixture laptop query")
    pool = pool_for("gq-p01", PRODUCTS, catalog)
    elig = [
        eligibility(
            "gq-p01", p, [(GoldField.CATEGORY, FilterOperator.EQ, ConstraintStatus.SATISFIED)]
        )
        for p in PRODUCTS
    ]
    primaries = [
        judgement("gq-p01", p, Label.RELEVANT if i < 3 else Label.BORDERLINE)
        for i, p in enumerate(PRODUCTS)
    ]
    primary_bytes = encode_jsonl(primaries)
    seal = sha256_hex(primary_bytes)
    sealed_at = after_delay(1)
    sample = build_consistency_sample(primaries, seal, sealed_at)
    queue = build_adjudication_queue(primaries, [q], elig)
    sampled = sample.items[0]
    repeats = [
        judgement(
            sampled.query_id,
            sampled.product_id,
            Label.RELEVANT,
            round_=Round.CONSISTENCY,
            at=after_delay(80),
        )
    ]
    adjudications = [
        judgement(
            "gq-p01",
            PRODUCTS[3],
            Label.NOT_RELEVANT,
            round_=Round.ADJUDICATION,
            at=after_delay(90),
            review_reasons=(ReviewReason.BORDERLINE,),
            changes_label=True,
            adjudication_reason="Violates the intent on review.",
        )
    ]
    changes = [
        LabelChange(
            query_id="gq-p01",
            product_id=PRODUCTS[3],
            kind=LabelChangeKind.ADJUDICATION,
            previous_label=Label.BORDERLINE,
            new_label=Label.NOT_RELEVANT,
            reason="Violates the intent on review.",
            changed_by=HANDLE,
            changed_at=after_delay(90),
            from_golden_version=1,
            to_golden_version=1,
        )
    ]
    provenance = [
        ProvenanceEntry(
            query_id="gq-p01",
            product_id=p,
            added_by="system_union",
            sources=(SourceHit(system=SourceSystem.V0_LEXICAL, rank=i + 1, score="1.5"),),
        )
        for i, p in enumerate(PRODUCTS)
    ]
    return {
        Role.QUERIES: encode_jsonl([q]),
        Role.GOLD_CONSTRAINTS: encode_jsonl([gold("gq-p01", GoldField.CATEGORY, "laptop")]),
        Role.PRODUCT_INTENTS: encode_jsonl([]),
        Role.QUERY_EXTRACTION: encode_jsonl(
            [
                QueryExtraction(
                    query_id="gq-p01",
                    target=ExtractionTarget.GOLD_CONSTRAINT,
                    field="category",
                    operator=FilterOperator.EQ,
                    gold_value="laptop",
                    status=ExtractionStatus.EXTRACTED,
                    parsed_value="laptop",
                    fp1_filterable=True,
                    fp1_applied=True,
                )
            ]
        ),
        Role.AUTHORING_ATTESTATION: encode_json_object(
            AuthoringAttestation(
                partition=Partition.PILOT_DEV,
                purpose=AuthoringPurpose.DEVELOPMENT_RUBRIC_CALIBRATION,
                drafted_by=DraftedBy.AI_ASSISTANT,
                confirmed_by=HANDLE,
                confirmed_at=T0,
                blind_to_parser_output=False,
                final_test_evidence=False,
                input_sha256="a" * 64,
                query_count=1,
            )
        ),
        Role.POOL: encode_jsonl(pool),
        Role.ELIGIBILITY: encode_jsonl(elig),
        Role.POOL_REPORT: encode_json_object(pool_report(pool)),
        Role.PRIMARY_JUDGEMENTS: primary_bytes,
        Role.CONSISTENCY_SAMPLE: encode_json_object(sample),
        Role.ADJUDICATION_QUEUE: encode_jsonl(queue),
        Role.CONSISTENCY_JUDGEMENTS: encode_jsonl(repeats),
        Role.ADJUDICATION_JUDGEMENTS: encode_jsonl(adjudications),
        Role.LABEL_CHANGES: encode_jsonl(changes),
        Role.PARTITIONS: encode_jsonl(
            [PartitionAssignment(query_id="gq-p01", partition=Partition.PILOT_DEV)]
        ),
        Role.FINAL_JUDGEMENTS: encode_jsonl(derive_final_judgements(primaries, adjudications)),
        Role.PROVENANCE: encode_json_object(provenance_bundle(provenance)),
    }


def _manifest(stage: Stage, files, previous: Manifest | None, **overrides) -> Manifest:
    index = list(Stage).index(stage)
    fields = dict(
        golden_version=1,
        stage=stage,
        dataset_id="synthetic-seed",
        dataset_version="1",
        dataset_checksum_sha256="2c1c5fa43eec5f7346df40c17e57c07f6594ab1833c5d9d4c862e6d2e8bbbbc8",
        versions=VERSIONS,
        entries=tuple(
            sorted(
                (entry_for(r, PATHS[r], files[r]) for r in stage_roles(stage)), key=lambda e: e.path
            )
        ),
        provenance_binding=sha256_hex(files[Role.PROVENANCE]) if index >= 1 else None,
        primary_sealed_at=after_delay(1) if index >= 2 else None,
        labels_frozen_at=after_delay(100) if index >= 3 else None,
        previous_manifest_sha256=previous.manifest_sha256 if previous else None,
    )
    fields.update(overrides)
    return build_manifest(**fields)


@pytest.fixture
def files(catalog):
    return _files(catalog)


@pytest.fixture
def chain(files):
    manifests: list[Manifest] = []
    for stage in Stage:
        manifests.append(_manifest(stage, files, manifests[-1] if manifests else None))
    return manifests


def test_full_legal_chain(chain, files):
    validate_chain(chain)
    final = chain[-1]
    assert final.stage is Stage.PROVENANCE_UNSEALED
    assert final.entry(Role.PROVENANCE).file_sha256 == final.provenance_binding
    for entry in final.entries:
        verify_entry(entry, files[entry.role])


def test_provenance_is_only_a_binding_before_unsealing(chain):
    for manifest in chain[:-1]:
        assert Role.PROVENANCE not in {e.role for e in manifest.entries}
    assert chain[0].provenance_binding is None
    assert len({m.provenance_binding for m in chain[1:]}) == 1


def test_self_hash_excludes_only_its_own_field(chain):
    manifest = chain[2]
    body = manifest.model_dump(mode="json")
    assert compute_manifest_sha256(body) == manifest.manifest_sha256
    assert (
        compute_manifest_sha256({**body, "manifest_sha256": "0" * 64}) == manifest.manifest_sha256
    )
    assert (
        compute_manifest_sha256({**body, "dataset_id": "tampered-dataset"})
        != manifest.manifest_sha256
    )
    with pytest.raises(ValidationError, match="does not match the manifest body"):
        Manifest.model_validate({**body, "dataset_id": "tampered-dataset"})
    assert manifest.primary_seal_sha256() == manifest.entry(Role.PRIMARY_JUDGEMENTS).file_sha256


def test_manifest_round_trips_canonically(chain):
    data = encode_manifest(chain[-1])
    assert decode_manifest(data) == chain[-1]
    assert encode_manifest(decode_manifest(data)) == data


def test_build_manifest_never_accepts_a_supplied_hash(files):
    with pytest.raises(ContractError, match="computed"):
        _manifest(Stage.QUERIES_FROZEN, files, None, manifest_sha256="0" * 64)


def test_stage_roles_are_exact(files, chain):
    with pytest.raises(ValidationError, match="requires exactly the roles"):
        _manifest(
            Stage.POOL_FROZEN,
            files,
            chain[0],
            entries=tuple(
                sorted(
                    [
                        *chain[1].entries,
                        entry_for(Role.PROVENANCE, PATHS[Role.PROVENANCE], files[Role.PROVENANCE]),
                    ],
                    key=lambda e: e.path,
                )
            ),
        )
    with pytest.raises(ValidationError, match="requires exactly the roles"):
        _manifest(Stage.POOL_FROZEN, files, chain[0], entries=chain[0].entries)


def test_binding_and_seal_time_presence_rules(files, chain):
    with pytest.raises(ValidationError, match="provenance_binding"):
        _manifest(Stage.POOL_FROZEN, files, chain[0], provenance_binding=None)
    with pytest.raises(ValidationError, match="provenance_binding"):
        _manifest(Stage.QUERIES_FROZEN, files, None, provenance_binding="1" * 64)
    with pytest.raises(ValidationError, match="primary_sealed_at"):
        _manifest(Stage.PRIMARY_SEALED, files, chain[1], primary_sealed_at=None)
    with pytest.raises(ValidationError, match="previous manifest"):
        _manifest(Stage.POOL_FROZEN, files, None)


def test_unsealed_provenance_must_match_its_binding(files, chain):
    with pytest.raises(ValidationError, match="does not match its binding"):
        _manifest(Stage.PROVENANCE_UNSEALED, files, chain[3], provenance_binding="1" * 64)


def test_illegal_transitions(files, chain):
    with pytest.raises(ContractError, match="illegal stage transition"):
        validate_transition(chain[0], chain[2])  # skip
    with pytest.raises(ContractError, match="illegal stage transition"):
        validate_transition(chain[2], chain[1])  # backwards
    with pytest.raises(ContractError, match="does not point"):
        validate_transition(chain[1], _manifest(Stage.PRIMARY_SEALED, files, chain[0]))
    changed_binding = _manifest(Stage.PRIMARY_SEALED, files, chain[1], provenance_binding="1" * 64)
    with pytest.raises(ContractError, match="provenance_binding changed"):
        validate_transition(chain[1], changed_binding)
    later_seal = _manifest(Stage.LABELS_FROZEN, files, chain[2], primary_sealed_at=after_delay(2))
    with pytest.raises(ContractError, match="primary_sealed_at changed"):
        validate_transition(chain[2], later_seal)
    other_versions = _manifest(
        Stage.POOL_FROZEN,
        files,
        chain[0],
        versions=VERSIONS.model_copy(update={"rubric": "rubric-2"}),
    )
    with pytest.raises(ContractError, match="versions changed"):
        validate_transition(chain[0], other_versions)
    with pytest.raises(ContractError, match="start at queries_frozen"):
        validate_chain(chain[1:])


def test_frozen_entries_cannot_change_later(files, chain):
    edited = dict(files)
    edited[Role.PRIMARY_JUDGEMENTS] = encode_jsonl(
        [judgement("gq-p01", p, Label.NOT_RELEVANT) for p in PRODUCTS]
    )
    resealed = _manifest(Stage.LABELS_FROZEN, edited, chain[2])
    with pytest.raises(ContractError, match="primary_judgements changed after it was frozen"):
        validate_transition(chain[2], resealed)


def test_entry_rules():
    good = dict(
        role=Role.POOL,
        kind="generated",
        file_sha256="a" * 64,
        records_sha256="a" * 64,
        record_count=1,
    )
    assert ManifestEntry(path="v1/pool.jsonl", **good)
    for bad in ("../pool.jsonl", "/abs/pool.jsonl", "C:/pool.jsonl", "v1\\pool.jsonl", "v1/../x"):
        with pytest.raises(ValidationError):
            ManifestEntry(path=bad, **good)
    with pytest.raises(ValidationError, match="is generated"):
        ManifestEntry(path="v1/pool.jsonl", **{**good, "kind": "recorded"})


def test_verify_entry_hash_and_regeneration_rules(files):
    pool_entry = entry_for(Role.POOL, PATHS[Role.POOL], files[Role.POOL])
    assert pool_entry.records_sha256 == pool_entry.file_sha256  # accepted files are canonical
    verify_entry(pool_entry, files[Role.POOL], regenerated=files[Role.POOL])
    with pytest.raises(ContractError, match="file_sha256"):
        verify_entry(pool_entry, files[Role.POOL].replace(b"SYN-LAP-0001", b"SYN-LAP-0009", 1))
    with pytest.raises(ContractError, match="byte-identical"):
        verify_entry(pool_entry, files[Role.POOL], regenerated=files[Role.POOL] + b" ")
    primary_entry = entry_for(
        Role.PRIMARY_JUDGEMENTS, PATHS[Role.PRIMARY_JUDGEMENTS], files[Role.PRIMARY_JUDGEMENTS]
    )
    verify_entry(primary_entry, files[Role.PRIMARY_JUDGEMENTS])  # recorded bytes with timestamps
    with pytest.raises(ContractError, match="never regenerated"):
        verify_entry(
            primary_entry,
            files[Role.PRIMARY_JUDGEMENTS],
            regenerated=files[Role.PRIMARY_JUDGEMENTS],
        )


def test_judgement_files_contain_only_their_round(files):
    with pytest.raises(ContractError, match="only consistency judgements"):
        entry_for(
            Role.CONSISTENCY_JUDGEMENTS,
            PATHS[Role.CONSISTENCY_JUDGEMENTS],
            files[Role.PRIMARY_JUDGEMENTS],
        )


def test_generated_files_contain_no_timestamps_or_handles(files):
    for role in (
        Role.POOL,
        Role.ELIGIBILITY,
        Role.CONSISTENCY_SAMPLE,
        Role.ADJUDICATION_QUEUE,
        Role.FINAL_JUDGEMENTS,
        Role.PROVENANCE,
    ):
        text = files[role].decode()
        assert HANDLE not in text and "annotated_at" not in text and "recorded_at" not in text
    # The consistency sample records the seal time it is bound to; that is its only timestamp.
    assert "primary_sealed_at" in files[Role.CONSISTENCY_SAMPLE].decode()
