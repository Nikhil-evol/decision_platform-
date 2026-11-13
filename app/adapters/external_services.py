from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


class ExternalDependencyError(Exception):
    def __init__(self, service: str, message: str, transient: bool = True) -> None:
        self.service = service
        self.transient = transient
        super().__init__(f"[{service}] {message}")


@dataclass
class FailureMode:
    fail_times: int = 0
    permanent: bool = False
    error_message: str = "Simulated upstream timeout"


class SimulatedExternalAdapter:
    """
    Simulates external financial intelligence & verification providers used in lending
    and onboarding journeys (Bureau + Bank Statement Analyzer, GSTN Analyzer, CKYC Registry)
    with deterministic fault injection for testing transient and prolonged outages.
    """

    def __init__(self) -> None:
        self._call_counts: Dict[str, int] = {}
        self._failure_modes: Dict[str, FailureMode] = {}
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            "bureau_bsa_aggregator": self._bureau_and_bsa_check,
            "gstn_analyzer": self._gstn_registry_check,
            "ckyc_registry": self._ckyc_verification_check,
        }

    def configure_failure(
        self,
        service: str,
        fail_times: int = 0,
        permanent: bool = False,
        error_message: str = "Simulated upstream 503 Service Unavailable",
    ) -> None:
        self._failure_modes[service] = FailureMode(
            fail_times=fail_times,
            permanent=permanent,
            error_message=error_message,
        )
        self._call_counts[service] = 0

    def reset_failures(self) -> None:
        self._failure_modes.clear()
        self._call_counts.clear()

    def get_call_count(self, service: str) -> int:
        return self._call_counts.get(service, 0)

    def get_status_snapshot(self) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {}
        for svc in self._handlers:
            mode = self._failure_modes.get(svc)
            snapshot[svc] = {
                "total_calls": self._call_counts.get(svc, 0),
                "failure_configured": mode is not None and (mode.permanent or mode.fail_times > 0),
                "fail_times": mode.fail_times if mode else 0,
                "permanent": mode.permanent if mode else False,
                "error_message": mode.error_message if mode else None,
            }
        return snapshot

    def invoke(self, service: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._call_counts[service] = self._call_counts.get(service, 0) + 1
        attempt = self._call_counts[service]

        mode = self._failure_modes.get(service)
        if mode:
            if mode.permanent:
                raise ExternalDependencyError(service, mode.error_message, transient=False)
            if attempt <= mode.fail_times:
                raise ExternalDependencyError(
                    service, f"{mode.error_message} (attempt {attempt})", transient=True
                )

        handler = self._handlers.get(service)
        if not handler:
            raise ExternalDependencyError(
                service, f"Unknown external service '{service}'", transient=False
            )

        return handler(payload)

    def _bureau_and_bsa_check(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Deterministic CIBIL + Bank Statement Analyzer (BSA) metrics unless explicitly passed
        monthly_inflow = float(payload.get("monthly_net_inflow", 60000))
        existing_emi = float(payload.get("existing_monthly_emi", 0))
        requested_emi = float(payload.get("proposed_emi", 10000))

        if "cibil_score" in payload and payload["cibil_score"] is not None:
            cibil = int(payload["cibil_score"])
        else:
            cibil = 775 if monthly_inflow >= 100000 else (690 if monthly_inflow >= 50000 else 610)

        foir = round(((existing_emi + requested_emi) / monthly_inflow) * 100, 2) if monthly_inflow > 0 else 100.0
        abb_to_emi_ratio = round((monthly_inflow * 0.45) / requested_emi, 2) if requested_emi > 0 else 5.0
        cheque_bounces = int(payload.get("inward_bounces_6m", 0))

        return {
            "bureau_cibil_score": cibil,
            "bsa_foir_percent": foir,
            "bsa_abb_to_emi_ratio": abb_to_emi_ratio,
            "bsa_inward_bounces_6m": cheque_bounces,
            "bureau_default_flag": bool(payload.get("has_written_off_account", False)),
        }

    def _gstn_registry_check(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        gstin = str(payload.get("gstin", "")).strip().upper()
        is_active = len(gstin) == 15 and not gstin.startswith("00")
        filing_regularity = 96 if is_active else 0
        return {
            "gst_active": is_active,
            "gst_filing_compliance_pct": filing_regularity,
            "gst_grade": "A" if is_active else "D",
        }

    def _ckyc_verification_check(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        pan = str(payload.get("pan_number", "")).strip().upper()
        pan_valid = len(pan) == 10 and pan[:5].isalpha() and pan[5:9].isdigit() and pan[9].isalpha()
        name_match_score = 94 if pan_valid else 35
        return {
            "ckyc_pan_valid": pan_valid,
            "ckyc_name_match_score": name_match_score,
        }
