# DECISION 009: JWT Secret Startup Guard

## ARCHITECT proposes:

Add a startup check in `main.py` that raises `RuntimeError` if the JWT secret is still the
default `"change-me-in-production"` AND the app appears to be running in production mode.

Production detection heuristic:
- `s3_endpoint_url` is empty or does not contain `localhost`
- `base_url` does not contain `localhost`
- If BOTH conditions are true, we're in production.

If either contains `localhost`, we're in dev mode and the default secret is allowed.

This is the simplest guard that prevents the most dangerous misconfiguration (deploying with
a known-exploitable JWT secret) without breaking local development.

Tradeoff: The heuristic is not airtight (a staging environment with a custom domain would
trigger it), but that's the correct behavior — staging should also use a real secret.

## ADVERSARY attacks:

1. **The heuristic can be bypassed.** An operator who sets `base_url=http://localhost:5173`
   on a public server would bypass the check. However, this is a self-inflicted
   misconfiguration — the base_url is used in emails and Stripe redirects, so a localhost
   value would break the product anyway. The attacker would need the operator to
   simultaneously misconfigure base_url AND forget to set the JWT secret.

2. **RuntimeError on startup is destructive.** If a production deploy has this
   misconfiguration, the entire app won't start. This is correct behavior — a running app
   with a known JWT secret is worse than a crashed app. However, the error message should
   be clear and actionable so operators can fix it quickly.

## JUDGE decides:

**Green light.** The heuristic is pragmatic and the failure mode (crash on bad config) is
safer than the alternative (silently running with exploitable auth). The bypass scenario
in Attack 1 requires a double-misconfiguration that would already break the product.

Required changes:
- Error message must include: the setting name, what to do, and that dev mode is auto-detected.
- Add to the existing `startup` event handler, not a separate one.
