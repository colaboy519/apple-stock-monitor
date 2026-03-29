# Apple CTO Part Numbers & Availability Monitoring — Research Findings

*Date: 2026-03-30*

## 1. Part Number System

### Standard (Retail) SKUs — Singapore (ZP/A suffix)

| SKU | Product | Config | Pickup Available? |
|-----|---------|--------|-------------------|
| `MCX44ZP/A` | Mac Mini M4 Pro | 12c CPU/16c GPU, 24GB, 512GB, 1GbE | Yes (retail) |
| `MCYT4ZP/A` | Mac Mini M4 | various standard configs | Yes |
| `MU963ZP/A` | Mac Studio M4 Max | 14c/32c GPU, 36GB, 512GB (base) | Yes (retail) |
| `MHQH4ZP/A` | Mac Studio M4 Max | **16c/40c GPU, 64GB, 1TB** | **Yes (retail!)** |
| `MU973ZP/A` | Mac Studio M3 Ultra | 28c/60c GPU, 96GB, 1TB | Yes (retail) |

**Key finding:** The Mac Studio M4 Max with 64GB/1TB (MHQH4ZP/A) is a **standard retail SKU**, not CTO. It can be picked up in-store and monitored via the pickup API.

### CTO (Configure-to-Order) Part Numbers

CTO products do NOT have traditional `XXXXX/A` part numbers. Instead they use:
- A **base part prefix** (e.g., `Z1JV` for Mac Mini M4 Pro CTO, `Z1CD` for Mac Studio M4 Max CTO)
- Followed by a **variant suffix** assigned by US retailers (e.g., `Z1JV000KT`, `Z1CD4LL/A`)
- These suffixes vary by retailer and region — they are NOT universal

| Base Part | Product Family | CTO Reason |
|-----------|---------------|------------|
| `Z1JV` | Mac Mini M4 Pro CTO | 48GB/64GB RAM, 14c upgrade, 10GbE, larger storage |
| `Z1CD` | Mac Studio M4 Max CTO | Non-standard RAM/storage combos on 16c/40c chip |

### Apple's Internal Option Codes

Apple's CTO system uses internal option codes (format: `065-XXXX`). Here are the decoded ones for Singapore:

**Mac Mini M4 Pro:**
| Option Code | Meaning |
|-------------|---------|
| `065-CGYD` | Processor: M4 Pro 12c/16c |
| `065-CGYF` | Processor: M4 Pro 14c/20c |
| `065-CJQ6` | Memory: 64GB |
| `065-CK0L` | Storage: 512GB |
| `065-CK0Q` | Storage: 1TB |
| `065-CGYX` | Ethernet: 1GbE |
| `065-CGYY` | Ethernet: 10GbE |
| `065-CK4M` | Thunderbolt: standard |
| `065-CH3P` | Software: none (final) |
| `065-CH3T` | Software: none (logic) |
| `ZP065-CJXQ` | Country kit: Singapore (ZP) |

**Mac Studio M4 Max:**
| Option Code | Meaning |
|-------------|---------|
| `065-CGWH` | Processor: M4 Max 14c/32c |
| `065-CGWJ` | Processor: M4 Max 16c/40c |
| `065-CGWM` | Memory: 36GB |
| `065-CGWP` | Memory: 64GB |
| `065-CKT9` | Storage: 512GB |
| `065-CKTC` | Storage: 1TB |
| `065-CGXJ` | Thunderbolt: standard |
| `065-CGXH` | Ethernet: standard |
| `065-CGXT` | Software: none (final) |
| `065-CGXW` | Software: none (logic) |
| `ZP065-CKTJ` | Country kit: Singapore (ZP) |

**Important:** The M4 Max 14c/32c GPU variant ONLY supports 36GB RAM. 64GB requires the 16c/40c GPU variant.

## 2. Apple Store APIs

### delivery-message API (works for all products)

**Standard SKUs:**
```
GET https://www.apple.com/sg/shop/delivery-message?parts.0=MHQH4ZP%2FA
```

**CTO configs:**
```
GET https://www.apple.com/sg/shop/delivery-message
    ?parts.0=MAC_MINI_2024_ROC_U
    &option.0=065-CJQ6,065-CK4M,065-CK0Q,065-CGYX,ZP065-CJXQ,065-CH3P,065-CGYF,065-CH3T
```

