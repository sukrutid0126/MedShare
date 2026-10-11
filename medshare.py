"""
MedShare prototype: coordination of surplus, unopened institutional medicine
stock between donors (pharmacies/clinics) and charitable healthcare providers.

Scope guard rails (from the problem statement):
  * Only unopened institutional stock with documented provenance and storage history.
  * Expired, recalled, damaged, opened or unverifiable stock is excluded.
  * Eligibility rules and minimum shelf life come from a jurisdiction Policy that
    must carry a professional reviewer's sign-off; matching refuses to run otherwise.
  * Exact-product matching only (ingredient + strength + dosage form).
    The platform never substitutes, and never authorises dispensing.
    Every proposed match needs professional (pharmacist) confirmation.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Optional


# ----------------------------------------------------------------- policy ---
@dataclass(frozen=True)
class Policy:
    """Jurisdiction-specific rules. Values here are PLACEHOLDERS for the demo;
    real values must be set with professional/legal review."""
    jurisdiction: str
    min_remaining_shelf_life_days: int      # at delivery
    min_use_window_days: int                # recipient must be able to use stock this far before expiry
    transfer_lead_days: int                 # assumed days from match to delivery
    allowed_storage_classes: tuple = ("ROOM", "COLD")
    reviewed_by: Optional[str] = None       # professional who approved these rules
    reviewed_on: Optional[date] = None

    @property
    def is_reviewed(self) -> bool:
        return bool(self.reviewed_by) and self.reviewed_on is not None


class PolicyNotReviewed(Exception):
    pass


# ----------------------------------------------------------------- models ---
class Status(str, Enum):
    PENDING = "pending_verification"
    AVAILABLE = "available"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"     # recalled or expired after listing
    FULLY_RESERVED = "fully_reserved"


@dataclass
class Party:
    id: str
    name: str
    kind: str                       # "donor" or "recipient"
    licence_verified: bool = False
    storage_classes: tuple = ("ROOM",)   # recipients: what they can store


@dataclass
class Listing:
    id: str
    donor_id: str
    ingredient: str
    strength: str
    form: str
    qty: int
    batch_no: str
    expiry: date
    sealed: bool                    # unopened, original packaging intact
    damaged: bool
    storage_class: str              # "ROOM" or "COLD"
    provenance_docs: list = field(default_factory=list)   # e.g. invoice / wholesaler ids
    storage_log_present: bool = False
    storage_excursions: Optional[int] = None  # None = unknown -> unverifiable
    status: Status = Status.PENDING
    reserved: int = 0
    verified_by: Optional[str] = None
    rejection_reasons: list = field(default_factory=list)

    @property
    def available_qty(self) -> int:
        return self.qty - self.reserved

    @property
    def product_key(self):
        return product_key(self.ingredient, self.strength, self.form)


@dataclass
class Request:
    id: str
    recipient_id: str
    ingredient: str
    strength: str
    form: str
    qty_needed: int
    monthly_use: int                # units the recipient actually consumes per 30 days
    free_storage_units: int         # spare storage capacity
    needed_by: date
    allocated: int = 0

    @property
    def product_key(self):
        return product_key(self.ingredient, self.strength, self.form)

    @property
    def outstanding(self) -> int:
        return self.qty_needed - self.allocated


@dataclass
class Match:
    id: str
    listing_id: str
    request_id: str
    qty: int
    delivery: date
    expiry: date
    explanation: list
    state: str = "proposed"         # proposed -> confirmed / cancelled
    confirmed_by: Optional[str] = None


def product_key(ingredient: str, strength: str, form: str):
    norm = lambda s: "".join(s.lower().split())
    return (norm(ingredient), norm(strength), norm(form))


# --------------------------------------------------------------- platform ---
class Platform:
    ALERT_THRESHOLDS = (90, 60, 30, 14)

    def __init__(self, policy: Policy):
        self.policy = policy
        self.parties: dict[str, Party] = {}
        self.listings: dict[str, Listing] = {}
        self.requests: dict[str, Request] = {}
        self.matches: dict[str, Match] = {}
        self.recalled: set = set()          # (product_key, batch_no)
        self.audit: list = []
        self._ids = itertools.count(1)

    # -- helpers
    def _log(self, event: str, **info):
        self.audit.append({"event": event, **info})

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids):03d}"

    # -- parties
    def register_party(self, party: Party):
        self.parties[party.id] = party
        self._log("party_registered", id=party.id, kind=party.kind, licence_verified=party.licence_verified)

    # -- listings
    def submit_listing(self, **kw) -> Listing:
        listing = Listing(id=self._new_id("L"), **kw)
        self.listings[listing.id] = listing
        self._log("listing_submitted", id=listing.id, donor=listing.donor_id)
        return listing

    def check_listing(self, l: Listing, today: date) -> list:
        """Return every reason the listing is NOT eligible (empty list = eligible)."""
        why = []
        donor = self.parties.get(l.donor_id)
        if not donor or not donor.licence_verified:
            why.append("donor licence not verified")
        if l.expiry <= today:
            why.append("expired")
        if (l.product_key, l.batch_no) in self.recalled:
            why.append("batch recalled")
        if l.damaged:
            why.append("damaged")
        if not l.sealed:
            why.append("opened / packaging not intact")
        if not l.provenance_docs:
            why.append("no documented provenance")
        if not l.storage_log_present or l.storage_excursions is None:
            why.append("storage history missing")
        elif l.storage_excursions > 0:
            why.append(f"{l.storage_excursions} storage excursion(s) recorded")
        if l.storage_class not in self.policy.allowed_storage_classes:
            why.append(f"storage class {l.storage_class} not allowed by policy")
        if l.expiry > today and (l.expiry - today).days < self.policy.min_remaining_shelf_life_days:
            why.append(f"remaining shelf life below policy minimum "
                       f"({self.policy.min_remaining_shelf_life_days} d)")
        return why

    def verify_listing(self, listing_id: str, reviewer: str, today: date) -> Listing:
        """Human verification step: records who approved or why it was rejected."""
        l = self.listings[listing_id]
        reasons = self.check_listing(l, today)
        if reasons:
            l.status, l.rejection_reasons = Status.REJECTED, reasons
            self._log("listing_rejected", id=l.id, reviewer=reviewer, reasons=reasons)
        else:
            l.status, l.verified_by = Status.AVAILABLE, reviewer
            self._log("listing_verified", id=l.id, reviewer=reviewer)
        return l

    def recall_batch(self, ingredient, strength, form, batch_no):
        """Recall notice: quarantine stock and cancel open proposals using it."""
        self.recalled.add((product_key(ingredient, strength, form), batch_no))
        for l in self.listings.values():
            if l.batch_no == batch_no and l.product_key == product_key(ingredient, strength, form):
                l.status = Status.QUARANTINED
                for m in self.matches.values():
                    if m.listing_id == l.id and m.state in ("proposed", "confirmed"):
                        self._cancel(m, "batch recalled")
                self._log("listing_quarantined", id=l.id, reason="recall")

    def _cancel(self, m: Match, why: str):
        m.state = "cancelled"
        self.listings[m.listing_id].reserved -= m.qty
        self.requests[m.request_id].allocated -= m.qty
        self._log("match_cancelled", id=m.id, reason=why)

    # -- requests
    def submit_request(self, **kw) -> Request:
        r = Request(id=self._new_id("R"), **kw)
        self.requests[r.id] = r
        self._log("request_submitted", id=r.id, recipient=r.recipient_id)
        return r

    # -- matching
    def run_matching(self, today: date) -> dict:
        """Propose matches. Returns diagnostics: request_id -> [(listing_id, reason)]
        so every non-match is explainable."""
        if not self.policy.is_reviewed:
            raise PolicyNotReviewed(
                "Policy has no professional review sign-off; matching is disabled.")
        diagnostics: dict = {}
        delivery = today + timedelta(days=self.policy.transfer_lead_days)

        # re-check stock: it may have expired or been recalled since verification
        for l in self.listings.values():
            if l.status in (Status.AVAILABLE, Status.FULLY_RESERVED) and l.expiry <= today:
                l.status = Status.QUARANTINED
                self._log("listing_quarantined", id=l.id, reason="expired")

        open_reqs = sorted((r for r in self.requests.values() if r.outstanding > 0),
                           key=lambda r: (r.needed_by, r.id))
        for r in open_reqs:
            diagnostics[r.id] = []
            recipient = self.parties.get(r.recipient_id)
            if not recipient or not recipient.licence_verified:
                diagnostics[r.id].append(("*", "recipient licence not verified"))
                continue
            # First-expiry-first-out: use the stock that would otherwise be wasted first
            candidates = sorted(
                (l for l in self.listings.values() if l.status == Status.AVAILABLE),
                key=lambda l: (l.expiry, l.id))
            for l in candidates:
                if r.outstanding <= 0:
                    break
                reason = self._why_not(l, r, recipient, delivery)
                if reason:
                    if l.product_key == r.product_key:   # only explain near-misses
                        diagnostics[r.id].append((l.id, reason))
                    continue
                qty, expl = self._allocation(l, r, delivery)
                if qty <= 0:
                    diagnostics[r.id].append((l.id, "recipient cannot use any units before expiry"))
                    continue
                m = Match(self._new_id("M"), l.id, r.id, qty, delivery, l.expiry, expl)
                self.matches[m.id] = m
                l.reserved += qty
                r.allocated += qty
                if l.available_qty == 0:
                    l.status = Status.FULLY_RESERVED
                self._log("match_proposed", id=m.id, listing=l.id, request=r.id, qty=qty)
        return diagnostics

    def _why_not(self, l, r, recipient, delivery) -> Optional[str]:
        if l.product_key != r.product_key:
            return "different product (no substitution)"
        if l.storage_class not in recipient.storage_classes:
            return f"recipient cannot store {l.storage_class} stock"
        remaining = (l.expiry - delivery).days
        if remaining < self.policy.min_remaining_shelf_life_days:
            return f"only {remaining} d shelf life at delivery"
        if (l.product_key, l.batch_no) in self.recalled:
            return "batch recalled"
        return None

    def _allocation(self, l, r, delivery):
        remaining = (l.expiry - delivery).days
        usable_days = max(0, remaining - self.policy.min_use_window_days)
        usable = int(r.monthly_use / 30 * usable_days)
        free_storage = r.free_storage_units
        qty = min(l.available_qty, r.outstanding, usable, free_storage)
        limits = {"stock available": l.available_qty, "still needed": r.outstanding,
                  "usable before expiry": usable, "storage capacity": free_storage}
        binding = min(limits, key=limits.get)
        expl = [f"exact product match: {l.ingredient} {l.strength} {l.form}",
                f"{remaining} d shelf life left at delivery on {delivery}",
                f"recipient can use ~{usable} units in {usable_days} d at {r.monthly_use}/month",
                f"allocated {qty} units (limited by {binding})",
                "needs pharmacist confirmation before any transfer"]
        return qty, expl

    def confirm_match(self, match_id: str, professional: str):
        m = self.matches[match_id]
        m.state, m.confirmed_by = "confirmed", professional
        self._log("match_confirmed", id=m.id, by=professional)

    # -- expiry alerts
    def expiry_alerts(self, today: date) -> list:
        """Alert on unreserved stock approaching expiry, with the number of open
        requests for the same product so donors/recipients can act."""
        out = []
        for l in self.listings.values():
            if l.status != Status.AVAILABLE or l.available_qty <= 0:
                continue
            days = (l.expiry - today).days
            band = next((t for t in sorted(self.ALERT_THRESHOLDS) if days <= t), None)
            if band is None:
                continue
            demand = sum(1 for r in self.requests.values()
                         if r.product_key == l.product_key and r.outstanding > 0)
            out.append({"listing": l.id, "product": f"{l.ingredient} {l.strength} {l.form}",
                        "unreserved_qty": l.available_qty, "days_left": days,
                        "band": f"<= {band} d", "open_requests_for_product": demand})
        return sorted(out, key=lambda a: a["days_left"])
