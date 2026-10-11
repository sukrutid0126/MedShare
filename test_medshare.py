import unittest
from dataclasses import replace
from datetime import date, timedelta

from medshare import Party, Platform, Policy, PolicyNotReviewed, Status

TODAY = date(2026, 10, 10)
POLICY = Policy("TEST", 90, 30, 3, reviewed_by="Reviewer", reviewed_on=TODAY)


def make(policy=POLICY):
    p = Platform(policy)
    p.register_party(Party("D", "Donor", "donor", True))
    p.register_party(Party("C", "Charity", "recipient", True, ("ROOM",)))
    return p


def listing(p, **over):
    kw = dict(donor_id="D", ingredient="Metformin", strength="500 mg", form="tablet", qty=1000,
              batch_no="B1", expiry=TODAY + timedelta(days=200), sealed=True, damaged=False,
              storage_class="ROOM", provenance_docs=["INV"], storage_log_present=True,
              storage_excursions=0)
    kw.update(over)
    l = p.submit_listing(**kw)
    return p.verify_listing(l.id, "Rev", TODAY)


def request(p, **over):
    kw = dict(recipient_id="C", ingredient="metformin", strength="500mg", form="Tablet",
              qty_needed=500, monthly_use=300, free_storage_units=1000, needed_by=TODAY + timedelta(days=10))
    kw.update(over)
    return p.submit_request(**kw)


class Tests(unittest.TestCase):
    def test_exclusions(self):
        p = make()
        cases = {
            "expired": dict(expiry=TODAY - timedelta(days=1)),
            "opened": dict(sealed=False),
            "damaged": dict(damaged=True),
            "no provenance": dict(provenance_docs=[]),
            "no storage": dict(storage_log_present=False, storage_excursions=None),
            "excursion": dict(storage_excursions=2),
            "short life": dict(expiry=TODAY + timedelta(days=30)),
        }
        for name, over in cases.items():
            self.assertEqual(listing(p, **over).status, Status.REJECTED, name)
        self.assertEqual(listing(p).status, Status.AVAILABLE)

    def test_policy_requires_review(self):
        p = make(replace(POLICY, reviewed_by=None))
        listing(p); request(p)
        with self.assertRaises(PolicyNotReviewed):
            p.run_matching(TODAY)

    def test_no_substitution(self):
        p = make(); listing(p)
        r = request(p, strength="850 mg")
        p.run_matching(TODAY)
        self.assertEqual(r.allocated, 0)

    def test_allocation_capped_by_usable_quantity(self):
        p = make(); listing(p, expiry=TODAY + timedelta(days=130))
        r = request(p, qty_needed=900, monthly_use=300)
        p.run_matching(TODAY)
        # 127 d at delivery - 30 d buffer = 97 d * 10/day = 970 -> capped by need (900) and stock (1000)
        self.assertEqual(r.allocated, 900)
        p2 = make(); listing(p2, expiry=TODAY + timedelta(days=130))
        r2 = request(p2, qty_needed=900, monthly_use=100)
        p2.run_matching(TODAY)
        self.assertEqual(r2.allocated, 323)   # int(100/30 * 97)

    def test_capacity_cap(self):
        p = make(); listing(p)
        r = request(p, free_storage_units=50)
        p.run_matching(TODAY)
        self.assertEqual(r.allocated, 50)

    def test_first_expiry_first_out(self):
        p = make()
        late = listing(p, batch_no="LATE", expiry=TODAY + timedelta(days=300))
        early = listing(p, batch_no="EARLY", expiry=TODAY + timedelta(days=150))
        request(p, qty_needed=100)
        p.run_matching(TODAY)
        self.assertEqual(early.reserved, 100)
        self.assertEqual(late.reserved, 0)

    def test_recall_cancels_and_releases(self):
        p = make(); l = listing(p); r = request(p)
        p.run_matching(TODAY)
        self.assertGreater(r.allocated, 0)
        p.recall_batch("Metformin", "500 mg", "tablet", "B1")
        self.assertEqual(l.status, Status.QUARANTINED)
        self.assertEqual((r.allocated, l.reserved), (0, 0))

    def test_cold_chain(self):
        p = make(); listing(p, storage_class="COLD")
        r = request(p)
        p.run_matching(TODAY)
        self.assertEqual(r.allocated, 0)

    def test_expiry_alerts(self):
        p = make(); listing(p, expiry=TODAY + timedelta(days=100))
        request(p, qty_needed=100000, monthly_use=0)  # open demand, nothing usable
        alerts = p.expiry_alerts(TODAY + timedelta(days=50))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["band"], "<= 60 d")


if __name__ == "__main__":
    unittest.main()
