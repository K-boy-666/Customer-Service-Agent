"""Quick script to query shipment info."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from database import get_db

def get_shipment(order_id):
    conn = get_db()
    ship = conn.execute(
        "SELECT * FROM shipments WHERE order_id = ?", (order_id,)
    ).fetchone()
    if not ship:
        print(f"No shipment found for order {order_id}")
        return
    events = conn.execute(
        "SELECT * FROM shipment_events WHERE shipment_id = ? ORDER BY event_time",
        (ship["id"],),
    ).fetchall()
    print(f"Order: {order_id}")
    print(f"Carrier: {ship['carrier']}")
    print(f"Tracking: {ship['tracking_number']}")
    print(f"Status: {ship['status']}")
    print(f"Est Delivery: {ship['est_delivery']}")
    print(f"Events ({len(events)}):")
    for e in events:
        print(f"  [{e['event_time']}] {e['status']} - {e['location']}: {e['description']}")

if __name__ == "__main__":
    get_shipment("ORD-20260606-001")
