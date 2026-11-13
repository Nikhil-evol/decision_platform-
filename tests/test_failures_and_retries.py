from __future__ import annotations


def test_transient_dependency_failure_recovers_within_step_retries(isolated_env):
    client = isolated_env["client"]
    adapter = isolated_env["app"].state.external_adapter
    adapter.configure_failure("bureau_bsa_aggregator", fail_times=2)

    resp = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "transient-retry-01"},
        json={
            "workflow_id": "msme_credit_underwriting",
            "payload": {
                "applicant_name": "Ananya Joshi",
                "age": 31,
                "monthly_net_inflow": 130000,
                "existing_monthly_emi": 10000,
                "proposed_emi": 20000,
                "loan_amount": 600000,
                "kyc_verified": True,
                "borrower_segment": "salaried",
            },
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "APPROVED"
    assert adapter.get_call_count("bureau_bsa_aggregator") == 3


def test_exhausted_dependency_enters_retry_pending_and_recovers_on_retry(isolated_env):
    client = isolated_env["client"]
    adapter = isolated_env["app"].state.external_adapter
    adapter.configure_failure("bureau_bsa_aggregator", fail_times=5)

    initial = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "pending-retry-01"},
        json={
            "workflow_id": "msme_credit_underwriting",
            "payload": {
                "applicant_name": "Karthik Menon",
                "age": 36,
                "monthly_net_inflow": 150000,
                "existing_monthly_emi": 15000,
                "proposed_emi": 25000,
                "loan_amount": 750000,
                "kyc_verified": True,
                "borrower_segment": "msme_proprietor",
            },
        },
    )
    assert initial.status_code == 201
    init_data = initial.json()
    assert init_data["status"] == "RETRY_PENDING"
    exec_id = init_data["execution_id"]

    adapter.reset_failures()
    retried = client.post(f"/api/v1/executions/{exec_id}/retry")
    assert retried.status_code == 200
    retry_data = retried.json()
    assert retry_data["status"] == "APPROVED"
    assert retry_data["retry_count"] == 1
    assert [s["to_state"] for s in retry_data["state_history"]] == [
        "RECEIVED",
        "VALIDATED",
        "EVALUATING",
        "RETRY_PENDING",
        "EVALUATING",
        "APPROVED",
    ]


def test_partial_save_failure_rolls_back_atomically(isolated_env):
    client = isolated_env["client"]
    repo = isolated_env["app"].state.repository

    failed_resp = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "atomic-rollback-01"},
        json={
            "workflow_id": "msme_credit_underwriting",
            "simulate_storage_failure": True,
            "payload": {
                "applicant_name": "Divya Iyer",
                "age": 28,
                "monthly_net_inflow": 120000,
                "existing_monthly_emi": 10000,
                "proposed_emi": 18000,
                "loan_amount": 450000,
                "kyc_verified": True,
                "borrower_segment": "salaried",
            },
        },
    )
    assert failed_resp.status_code == 503
    assert repo.find_by_idempotency_key("atomic-rollback-01") is None

    recovered = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "atomic-rollback-01"},
        json={
            "workflow_id": "msme_credit_underwriting",
            "simulate_storage_failure": False,
            "payload": {
                "applicant_name": "Divya Iyer",
                "age": 28,
                "monthly_net_inflow": 120000,
                "existing_monthly_emi": 10000,
                "proposed_emi": 18000,
                "loan_amount": 450000,
                "kyc_verified": True,
                "borrower_segment": "salaried",
            },
        },
    )
    assert recovered.status_code == 201
    assert recovered.json()["status"] == "APPROVED"
