# CareConnect Live Integrations

## Stripe Checkout

The code now uses real Stripe-hosted Checkout when these variables are present:

- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `APP_BASE_URL`

Webhook endpoint:

`POST /api/stripe/webhook`

Configure Stripe to send at least:

- `checkout.session.completed`
- `checkout.session.expired`
- `payment_intent.payment_failed`

CareConnect only places internal appointment/user IDs in Stripe metadata. Do not place symptoms, diagnoses, visit reasons, or other PHI in Stripe metadata.

## Daily video

Set:

- `DAILY_API_KEY`

CareConnect will:
1. Create a private room per appointment.
2. Create a meeting token for the patient or doctor.
3. Make the clinician the room owner.
4. Return a short-lived join URL.

When Daily is not configured, the endpoint remains in safe placeholder mode.

## Production warning

Adding keys makes the integrations functional, but does not by itself establish HIPAA compliance. Before real patient use, confirm BAAs and healthcare eligibility for every vendor, complete access-control/audit/security work, credential clinicians, define emergency escalation, and obtain appropriate legal/compliance review.
