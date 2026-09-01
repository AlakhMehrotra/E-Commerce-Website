# Shri Jeevani Sarees — Third-Party Setup Guide

Yeh ek hi jagah pe woh saare steps hain jo tumhe **Email (Gmail SMTP)**,
**Razorpay (online payment)**, aur **Shiprocket (delivery)** ko live karne
ke liye karne hain. Code side sab already ready hai — yeh guide sirf
account setup aur environment variables ke liye hai.

Sabse neeche ek **complete environment variables ki list** bhi hai jo
tumhe apne server (Railway ya jahan bhi deploy kiya hai) ke
**Environment Variables** section mein daalni hai.

---

## 1. Email (Gmail SMTP) — Order confirmations & admin alerts

Isse customer ko order confirmation, shipping confirmation, password
reset emails jaate hain, aur tumhe (admin) naye order ka alert milta hai.

**Steps:**
1. Apni Gmail ID mein **2-Step Verification** on karo
   (myaccount.google.com → Security → 2-Step Verification).
2. Usi page se **App Passwords** banao (Search "App Passwords", ya
   myaccount.google.com/apppasswords). Yeh ek 16-digit password dega —
   yeh tumhara normal Gmail password nahi hai, alag hai.
3. Environment variables mein daalo (neeche list mein bhi hain):
   - `MAIL_SERVER` = `smtp.gmail.com`
   - `MAIL_PORT` = `587`
   - `MAIL_USERNAME` = tumhari Gmail ID
   - `MAIL_PASSWORD` = wahi 16-digit App Password (spaces hata ke)
   - `ADMIN_NOTIFY_EMAIL` = jis email pe tumhe "New Order" alerts chahiye
     (agar set nahi karoge to `MAIL_USERNAME` wali email pe hi aayega)

**Test kaise karein:** Ek order khud place karo — customer confirmation
email aur admin "New Order" alert dono aane chahiye.

---

## 2. Razorpay — Online payment (UPI/Card)

Isse customer online payment kar sake, aur paisa seedha tumhare bank
account mein aaye.

