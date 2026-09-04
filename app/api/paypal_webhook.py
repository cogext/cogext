"""PayPal payment webhook → auto-provision COGEXT account + send welcome email."""
import logging
import os
import secrets
import string

import httpx
import resend
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.auth import generate_api_key
from app.db.connection import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()

PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "7W1092740M6082718")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = "hello@cogextai.com"

resend.api_key = RESEND_API_KEY


async def _verify_paypal_webhook(request: Request, body: bytes) -> bool:
    """Verify PayPal webhook signature via PayPal API."""
    try:
        headers = request.headers
        payload = {
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": PAYPAL_WEBHOOK_ID,
            "webhook_event": body.decode("utf-8"),
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api-m.paypal.com/v1/notifications/verify-webhook-signature",
                json=payload,
                timeout=10,
            )
            data = resp.json()
            return data.get("verification_status") == "SUCCESS"
    except Exception as e:
        logger.warning("PayPal webhook verification failed: %s", e)
        return False


async def _provision_account(email: str, plan: str = "scale") -> dict:
    """Create or return existing account + API key."""
    sb = get_supabase()

    existing = await sb.table("api_keys").select(
        "key, account_id, email, label"
    ).eq("email", email).eq("is_active", True).maybe_single().execute()

    if existing and existing.data:
        row = existing.data
        # Upgrade label to scale if not already
        await sb.table("api_keys").update({"label": plan}).eq("account_id", row["account_id"]).execute()
        return {"api_key": row["key"], "account_id": row["account_id"], "email": email, "new": False}

    key = generate_api_key()
    result = await sb.table("api_keys").insert({
        "key": key,
        "email": email,
        "label": plan,
    }).execute()

    if not result.data:
        raise ValueError(f"Failed to create account for {email}")

    row = result.data[0]
    return {"api_key": row["key"], "account_id": row["account_id"], "email": email, "new": True}


def _send_welcome_email(email: str, api_key: str, account_id: str, is_new: bool) -> None:
    """Send welcome email with API key via Resend."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping welcome email")
        return

    subject = "Your COGEXT Scale API Key" if is_new else "COGEXT Scale — Your Account Details"
    body_html = f"""
    <div style="font-family: monospace; max-width: 600px; margin: 0 auto; padding: 40px 20px; color: #18181b;">
      <h1 style="font-size: 24px; font-weight: 700; margin-bottom: 8px;">Welcome to COGEXT Scale</h1>
      <p style="color: #52525b; margin-bottom: 32px;">Your payment was received. Here are your credentials — keep them secret.</p>

      <div style="background: #f4f4f5; border-radius: 12px; padding: 24px; margin-bottom: 24px;">
        <p style="font-size: 11px; color: #71717a; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 8px;">API Key</p>
        <p style="font-size: 14px; font-weight: 600; word-break: break-all; margin: 0;">{api_key}</p>
      </div>

      <div style="background: #f4f4f5; border-radius: 12px; padding: 24px; margin-bottom: 32px;">
        <p style="font-size: 11px; color: #71717a; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 8px;">Account ID</p>
        <p style="font-size: 14px; font-weight: 600; margin: 0;">{account_id}</p>
      </div>

      <p style="margin-bottom: 8px;"><strong>Quick start:</strong></p>
      <pre style="background: #18181b; color: #e4e4e7; padding: 16px; border-radius: 8px; font-size: 12px; overflow-x: auto;">curl -X POST https://cogext.onrender.com/api/v1/ingest \\
  -H "Authorization: Bearer {api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{{"message": "I will send the report by Friday", "source_agent_id": "your-agent-id"}}'</pre>

      <p style="margin-top: 32px; color: #52525b; font-size: 13px;">
        Docs: <a href="https://docs.cogextai.com" style="color: #4f46e5;">docs.cogextai.com</a><br/>
        Support: <a href="mailto:hello@cogextai.com" style="color: #4f46e5;">hello@cogextai.com</a>
      </p>
    </div>
    """

    try:
        resend.Emails.send({
            "from": f"COGEXT <{FROM_EMAIL}>",
            "to": [email],
            "subject": subject,
            "html": body_html,
        })
        logger.info("Welcome email sent to %s", email)
    except Exception as e:
        logger.error("Failed to send welcome email to %s: %s", email, e)


@router.post("/webhooks/paypal", tags=["billing"])
async def paypal_webhook(request: Request) -> JSONResponse:
    """Receive PayPal payment.sale.completed → provision account → send API key."""
    body = await request.body()

    # Verify signature (skip in dev if headers missing)
    if request.headers.get("paypal-transmission-id"):
        verified = await _verify_paypal_webhook(request, body)
        if not verified:
            logger.warning("PayPal webhook signature verification failed")
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    import json
    try:
        event = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("event_type", "")
    logger.info("PayPal webhook received: %s", event_type)

    if event_type != "PAYMENT.SALE.COMPLETED":
        return JSONResponse({"status": "ignored", "event_type": event_type})

    # Extract payer email
    resource = event.get("resource", {})
    payer_email = (
        resource.get("payer", {}).get("payer_info", {}).get("email")
        or event.get("resource", {}).get("payer_email_address")
        or event.get("resource", {}).get("payer", {}).get("email_address")
    )

    if not payer_email:
        logger.error("Could not extract payer email from PayPal event: %s", event)
        raise HTTPException(status_code=422, detail="Payer email not found in webhook payload")

    # Provision account
    try:
        account = await _provision_account(payer_email, plan="scale")
    except Exception as e:
        logger.error("Account provisioning failed for %s: %s", payer_email, e)
        raise HTTPException(status_code=500, detail="Account provisioning failed")

    # Send welcome email
    _send_welcome_email(
        email=account["email"],
        api_key=account["api_key"],
        account_id=account["account_id"],
        is_new=account["new"],
    )

    return JSONResponse({
        "status": "ok",
        "email": payer_email,
        "account_id": account["account_id"],
        "new_account": account["new"],
    })
