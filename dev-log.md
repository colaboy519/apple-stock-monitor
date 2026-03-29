
## 2026-03-30 00:50 — CTO Part Number Research & Delivery Monitoring

- **Done:**
  - Discovered Apple's internal CTO option code system (065-XXXX format)
  - Found that Mac Studio M4 Max 64GB/1TB (MHQH4ZP/A) is a STANDARD retail SKU — can be picked up in-store
  - Found that ALL Mac Mini M4 Pro 64GB configs are CTO only (base part Z1JV), no retail SKU exists
  - Mapped all CTO option codes for Mac Mini 64GB and Mac Studio 64GB Singapore configs
  - Discovered Apple's delivery-message API works for CTO configs (product + option codes)
  - Added CTO delivery monitoring to monitor.py with change detection
  - Added standard SKU delivery estimate tracking
  - Wrote comprehensive RESEARCH.md with all API endpoints and option codes

- **Decisions:**
  - Mac Studio 64GB is the easier path — retail SKU with fast delivery (1-2 days)
  - Mac Mini 64GB has 16-18 week CTO lead time, no in-store pickup possible
  - Monitor delivery estimate changes for CTO to detect production batch availability

- **State:**
  - monitor.py now tracks: pickup availability, delivery estimates (retail + CTO), page changes
  - All 4 Singapore Apple Stores checked for pickup
  - CTO baseline estimates captured in .state/ directory

- **Blockers:** None
