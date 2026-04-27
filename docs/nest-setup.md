# Nest camera setup (optional, advanced)

If you have Nest cameras (Gen-2 battery or wired), sprinkler-ai can grab a daylight
snapshot and pipe it through Claude Vision for lawn-health analysis. The result is
cached and fed into the next morning's irrigation plan.

This is **optional and fiddly**. Skip it unless you genuinely want camera-assisted
plans — the system works fine without it.

## What you need

1. A **Google Device Access** project — one-time **$5 USD fee** (Google charges this
   to deter casual abuse).
2. A **Google Cloud OAuth client** (free).
3. Cameras already added to the **Google Home** app.

## Walkthrough

### 1. Enable the Smart Device Management API

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a
   project (or pick an existing one).
2. **APIs & Services → Library** → search for **Smart Device Management API** → Enable.

### 2. Create OAuth credentials

1. **APIs & Services → OAuth consent screen** → choose "External" → create.
   Fill in app name + your email as developer/support contacts. Add yourself as a
   test user.
2. **APIs & Services → Credentials → Create Credentials → OAuth Client ID**.
   Type: **Web application**. Authorized redirect URI:
   `https://www.google.com` (yes, the literal URL — easiest for the manual flow).
3. Save the Client ID and Client Secret. Add to `.env`:
   ```
   NEST_CLIENT_ID=<...>.apps.googleusercontent.com
   NEST_CLIENT_SECRET=GOCSPX-<...>
   ```

### 3. Pay $5 and create a Device Access project

1. Go to [console.nest.google.com/device-access](https://console.nest.google.com/device-access).
2. Pay the one-time fee. Create a project. Use the OAuth Client ID from step 2.
3. Note the **Project ID** (a UUID). Add to `.env`:
   ```
   NEST_PROJECT_ID=<uuid>
   ```

### 4. Get a refresh token

This is the only manual step. You need to do an OAuth dance once to get a refresh
token, then sprinkler-ai uses it forever (until revoked).

1. Open this URL in your browser, replacing `<NEST_PROJECT_ID>` and `<NEST_CLIENT_ID>`:
   ```
   https://nestservices.google.com/partnerconnections/<NEST_PROJECT_ID>/auth?redirect_uri=https://www.google.com&access_type=offline&prompt=consent&client_id=<NEST_CLIENT_ID>&response_type=code&scope=https://www.googleapis.com/auth/sdm.service
   ```
2. Approve the consent screen. You'll land on `google.com?code=4/0AeaY...&scope=...`.
   Copy the `code=` parameter (it's URL-encoded; decode it once if it has `%2F`).
3. Exchange the code for a refresh token (replace placeholders):
   ```bash
   curl -s -X POST https://oauth2.googleapis.com/token \
     -d client_id=<NEST_CLIENT_ID> \
     -d client_secret=<NEST_CLIENT_SECRET> \
     -d code=<the code from step 2> \
     -d grant_type=authorization_code \
     -d redirect_uri=https://www.google.com
   ```
4. The response JSON contains `refresh_token`. Save it to `.env`:
   ```
   NEST_REFRESH_TOKEN=1//0...<...>
   ```

### 5. Map cameras to zones in `config.yaml`

```yaml
camera_targets:
  - room_keyword: backyard       # case-insensitive substring of the camera's "Room"
    label: backyard-cam
    covers_zones: [4, 5]
  - room_keyword: front
    label: front-cam
    covers_zones: [1, 2, 7]

camera_descriptions:
  backyard-cam: |
    My patio is the one with the dark fire-pit table. The lawn between the patio
    and the wooden posts at the back is mine. Anything past the posts is the
    neighbor's.
  front-cam: |
    My front lawn is between the driveway (mine) and the sidewalk in the distance.

invisible_zones_note: "Zones 3 and 6 (the side yards) are not visible to any camera."
```

The `camera_descriptions` block is **important**. Without it the model often
mistakes neighbors' lawns for yours and reports problems on someone else's grass.

### 6. Test

```bash
.venv/bin/sprinkler-ai --vision-snapshot
```

This grabs a frame from each mapped camera, runs Claude vision, and writes
`data/vision_cache.json`. The next regular `sprinkler-ai` run picks it up.

If you want this to run automatically each morning before the planning job:

```bash
mkdir -p ~/.config/systemd/user
cp contrib/sprinkler-ai-vision.service ~/.config/systemd/user/
cp contrib/sprinkler-ai-vision.timer   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now sprinkler-ai-vision.timer
```

The timer fires at 8:30 am — cameras get good light, the cache is fresh by the
time the irrigation timer runs the following 4 am.

## Common gotchas

- **"403 Forbidden" from SDM API** — the camera isn't shared into the Device
  Access project. Re-do step 4 (the consent dance) and tick the camera you want.
- **"video track never arrived"** — Nest sometimes refuses WebRTC for a few
  minutes after a previous stream. Wait, retry.
- **Cameras keep dying** — battery cameras don't support continuous streaming.
  We open one stream per camera per day for ~5 seconds; impact on battery is minor.
