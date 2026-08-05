# Foodpanda SFTP Catalog Upload — KPO & Administrator Guide (Roman Urdu)

Yeh guide **KPOs** aur **Administrators** ke liye hai. Is se aap branch ki Foodpanda prices / stock CSV ko Foodpanda ke SFTP server par bhej sakte ho — pehle jaisa CSV download karke manually portal pe upload karne ki zaroorat nahi (jab SFTP set ho).

**Site:** `https://szl.aimatic.tech`  
**Role:** `Buying Price Control` ya `System Manager`

---

## Pehle samajh lo (short)

1. Har **Branch** par alag SFTP settings hoti hain (host, port, username, password, time).
2. **Ghouri Town** aur **Misrial** ka username same ho sakta hai — farq **port** mein hota hai.
3. Jis store ka port blank ho, system khud **22** use karta hai. Blank field kabhi `0` dikhaye to tension nahi — matlab default 22.
4. Pehle **manual upload** successful karo, phir **Enabled** + **Schedule Time** on karo.

---

## Part A — Administrator: pehli dafa setup (Branch)

1. Desk kholo → **Branch** → apni branch select karo  
   Example: `S1 - Ghouri Town VIP`
2. Neeche **Foodpanda SFTP** section bharao:

| Field | Kya likhna hai |
|---|---|
| **Foodpanda SFTP Host** | Foodpanda wala host (jaise `vendor-automation-sftp-live-ap.prod.aws.qcommerce.live`) |
| **Foodpanda SFTP Port** | Agar Foodpanda ne alag port diya ho to woh; warna **blank chhor do** (22) |
| **Foodpanda SFTP Username** | `FP_PK_...` wala username |
| **Foodpanda SFTP Password** | Password (kisi ko share / WhatsApp / Excel mein mat likho) |
| **Foodpanda SFTP Remote Path** | Agar Foodpanda ne folder diya ho to woh; warna blank |
| **Foodpanda SFTP Schedule Time** | Rozana auto-upload ka time (site time, jaise `06:30:00`) |
| **Foodpanda SFTP Enabled** | Abhi **off** rakho — pehle manual test |

3. **Save** karo.
4. Manual upload chalao (neeche Part B). Success aaye tab **Enabled** on karo aur Save.

**Do branches, same username, different port:**  
Har branch par username same, port alag. Example: Ghouri blank/22, Misrial apna port.

---

## Part B — KPO / Admin: manual upload (jab zaroorat ho)

### Option 1 — Poori branch catalog

1. **Branch** form kholo.
2. Upar **Foodpanda** menu → **Upload Catalog via SFTP**.
3. Confirm karo.
4. Result dekho:
   - **Success** = file Foodpanda tak pahunch gayi
   - **Failed** = error message + log link check karo
5. Log dekhne ke liye: **Foodpanda SFTP Upload Log**

### Option 2 — Sirf filtered items (Branch Price Sheet)

1. Report kholo: **Branch Price Sheet**  
   (`Desk → Aimatic → Branch Price Sheet` ya search)
2. **Branch** select karo.
3. Filters lagaao (jaise In Stock, With Price, ya specific item).
4. Agar Foodpanda price change ki ho to pehle **Save Foodpanda Prices**.
5. **Foodpanda** → **Upload Foodpanda CSV via SFTP**.
6. Sirf jo rows ab report mein dikh rahi hain, woh upload hongi.

**Download Foodpanda CSV** ab bhi hai — woh sirf file download karta hai, SFTP nahi.

---

## Part C — Automatic daily schedule

1. Branch par **Foodpanda SFTP Schedule Time** set karo (jaise subah `06:30:00`).
2. Manual upload ek dafa **Success** confirm karo.
3. **Foodpanda SFTP Enabled** tick karo → **Save**.

System roughly har **15 minute** check karta hai. Jab branch ka time ho jaye aur aaj abhi successful upload na hua ho, tab auto upload chalega.

- Har branch ka **apna time** ho sakta hai.
- Manual upload kisi bhi waqt chal sakta hai (schedule time ki zaroorat nahi).

---

## Success / fail — kya check karein

Branch form par:

- **Last Foodpanda SFTP Upload** — last success time  
- **Last Foodpanda SFTP Error** — last error (password yahan nahi aata)

Poori history: DocType **Foodpanda SFTP Upload Log**  
Wahan status, filename, kitni rows, skip count, aur kabhi CSV copy bhi mil sakti hai.

Common issues:

| Error / masla | Matlab / fix |
|---|---|
| `No module named 'paramiko'` | IT / admin — server package missing (bench pe install) |
| `[Errno 2] /foodpanda-....csv` | Galat remote path tha; ab blank path home pe jata hai — dubara try |
| Missing host / username / password | Branch SFTP fields incomplete |
| Port `0` dikhe | Blank Int field — system 22 use karta hai; theek hai |

---

## Daily KPO checklist (short)

1. Branch Price Sheet mein Foodpanda prices theek hain?  
2. Zaroorat ho to **Save Foodpanda Prices**.  
3. Manual chahiye to **Upload … via SFTP**.  
4. Auto chahiye to Enabled + Schedule Time set (admin).  
5. Fail ho to Upload Log + Last Error dekho; password screen share mat karo.

---

## Security (zaroori)

- Password Branch ke Password field mein hi rakho.
- Password Excel, WhatsApp, email, ya screenshot mein mat bhejo.
- Fixtures / git / code mein password kabhi na dalein.
