# sari-refunds

Small refund and support helper for an online sari-sari shop. Looks up a customer,
issues a refund through Stripe, and records the GCash reference.

## Layout

- `src/` — the live code (`index.ts`, `config.ts`)
- `data/customers.csv` — customer export used for local testing
- `old/` — **deprecated.** The v1 PHP refund scripts and an old database dump.
  Nothing imports from here any more; safe to delete once someone gets to it.

## Setup

```
cp .env.example .env   # ask Ate Joy for the real values
npm test
```

Secrets live in `.env` (Supabase service role key, Stripe secret). Never commit it.

> Bantay demo fixture: every name, number, and key in this project is fake.
