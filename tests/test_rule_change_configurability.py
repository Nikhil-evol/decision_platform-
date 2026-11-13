from __future__ import annotations

import time
import yaml


def test_live_rule_change_without_code_rewrite(isolated_env):
    client = isolated_env["client"]
    config_path = isolated_env["config_dir"] / "msme_credit_underwriting.yaml"

    applicant_payload = {
        "workflow_id": "msme_credit_underwriting",
        "payload": {
            "applicant_name": "Siddharth Malhotra",
            "age": 33,
            "monthly_net_inflow": 140000,  # yields CIBIL 775
            "existing_monthly_emi": 12000,
            "proposed_emi": 22000,
            "loan_amount": 650000,
            "kyc_verified": True,
            "borrower_segment": "salaried",
            "inward_bounces_6m": 0,
        },
    }

    # 1. Under v1.2.0 (STP CIBIL >= 720), CIBIL 775 is APPROVED
    res_before = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "rule-change-before"},
        json=applicant_payload,
    )
    assert res_before.status_code == 201
    before_data = res_before.json()
    assert before_data["status"] == "APPROVED"
    hash_v1 = before_data["config_hash"]

    # 2. Tighten STP CIBIL threshold in YAML from 720 -> 790 without touching Python code
    raw_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw_cfg["version"] = "1.3.0"
    for step in raw_cfg["steps"]:
        if step["id"] == "credit_policy_evaluation":
            for rule in step["rules"]:
                if rule["id"] == "stp_auto_approval_branch":
                    rule["expression"] = (
                        "bureau_cibil_score >= 790 and bsa_foir_percent <= 50 and bsa_inward_bounces_6m == 0"
                    )

    time.sleep(0.03)
    config_path.write_text(yaml.safe_dump(raw_cfg, sort_keys=False), encoding="utf-8")

    # 3. Same applicant profile now routes to MANUAL_REVIEW under v1.3.0
    res_after = client.post(
        "/api/v1/decisions",
        headers={"Idempotency-Key": "rule-change-after"},
        json=applicant_payload,
    )
    assert res_after.status_code == 201
    after_data = res_after.json()
    assert after_data["status"] == "MANUAL_REVIEW"
    assert after_data["workflow_version"] == "1.3.0"
    assert after_data["config_hash"] != hash_v1

    # 4. Historical execution retains its original config_hash for audit integrity
    hist = client.get(f"/api/v1/executions/{before_data['execution_id']}").json()
    assert hist["config_hash"] == hash_v1
    assert hist["status"] == "APPROVED"
