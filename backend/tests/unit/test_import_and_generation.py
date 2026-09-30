"""
Import of a (small) Databricks view -> proposal -> apply -> generate -> shadow compare.

The S/4 output below was produced with the view's commented-out routing line
active, like the real extracts, so the importer must detect the drift and
offer the variant as an alternative.
"""
import pandas as pd

from app.generation import service as generation
from app.importers import service as importer
from app.importers.sql_translate import load_blocks
from app.mapping import repository
from app.mapping.model import EccSource, FieldSpec, KeySpec, MappingDoc
from app.proposals import service as proposals
from tests.conftest import put_snapshot

VIEW = """
%sql
CREATE OR REPLACE VIEW con_dev.s4_mdm.mm_S_MARC
AS
WITH base AS (
    SELECT distinct * from (
        SELECT marc.matnr, marc.werks,
        case when MARC.BESKZ = 'E' then 'US30'
        -- when MARC.BESKZ = 'F' then 'US30'
        else marc.werks end as derived_werks
        FROM con_dev.silver_sap.marc_dap MARC
        INNER JOIN con_dev.silver_sap.mara_dap MARA ON MARC.MATNR = MARA.MATNR AND MARA.LVORM IS NULL
        where MARC.LVORM IS NULL and MARC.WERKS = '1021'
     UNION ALL
        SELECT marc.matnr, marc.werks,
        case when MARC.BESKZ in ('E','F') then 'US27' else marc.werks end as derived_werks
        FROM con_dev.silver_sap.marc_dap MARC
        INNER JOIN con_dev.silver_sap.mara_dap MARA ON MARC.MATNR = MARA.MATNR AND MARA.LVORM IS NULL
        where MARC.LVORM IS NULL and MARC.WERKS = '1021')
),
mapped AS (
    SELECT distinct
    `MARC`.`MATNR` as PRODUCT,
    base.derived_werks as werks,
    case when MARC.BESKZ = 'E' and base.derived_werks = 'US30' then 'F' else `MARC`.`BESKZ` end as BESKZ,
    `MARC`.`DISMM`,
    case when MARA.MTART = 'FERT' then (`MARC`.`BSTMI` * MARM_EA.UMREN) else `MARC`.`BSTMI` end as BSTMI,
    'CG01' as KOKRS
    FROM base
  left join con_dev.silver_sap.marc_dap MARC on MARC.matnr = base.matnr
  LEFT JOIN con_dev.silver_sap.marm_dap MARM_EA ON MARC.MATNR = MARM_EA.MATNR AND MARM_EA.MEINH = 'EA'
  left join con_dev.silver_sap.mara_dap MARA on MARC.MATNR=MARA.MATNR
  WHERE MARA.LVORM is null and MARC.LVORM is null and MARC.WERKS = '1021' and base.DERIVED_WERKS <> '1021'
)
SELECT * FROM mapped
"""


def _doc() -> MappingDoc:
    def f(fid, col, dtype="Text"):
        return FieldSpec(id=fid, s4_label=fid, s4_column=col, data_type=dtype, ecc_source=EccSource(table="MARC", field=col))
    return MappingDoc(object="MARC", s4_sheet="S_MARC", key=KeySpec(fields=["product_number", "plant"]),
                      fields=[f("product_number", "PRODUCT"), f("plant", "werks"), f("procurement_type", "BESKZ"),
                              f("mrp_type", "DISMM"), f("min_lot", "BSTMI", "Number"), f("co_area", "KOKRS")])


def _data():
    ecc_rows, s4_rows = [], []
    mtart = {}
    for i in range(40):
        mat = str(500000 + i)
        beskz = ["E", "F", "X"][i % 3]
        mtart[mat] = "FERT" if i % 4 == 0 else "ROH"
        ecc_rows.append({"Material": mat, "Plant": "1021", "Procurement type": beskz, "MRP Type": "PD",
                         "Minimum Lot Size": str(i), "DF at plant level": ""})
        # the S/4 output was built with the commented-out line active: F also goes to US30
        targets = (["US30"] if beskz in ("E", "F") else []) + (["US27"] if beskz in ("E", "F") else [])
        for t in targets:
            s4_rows.append({"PRODUCT": mat.zfill(18), "werks": t,
                            "BESKZ": "F" if (beskz == "E" and t == "US30") else beskz, "DISMM": "PD",
                            "BSTMI": f"{i * 6 if mtart[mat] == 'FERT' else i}.000", "KOKRS": "CG01"})
    mara = pd.DataFrame({"Material": list(mtart), "Material Type": list(mtart.values()),
                         "DF at client level": [""] * len(mtart)})
    marm = pd.DataFrame({"Material": list(mtart), "Alternative Unit of Measure": ["EA"] * len(mtart),
                         "Denominator": ["6"] * len(mtart), "Numerator": ["1"] * len(mtart)})
    return pd.DataFrame(ecc_rows, dtype=str), pd.DataFrame(s4_rows, dtype=str), mara, marm


