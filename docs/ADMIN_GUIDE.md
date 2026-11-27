# Admin guide

For shop staff. It explains the daily work in the admin panel: orders, packing proof, riders,
products, prices, delivery options and settings.

Open the admin panel at `https://YOUR-SHOP/admin` and log in with your admin account. The menu on
the left has: **Dashboard, Products, Orders, Members, Delivery, Pamoja deals, Promotions, Reports, Shop settings,
Activity Logs** and **View Live Site**.

Contents:

1. [Daily order flow](#1-daily-order-flow)
2. [Packing proof](#2-packing-proof-see-it-before-we-send-it)
3. [Sending to a rider](#3-sending-to-a-rider-delivery-pin)
4. [Pickup orders](#4-pickup-orders)
5. [Gift orders (Tuma kwa Mama)](#5-gift-orders-tuma-kwa-mama)
6. [Products and photos](#6-products-and-photos)
7. [Honest prices](#7-honest-prices)
8. [Delivery areas and pickup points](#8-delivery-areas-and-pickup-points)
9. [Promotions](#9-promotions)
10. [Shop settings](#10-shop-settings)
11. [Members](#11-members)
12. [Reports](#12-reports)
13. [Activity logs](#13-activity-logs)
14. [Two-step login for admins](#14-two-step-login-for-admins)
15. [Pamoja deals (buy together)](#15-pamoja-deals-buy-together)

---

## 1. Daily order flow

Go to **Orders**. Use the status filter to see, for example, only *Processing* orders. Click the
eye button to open an order.

### What each status means

| Status | What it means | Who sets it |
|---|---|---|
| **Pending** | The order was just created. You should not normally see it: checkout moves it on straight away. | The shop |
| **Pending Payment** | The customer chose M-Pesa. The payment prompt was sent to their phone and we are waiting for Safaricom. | The shop |
| **Processing** | A cash-on-delivery order is confirmed. Pack it. | The shop |
| **Paid** | The M-Pesa payment came in (the receipt number is in the order history). Pack it. | The shop (M-Pesa) |
| **Shipped** | The parcel has left with a rider, or is **ready for pickup**. The customer gets a 4-digit PIN. | You |
| **Delivered** | The customer has the parcel (for pickup: they collected it). | Rider or you, with the PIN |
| **Cancelled** | The order will not go ahead. The items go back into stock. | You |
| **Payment Failed** | The M-Pesa payment did not go through (cancelled, wrong PIN, no money). The order is cancelled and the items go back into stock. | The shop (M-Pesa) |
| **Refunded** | You have given the money back. Stock is **not** put back automatically - fix the stock on the product if the item is back on the shelf. | You |

The customer sees these as simple steps: *Order placed → Confirmed → On the way → Delivered*
(for pickup: *Order placed → Confirmed → Ready for pickup → Collected*).

### A normal day

1. Open each **Processing** or **Paid** order.
2. Pack it and add the [packing proof](#2-packing-proof-see-it-before-we-send-it).
3. Wait for the customer to confirm (or call them if they report a problem).
4. Set the status to **Shipped** and send the rider the link ([section 3](#3-sending-to-a-rider-delivery-pin)).
5. The order becomes **Delivered** when the customer's PIN is entered.

### Changing the status

On the order page, on the right:

1. Choose the new status in **Order Status**.
2. Optional: type a **Note for the customer**, e.g. "Rider: John, 0712 345 678". It appears in the
   order history and in the email/SMS.
3. Click **Update Status**.

The customer is emailed (and sent an SMS, if SMS is switched on) when the status changes to
Processing, Paid, Shipped, Delivered, Cancelled, Refunded or Payment Failed.

Good to know:

- **Cancelling** puts the items back into stock. If you reopen a cancelled order (choose another
  status), the items are taken out of stock again. If there is not enough stock left, you get a
  warning and nothing changes.
- **Cancelling a paid order does not send the money back.** Refund the customer yourself in M-Pesa,
  then set the status to **Refunded**.
- **Pending Payment that never changes**: if Safaricom never answers, the order stays in Pending
  Payment and its items stay out of stock. Check with the customer. If they did not pay, set it
  to **Cancelled**.
- If a customer paid **less** than the order total, the order stays in Pending Payment and
  "M-Pesa amount mismatch" is written in the [Activity Logs](#13-activity-logs). Call the customer.
- Each order page also has the customer's **Phone** with a **WhatsApp** link, the **Area**,
  **Address**, **Landmark**, and **Open pin in Google Maps** if the customer shared their location.

---

## 2. Packing proof ("see it before we send it")

Before you send an order, show the customer the exact item they will get.

### How to upload

1. Open the order. Find the **Packing proof** box.
2. Tap the file button. On a phone it opens the camera.
3. Take photos or a short video: the item working, the colour and size, and the sealed parcel
   with the label.
4. Tap **Add & ask customer**.

Limits: up to **10 files** per order; videos up to **25 MB** each. Photos: PNG, JPG, GIF or WebP.
Videos: MP4, MOV, M4V, 3GP or WebM. Files that are not real photos or videos are refused with a
"Not added" message. Click **Remove** under a file to delete it.

These files are private: only the customer who ordered and admins can see them.

### What the customer sees

The customer gets an email (and SMS) titled "Check your item before we send it". On their order
page they see the photos/videos and two buttons: **Looks right** and **Something's wrong**.

The badge on your Packing proof box shows their answer:

| Badge | What to do |
|---|---|
| *Waiting for the customer* | They have not answered yet. You may wait, or call them. |
| *Customer: looks right* | Send it. |
| *Customer reported a problem* | **Do not send yet.** |

### When the customer says "Something's wrong"

1. Read what they wrote (shown in red: "Customer says: ...").
2. Call the number shown under it **before sending anything**. For a gift order this is the buyer's
   number, not the receiver's.
3. Fix the problem (right colour, right size, a working item).
4. Upload new photos/video. Adding new files clears the old answer and asks the customer again.

The shop does not stop you from shipping after a problem report - it is up to you to check.

---

## 3. Sending to a rider (delivery PIN)

### Step by step

1. Set the order to **Shipped** and click **Update Status**. The shop makes a new 4-digit PIN and
   sends it to the customer (SMS, email and their order page).
2. The **Delivery confirmation** box appears with the **Rider link**.
3. Tap **WhatsApp** to send the link to the rider with the order number, name and area filled in.
   Or tap **Copy** and send it another way.
4. The rider opens the link. They see the customer's name and phone, area, address, landmark,
   items and an **Open in Google Maps** button (if there is a map pin). They see no prices.
5. At handover the rider asks the customer for the PIN, types it and taps **Confirm delivered**.
   The order becomes **Delivered** and the customer is told.

The rider link works for **7 days** (the server can change this). It only shows the delivery details.
If it has expired, the rider sees "This delivery link has expired" - open the order again and send
the new link.

The customer should give the PIN **only once they have the parcel**. Tell riders never to ask for
it before.

### Wrong PIN

After 5 wrong PINs in 15 minutes the order is locked for 15 minutes ("Too many wrong PINs").
After **10 wrong PINs in total** the PIN is locked for good: the order page shows a red
"PIN is locked" box. Call the customer, and once you are sure they have the parcel, use the
override below. Each wrong PIN is written in the Activity Logs.

### The customer lost the PIN

1. The PIN is always on the customer's order page (**Delivery PIN** box), and was in the SMS and
   email. Ask them to look there.
2. For a gift order the buyer can tap **Re-send their text** on their order page to text the PIN to
   the receiver again (only when SMS is switched on; at most 3 times an hour).
3. Staff cannot see the PIN - it is not shown in the admin panel. If the customer really cannot find
   it, use the override below.

Do **not** set the order back to Processing and then Shipped again unless you mean to: every time an
order is set to Shipped, a **new** PIN is made and the old one stops working - and so does the old
rider link (send the rider the new link).

### Marking delivered without the PIN (override)

Use this only when you are sure the customer has the parcel (e.g. they collected and paid, but their
phone is off).

1. On the order page choose **Delivered** in Order Status.
2. Fill in **Only for "Delivered" without the customer's PIN: reason**, e.g. "Customer paid and
   collected, phone off".
3. Click **Update Status**.

Without a reason the shop refuses and asks for the PIN. The reason and your username are saved and
shown **only on the admin order page** (Delivery confirmation box). The customer just sees
"Marked delivered by our staff".

---

## 4. Pickup orders

A pickup order is one where the customer chose a **pickup point** (see
[section 8](#8-delivery-areas-and-pickup-points)). There is no delivery address and no rider link.

1. Pack the order and add packing proof as usual.
2. When it is ready on the counter, set it to **Shipped**. The customer is told "Your order is ready
   for pickup" and gets a **Pickup PIN**.
3. When they come, ask for the PIN. On the order page, type it in **Customer's PIN** and click
   **Confirm delivered**. The customer is told "Order collected".

If they cannot show the PIN but you are sure it is them, use the
[override with a reason](#marking-delivered-without-the-pin-override).

---

## 5. Gift orders (Tuma kwa Mama)

A customer can send an order to someone else (family, friends). The order page shows a yellow
**Gift order** badge with the buyer's name and phone and their message.

- **Receiver** and **Phone** on the order are the person receiving the parcel. The rider calls them
  and they get the PIN.
- The buyer gets the normal emails and SMS. The receiver gets their own short SMS: "a parcel is
  coming", the PIN when it is shipped, and "delivered/collected". They also get a tracking link that
  needs no account.

### What staff must not show the receiver

- **No prices.** Do not put a receipt or price tag in the parcel, and do not say the price on the
  phone. The receiver's SMS, tracking page and the rider page show no prices.
- **Cash on delivery is the only exception**: the receiver must pay the order total. The order page
  says "they pay KSh ... on delivery". The receiver's first SMS and tracking page also show this amount.
  Tell the rider to collect exactly that amount.
- If there is a problem with the packing proof, call the **buyer**, not the receiver.

### Limits that protect the shop's SMS

- The buyer's message is sent in our SMS, so it may not contain links or phone numbers (checkout
  refuses them). Only the buyer's first name is used.
- Each customer can place at most **3 gift orders a day**. If a real customer needs more, they can
  contact you.

---

## 6. Products and photos

Go to **Products**. Click **Add New Product**, or the pencil button to edit one.

| Field | Notes |
|---|---|
| **Product Name** | Up to 100 characters. |
| **Photos** | Up to **8** per product. PNG, JPG or GIF. You can choose several at once. |
| **Current Price** | What the customer pays now, in KSh. |
| **Original Price** | The "was" price for a sale. Leave blank if not on sale. See [section 7](#7-honest-prices). |
| **Description** | Up to 1000 characters. |
| **Category** | Choose from the list. |
| **Stock Quantity** | 0 = sold out. Customers cannot buy more than you have. |

Click **Add Product** / **Edit Product** to save.

### Photo tips (also shown on the page)

- Square photos, at least 800 × 800 pixels.
- A plain white or light background, with the product filling most of the frame.
- Add the front, the back and a close-up.

The shop makes smaller copies of every photo automatically, so customers on mobile bundles do not
download big files. It also removes the location data that phones store in photos.

### Main photo

The **main** photo is the one shown on product cards. When editing, tick **Main** under the photo you
want. Tick **Remove** under any photo you want to delete. New photos are added after the current ones.

### Deleting a product

A product that is in any past order **cannot be deleted**. Set its stock to 0 instead.

### Alerts to customers

When you save a product and the price goes **down**, or the stock goes from 0 to more than 0,
customers who have it in their wishlist get an email (and SMS, if switched on). Each person gets at
most one alert per product per day.

---

## 7. Honest prices

The shop only shows a crossed-out "was" price and a discount badge if the discount is real:

1. The product must really have sold at the "was" price (or higher) for at least **14 days in the
   last 90 days**.
2. A discount shows for at most **60 days**. After that, the sale price is simply the normal price.

Customers can read these rules on the **Honest prices** page (`/honest-prices`), and each product
page shows a 90-day **Price history** chart.

### What the messages on the edit page mean

When a product has an Original Price, the edit page shows one of these under it:

| Message | Meaning | What to do |
|---|---|---|
| *Discount shown to customers (checked).* | The rule is met. Customers see the "was" price. | Nothing. |
| *Not shown yet: it sold at KSh X for N of the 14 days needed.* | The "was" price is not proven yet. Customers only see the current price. | Keep the normal price for now and start the sale later. |
| *Not shown any more: this sale has run for over 60 days.* | The sale is too old. | Make the sale price the normal price (clear Original Price), or end the sale. |

Rules for entering prices:

- If Original Price is lower than Current Price, it is not saved and you get a warning.
- If they are the same, there is no sale.
- Do not raise a price for a few days just to "start a sale" - the rule needs 14 days at the higher price.

---

## 8. Delivery areas and pickup points

Go to **Delivery** (page title "Delivery & Pickup"). These options appear at checkout. Customers
cannot check out if there are none.

| Field | Notes |
|---|---|
| **Name** | e.g. "Nairobi suburbs" or "Pick up from our shop". |
| **Type** | **Delivery area** (customer types an address) or **Pickup point** (no address). |
| **Fee (KSh)** | Added to the order total. 0 = free. |
| **Free when the order reaches (KSh, optional)** | e.g. 3000: orders of KSh 3,000 or more deliver free. Leave empty for no offer. |
| **Delivery time** | e.g. "Same day", "1-2 days". |
| **Areas covered, or pickup address and opening hours** | Shown to the customer. For a pickup point, put the address and hours here. |
| **Order in list (0 is first)** | Sort order at checkout. |
| **Show at checkout** | Untick to hide an option without deleting it. |

A new shop starts with five options: Nairobi CBD (KSh 150), Nairobi suburbs (KSh 250), Greater
Nairobi (KSh 400), Rest of Kenya by courier (KSh 600) and Pick up from our shop (free). Change them to
match your business.

Click the pencil to edit, the bin to delete. Deleting an option does not change past orders.

---

## 9. Promotions

Go to **Promotions** → **Add New Promotion**. Active promotions show as banners on the home page.

| Field | Notes |
|---|---|
| **Promotion Title** | Required. |
| **Promotion Image** | JPG, PNG or GIF. |
| **Link** | A page on the shop starting with `/` (e.g. `/?category=Electronics` or `/product/12`) or an `https://` address. |
| **Image Alt Text** | A short description of the picture, for people who cannot see it. |
| **Featured Original Price / Featured Promotional Price** | Optional. If both are set and the original is higher, the banner shows both prices and a "-%" badge. |
| **Discount Text** | e.g. "Summer Sale". Shown if there are no prices. |
| **Active (Show on Homepage)** | Untick to hide. |
| **Display Order (0 is first)** | Order of the banners. |

**Important:** banner prices are **not** checked by the honest-price rule. Only put a "was" price on
a banner if the product really sold at that price, and match the price on the product itself.

---

## 10. Shop settings

Go to **Shop settings**. These details appear in the footer, on the Contact page and in your
policies. Empty fields are hidden.

| Setting | Notes |
|---|---|
| **Short description** | One line about the shop (footer and link previews). |
| **Phone number** | e.g. 0712 345 678. |
| **WhatsApp number** | e.g. 0712 345 678. Turns on all the WhatsApp buttons ("Ask on WhatsApp", the chat button). Leave empty to hide them. |
| **Email address** | Customer support email, e.g. you@example.com. |
| **Shop address** | Street, building and town. |
| **Opening hours** | e.g. Mon-Sat 8am-7pm. |
| **Google Maps link** | The "Share" link of your shop on Google Maps. Must start with `https://`. |
| **Facebook / Instagram / TikTok** | Optional. Must start with `https://`. |
| **Return window (days)** | How many days customers have to return an item. Default 7. |

Click **Save settings**. Use **Preview the Contact page** to check.

---

## 11. Members

Go to **Members** to see all accounts (customers and admins).

- **Add New Member**: username, email, password, **Is Admin?** and **Email Confirmed?**.
- Edit button (person with a pencil): edit. Leave the password blank to keep the current one. Tick **Email Confirmed?** if
  a customer cannot use the email link.
- Key button: **Reset Password** for someone.
- Delete button (red): delete. Members with orders cannot be deleted. You cannot delete yourself or remove
  your own admin rights - ask another admin.

Give admin rights only to people who need them. Admins can see all customer details.

---

## 12. Reports

Under **Reports** in the menu:

| Report | What it shows |
|---|---|
| **Sales Over Time** | Total sales, number of orders, average order value and a daily chart, for the dates you choose (default: last 30 days). |
| **Sales by Product** | Quantity and revenue per product, with a category filter and sorting. |
| **Low Stock Report** | Products with stock at or below the number you choose (default 5). |

Choose the dates or filter and click **Apply Filter**.

**Note:** Sales Over Time, Sales by Product and the dashboard's "Paid online (all time)" only count
orders whose status is **Paid** right now. Cash-on-delivery orders, and M-Pesa orders already moved
on to Shipped or Delivered, are not included. The dashboard's **Sales today** counts every order that
is still going ahead.

---

## 13. Activity logs

**Activity Logs** lists what happened in the shop, newest first: logins, orders placed, status changes,
products added or edited, packing proof added, deliveries confirmed with a PIN, wrong PINs, M-Pesa
payments and problems, verification and password-reset emails that failed, and two-step login changes.

Use it to answer "who changed this order?" or "did the M-Pesa payment arrive?".

---

## 14. Two-step login for admins

Two-step login means you need your password **and** a 6-digit code from an app on your phone.
On the live shop (Docker setup) admins must turn it on before the admin panel opens.

### Turning it on

1. Install an authenticator app on your phone: Google Authenticator, Microsoft Authenticator or Authy.
2. In the shop, open your account menu → **Account security** → **Turn on two-step login**.
3. Scan the QR code with the app (or type the key shown).
4. Type the 6-digit code from the app and tap **Turn on**.
5. You now see **8 recovery codes**. Print or copy them and keep them somewhere safe, away from your
   phone. Tap **I've saved them**.

If the code is refused, check that the time on your phone is set automatically.

### Logging in

After your password, the shop asks for the code from your app. You have 5 minutes. After 5 wrong codes,
wait 15 minutes. If you do not have your phone, type one of your recovery codes instead. Each recovery
code works once. When you are running low, go to **Account security** → **Make new recovery codes**.

### Lost phone and recovery codes

Ask whoever runs the server to run:

```bash
flask --app app reset-2fa USERNAME
```

(On the Docker setup: `docker compose exec web flask --app app reset-2fa USERNAME`.)
This turns two-step login off for that account. Log in with your password and turn it on again
straight away.

---

## 15. Pamoja deals (buy together)

A Pamoja deal lowers the price as more people buy. Example: 1 buyer KSh 2,500, 5 buyers KSh 2,100,
10 buyers KSh 1,900. **Everyone pays the lowest price reached when the deal ends**, even the people
who joined first. Customers share the deal page on WhatsApp to bring in their chama, workmates or
family.

### Starting a deal

1. Open **Pamoja deals** in the menu.
2. Under **Start a deal** choose the product, an optional headline, the **price steps**
   (`buyers:price`, e.g. `1:2500, 5:2100, 10:1900`), the end date and time (shop time), and
   **Max per person**.
3. Click **Start deal**. Then open the deal page (click its name) and share it on WhatsApp.

Rules: the first step is for 1 buyer and can't be above the normal price; each step needs more
buyers and a lower price; up to 5 steps; one running deal per product. Buyers are counted per
person, not per item.

### While the deal runs

- Customers join from the deal page; the item goes to checkout at the current deal price.
- **Don't send deal orders yet.** The order page shows "In a Pamoja deal" and the shop refuses
  *Shipped* or *Delivered* until the deal has ended, because the final price isn't known.
- You can **Extend** a deal (only later, never earlier) or **End now**.

### When the deal ends

The deal ends by itself at the end time (see "Ending deals on time" in OPERATIONS.md), or when you
click **End now**. Then:

- Every buyer's order is lowered to the final price, and they get an email/SMS.
- **Cash on delivery:** collect the new, lower total shown on the order.
- **Paid by M-Pesa:** the order page shows a blue box "Refund KSh ... to the customer's M-Pesa".
  Send that amount back; the deals list shows how many refunds are due for each deal.
- Now pack and send the orders as usual.
