"""Run: python demo.py"""
from datetime import date, timedelta

from medshare import Party, Platform, Policy

TODAY = date(2026, 10, 10)

# DEMO values only. Real values must come from professional review for the jurisdiction.
policy = Policy(
    jurisdiction="DEMO-REGION",
    min_remaining_shelf_life_days=90,
    min_use_window_days=30,
    transfer_lead_days=3,
    reviewed_by="Demo Reviewer (placeholder)",
    reviewed_on=TODAY,
)
p = Platform(policy)

p.register_party(Party("D1", "City Hospital Pharmacy", "donor", True))
p.register_party(Party("D2", "Unlicensed Seller", "donor", False))
p.register_party(Party("C1", "Hope Charity Clinic", "recipient", True, ("ROOM",)))
p.register_party(Party("C2", "Rural Care Trust", "recipient", True, ("ROOM", "COLD")))


def stock(donor, ing, strength, form, qty, batch, days, **kw):
    base = dict(donor_id=donor, ingredient=ing, strength=strength, form=form, qty=qty,
                batch_no=batch, expiry=TODAY + timedelta(days=days), sealed=True, damaged=False,
                storage_class="ROOM", provenance_docs=["INV-1001"], storage_log_present=True,
                storage_excursions=0)
    base.update(kw)
    return p.submit_listing(**base)


l1 = stock("D1", "Metformin", "500 mg", "tablet", 1000, "B100", 200)
l2 = stock("D1", "Metformin", "500 mg", "tablet", 600, "B101", 130)
l3 = stock("D1", "Amoxicillin", "250 mg", "capsule", 300, "B200", 150, sealed=False)  # opened
l4 = stock("D1", "Insulin glargine", "100 IU/mL", "vial", 40, "B300", 160, storage_class="COLD")
l5 = stock("D1", "Paracetamol", "500 mg", "tablet", 500, "B400", 60)                  # too short
l6 = stock("D2", "Atorvastatin", "20 mg", "tablet", 200, "B500", 220)                 # unlicensed donor
l7 = stock("D1", "Amlodipine", "5 mg", "tablet", 400, "B600", 240, storage_excursions=None,
           storage_log_present=False)                                                  # no storage history
l8 = stock("D1", "Salbutamol", "100 mcg", "inhaler", 80, "B700", 100)

print("== Verification ==")
for l in p.listings.values():
    p.verify_listing(l.id, "Pharmacist A", TODAY)
    print(f"{l.id} {l.ingredient:<17} {l.status.value:<8}", "; ".join(l.rejection_reasons))

p.submit_request(recipient_id="C1", ingredient="metformin", strength="500mg", form="Tablet",
                 qty_needed=900, monthly_use=450, free_storage_units=700,
                 needed_by=TODAY + timedelta(days=14))
p.submit_request(recipient_id="C2", ingredient="Insulin glargine", strength="100 IU/mL", form="vial",
                 qty_needed=30, monthly_use=10, free_storage_units=50,
                 needed_by=TODAY + timedelta(days=7))
p.submit_request(recipient_id="C1", ingredient="Insulin glargine", strength="100 IU/mL", form="vial",
                 qty_needed=10, monthly_use=5, free_storage_units=20,
                 needed_by=TODAY + timedelta(days=10))              # cannot store COLD
p.submit_request(recipient_id="C1", ingredient="Amoxicillin", strength="250 mg", form="capsule",
                 qty_needed=100, monthly_use=60, free_storage_units=100,
                 needed_by=TODAY + timedelta(days=10))              # only listing was opened

print("\n== Matching ==")
diag = p.run_matching(TODAY)
for m in p.matches.values():
    r, l = p.requests[m.request_id], p.listings[m.listing_id]
    print(f"{m.id}: {m.qty} x {l.ingredient} ({l.id}) -> {p.parties[r.recipient_id].name} [{m.state}]")
    for line in m.explanation:
        print("     -", line)
print("\nUnmatched / near-miss diagnostics:")
for rid, items in diag.items():
    r = p.requests[rid]
    print(f"{rid} {r.ingredient}: outstanding {r.outstanding}")
    for lid, why in items:
        print(f"     {lid}: {why}")
    if r.outstanding and not items:
        print("     no verified listing of this exact product")

print("\n== Expiry alerts (at demo date +40 d) ==")
for a in p.expiry_alerts(TODAY + timedelta(days=40)):
    print(a)

print("\n== Recall of Metformin batch B100 ==")
p.recall_batch("Metformin", "500 mg", "tablet", "B100")
print({m.id: m.state for m in p.matches.values()})
p.run_matching(TODAY)
print("re-matched:", [(m.id, m.listing_id, m.qty) for m in p.matches.values() if m.state == "proposed"])

print("\n== Audit trail (last 5) ==")
for e in p.audit[-5:]:
    print(e)