def test_translator_finds_blocks_and_commented_lines(tmp_path):
    (tmp_path / "plant_1021.sql").write_text(VIEW, encoding="utf-8")
    blocks = load_blocks(tmp_path)
    assert [(b.object, b.plants) for b in blocks] == [("MARC", ["1021"])]
    assert blocks[0].restored_lines == ["when MARC.BESKZ = 'F' then 'US30'"]


def test_import_detects_drift_and_the_variant_reproduces_the_output(env, monkeypatch):
    sql_dir = env / "sql"
    sql_dir.mkdir()
    (sql_dir / "plant_1021.sql").write_text(VIEW, encoding="utf-8")
    monkeypatch.setattr(importer, "SQL_DIR", sql_dir)
    ecc, s4, mara, marm = _data()
    repository.save(_doc(), updated_by="test", bump=False)
    put_snapshot("MARC", "ECC", ecc)
    put_snapshot("MARC", "S4", s4)
    put_snapshot("REF", "MARA", mara)
    put_snapshot("REF", "MARM", marm)

    rep = importer.import_object("MARC")
    assert rep["untranslated"] == {}
    assert rep["drift"] and "commented-out lines restored" in rep["drift"]
    assert rep["variant"]["rows"]["theirs_reproduced_share"] == 1.0
    assert rep["main"]["rows"]["theirs_reproduced_share"] < 0.8

    p = proposals.get(rep["proposal_id"])
    routes = [o for o in p["ops"] if o.get("alternative_group")]
    assert len(routes) == 2
    variant = max(routes, key=lambda o: o["confidence"])
    decisions = [{"op_id": o["op_id"], "decision": "rejected" if (o.get("alternative_group") and o is not variant)
                  else "accepted"} for o in p["ops"]]
    proposals.decide(p["proposal_id"], decisions, by="t")
    applied = proposals.apply(p["proposal_id"], by="t")
    assert not [o for o in applied["ops"] if o["status"] in ("failed", "stale")]

    doc = repository.load("MARC")
    assert doc.field("min_lot").provenance.source == "databricks"
    out = generation.run("MARC")
    shadow = out["shadow"]
    assert shadow["rows"]["theirs_reproduced_share"] == 1.0
    assert shadow["rows"]["ours_confirmed_share"] == 1.0
    assert shadow["summary"]["fields_mismatching"] == 0, [f for f in shadow["fields"] if f["status"] != "match"]


def test_accepting_both_alternatives_is_refused(env, monkeypatch):
    sql_dir = env / "sql"
    sql_dir.mkdir()
    (sql_dir / "plant_1021.sql").write_text(VIEW, encoding="utf-8")
    monkeypatch.setattr(importer, "SQL_DIR", sql_dir)
    ecc, s4, mara, marm = _data()
    repository.save(_doc(), updated_by="test", bump=False)
    for obj, side, df in (("MARC", "ECC", ecc), ("MARC", "S4", s4), ("REF", "MARA", mara), ("REF", "MARM", marm)):
        put_snapshot(obj, side, df)
    rep = importer.import_object("MARC")
    p = proposals.get(rep["proposal_id"])
    proposals.decide(p["proposal_id"], [{"op_id": o["op_id"], "decision": "accepted"} for o in p["ops"]])
    try:
        proposals.apply(p["proposal_id"])
    except proposals.ProposalError as exc:
        assert "one alternative" in str(exc)
    else:
        raise AssertionError("applying two alternatives should be refused")


