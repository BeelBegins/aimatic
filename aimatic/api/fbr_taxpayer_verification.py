import json
from typing import Any

import frappe
import requests
from frappe.utils import now_datetime, nowdate

GET_REG_TYPE_URL = "https://gw.fbr.gov.pk/dist/v1/Get_Reg_Type"
STATL_URL = "https://gw.fbr.gov.pk/dist/v1/statl"
PRODUCTION_ENVIRONMENTS = {"Production", "Live"}
PERMISSION_FAULT_CODE = "900908"


@frappe.whitelist()
def verify_supplier_ntn(tax_id: str | None = None):
    tax_id = (tax_id or "")
    if not tax_id.strip():
        return _error_result(
            message="Please enter an NTN in Tax ID/NTN before verifying.",
            indicator="red",
            overwrite_fields=False,
        )

    settings = _get_production_fbr_settings()
    if not settings["ok"]:
        return settings

    try:
        registration_response, registration_data = _call_fbr_api(
            url=GET_REG_TYPE_URL,
            payload={"Registration_No": tax_id},
            settings=settings,
        )
    except requests.RequestException as exc:
        return _error_result(
            message=_network_error_message("NTN verification", exc),
            indicator="red",
            overwrite_fields=False,
        )

    registration_error = _response_error_result(registration_response, registration_data)
    if registration_error:
        return registration_error

    try:
        atl_response, atl_data = _call_fbr_api(
            url=STATL_URL,
            payload={"regno": tax_id, "date": nowdate()},
            settings=settings,
        )
    except requests.RequestException as exc:
        return _error_result(
            message=_network_error_message("ATL status", exc),
            indicator="red",
            overwrite_fields=False,
        )

    atl_error = _response_error_result(atl_response, atl_data)
    if atl_error:
        return atl_error

    registration_status, registration_type = _parse_registration_result(registration_data)
    atl_status = _parse_atl_status(atl_data, registration_status)

    if registration_status == "Unregistered":
        message = "NTN is not registered with FBR."
        indicator = "red"
    elif atl_status == "Active":
        message = "NTN is registered and active."
        indicator = "green"
    else:
        message = "NTN is registered but inactive."
        indicator = "orange"

    verified_on = now_datetime().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "ok": True,
        "indicator": indicator,
        "message": message,
        "overwrite_fields": True,
        "field_updates": {
            "custom_fbr_registration_status": registration_status,
            "custom_fbr_registration_type": registration_type,
            "custom_fbr_atl_status": atl_status,
            "custom_fbr_verified_on": verified_on,
            "custom_fbr_verification_message": message,
        },
    }


def _get_production_fbr_settings() -> dict[str, Any]:
    names = frappe.get_all(
        "FBR Integration Settings",
        filters={
            "enabled": 1,
            "environment": ["in", sorted(PRODUCTION_ENVIRONMENTS)],
        },
        order_by="modified desc",
        pluck="name",
    )

    if not names:
        return _error_result(
            message="Enabled Production FBR Integration Settings not found.",
            indicator="red",
            overwrite_fields=False,
        )

    for name in names:
        settings = frappe.get_doc("FBR Integration Settings", name)
        token = settings.get_password("security_token", raise_exception=False)
        if token:
            return {
                "ok": True,
                "name": settings.name,
                "security_token": token,
                "request_timeout": settings.request_timeout or 30,
                "verify_ssl": bool(settings.verify_ssl),
            }

    return _error_result(
        message="Production FBR Security Token is missing in FBR Integration Settings.",
        indicator="red",
        overwrite_fields=False,
    )


def _call_fbr_api(*, url: str, payload: dict[str, Any], settings: dict[str, Any]):
    response = requests.request(
        method="GET",
        url=url,
        headers={
            "Authorization": f"Bearer {settings['security_token']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        data=json.dumps(payload),
        timeout=int(settings.get("request_timeout") or 30),
        verify=bool(settings.get("verify_ssl")),
    )
    return response, _parse_json_response(response)


def _parse_json_response(response: requests.Response):
    try:
        return response.json()
    except Exception:
        return {"raw_response": response.text}


def _response_error_result(response: requests.Response, data: Any):
    fault_code = _extract_fault_code(data)

    if response.status_code == 403 and fault_code == PERMISSION_FAULT_CODE:
        return _error_result(
            message="The Production FBR token is not authorized for the NTN verification API.",
            indicator="red",
            overwrite_fields=False,
        )

    if response.status_code == 401:
        return _error_result(
            message="FBR rejected the Production token.",
            indicator="red",
            overwrite_fields=False,
        )

    if response.status_code >= 400:
        detail = _extract_error_message(data)
        if detail:
            return _error_result(
                message=detail,
                indicator="red",
                overwrite_fields=False,
            )

        return _error_result(
            message=f"FBR verification request failed with HTTP {response.status_code}.",
            indicator="red",
            overwrite_fields=False,
        )

    return None


def _extract_fault_code(data: Any) -> str:
    if isinstance(data, dict):
        fault = data.get("fault") or {}
        if isinstance(fault, dict):
            return str(fault.get("code") or "")
        return str(data.get("code") or "")
    return ""


def _parse_registration_result(data: Any):
    if _looks_unregistered(data):
        return "Unregistered", ""

    container = _primary_container(data)
    registration_type = _first_value(
        container,
        [
            "Registration_Type",
            "RegistrationType",
            "Reg_Type",
            "RegType",
            "registration_type",
            "registrationType",
            "reg_type",
            "Type",
            "type",
        ],
    )
    return "Registered", registration_type or "Registered"


def _parse_atl_status(data: Any, registration_status: str):
    text = _as_text(data)
    normalized = text.lower()

    if any(token in normalized for token in ["inactive", "not active", "suspended"]):
        return "Inactive"
    if any(token in normalized for token in ["not found", "no record", "not available"]):
        return "Not Found"
    if "active" in normalized:
        return "Active"

    container = _primary_container(data)
    candidate = (_first_value(
        container,
        ["ATL_Status", "atl_status", "Status", "status", "ATL", "atl", "Active", "active"],
    ) or "").strip().lower()

    if candidate in {"active", "true", "yes", "1"}:
        return "Active"
    if candidate in {"inactive", "false", "no", "0", "not active"}:
        return "Inactive"

    if registration_status == "Unregistered":
        return "Not Found"
    return "Inactive"


def _looks_unregistered(data: Any) -> bool:
    if data in (None, "", [], {}):
        return True

    text = _as_text(data).lower()
    return any(token in text for token in ["unregistered", "not registered", "registration not found"])


def _primary_container(data: Any):
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                return item
    return {}


def _first_value(container: dict[str, Any], keys: list[str]):
    for key in keys:
        value = container.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _as_text(data: Any) -> str:
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        return str(data)


def _error_result(*, message: str, indicator: str, overwrite_fields: bool):
    return {
        "ok": False,
        "indicator": indicator,
        "message": message,
        "overwrite_fields": overwrite_fields,
    }
