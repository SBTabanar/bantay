// Runtime configuration. Values come from .env (loaded by the process manager).
export const config = {
  supabaseUrl: process.env.SUPABASE_URL ?? "",
  supabaseServiceRoleKey: process.env.SUPABASE_SERVICE_ROLE_KEY ?? "",
  stripeSecret: process.env.STRIPE_SECRET ?? "",
  gcashWebhookSecret: process.env.GCASH_WEBHOOK_SECRET ?? "",
};

export function assertConfig(): void {
  const missing = Object.entries(config).filter(([, v]) => !v).map(([k]) => k);
  if (missing.length) throw new Error(`missing config: ${missing.join(", ")}`);
}