def _import_and_apply(env, monkeypatch):
    sql_dir = env / "sql"
    sql_dir.mkdir(exist_ok=True)
    (sql_dir / "plant_1021.sql").write_text(VIEW, encoding="utf-8")
    monkeypatch.setattr(importer, "SQL_DIR", sql_dir)
    ecc, s4, mara, marm = _data()
    repository.save(_doc(), updated_by="test", bump=False)
    for obj, side, df in (("MARC", "ECC", ecc), ("MARC", "S4", s4), ("REF", "MARA", mara), ("REF", "MARM", marm)):
        put_snapshot(obj, side, df)
    rep = importer.import_object("MARC")
    p = proposals.get(rep["proposal_id"])
    best = max((o for o in p["ops"] if o.get("alternative_group")), key=lambda o: o["confidence"])
    proposals.decide(p["proposal_id"], [{"op_id": o["op_id"], "decision": "rejected" if o.get("alternative_group")
                                         and o is not best else "accepted"} for o in p["ops"]], by="t")
    proposals.apply(p["proposal_id"], by="t")
    return sql_dir


def test_reimport_proposes_only_what_changed(env, monkeypatch):
    sql_dir = _import_and_apply(env, monkeypatch)
    v_before = repository.load("MARC").mapping_version
    rep = importer.import_object("MARC")                        # same SQL again
    p = proposals.get(rep["proposal_id"])
    assert rep["unchanged_ops"] > 0
    assert {o["op"] for o in p["ops"] if not o.get("alternative_group")} == set(), \
        [(o["op"], o.get("field_id")) for o in p["ops"]]        # nothing but the routing alternatives
    assert all(o.get("preview", {}).get("target") for o in p["ops"])
    # the alternative that equals the applied routing says why it is proposed again
    assert any("no change to the current rule" in str(o["preview"]["after"]) for o in p["ops"] if o.get("alternative_group"))
    assert any(d["by"] == "import" for d in p["dropped_ops"])

    # change one constant: only that rule is proposed, and it applies cleanly (no "already exists")
    (sql_dir / "plant_1021.sql").write_text(VIEW.replace("'CG01' as KOKRS", "'CG21' as KOKRS"), encoding="utf-8")
    rep = importer.import_object("MARC")
    p = proposals.get(rep["proposal_id"])
    rules = [o for o in p["ops"] if not o.get("alternative_group")]
    assert [(o["op"], o["field_id"]) for o in rules] == [("set_transform", "co_area")]
    assert "co_area" in rules[0]["preview"]["target"]
    best = max((o for o in p["ops"] if o.get("alternative_group")), key=lambda o: o["confidence"])
    proposals.decide(p["proposal_id"], [{"op_id": o["op_id"], "decision": "rejected" if o.get("alternative_group")
                                         and o is not best else "accepted"} for o in p["ops"]], by="t")
    applied = proposals.apply(p["proposal_id"], by="t")
    assert not [o for o in applied["ops"] if o["status"] in ("failed", "stale")]
    assert repository.load("MARC").mapping_version == v_before + 1


def test_conflict_rules():
    wb_hard = {"logic_type": "Hardcoded", "logic": 'Hardcoded as "CG01"'}
    assert importer.conflict(wb_hard, "Hardcoded", {"CG01"}) is None
    # per-plant constants where one plant gets another value (INC-025)
    cf = importer.conflict(wb_hard, "Hardcoded", {"1000", "1025", "CG01", "CG25"})
    assert cf and "CG25" in cf["detail"]
    # '-' in the workbook is a placeholder, not a value (INC-003)
    wb_der = {"logic_type": "Derived", "logic": "If plant is '1025' then 'PL01' else '-'"}
    assert importer.conflict(wb_der, "Derived", {"1025", "PL01"}) is None


def test_report_records_input_versions_and_filter_samples(env, monkeypatch):
    _import_and_apply(env, monkeypatch)
    out = generation.run("MARC")
    assert set(out["snapshots"]) >= {"ECC", "S4", "REF/MARA", "REF/MARM"}
    assert out["snapshots"]["REF/MARM"]                      # the MARM version the prices / lot sizes used
    for flt in out["filters"]:
        if flt.get("rows_removed"):
            assert flt["samples"] and set(flt["samples"][0]) <= {"product_number", "plant"}