**Steps:**
1. [razorpay.com](https://razorpay.com) par business account banao.
2. KYC complete karo: bank account (jahan paisa aana hai), PAN card,
   business proof documents upload karo. Approve hone mein 1-2 din lagte
   hain.
3. KYC pending rehte hue bhi **Test Mode** ke API keys mil jaate hain
   (Dashboard → Settings → API Keys → Generate Test Key) — inse checkout
   flow test kar sakte ho bina real paisa kharch kiye.
4. KYC approve hone ke baad, Dashboard ke top-right se **Live Mode** pe
   switch karo, phir Settings → API Keys → **Generate Live Key**. Do
   cheezein milengi:
   - **Key ID** (`rzp_live_...` se shuru)
   - **Key Secret** — yeh sirf ek baar dikhta hai, turant safe jagah
     save kar lo.
5. Environment variables mein daalo:
   - `RAZORPAY_KEY_ID`
   - `RAZORPAY_KEY_SECRET`
6. **Webhook setup** (reliability ke liye — agar customer payment ke
   baad browser band kar de, tab bhi order "paid" mark ho jaaye):
   - Dashboard → Settings → Webhooks → Add New Webhook
   - URL: `https://tumhari-site.com/api/webhooks/razorpay`
   - Event: `payment.captured` (chaaho to `payment.failed` bhi)
   - Yahan se ek **Webhook Secret** milega → `RAZORPAY_WEBHOOK_SECRET`
     env variable mein daalo.

**Test kaise karein:** Deploy karke ek chhota real order (₹1-10) khud
online pay karke place karo. Razorpay Dashboard ke Payments section
mein transaction dikhna chahiye, aur kuch dino mein bank account mein
paisa aana chahiye (Razorpay ka settlement cycle, usually T+2/T+3 din).

---

## 3. Shiprocket — Automatic delivery/courier

Isse order place hote hi (COD ho ya prepaid) Shiprocket par shipment
automatically ban jaata hai aur courier pickup request ho jaati hai —
koi manual step nahi karna padta.

**Steps:**
1. [shiprocket.com](https://shiprocket.com) par business account banao,
   KYC complete karo (bank account jahan COD ka paisa aana hai, PAN,
   GST agar hai).
2. Dashboard → Settings → **Pickup Addresses** mein apna
   warehouse/shop address add karo. Isko ek nickname doge (jaise
   `Primary`) — yeh nickname env variable mein use hoga.
3. Dashboard → Settings → **Courier Priority** mein couriers (Delhivery,
   Xpressbees, Ecom Express jo bhi available hain) enable karo aur
   priority set karo — yeh decide karta hai order aane par kaunsa
   courier auto-pick hoga.
4. Shiprocket mein alag se koi "API key" nahi hoti — tumhara login
   **email + password** hi API authentication ke liye use hota hai.
   Isliye ek dedicated Shiprocket login rakho.
5. Environment variables mein daalo:
   - `SHIPROCKET_EMAIL`
   - `SHIPROCKET_PASSWORD`
   - `SHIPROCKET_PICKUP_LOCATION` = wahi nickname jo step 2 mein diya
     tha (default: `Primary`)
   - (Optional) Package size/weight defaults already saree ke hisaab se
     set hain — agar alag chahiye to `SHIPROCKET_PACKAGE_WEIGHT_KG`,
     `SHIPROCKET_PACKAGE_LENGTH_CM`, `SHIPROCKET_PACKAGE_BREADTH_CM`,
     `SHIPROCKET_PACKAGE_HEIGHT_CM` set kar sakte ho.

**Test kaise karein:** Ek chhota test order place karo (COD aur prepaid
dono try karo). Shiprocket Dashboard ke Orders section mein order turant
dikhna chahiye; agar Courier Priority sahi set hai to AWB bhi
automatically generate ho jaayega aur tumhari website par order ka
status "Shipped" ho jaayega.

---

## 4. Environment Variables — Complete List

Yeh sab apne server (Railway → Project → Variables) mein daal do:

| Variable | Kahan se milega | Zaroori? |
|---|---|---|
| `MAIL_SERVER` | Fixed value: `smtp.gmail.com` | Haan |
| `MAIL_PORT` | Fixed value: `587` | Haan |
| `MAIL_USERNAME` | Tumhari Gmail ID | Haan |
| `MAIL_PASSWORD` | Gmail App Password (16-digit) | Haan |
| `ADMIN_NOTIFY_EMAIL` | Jis email pe order-alerts chahiye | Optional |
| `RAZORPAY_KEY_ID` | Razorpay Dashboard → API Keys | Haan (online payment ke liye) |
| `RAZORPAY_KEY_SECRET` | Razorpay Dashboard → API Keys | Haan (online payment ke liye) |
| `RAZORPAY_WEBHOOK_SECRET` | Razorpay Dashboard → Webhooks | Recommended |
| `SHIPROCKET_EMAIL` | Tumhara Shiprocket login email | Haan (auto-delivery ke liye) |
| `SHIPROCKET_PASSWORD` | Tumhara Shiprocket login password | Haan (auto-delivery ke liye) |
| `SHIPROCKET_PICKUP_LOCATION` | Shiprocket → Pickup Addresses ka nickname | Haan (auto-delivery ke liye) |
| `SHIPROCKET_PACKAGE_WEIGHT_KG` | Default `0.5` | Optional |
| `SHIPROCKET_PACKAGE_LENGTH_CM` | Default `30` | Optional |
| `SHIPROCKET_PACKAGE_BREADTH_CM` | Default `25` | Optional |
| `SHIPROCKET_PACKAGE_HEIGHT_CM` | Default `5` | Optional |

> **Note:** Jo integration configure nahi karoge (empty chhod doge), wo
> automatically disable ho jaata hai — site crash nahi hogi. Jaise agar
> Shiprocket variables nahi daaloge, to bas auto-shipment nahi banega,
> baaki sab normal chalega.

---

## 5. Final Checklist (deploy se pehle)

- [ ] Gmail App Password generate karke `MAIL_*` variables set kiye
- [ ] Ek test order place karke confirmation + admin alert email check kiya
- [ ] Razorpay KYC submit kiya
- [ ] Razorpay Test Mode mein checkout try kiya
- [ ] Razorpay Live keys set kiye (KYC approve hone ke baad)
- [ ] Razorpay Webhook add kiya
- [ ] Shiprocket account + KYC complete kiya
- [ ] Shiprocket Pickup Location add kiya
- [ ] Shiprocket Courier Priority set ki
- [ ] `SHIPROCKET_*` variables set kiye
- [ ] Ek real chhota order (COD + prepaid dono) end-to-end test kiya
