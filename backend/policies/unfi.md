---
retailer: unfi
display_name: UNFI
dispute_window_days: 60
required_docs:
  SHORT: [pod, bol]
  DMG: [pod]
  PRICE: [invoice, po]
  OTIF: [pod, bol]
  PROMO: [remittance]
---
# UNFI deduction policy (illustrative, synthetic text for demo use)

## 1.1 Dispute window
Disputes must be submitted within 60 days of the deduction date through the vendor portal.

## 2.1 Shortage claims
A shortage is recognised only when the signed delivery receipt shows fewer units than the bill of lading. Matching quantities mean the claim is invalid.

## 2.2 Damage claims
Damage claims need a damage annotation on the delivery receipt signed by the receiver. Without it the claim is rejected.

## 2.3 Pricing claims
Pricing adjustments are calculated from the purchase order price. Invoices that match the purchase order price are not adjusted.

## 2.4 Delivery window compliance
On-time in-full penalties are applied only to receipts outside the appointment window printed on the bill of lading.

## 3.1 Duplicate deductions
A deduction repeated for the same invoice, reason and amount is a duplicate and is credited back on dispute.

## 3.2 Promotional and trade deductions
Promotional deductions require the signed trade agreement and are routed to the commercial team.
