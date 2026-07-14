import frappe
from frappe.oauth import OAuthWebRequestValidator


class AimaticOAuthRequestValidator(OAuthWebRequestValidator):
    """Closes a gap in Frappe core's stock OAuth2 provider: rotating a refresh
    token (RefreshTokenGrant) never revokes the refresh token it replaced, so a
    stolen-then-rotated-away refresh token stays usable forever. This overrides
    two methods to add rotation + replay detection, per aimatic's own
    requirement that revoking a device/user must stop further sync — without
    touching frappe core (frappe/oauth.py), via the override_whitelisted_methods
    hook (see aimatic/oauth/endpoints.py + hooks.py).
    """

    def save_bearer_token(self, token, request, *args, **kwargs):
        # oauthlib's RefreshTokenGrant sets request.refresh_token to the
        # presented (about-to-be-replaced) refresh token before calling this;
        # it's None on an authorization_code grant (nothing to revoke yet).
        old_refresh_token = getattr(request, "refresh_token", None)

        redirect_uri = super().save_bearer_token(token, request, *args, **kwargs)

        if old_refresh_token:
            frappe.db.set_value(
                "OAuth Bearer Token",
                {"refresh_token": old_refresh_token},
                "status",
                "Revoked",
            )
            frappe.db.commit()

        return redirect_uri

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        existing = frappe.db.get_value(
            "OAuth Bearer Token",
            {"refresh_token": refresh_token},
            ["name", "status", "user", "client"],
            as_dict=True,
        )

        if existing and existing.status == "Revoked":
            # Replay of a refresh token that was already rotated away — treat
            # as a compromise signal and kill every other active token for
            # this user+client, not just the replayed one.
            frappe.db.set_value(
                "OAuth Bearer Token",
                {"user": existing.user, "client": existing.client, "status": "Active"},
                "status",
                "Revoked",
            )
            frappe.get_doc({
                "doctype": "POS Device Audit Log",
                "user": existing.user if frappe.db.exists("User", existing.user) else None,
                "status": "refresh_reuse_detected",
            }).insert(ignore_permissions=True)
            frappe.db.commit()
            return False

        return super().validate_refresh_token(refresh_token, client, request, *args, **kwargs)
