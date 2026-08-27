# CareConnect — Payments + Video Integration Build

This build extends the deployment-ready MVP with:

- Real Stripe-hosted Checkout integration
- Stripe webhook payment confirmation
- Appointment-specific pricing
- Real Daily private-room creation
- Short-lived Daily meeting tokens
- Doctor-owner / patient-participant video roles
- Safe fallback when Stripe or Daily keys are absent
- PostgreSQL-ready deployment

## Run locally
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

See:
- `DEPLOY.md`
- `INTEGRATIONS.md`
- `.env.example`

## Important
This is still not approved for real PHI or clinical use. Live API integrations and public hosting are only pieces of a production telehealth platform.
