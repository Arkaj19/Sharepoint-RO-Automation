import json

import pytest

from app.mapping import compat, repository
from app.mapping.model import MappingDoc
from scripts import migrate_mapping_to_yaml as mig
from tests.conftest import FIXTURES, synthetic_doc


def test_migration_reproduces_legacy_mapping_exactly(env):
    raw_all = json.loads((FIXTURES / "legacy_marc_mbew_mapping.json").read_text(encoding="utf-8"))
    for obj, raw in raw_all.items():
        doc = mig.build_doc(obj, raw)
        assert mig._compat_view(doc) == mig._legacy_view(raw["fields"])
        repository.save(doc, updated_by="test", bump=False)
        # the compat layer (old mapping_loader API) reads the same thing back from YAML
        legacy_keys = [f["s4_field"] for f in raw["fields"] if f["is_key_marker"]]
        assert compat.get_key_fields(obj)[0] == legacy_keys[0]
        assert [m.s4_field for m in compat.get_field_mappings(obj)] == [f["s4_field"] for f in raw["fields"]]
        assert compat.get_mandatory_fields(obj) == [f["s4_field"] for f in raw["fields"] if f["mandatory"]]
    assert compat.get_key_fields("MARC") == ["Product Number", "Plant"]
    assert compat.get_key_fields("MBEW") == ["Product Number", "Valuation Area"]


def test_save_load_roundtrip_history_and_hash(env):
    doc = synthetic_doc()
    saved = repository.save(doc, updated_by="test", bump=False)
    assert saved.mapping_version == 1
    assert not repository.is_hand_edited(saved)

    saved.fields[2].ecc_column = "MRP Type"
    v2 = repository.save(saved, updated_by="test")
    assert v2.mapping_version == 2
    assert (repository.history_dir("TEST") / "v0001.yaml").exists()
    assert repository.load_version("TEST", 1).fields[2].ecc_column is None
    assert [v["version"] for v in repository.list_versions("TEST")] == [1, 2]


def test_hand_edit_is_detected_and_comments_survive_a_save(env):
    repository.save(synthetic_doc(), updated_by="test", bump=False)
    p = repository.mapping_path("TEST")
    text = p.read_text(encoding="utf-8")
    text = text.replace("s4_label: MRP Type", "s4_label: MRP Type  # confirmed with the MRP team")
    text = text.replace("length: 13", "length: 15")
    p.write_text(text, encoding="utf-8")
    doc = repository.load("TEST")
    assert repository.is_hand_edited(doc)
    assert doc.field("safety_stock").length == 15

    doc.field("plant").ecc_column = "Plant"
    repository.save(doc, updated_by="test")
    after = p.read_text(encoding="utf-8")
    assert "# confirmed with the MRP team" in after
    assert not repository.is_hand_edited(repository.load("TEST"))


def test_model_rejects_bad_cross_references():
    doc = synthetic_doc()
    data = doc.model_dump(mode="json", by_alias=True)
    data["key"]["fields"] = ["nope"]
    with pytest.raises(ValueError):
        MappingDoc.model_validate(data)
    data = doc.model_dump(mode="json", by_alias=True)
    data["fields"][2]["transform"] = [{"op": "value_map", "map": "missing"}]
    with pytest.raises(ValueError):
        MappingDoc.model_validate(data)


def test_fanout_map_only_allowed_as_first_step_of_key_field():
    data = synthetic_doc().model_dump(mode="json", by_alias=True)
    data["value_maps"] = {"xw": {"entries": [{"from": "1021", "to": "US27"}, {"from": "1021", "to": "US30"}]}}
    data["fields"][2]["transform"] = [{"op": "value_map", "map": "xw"}]   # mrp_type is not a key field
    with pytest.raises(ValueError, match="fan-out"):
        MappingDoc.model_validate(data)
    data["fields"][2]["transform"] = []
    data["fields"][1]["transform"] = [{"op": "value_map", "map": "xw"}]
    assert MappingDoc.model_validate(data).fanout_fields() == {"plant": "xw"}
