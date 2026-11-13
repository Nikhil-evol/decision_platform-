# Decision Explanation Examples

## Example 1: STP Auto-Approval (`msme_credit_underwriting`)

### Input Request
```json
{
  "workflow_id": "msme_credit_underwriting",
  "payload": {
    "applicant_name": "Aarav Sharma",
    "age": 32,
    "monthly_net_inflow": 145000,
    "existing_monthly_emi": 15000,
    "proposed_emi": 28000,
    "loan_amount": 800000,
    "kyc_verified": true,
    "borrower_segment": "msme_proprietor",
    "inward_bounces_6m": 0
  }
}
```

### Enriched Signals & Rule Trace
- **Final Outcome**: `APPROVED`
- **External Enrichment (`bureau_bsa_aggregator`)**:
  - `bureau_cibil_score`: `775`
  - `bsa_foir_percent`: `29.66`
  - `bsa_abb_to_emi_ratio`: `2.33`
  - `bsa_inward_bounces_6m`: `0`
  - `bureau_default_flag`: `false`
- **Audit Reasoning**:
  1. `kyc_mandatory_check` (`kyc_verified == True`) -> **PASS** (`{"kyc_verified": true}`) -> `NEXT`
  2. `active_income_segment` -> **PASS** (`{"borrower_segment": "msme_proprietor"}`) -> `NEXT`
  3. `fetch_bureau_bsa_signals` -> **PASS** (1 attempt) -> `CONTINUE`
  4. `writeoff_compliance_guard` (`bureau_default_flag == False`) -> **PASS** -> `NEXT`
  5. `minimum_cibil_floor` (`bureau_cibil_score >= 650`) -> **PASS** (`{"bureau_cibil_score": 775}`) -> `NEXT`
  6. `cheque_bounce_guard` (`bsa_inward_bounces_6m <= 2`) -> **PASS** (`{"bsa_inward_bounces_6m": 0}`) -> `NEXT`
  7. `stp_auto_approval_branch` (`bureau_cibil_score >= 720 and bsa_foir_percent <= 50 and bsa_inward_bounces_6m == 0`) -> **PASS** -> `APPROVE`

---

## Example 2: Borderline Cash-Flow Escalation to `MANUAL_REVIEW`

### Input Request
```json
{
  "workflow_id": "msme_credit_underwriting",
  "payload": {
    "applicant_name": "Meera Nair",
    "age": 29,
    "monthly_net_inflow": 65000,
    "existing_monthly_emi": 18000,
    "proposed_emi": 22000,
    "loan_amount": 550000,
    "kyc_verified": true,
    "borrower_segment": "salaried",
    "inward_bounces_6m": 1
  }
}
```

### Output & Audit Reasoning
- **Final Outcome**: `MANUAL_REVIEW`
- **Observed Metrics**: `bureau_cibil_score = 690`, `bsa_foir_percent = 61.54`, `bsa_inward_bounces_6m = 1`
- **Reasoning**:
  > Escalated to manual review at step 'credit_policy_evaluation' via rule 'stp_auto_approval_branch': Routed to Credit Underwriter CAM review due to borderline CIBIL (<720), FOIR (>50%), or minor bounce history. [Observed: {'bureau_cibil_score': 690}]

---

## Example 3: GSTIN Compliance Rejection (`gst_vendor_onboarding`)

### Input Request
```json
{
  "workflow_id": "gst_vendor_onboarding",
  "payload": {
    "entity_name": "Apex Cancelled Traders",
    "gstin": "00INVALID123456",
    "annual_turnover": 6000000,
    "contract_value": 500000,
    "msme_udyam_verified": false
  }
}
```

### Output & Audit Reasoning
- **Final Outcome**: `REJECTED`
- **External Enrichment (`gstn_analyzer`)**: `{"gst_active": false, "gst_filing_compliance_pct": 0, "gst_grade": "D"}`
- **Reasoning**:
  > Rejected at step 'vendor_policy_rules' via rule 'active_gstin_mandatory': GSTIN is cancelled, suspended, or invalid in GSTN records. [Observed: {'gst_active': False}]
