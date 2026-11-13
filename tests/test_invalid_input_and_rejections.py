from __future__ import annotations


def test_invalid_schema_missing_and_wrong_type(isolated_env):
    client = isolated_env["client"]
    resp = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "invalid-schema-001"},
        json={
            "workflow_id": "msme_credit_underwriting",
            "payload": {
                "applicant_name": "Rohan Verma",
                "age": "thirty",  # Invalid type: expected int
                "monthly_net_inflow": -100,  # Below min_value
            },
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "REJECTED"
    assert data["audit_trail"][0]["rule_id"] == "schema_validation"
    assert "age" in data["decision_summary"]


def test_mandatory_kyc_gate_short_circuits_before_external_call(isolated_env):
    client = isolated_env["client"]
    resp = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "kyc-reject-001"},
        json={
            "workflow_id": "msme_credit_underwriting",
            "payload": {
                "applicant_name": "Sneha Kulkarni",
                "age": 31,
                "monthly_net_inflow": 150000,
                "existing_monthly_emi": 10000,
                "proposed_emi": 20000,
                "loan_amount": 500000,
                "kyc_verified": False,
                "borrower_segment": "salaried",
            },
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "REJECTED"
    assert len(data["audit_trail"]) == 1
    assert data["audit_trail"][0]["rule_id"] == "kyc_mandatory_check"
    assert isolated_env["app"].state.external_adapter.get_call_count("bureau_bsa_aggregator") == 0
