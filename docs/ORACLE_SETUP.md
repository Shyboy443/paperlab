# PaperLab on Oracle Cloud "Always Free"

This guide moves PaperLab (the website, the Trading Desk and MACD live) from Railway to a free Oracle Cloud server.

**Time:** about 45 minutes. Most of it is waiting for Oracle.

**Files used:** everything is in `deploy/oracle/`:

| File | Where it runs | What it does |
|---|---|---|
| `setup-server.sh` | once, on the server | Installs Docker, opens ports 80 and 443, prepares `/opt/paperlab` |
| `export-env.ps1` | once, on your PC | Copies your Railway settings into a local file, without showing any value |
| `push.ps1` | on your PC, every time you update | Uploads the code and (re)starts PaperLab |
| `docker-compose.yml`, `Caddyfile` | uploaded by `push.ps1` | PaperLab, plus Caddy for free HTTPS |

You do the account steps (1 to 5) yourself, because they need your card and your login. Steps 6 to 9 are one command
each. You can run them, or tell me the server's IP and I'll guide you.

---

## Before you start: your old data

Railway stopped PaperLab when the trial expired, so its stored data can't be reached without paying Railway again. That
data includes desk history, MACD live's settings and trade log, linked accounts, saved API keys, and the Telegram link.

- **Start fresh (simplest).** PaperLab starts empty on Oracle:
  - the paper desks start new forward books;
  - you re-enter your Binance key and Telegram token once (step 9);
  - you switch MACD live on again.
  - Nothing financial is lost: your Binance account is flat.
- **Keep the data.** Pay Railway for one month (Hobby, $5) so the volume can be copied, then cancel. Ask me first; the
  copy needs a working `railway ssh`.

---

## 1. Create the Oracle account

1. Go to <https://www.oracle.com/cloud/free/> and click **Start for free**.
2. **Home region:** pick one near you that has Ampere capacity, for example *Singapore*, *Mumbai* or *Frankfurt*. This
   choice can't be changed later.
3. Card verification: Oracle places a small temporary hold. Always Free resources cost nothing.
4. **Recommended:** after the account is active, open *Billing → Upgrade and manage payment* and switch to
   **Pay As You Go**.
   - Always Free resources stay free.
   - Oracle stops reclaiming "idle" free servers. A Pay As You Go account isn't subject to that clean-up, and
     PaperLab is mostly idle CPU.
   - You get better odds of Ampere capacity.
   - Add a **budget alert** of $1 (*Billing → Budgets*) so you're warned if anything ever costs money.

## 2. Make an SSH key on your PC

In PowerShell:

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\oracle_paperlab -N '""'
Get-Content $env:USERPROFILE\.ssh\oracle_paperlab.pub
```

Copy the line it prints. That's the **public** key. The private key never leaves your PC.

## 3. Create the server

*Menu → Compute → Instances → Create instance*:

- **Name:** `paperlab`
- **Image:** *Change image* → **Canonical Ubuntu 24.04** (the Arm/aarch64 version is picked automatically for Ampere)
- **Shape:** *Change shape* → *Ampere* → **VM.Standard.A1.Flex**, **2 OCPU, 12 GB memory**
  - This is free. If the console still shows 4 OCPU / 24 GB as free, that works too.
- **Networking:** *Create new virtual cloud network* and *Create new public subnet*, with **Assign a public IPv4
  address** ticked
- **Add SSH keys:** *Paste public keys*, then paste the line from step 2
- **Boot volume:** 50 GB (up to 200 GB is free)
- Click **Create**. After 1 to 2 minutes the instance is *Running*. Note its **Public IP address**.

*"Out of capacity"?* Try another *availability domain* in the same screen, or try again later; it usually clears within
hours. Pay As You Go accounts get capacity more easily.

**Optional, so the IP never changes:** *Instance → Attached VNICs → IPv4 addresses → Edit*, and set *Reserved public IP*
(free).

## 4. Open the web ports in Oracle's firewall

*Instance → Primary VNIC → Subnet → Security Lists → Default Security List → Add Ingress Rules*. Add two rules:

| Source CIDR | IP Protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

Port 22 (SSH) is already open. `setup-server.sh` opens the same two ports inside the server's own firewall.

## 5. Restrict your Binance API key to the server's IP (recommended)

On Binance: *API Management → your key → Edit restrictions → Restrict access to trusted IPs only*. Add the server's
public IP. A stolen copy of the key is then useless anywhere else. Railway had no fixed IP, so this wasn't possible
before.

## 6. Set up the server (one time)

In PowerShell, from the project folder `D:\Projects\BinanceBOt\paperlab`, with your IP in place of `IP`:

```powershell
scp -i $env:USERPROFILE\.ssh\oracle_paperlab deploy\oracle\setup-server.sh ubuntu@IP:~
ssh -i $env:USERPROFILE\.ssh\oracle_paperlab ubuntu@IP "bash setup-server.sh IP"
```

It ends with **"The site will be: https://IP-WITH-DASHES.sslip.io"**. That's your free web address. `sslip.io` turns
the name back into your IP, and Caddy gets a real HTTPS certificate for it. You can switch to your own domain later:
edit `/opt/paperlab/.env` on the server.

## 7. Copy your settings from Railway (one time)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\oracle\export-env.ps1
```

