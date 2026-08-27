# Deploy CareConnect

## Fastest path: Render

1. Put this project in a GitHub repository.
2. In Render, create a new Blueprint and select the repository.
3. Render reads `render.yaml`.
4. It creates:
   - the CareConnect web service
   - a PostgreSQL database
   - a generated SECRET_KEY
5. After deployment, open the public Render URL.

Health check:
`/health`

## Railway

1. Push the project to GitHub.
2. Create a Railway project from the repository.
3. Add PostgreSQL.
4. Set:
   - `APP_ENV=production`
   - `SECRET_KEY=<long random value>`
   - `DATABASE_URL=<Railway PostgreSQL URL>`
5. Railway will use `railway.json` / Dockerfile.

## Phone use

Once deployed, the public HTTPS URL works in Safari or Chrome on a phone.
On iPhone, use Share → Add to Home Screen for an app-like launcher.

## Still required before real patient use

This deployment configuration makes the demo reachable online. It does NOT make the platform HIPAA-compliant or clinically production-ready.

Before handling real patient information, complete a security/compliance review and replace demo integrations with appropriate production services, including secure video, payment processing, audit logs, identity verification, clinician credentialing, backups, monitoring, incident response, and legal/telehealth review.
