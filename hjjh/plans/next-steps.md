# Next Steps — Deploy Hermes Prime to Fly.io

Prerequisites: a machine with `fly` CLI installed and a GitHub account.

---

## Step 1: Install Fly CLI and authenticate

```bash
# Install
curl -L https://fly.io/install.sh | sh

# Log in (opens browser)
fly auth login
```

Add a payment method if you haven't already:
https://fly.io/dashboard/personal/billing

---

## Step 2: Create the Fly apps

```bash
fly apps create hermes-prime-overseer
fly apps create hermes-prime-hunter
```

---

## Step 3: Create an app-scoped Fly deploy token

This token is scoped to the Hunter app **only** — it cannot touch the
Overseer or any other app in your org.

```bash
fly tokens create deploy --app hermes-prime-hunter
```

Save the output. This becomes `FLY_API_TOKEN`.

---

## Step 4: Create the Hunter GitHub repo

1. Go to https://github.com/new
2. Name: `hermes-hunter-live` (or whatever you prefer)
3. Visibility: **Private** (recommended — this will contain security tooling)
4. **Do not** initialise with README, .gitignore, or license — leave it completely empty
5. Click **Create repository**
6. Note the repo path: `your-username/hermes-hunter-live`

This becomes `HUNTER_REPO`.

---

## Step 5: Create a GitHub fine-grained PAT

1. Go to https://github.com/settings/personal-access-tokens/new
2. Token name: `hermes-overseer`
3. Expiration: 90 days (set a calendar reminder to rotate)
4. Resource owner: your account
5. Repository access → **Only select repositories** → select `hermes-hunter-live`
6. Permissions → Repository permissions:
   - **Contents**: Read and write (for clone + push)
   - **Metadata**: Read (auto-selected)
   - Leave everything else as **No access**
7. Click **Generate token**
8. Copy the token immediately (you won't see it again)

This becomes `GITHUB_PAT`.

### Why fine-grained + single-repo

The Overseer pushes code to the Hunter repo. If the token leaks, the
blast radius is limited to one empty repo that contains only
Overseer-generated code. Your main codebase (`hermes-prime`) and all
other repos are untouched.

---

## Step 6: Get an Elephantasm API key

1. Sign up / log in at Elephantasm
2. Go to your dashboard → API keys
3. Create a new key
4. Copy it

This becomes `ELEPHANTASM_API_KEY`.

> Optional for initial testing. The memory calls are non-fatal — Hermes
> runs fine without it, you just lose cross-session memory.

---

## Step 7: Get an OpenRouter API key

1. Go to https://openrouter.ai/keys
2. Create a new key
3. Add credits ($10–20 is enough for initial testing)
4. Copy the key

This becomes `OPENROUTER_API_KEY`.

---

## Step 8: Choose a ttyd password

Pick or generate a strong password for browser terminal access:

```bash
openssl rand -base64 24
```

This becomes `AUTH_PASSWORD`.

---

## Step 9: Set Fly secrets

```bash
# Overseer secrets (all seven required + auth password)
fly secrets set --app hermes-prime-overseer \
  FLY_API_TOKEN="<from step 3>" \
  HUNTER_FLY_APP="hermes-prime-hunter" \
  GITHUB_PAT="<from step 5>" \
  HUNTER_REPO="your-username/hermes-hunter-live" \
  ELEPHANTASM_API_KEY="<from step 6>" \
  OPENROUTER_API_KEY="<from step 7>" \
  AUTH_PASSWORD="<from step 8>"

# Hunter secrets (subset — only what the Hunter machine needs)
fly secrets set --app hermes-prime-hunter \
  ELEPHANTASM_API_KEY="<from step 6>" \
  OPENROUTER_API_KEY="<from step 7>"
```

---

## Step 10: Deploy

```bash
./scripts/deploy-overseer.sh
```

This will:
1. Create the persistent volume (`overseer_data`, 10GB)
2. Build and push the Hunter Docker image
3. Set `HUNTER_FLY_IMAGE` on the Overseer automatically
4. Deploy the Overseer

---

## Step 11: Connect

Open: `https://hermes-prime-overseer.fly.dev`

Log in with username `hermes` and your password from step 8.

Type `hermes` in the terminal. You're talking to the Overseer.

---

## Security summary

| Credential | Scope | Blast radius if leaked |
|------------|-------|----------------------|
| `FLY_API_TOKEN` | `hermes-prime-hunter` app only | Can create/destroy Hunter machines (ephemeral, disposable) |
| `GITHUB_PAT` | `hermes-hunter-live` repo only | Can read/write one empty repo of generated code |
| `AUTH_PASSWORD` | ttyd login | Browser terminal access (HTTPS only, Fly proxy) |
| `OPENROUTER_API_KEY` | Your OpenRouter account | LLM spend (set spend limits on OpenRouter dashboard) |
| `ELEPHANTASM_API_KEY` | Your Elephantasm account | Memory read/write |

No credential has access to the main `hermes-prime` repo or the Overseer's Fly app.
