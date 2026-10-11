"""MedShare front end (Streamlit).  Run:  streamlit run app.py
Uses the engine in medshare.py (keep both files in the same folder)."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from medshare import Party, Platform, Policy, PolicyNotReviewed, Status

st.set_page_config(page_title="MedShare", page_icon="💊", layout="wide")


# ------------------------------------------------------------- demo data ---
def seed(today: date) -> Platform:
    policy = Policy(
        jurisdiction="DEMO-REGION (placeholder values)",
        min_remaining_shelf_life_days=90,
        min_use_window_days=30,
        transfer_lead_days=3,
        reviewed_by="Demo Reviewer (placeholder)",
        reviewed_on=today,
    )
    p = Platform(policy)
    p.register_party(Party("D1", "City Hospital Pharmacy", "donor", True))
    p.register_party(Party("D2", "Unlicensed Seller", "donor", False))
    p.register_party(Party("C1", "Hope Charity Clinic", "recipient", True, ("ROOM",)))
    p.register_party(Party("C2", "Rural Care Trust", "recipient", True, ("ROOM", "COLD")))

    def stock(donor, ing, strength, form, qty, batch, days, verify=False, **kw):
        base = dict(donor_id=donor, ingredient=ing, strength=strength, form=form, qty=qty,
                    batch_no=batch, expiry=today + timedelta(days=days), sealed=True, damaged=False,
                    storage_class="ROOM", provenance_docs=["INV-1001"], storage_log_present=True,
                    storage_excursions=0)
        base.update(kw)
        l = p.submit_listing(**base)
        if verify:
            p.verify_listing(l.id, "Pharmacist A", today)

    # verified stock
    stock("D1", "Metformin", "500 mg", "tablet", 1000, "B100", 200, verify=True)
    stock("D1", "Metformin", "500 mg", "tablet", 600, "B101", 130, verify=True)
    stock("D1", "Insulin glargine", "100 IU/mL", "vial", 40, "B300", 160, verify=True,
          storage_class="COLD")
    stock("D1", "Salbutamol", "100 mcg", "inhaler", 80, "B700", 100, verify=True)
    # left pending so you can show live rejections in the Verify tab
    stock("D1", "Amoxicillin", "250 mg", "capsule", 300, "B200", 150, sealed=False)
    stock("D1", "Paracetamol", "500 mg", "tablet", 500, "B400", 60)
    stock("D2", "Atorvastatin", "20 mg", "tablet", 200, "B500", 220)
    stock("D1", "Amlodipine", "5 mg", "tablet", 400, "B600", 240,
          storage_log_present=False, storage_excursions=None)

    soon = lambda d: today + timedelta(days=d)
    p.submit_request(recipient_id="C1", ingredient="Metformin", strength="500 mg", form="tablet",
                     qty_needed=900, monthly_use=450, free_storage_units=700, needed_by=soon(14))
    p.submit_request(recipient_id="C2", ingredient="Insulin glargine", strength="100 IU/mL", form="vial",
                     qty_needed=30, monthly_use=10, free_storage_units=50, needed_by=soon(7))
    p.submit_request(recipient_id="C1", ingredient="Amoxicillin", strength="250 mg", form="capsule",
                     qty_needed=100, monthly_use=60, free_storage_units=100, needed_by=soon(10))
    return p


REAL_TODAY = date.today()
if "p" not in st.session_state:
    st.session_state.p = seed(REAL_TODAY)
    st.session_state.day_offset = 0
    st.session_state.diag = {}
p: Platform = st.session_state.p


def flash(msg: str, kind: str = "success"):
    st.session_state.flash = (kind, msg)
    st.rerun()


# --------------------------------------------------------------- sidebar ---
with st.sidebar:
    st.title("💊 MedShare")
    st.caption("Surplus medicine coordination (prototype)")
    st.session_state.day_offset = st.number_input(
        "Simulated days from today", 0, 365, st.session_state.day_offset,
        help="Move time forward in the demo to see expiry alerts and expiring stock.")
    today = REAL_TODAY + timedelta(days=st.session_state.day_offset)
    st.write(f"**Simulated date:** {today:%d %b %Y}")
    st.divider()
    pol = p.policy
    st.subheader("Active policy")
    st.write(f"**{pol.jurisdiction}**")
    st.write(f"Min shelf life at delivery: **{pol.min_remaining_shelf_life_days} d**")
    st.write(f"Min use window: **{pol.min_use_window_days} d**")
    st.write(f"Transfer lead time: **{pol.transfer_lead_days} d**")
    st.write(f"Reviewed by: {pol.reviewed_by or '— (matching disabled)'}")
    st.divider()
    st.info("Matches are proposals only. A pharmacist must confirm. This tool never "
            "authorises dispensing or substitution.")
    if st.button("Reset demo data"):
        for k in ("p", "day_offset", "diag", "flash"):
            st.session_state.pop(k, None)
        st.rerun()

if "flash" in st.session_state:
    kind, msg = st.session_state.pop("flash")
    getattr(st, kind)(msg)

donors = {x.name: x.id for x in p.parties.values() if x.kind == "donor"}
recipients = {x.name: x.id for x in p.parties.values() if x.kind == "recipient"}
pname = lambda l: f"{l.ingredient} {l.strength} {l.form}"

tabs = st.tabs(["Overview", "Donor: list stock", "Recipient: request", "Verify listings",
                "Matching", "Expiry alerts", "Audit trail"])

# -------------------------------------------------------------- overview ---
with tabs[0]:
    ls = list(p.listings.values())
    c = st.columns(4)
    c[0].metric("Available listings", sum(l.status == Status.AVAILABLE for l in ls))
    c[1].metric("Pending verification", sum(l.status == Status.PENDING for l in ls))
    c[2].metric("Open requests", sum(r.outstanding > 0 for r in p.requests.values()))
    c[3].metric("Proposed matches", sum(m.state == "proposed" for m in p.matches.values()))
    st.subheader("All listings")
    st.dataframe(pd.DataFrame([{
        "ID": l.id, "Product": pname(l), "Batch": l.batch_no, "Qty": l.qty, "Reserved": l.reserved,
        "Expires": l.expiry, "Days left": (l.expiry - today).days, "Storage": l.storage_class,
        "Donor": p.parties[l.donor_id].name, "Status": l.status.value,
        "Why rejected": "; ".join(l.rejection_reasons)} for l in ls]),
        width="stretch", hide_index=True)
    st.subheader("Requests")
    st.dataframe(pd.DataFrame([{
        "ID": r.id, "Recipient": p.parties[r.recipient_id].name,
        "Product": f"{r.ingredient} {r.strength} {r.form}", "Needed": r.qty_needed,
        "Allocated": r.allocated, "Monthly use": r.monthly_use,
        "Free storage": r.free_storage_units, "Needed by": r.needed_by}
        for r in p.requests.values()]), width="stretch", hide_index=True)

# ----------------------------------------------------------------- donor ---
with tabs[1]:
    st.subheader("List surplus stock")
    st.caption("Only unopened institutional stock with provenance and storage history is eligible. "
               "Submitted listings wait for pharmacist verification.")
    with st.form("listing_form", clear_on_submit=True):
        a, b, c3 = st.columns(3)
        donor = a.selectbox("Donor", list(donors))
        ing = b.text_input("Active ingredient")
        batch = c3.text_input("Batch number")
        a, b, c3 = st.columns(3)
        strength = a.text_input("Strength (e.g. 500 mg)")
        form = b.text_input("Dosage form (e.g. tablet)")
        qty = c3.number_input("Quantity (units)", 1, 1_000_000, 100)
        a, b, c3 = st.columns(3)
        expiry = a.date_input("Expiry date", today + timedelta(days=180))
        storage = b.selectbox("Storage class", ["ROOM", "COLD"])
        prov = c3.text_input("Provenance document ref (invoice / wholesaler id)")
        a, b, c3, d = st.columns(4)
        sealed = a.checkbox("Unopened, packaging intact", True)
        damaged = b.checkbox("Damaged", False)
        has_log = c3.checkbox("Storage log available", True)
        exc = d.number_input("Storage excursions recorded", 0, 100, 0)
        if st.form_submit_button("Submit listing"):
            if not (ing and strength and form and batch):
                st.error("Please fill ingredient, strength, form and batch.")
            else:
                p.submit_listing(
                    donor_id=donors[donor], ingredient=ing, strength=strength, form=form,
                    qty=int(qty), batch_no=batch, expiry=expiry, sealed=sealed, damaged=damaged,
                    storage_class=storage, provenance_docs=[prov] if prov else [],
                    storage_log_present=has_log, storage_excursions=int(exc) if has_log else None)
                flash("Listing submitted. Go to 'Verify listings' to review it.")

# ------------------------------------------------------------- recipient ---
with tabs[2]:
    st.subheader("Request a medicine")
    with st.form("request_form", clear_on_submit=True):
        a, b, c3 = st.columns(3)
        rec = a.selectbox("Recipient", list(recipients))
        ing = b.text_input("Active ingredient", key="r_ing")
        strength = c3.text_input("Strength", key="r_str")
        a, b, c3 = st.columns(3)
        form = a.text_input("Dosage form", key="r_form")
        qty = b.number_input("Quantity needed", 1, 1_000_000, 100)
        by = c3.date_input("Needed by", today + timedelta(days=14))
        a, b = st.columns(2)
        use = a.number_input("Actual monthly use (units)", 0, 1_000_000, 100,
                             help="Used to make sure stock can be consumed before it expires.")
        cap = b.number_input("Free storage capacity (units)", 0, 1_000_000, 500)
        if st.form_submit_button("Submit request"):
            if not (ing and strength and form):
                st.error("Please fill ingredient, strength and form.")
            else:
                p.submit_request(recipient_id=recipients[rec], ingredient=ing, strength=strength,
                                 form=form, qty_needed=int(qty), monthly_use=int(use),
                                 free_storage_units=int(cap), needed_by=by)
                flash("Request submitted. Run matching in the 'Matching' tab.")

# ---------------------------------------------------------------- verify ---
with tabs[3]:
    st.subheader("Verification queue")
    reviewer = st.text_input("Reviewing pharmacist name", key="reviewer")
    pending = [l for l in p.listings.values() if l.status == Status.PENDING]
    if not pending:
        st.success("Nothing waiting for verification.")
    for l in pending:
        with st.expander(f"{l.id} · {pname(l)} · batch {l.batch_no} · {l.qty} units"):
            reasons = p.check_listing(l, today)
            if reasons:
                st.error("Pre-check would REJECT this listing:")
                for r in reasons:
                    st.write(f"- {r}")
            else:
                st.success("Pre-check passed: eligible under the active policy.")
            if st.button("Record decision", key=f"v_{l.id}"):
                if not reviewer.strip():
                    st.warning("Enter the reviewing pharmacist's name first.")
                else:
                    res = p.verify_listing(l.id, reviewer.strip(), today)
                    flash(f"{res.id}: {res.status.value}", "success" if not reasons else "warning")

# -------------------------------------------------------------- matching ---
with tabs[4]:
    st.subheader("Matching")
    if st.button("Run matching", type="primary"):
        try:
            st.session_state.diag = p.run_matching(today)
            flash("Matching complete.")
        except PolicyNotReviewed as e:
            st.error(str(e))
    active = [m for m in p.matches.values() if m.state != "cancelled"]
    if not active:
        st.info("No matches yet. Verify some listings, then run matching.")
    pro = st.text_input("Confirming pharmacist name", key="confirmer")
    for m in active:
        r, l = p.requests[m.request_id], p.listings[m.listing_id]
        title = (f"{m.id} · {m.qty} × {pname(l)} → {p.parties[r.recipient_id].name} · "
                 f"{m.state.upper()}")
        with st.expander(title, expanded=(m.state == "proposed")):
            st.write("**Why this match**")
            for line in m.explanation:
                st.write(f"- {line}")
            if m.state == "proposed" and st.button("Confirm match", key=f"c_{m.id}"):
                if not pro.strip():
                    st.warning("Enter the confirming pharmacist's name first.")
                else:
                    p.confirm_match(m.id, pro.strip())
                    flash(f"{m.id} confirmed by {pro.strip()}.")
            elif m.state == "confirmed":
                st.caption(f"Confirmed by {m.confirmed_by}")
    cancelled = [m for m in p.matches.values() if m.state == "cancelled"]
    if cancelled:
        st.caption("Cancelled: " + ", ".join(m.id for m in cancelled))

    if st.session_state.diag:
        st.subheader("Why requests were not (fully) filled")
        for rid, items in st.session_state.diag.items():
            r = p.requests[rid]
            if r.outstanding > 0:
                st.write(f"**{rid}** · {r.ingredient} {r.strength}: {r.outstanding} units outstanding")
                for lid, why in items:
                    st.write(f"- {lid}: {why}")
                if not items:
                    st.write("- no verified listing of this exact product")

    st.divider()
    st.subheader("Issue a recall notice")
    opts = {f"{l.id} · {pname(l)} · batch {l.batch_no}": l for l in p.listings.values()
            if l.status != Status.QUARANTINED}
    if opts:
        pick = st.selectbox("Batch to recall", list(opts))
        if st.button("Recall batch"):
            l = opts[pick]
            p.recall_batch(l.ingredient, l.strength, l.form, l.batch_no)
            flash(f"Batch {l.batch_no} recalled: stock quarantined, open matches cancelled.", "warning")

# ---------------------------------------------------------------- alerts ---
with tabs[5]:
    st.subheader("Expiry alerts")
    st.caption("Unreserved stock approaching expiry. Use the sidebar to move time forward.")
    alerts = p.expiry_alerts(today)
    if alerts:
        st.dataframe(pd.DataFrame(alerts), width="stretch", hide_index=True)
    else:
        st.success("No unreserved stock is near expiry.")

# ----------------------------------------------------------------- audit ---
with tabs[6]:
    st.subheader("Audit trail")
    if p.audit:
        st.dataframe(pd.DataFrame(p.audit).fillna("").astype(str)[::-1],
                     width="stretch", hide_index=True)
