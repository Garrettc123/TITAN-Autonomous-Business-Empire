"""
Revenue Check — TITAN
Reads live MRR from Stripe and logs to console
"""
import os
import sys
from datetime import datetime

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")


def main():
    if not STRIPE_SECRET_KEY:
        print("[REVENUE] STRIPE_SECRET_KEY not set — skipping")
        return
    
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        
        subs = stripe.Subscription.list(status="active", limit=100)
        mrr = 0.0
        count = 0
        
        for sub in subs.auto_paging_iter():
            for item in sub["items"]["data"]:
                plan = item["price"]
                amount = plan["unit_amount"] / 100
                interval = plan.get("recurring", {}).get("interval", "month")
                if interval == "year":
                    amount = amount / 12
                mrr += amount
                count += 1
        
        arr = mrr * 12
        print(f"[REVENUE] {datetime.utcnow().date()} | Active subs: {count} | MRR: ${mrr:,.2f} | ARR: ${arr:,.2f}")
    
    except ImportError:
        print("[REVENUE] stripe not installed. Run: pip install stripe")
    except Exception as e:
        print(f"[REVENUE] Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