Response includes: delivery dates, shipping estimates, base part number, message type.

### pickup-message API (standard SKUs only)

```
GET https://www.apple.com/sg/shop/retail/pickup-message
    ?pl=true&parts.0=MHQH4ZP%2FA&location=238857
```

Returns per-store availability for all Singapore Apple Stores.

### fulfillment-messages API

Works for US (`apple.com/shop/fulfillment-messages`) but returns 404 for Singapore (`apple.com/sg/shop/fulfillment-messages`). Singapore uses the `pickup-message` endpoint instead.

### CTO Configuration API

```
GET https://www.apple.com/sg/shop/api/cto/update-config
    ?collection=MAC_MINI_2024_COLLECTION
    &fae=true
    &sv.memory-dimensionMemory=64gb
    &sv.processor-dimensionChip=m4pro
    &sv.processor-dimensionChip-cpuCoreCount-gpuCoreCount=m4pro-14-20
```

Returns available options, price deltas, and compatibility for all dimensions.

### updateSummary API

```
GET https://www.apple.com/sg/shop/updateSummary
    ?fae=true
    &node=home/shop_mac/family/mac_mini
    &step=select
    &product=MAC_MINI_2024_ROC_U
    &option.memory=065-CJQ6
    &option.storage=065-CK0Q
    ...
```

Returns full pricing, option part numbers, and financing info for a specific CTO config.

## 3. Current Delivery Estimates (as of 2026-03-30)

| Product | Config | Delivery | Type |
|---------|--------|----------|------|
| Mac Mini M4 Pro 24GB/512GB (MCX44ZP/A) | Standard | 22-29 Apr 2026 | Delivery |
| Mac Studio M4 Max 36GB/512GB (MU963ZP/A) | Standard | 31 Mar 2026 | Delivery |
| **Mac Studio M4 Max 64GB/1TB (MHQH4ZP/A)** | **Standard** | **31 Mar 2026** | **Delivery** |
| Mac Studio M3 Ultra 96GB/1TB (MU973ZP/A) | Standard | 31 Mar 2026 | Delivery |
| Mac Mini M4 Pro 14c/64GB/1TB/1GbE (Z1JV CTO) | CTO | **16-18 weeks** (Jul-Aug 2026) | Ship |
| Mac Mini M4 Pro 14c/64GB/1TB/10GbE (Z1JV CTO) | CTO | **16-18 weeks** (Jul-Aug 2026) | Ship |
| Mac Mini M4 Pro 12c/64GB/512GB (Z1JV CTO) | CTO | **16-18 weeks** (Jul-Aug 2026) | Ship |
| Mac Studio M4 Max 16c/64GB/512GB (Z1CD CTO) | CTO | 7-14 May 2026 | Delivery |

**Critical insight:** Mac Mini 64GB CTO configs have a **16-18 week** lead time — drastically longer than standard configs. The Mac Studio 64GB is available much sooner because the 64GB/1TB variant (MHQH4ZP/A) is a standard retail product.

## 4. Monitoring Strategy

### For Mac Studio M4 Max 64GB:
- **MHQH4ZP/A is a retail SKU** — monitor via both pickup-message AND delivery-message APIs
- Can potentially be picked up in-store at Singapore Apple Stores
- Currently delivering in 1-2 days (fast turnaround)

### For Mac Mini M4 Pro 64GB:
- **No retail SKU exists** — all 64GB configs are CTO only
- Monitor via delivery-message API with CTO option codes
- Track delivery estimate changes: shortening estimates = new production batch
- Currently 16-18 weeks — any significant change warrants an alert

### For new product launches:
- Monitor buy pages for new SKU patterns and page content changes
- Watch for new standard configs that include 64GB (Apple could add these)

## 5. How to Discover New CTO Option Codes

1. Navigate to Apple's configurator page in a browser
2. Select desired options
3. Monitor network requests for `updateSummary` or `witb-details` API calls
4. Extract option codes from the URL parameters
5. Use these in the delivery-message API

The collection names follow the pattern: `MAC_MINI_2024_COLLECTION`, `MAC_STUDIO_2025_COLLECTION`
The product codes follow: `MAC_MINI_2024_ROC_U`, `MAC_STUDIO_2025_ROC_BB`
