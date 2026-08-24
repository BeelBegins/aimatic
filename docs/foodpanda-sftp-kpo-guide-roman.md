# Foodpanda SFTP — Kaise use karein (KPO & Admin)

Yeh simple guide hai un logon ke liye jo roz Foodpanda prices dekhte / update karte hain, aur unke liye jo pehli dafa branch pe SFTP setup karte hain.

**Kab use karein:** jab aap Foodpanda ko nayi prices / stock CSV bhejna chahte ho — CSV download karke alag se portal pe upload karne ki zaroorat nahi.

**Site:** https://szl.aimatic.tech  
**Permission:** `Buying Price Control` ya `System Manager`

---

## 1) Pehle yeh 4 baatein yaad rakhain

1. **SFTP host / username / password ek dafa Foodpanda Settings pe** — har branch ka alag password Branch pe nahi.
2. **Har store ka Chain ID aur Vendor ID Foodpanda Outlet pe** alag ho sakta hai. File ka naam `{prefix}_{vendor_id}.csv` hota hai (date/branch naam nahi).
3. **Port hamesha 22** hai (Foodpanda). Remote Path aksar `Catalog`.
4. Pehli dafa: pehle **manual upload** Success karo, phir Outlet pe auto schedule **Enabled** karo.

---

## 2) Administrator — pehli dafa setup

### Aap kahan jaain
1. Desk kholo.
2. **Foodpanda Settings** kholo — Host, Port `22`, Username, Password, Remote Path `Catalog`, Filename Prefix (Vendor Portal Integrations wala prefix).
3. **Foodpanda Outlet** kholo (har branch) — **Chain ID**, **Vendor ID**, optional filename prefix override, **Schedule Time**, phir test ke baad **SFTP Enabled**.

### Settings pe kya bharein

| Field | Aap kya karein |
|---|---|
| **SFTP Host** | `vendor-automation-sftp-live-ap.prod.aws.qcommerce.live` |
| **SFTP Port** | **22** |
| **SFTP Username** | `FP_PK_...` |
| **SFTP Password** | Password yahan |
| **SFTP Remote Path** | `Catalog` (catalog-only file) |
| **SFTP Filename Prefix** | Vendor Portal Integrations wala prefix; file `prefix_vendorid.csv` banegi |

### Outlet pe kya bharein

| Field | Aap kya karein |
|---|---|
| **Chain ID** | Is branch ka Foodpanda chain (har branch alag ho sakta hai) |
| **Vendor ID** | Is store ka vendor code (filename ke end mein yahi jata hai) |
| **Schedule Time** | Rozana auto upload (jaise `06:30:00`) — site time |
| **SFTP Enabled** | Abhi **tick mat karo** — pehle test upload |

### Phir
1. **Save** dabao.  
2. Outlet (ya Branch Price Sheet) se **Upload Catalog via SFTP** ek dafa chalao.  
3. Jab **Success** aa jaye, tab **SFTP Enabled** tick karke **Save** karo.

**Do stores:** username/password Settings pe same; har Outlet ka **Vendor ID** (aur zarurat ho to Chain ID) alag.

---

## 3) KPO / Admin — abhi upload karna ho (manual)

Do tareeqe hain. Jo situation ho, woh choose karo.

### Tareeqa A — Poori branch ki list bhejni ho

**Kab:** saari Foodpanda items update karni hon.

1. **Foodpanda Outlet** kholo (ya Branch form).  
2. **SFTP → Upload Catalog via SFTP** choose karo.  
3. Confirm pe **Yes** dabao.  
4. Screen pe result aayega:
   - **Success** → file Foodpanda ko mil gayi (`prefix_vendorid.csv`)  
   - **Failed** → error padho; **Foodpanda SFTP Upload Log** khol ke detail dekho  

### Tareeqa B — Sirf kuch items / filters wale rows

**Kab:** sirf in-stock, ya missing-price fix, ya kuch items change hue hon.

1. Desk pe **Branch Price Sheet** report kholo.  
2. Upar **Branch** select karo (sahi store).  
3. Filters lagaao (jaise In Stock, With Price, item search).  
4. Agar price change ki hai:
   - pehle **Foodpanda → Save Foodpanda Prices**  
   - phir upload  
5. **Foodpanda → Upload Foodpanda CSV via SFTP** dabao.  
6. Confirm karo.  
7. Sirf jo rows **abhi report mein dikh rahi hain**, wahi jaayengi.

**Note:** **Download Foodpanda CSV** alag cheez hai — woh file aapke computer pe save karti hai, Foodpanda ko nahi bhejti.

---

## 4) Auto daily upload (schedule)

**Kab:** roz same time pe automatically bhejni ho.

1. Foodpanda Outlet → **Schedule Time** set karo (example: `06:30:00`).  
2. Ek dafa **manual upload Success** confirm karo (Section 3).  
3. **SFTP Enabled** tick karo → **Save**.

Uske baad system bar-baar check karta hai (lagbhag har 15 minute). Jab aapka time ho chuka ho aur aaj abhi Success na hua ho, tab upload khud chalega.

- Har branch ka time alag ho sakta hai.  
- Manual upload schedule se pehle / baad kisi bhi waqt chal sakta hai.

---

## 5) Success ya fail — aap kahan dekhein

Foodpanda Outlet form pe:

- **Last SFTP Upload** → last successful time  
- **Last SFTP Error** → agar fail hua to short error  

Poori history ke liye Desk pe **Foodpanda SFTP Upload Log** kholo — status, filename, kitni rows gayi / skip hui.

### Common problems (simple)

| Aapko kya dikha | Aap kya karein |
|---|---|
| Missing host / username / password | **Foodpanda Settings** pe SFTP fields complete karke Save, phir dubara try |
| Port `0` dikh raha hai | Settings pe **22** set karo |
| `[Errno 2] /catalog_....csv` | Remote Path `Catalog` (leading slash nahi), dubara upload |
| File ignore / no vendor | Filename `{prefix}_{vendor_id}.csv` hona chahiye — prefix Vendor Portal se match kare, vendor_id Outlet pe |

---

## 6) Roz ka chhota checklist (KPO)

1. Branch Price Sheet mein prices theek hain?  
2. Change kiye hon to **Save Foodpanda Prices**.  
3. Abhi bhejni ho to **Upload … via SFTP**.  
4. Roz auto chahiye to admin se **Enabled + Schedule Time** confirm karo.  
5. Fail ho to **Upload Log** + Branch pe **Last Error** dekho.
