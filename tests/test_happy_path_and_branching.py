from __future__ import annotations


def test_msme_credit_underwriting_stp_auto_approve(isolated_env):
    client = isolated_env["client"]
    resp = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "msme-stp-001"},
        json={
            "workflow_id": "msme_credit_underwriting",
            "payload": {
                "applicant_name": "Aarav Sharma",
                "age": 32,
                "monthly_net_inflow": 145000,
                "existing_monthly_emi": 15000,
                "proposed_emi": 28000,
                "loan_amount": 800000,
                "kyc_verified": True,
                "borrower_segment": "msme_proprietor",
                "inward_bounces_6m": 0,
            },
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "APPROVED"
    assert data["enriched_data"]["bureau_cibil_score"] == 775
    assert data["enriched_data"]["bsa_foir_percent"] == 29.66
    assert len(data["audit_trail"]) == 7
    assert [s["to_state"] for s in data["state_history"]] == [
        "RECEIVED",
        "VALIDATED",
        "EVALUATING",
        "APPROVED",
    ]


def test_msme_credit_conditional_branch_to_manual_review(isolated_env):
    client = isolated_env["client"]
    # Monthly inflow 65,000 -> CIBIL 690 (>=650 floor, <720 STP threshold) -> MANUAL_REVIEW
    resp = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "msme-cam-002"},
        json={
            "workflow_id": "msme_credit_underwriting",
            "payload": {
                "applicant_name": "Meera Nair",
                "age": 29,
                "monthly_net_inflow": 65000,
                "existing_monthly_emi": 18000,
                "proposed_emi": 22000,
                "loan_amount": 550000,
                "kyc_verified": True,
                "borrower_segment": "salaried",
                "inward_bounces_6m": 1,
            },
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "MANUAL_REVIEW"
    last_audit = data["audit_trail"][-1]
    assert last_audit["rule_id"] == "stp_auto_approval_branch"
    assert last_audit["passed"] is False
    assert last_audit["data_referenced"]["bureau_cibil_score"] == 690


def test_multi_workflow_gst_vendor_onboarding(isolated_env):
    client = isolated_env["client"]
    resp = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "gst-vendor-001"},
        json={
            "workflow_id": "gst_vendor_onboarding",
            "payload": {
                "entity_name": "Kaveri Precision Components Pvt Ltd",
                "gstin": "29AAACR5055K1Z5",
                "annual_turnover": 12000000,
                "contract_value": 1800000,
                "msme_udyam_verified": True,
            },
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "APPROVED"
    assert data["enriched_data"]["gst_active"] is True
