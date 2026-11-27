# Putting the shop online

This guide uses Docker on a rented Linux server (a "VPS"). The same setup runs on any
provider: Hetzner, DigitalOcean, Contabo, or a Kenyan host such as Truehost or HostPinnacle.
You end up with:

- **web**: the shop, run by Gunicorn
- **db**: PostgreSQL, with data kept in a Docker volume
- **caddy**: the front door. It gets and renews free HTTPS certificates automatically.

Product photos are kept in their own volume, so they survive updates.

## What you need

- A server with Ubuntu 22.04 or 24.04, 1-2 GB RAM, and SSH access as a sudo user.
- A domain name, e.g. `esashop.co.ke`. In your domain's DNS settings, add an **A record**
  pointing `@` (and `www`) to the server's IP address. DNS changes can take up to an hour.

## 1. Install Docker (once)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # then log out and back in
```

## 2. Get the code

```bash
sudo mkdir -p /opt/shop && sudo chown $USER /opt/shop
git clone https://github.com/Brayowekesa/sales_platform.git /opt/shop
cd /opt/shop
```

## 3. Settings

```bash
cp .env.example .env
nano .env
```

Fill in at least:

| Setting | Value |
|---|---|
| `DOMAIN` | your domain, e.g. `esashop.co.ke` |
| `POSTGRES_PASSWORD` | output of `python3 -c "import secrets; print(secrets.token_hex(24))"` |
| `FLASK_SECRET_KEY` | output of `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `MAIL_*` | your email provider (needed for verification codes and order emails) |
| `MPESA_*` | your Daraja credentials; `MPESA_CALLBACK_URL=https://YOUR_DOMAIN/mpesa_callback` |

Leave `DATABASE_URL` empty: Docker connects the shop to its database automatically.
Never commit `.env`.

## 4. Start

```bash
docker compose up -d --build
docker compose ps          # all three should be "Up"; web should become "healthy"
docker compose logs -f web # Ctrl+C to stop watching
```

Open `https://YOUR_DOMAIN`. The first visit can take a few seconds while Caddy gets the certificate.

## 5. Create your admin account

```bash
docker compose exec web flask --app app create-admin
```

Then log in, and fill in **Admin → Shop settings** and **Admin → Delivery**.

### Moving an existing shop (SQLite) over

Copy your old `sales.db` and the `static/uploads` folder to the server, then:

```bash
docker compose cp sales.db web:/tmp/sales.db
docker compose exec web flask --app app copy-to-postgres /tmp/sales.db
docker compose cp static/uploads/. web:/app/static/uploads/
```

The copy refuses to run if the new database already has customers or orders.

## Updating to a new version

```bash
cd /opt/shop
sh deploy/backup.sh          # always back up first
git pull
docker compose up -d --build
```

Database changes (migrations) are applied automatically when the shop starts.

## Backups

```bash
sh deploy/backup.sh          # writes backups/db_*.dump and backups/uploads_*.tar.gz, keeps 14 days
```

Run it every night with cron (`crontab -e`):

```
15 2 * * * cd /opt/shop && sh deploy/backup.sh >> backups/backup.log 2>&1
```

**Keep a copy off the server.** If the server is lost, backups stored on it are lost too.
For example, use [rclone](https://rclone.org) to copy `backups/` to Google Drive or Backblaze B2 every night.

To restore (this replaces the live data, and the script asks you to type YES):

```bash
sh deploy/restore.sh backups/db_2026-01-31_0215.dump backups/uploads_2026-01-31_0215.tar.gz
```

## Useful commands

```bash
docker compose logs --tail 100 web     # recent shop logs (errors, requests)
docker compose restart web             # restart the shop
docker compose exec db psql -U shop    # database console
docker compose down                    # stop everything (data is kept in volumes)
```

## Security checklist

- [ ] `FLASK_SECRET_KEY` and `POSTGRES_PASSWORD` are long random values, and `.env` is not in git
- [ ] Only ports 22, 80 and 443 are open (`sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable`)
- [ ] SSH logs in with keys, not passwords
- [ ] Automatic security updates: `sudo apt install unattended-upgrades`
- [ ] Nightly backups run, and a copy goes off the server
- [ ] The M-Pesa callback URL in Daraja is `https://YOUR_DOMAIN/mpesa_callback`

## Managed platforms (Render, Railway, Fly.io)

The `Dockerfile` works on these too:

1. Create a PostgreSQL database and set `DATABASE_URL`.
2. Set the other variables from `.env.example`, plus `TRUSTED_PROXY_COUNT=1`, `SESSION_COOKIE_SECURE=1` and `AUTO_MIGRATE=0`.
3. Attach a **persistent disk** at `/app/static/uploads`, or product photos are lost on every deploy.
4. The platform provides HTTPS, so you don't need Caddy.
