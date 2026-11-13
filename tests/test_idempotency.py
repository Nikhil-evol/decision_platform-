from __future__ import annotations


def test_duplicate_request_returns_cached_decision_without_reexecution(isolated_env):
    client = isolated_env["client"]
    adapter = isolated_env["app"].state.external_adapter

    payload = {
        "workflow_id": "msme_credit_underwriting",
        "payload": {
            "applicant_name": "Vikramaditya Rao",
            "age": 34,
            "monthly_net_inflow": 160000,
            "existing_monthly_emi": 12000,
            "proposed_emi": 25000,
            "loan_amount": 700000,
            "kyc_verified": True,
            "borrower_segment": "salaried",
        },
    }

    first = client.post("/api/v1/decisions", headers={"Idempotency-Key": "idem-msme-100"}, json=payload)
    assert first.status_code == 201
    first_body = first.json()
    assert first_body["idempotent_replay"] is False
    assert adapter.get_call_count("bureau_bsa_aggregator") == 1

    second = client.post("/api/v1/decisions", headers={"Idempotency-Key": "idem-msme-100"}, json=payload)
    assert second.status_code == 201
    second_body = second.json()
    assert second_body["idempotent_replay"] is True
    assert second_body["execution_id"] == first_body["execution_id"]
    assert adapter.get_call_count("bureau_bsa_aggregator") == 1


def test_idempotency_key_conflict_on_mutated_payload(isolated_env):
    client = isolated_env["client"]
    base_payload = {
        "workflow_id": "msme_credit_underwriting",
        "payload": {
            "applicant_name": "Vikramaditya Rao",
            "age": 34,
            "monthly_net_inflow": 160000,
            "existing_monthly_emi": 12000,
            "proposed_emi": 25000,
            "loan_amount": 700000,
            "kyc_verified": True,
            "borrower_segment": "salaried",
        },
    }
    res1 = client.post("/api/v1/decisions", headers={"Idempotency-Key": "idem-conflict"}, json=base_payload)
    assert res1.status_code == 201

    mutated = {
        "workflow_id": "msme_credit_underwriting",
        "payload": {**base_payload["payload"], "loan_amount": 2500000},
    }
    res2 = client.post("/api/v1/decisions", headers={"Idempotency-Key": "idem-conflict"}, json=mutated)
    assert res2.status_code == 409
