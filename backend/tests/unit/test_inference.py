from app.inference import column_match, keys, value_maps


def test_alignment_infers_key_column_normalisation_and_fanout_crosswalk(synthetic):
    doc, ecc, s4 = synthetic
    al = keys.align(doc, ecc, s4)
    assert al.key_setup["product_number"]["ecc_column"] == "Material"
    assert al.key_setup["product_number"]["normalization"] == "strip_leading_zeros"
    assert al.key_setup["plant"]["ecc_column"] == "Plant"
    xw = value_maps.infer_crosswalk(al.doc, "plant", al.ecc, al.s4)
    accepted = {(e["from"], e["to"]) for e in xw["entries"] if e["accepted"]}
    assert accepted == {("1021", "US27"), ("1021", "US30"), ("1025", "US29")}
    assert xw["fan_out"] == {"1021": ["US27", "US30"]}
    assert al.stats["s4_rows_matched_share"] == 1.0


def test_column_matching_by_values_and_labels(synthetic):
    doc, ecc, s4 = synthetic
    al = keys.align(doc, ecc, s4)
    chosen = column_match.assign(column_match.match_fields(al, al.doc, ["mrp_type", "safety_stock"]))
    assert chosen["mrp_type"]["column"] == "MRP Type"
    assert chosen["safety_stock"]["column"] == "Safety Stock"      # '12' vs '12.0' agree after normalisation
    assert chosen["safety_stock"]["agreement"] == 1.0


def test_split_on_target_plant_is_found(synthetic):
    doc, ecc, s4 = synthetic
    al = keys.align(doc, ecc, s4)
    splits = value_maps.find_split(al, "Procurement type", "BESKZ")
    assert splits[0]["discriminator"] == "__target__plant"
    assert splits[0]["purity"] == 1.0
    assert {"src": "E", "when": "US30", "then": "F"} in splits[0]["rules"]
