# Foodpanda SFTP — Kaise use karein (KPO & Admin)

Yeh simple guide hai un logon ke liye jo roz Foodpanda prices dekhte / update karte hain, aur unke liye jo pehli dafa branch pe SFTP setup karte hain.

**Kab use karein:** jab aap Foodpanda ko nayi prices / stock CSV bhejna chahte ho — CSV download karke alag se portal pe upload karne ki zaroorat nahi.

**Site:** https://szl.aimatic.tech  
**Permission:** `Buying Price Control` ya `System Manager`

---

## 1) Pehle yeh 4 baatein yaad rakhain

1. **Har store / Branch ka setup alag hai** — host, port, username, password, time usi Branch form pe likhe jate hain.
2. **Ghouri Town** aur **Misrial** ka **username same** ho sakta hai. Farq aksar **port** mein hota hai.
3. **Port blank** chhorna theek hai → system **22** use karta hai. Kabhi blank save hone ke baad `0` dikhe to tension nahi (matlab 22).
4. Pehli dafa: pehle **manual upload** Success karo, phir auto schedule **Enabled** karo.

---

## 2) Administrator — pehli dafa setup (sirf ek dafa per branch)

### Aap kahan jaain
1. Desk kholo: https://szl.aimatic.tech  
2. Search mein **Branch** likho → apni branch kholo  
   Example: **S1 - Ghouri Town VIP**

### Form pe kya bharein (Foodpanda SFTP section)

| Field | Aap kya karein |
|---|---|
| **Host** | Foodpanda ka SFTP host paste karo |
| **Port** | Alag port diya ho to likho; warna **khali chhor do** |
| **Username** | `FP_PK_...` wala username |
| **Password** | Password yahan type / paste karo |
| **Remote Path** | Folder diya ho to likho; warna khali |
| **Schedule Time** | Rozana auto upload ka time (jaise `06:30:00`) — site time |
| **Enabled** | Abhi **tick mat karo** — pehle test upload |

### Phir
1. **Save** dabao.  
2. Neeche **Section 3** wala manual upload ek dafa chalao.  
3. Jab **Success** aa jaye, tab **Enabled** tick karke **Save** karo.

**Do stores, same username:**  
Har Branch pe username same rakh sakte ho; **port** us store ka alag set karo (Misrial pe jo port Foodpanda ne diya).

---

## 3) KPO / Admin — abhi upload karna ho (manual)

Do tareeqe hain. Jo situation ho, woh choose karo.

### Tareeqa A — Poori branch ki list bhejni ho

**Kab:** saari Foodpanda items update karni hon.

1. Apni **Branch** form kholo.  
2. Upar buttons mein **Foodpanda** pe click karo.  
3. **Upload Catalog via SFTP** choose karo.  
4. Confirm pe **Yes** dabao.  
5. Screen pe result aayega:
   - **Success** → file Foodpanda ko mil gayi  
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

1. Branch form → **Schedule Time** set karo (example: `06:30:00`).  
2. Ek dafa **manual upload Success** confirm karo (Section 3).  
3. **Foodpanda SFTP Enabled** tick karo → **Save**.

Uske baad system bar-baar check karta hai (lagbhag har 15 minute). Jab aapka time ho chuka ho aur aaj abhi Success na hua ho, tab upload khud chalega.

- Har branch ka time alag ho sakta hai.  
- Manual upload schedule se pehle / baad kisi bhi waqt chal sakta hai.

---

## 5) Success ya fail — aap kahan dekhein

Branch form pe:

- **Last Foodpanda SFTP Upload** → last successful time  
- **Last Foodpanda SFTP Error** → agar fail hua to short error  

Poori history ke liye Desk pe **Foodpanda SFTP Upload Log** kholo — status, filename, kitni rows gayi / skip hui.

### Common problems (simple)

| Aapko kya dikha | Aap kya karein |
|---|---|
| Missing host / username / password | Branch pe SFTP fields complete karke Save, phir dubara try |
| Port `0` dikh raha hai | Normal hai agar blank chhora tha — system 22 use karta hai |
| `[Errno 2] /foodpanda-....csv` | Remote Path khali rakho (ya sahi folder), dubara upload |
| `No module named 'paramiko'` | IT / admin ko bolo — server pe package chahiye |

---

## 6) Roz ka chhota checklist (KPO)

1. Branch Price Sheet mein prices theek hain?  
2. Change kiye hon to **Save Foodpanda Prices**.  
3. Abhi bhejni ho to **Upload … via SFTP**.  
4. Roz auto chahiye to admin se **Enabled + Schedule Time** confirm karo.  
5. Fail ho to **Upload Log** + Branch pe **Last Error** dekho.
