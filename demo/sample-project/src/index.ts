import { config, assertConfig } from "./config";

export interface Customer {
  id: string;
  name: string;
  mobile: string;
  tin: string;
  email: string;
}

export function refundAmount(orderTotal: number, restockingFee = 0): number {
  return Math.max(0, Math.round((orderTotal - restockingFee) * 100) / 100);
}

export function refundEmail(c: Customer, amount: number, gcashRef: string): string {
  return [
    `Hi ${c.name.split(" ")[0]},`,
    ``,
    `Your refund of PHP ${amount.toFixed(2)} has been sent to your GCash (ref ${gcashRef}).`,
    `Salamat po for your patience.`,
  ].join("\n");
}

if (process.argv[1]?.endsWith("index.ts")) {
  assertConfig();
  console.log(`refund service ready (supabase: ${config.supabaseUrl})`);
}