- It saves `%USERPROFILE%\paperlab-oracle\paperlab.env` and prints only the setting **names**. This file is outside the
  project, so it can't reach git.
- It includes `DASHBOARD_PASSWORD`, so the dashboard password stays the same.
- Railway still lets you read the settings with an expired trial. If it ever refuses, ask me for a template to fill in
  by hand.

## 8. Upload and start PaperLab

```powershell
powershell -ExecutionPolicy Bypass -File deploy\oracle\push.ps1 -Ip IP -Key $env:USERPROFILE\.ssh\oracle_paperlab
```

This first run takes about 5 minutes, because it builds the image on the server. Then open:

- **Public site:** `https://IP-WITH-DASHES.sslip.io/public/competition`
- **Private dashboard:** `https://IP-WITH-DASHES.sslip.io/`, with the same password as on Railway
- **Health:** `https://IP-WITH-DASHES.sslip.io/public/health/deep`

The certificate is fetched on the first visit. If the first load fails, wait 30 seconds and reload.

## 9. Re-enter what lived only in the old data (start fresh only)

On the private dashboard:

1. **System → Providers & Live:** save your Binance **mainnet** key, plus the testnet key if you use one. Save the
   Telegram bot token (and link it again with `/start` in Telegram), and the Alpaca keys if you used them.
2. **Trading Desk → MACD → Live trading:** press *Use it for desks* to link the saved mainnet key. Then switch MACD live
   on with your amount and loss limit, and type `GO LIVE MACD ZEC`. Mainnet needs a passed testnet round trip;
   on a fresh start, run it once from the same panel with a linked testnet key.

---

## Every day

- **Update after code changes:** run the step 8 command again. It uploads the code only and keeps the data. Add
  `-Settings` to also upload a changed settings file.
- **See the logs:**
  ```powershell
  ssh -i $env:USERPROFILE\.ssh\oracle_paperlab ubuntu@IP "cd /opt/paperlab && docker compose logs --tail 100 paperlab"
  ```
- **Restart:**
  ```powershell
  ssh -i $env:USERPROFILE\.ssh\oracle_paperlab ubuntu@IP "cd /opt/paperlab && docker compose restart paperlab"
  ```
- **Back up the data to your PC:**
  ```powershell
  ssh -i $env:USERPROFILE\.ssh\oracle_paperlab ubuntu@IP "sudo tar -czf /tmp/paperlab-data.tgz -C /opt/paperlab data"
  scp -i $env:USERPROFILE\.ssh\oracle_paperlab ubuntu@IP:/tmp/paperlab-data.tgz $env:USERPROFILE\paperlab-oracle\
  ```

**Automatic behaviour:**
- PaperLab and Caddy restart by themselves after a crash or a server reboot (`restart: unless-stopped`).
- Ubuntu installs security updates automatically (`unattended-upgrades`).

## Good to know

- **Cost:** €0 within Always Free.
  - Always Free covers 2 OCPU / 12 GB Ampere (reportedly 4 / 24 on older accounts), 200 GB of disk and 10 TB/month of
    outbound traffic.
  - PaperLab uses a small fraction of that now that the bots are off.
- **Idle reclaim:** Oracle may stop a *free-tier-only* server if it stays nearly idle for 7 days. The upgrade to Pay As
  You Go in step 1 avoids that.
- **Arm processor:** PaperLab's Python packages all have Arm builds. I haven't built this exact image on Arm yet, so if
  the first `push.ps1` reports a build error, send it to me.
- **Uptime:** there's no automatic failover. If the server stops, MACD live stops too. Its exchange-side 30% stop stays on
  Binance, so an open position is still protected.
