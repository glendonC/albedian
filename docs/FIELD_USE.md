# Field use: running Albedian on the laptop away from home

Albedian runs entirely on the laptop. Upload, plate solve, the revealed sky, the catalog stars, the 3D views and
the audio need no internet [tested by code audit: no runtime request leaves the machine on those paths].

## Before you leave (with internet)

1. From the repository root, once: `pnpm install` and `uv sync`.
2. Run `pnpm field` once and open the laptop address below, so the browser has the web fonts cached
   [inference: the browser cache may keep them; without them the page uses system fonts].
3. If you will use a phone, run `pnpm field --lan` once on your home network to check that the build passes and
   the phone opens the app [proposed].

## Start and stop

```bash
pnpm field
```

- It starts the API on `127.0.0.1:8000` and the web app on `localhost:5173`, then prints
  `Albedian is ready` with the laptop address, `http://localhost:5173` [tested].
- Open that address on the laptop. The laptop is the full experience.
- Stop both with Ctrl+C [tested]. If either one stops on its own, the other stops too [inference from code].
- If a port is taken, it says so and starts nothing; if the API does not answer within 90 seconds, it stops
  both and says so [inference from code].
- To keep the laptop awake while it runs, start it as `caffeinate -i pnpm field` (macOS) [inference: closing
  the lid still sleeps it].
- Other ports: `pnpm field --api-port 8100 --web-port 5180`.
- The API runs without its access log, so no request's query string is printed in the terminal [tested].

## Phone on the same network (opt-in)

```bash
pnpm field --lan
```

Use `--lan` only on a network you trust, such as the phone's own hotspot with the laptop joined to it, never on
hotel, airport or cafe Wi-Fi, and stop it when you are done.

What it does:

- It first builds the web app (26 seconds on this laptop [tested]), then serves only that build, never the
  development server. If the build fails, it says so and starts nothing [inference from code].
- The web app listens on every network interface of the laptop (`0.0.0.0`), not only the address it prints
  [tested].
- It prints a second address, `http://<laptop network address>:5173`, to open on the phone [tested]. On the
  laptop keep using `http://localhost:5173`: the laptop's own network address counts as the network.
- The API stays on `127.0.0.1`. The network reaches it only through the web app's `/api` proxy [tested].

What any device on that network can do while it runs:

- Open the app, upload photos and clips, start solves and send voice commands. With live Claude on
  (`ALBEDIAN_ENABLE_LIVE_CLAUDE=1`), each voice command is a paid call on your key [inference from code].
- Call every API route but one through the proxy, from a browser or from a script. The API accepts the laptop
  addresses and exactly one more origin, the phone address, but that check stops only browser pages: a script
  sends no origin and passes it [inference from code].
- Read back an upload's record, original and solution by its content hash. A device knows a hash only for a file
  it uploaded or was given [inference from code].

What it cannot get:

- Any file outside the built app (the app's code and the catalog, model and texture files it ships with): not
  `data/uploads`, not `data/ingest`, not notes or settings, by file path or any other path [tested with a planted
  file in a copy of the repository layout].
- The list of the clips in `data/uploads` (media discover). Every device but the laptop itself is refused it; the
  laptop keeps it [tested].

Also:

- macOS may ask whether to accept incoming connections for `node`; allow it for `--lan` to work [inference].
- The laptop needs a private network address (10.x, 172.16 to 172.31, or 192.168.x); without one, `--lan`
  says so and starts nothing [tested].

### What the phone gets over plain http

The phone opens a plain `http://` network address, so its browser does not treat the page as a secure context
[inference: browsers treat only https and localhost as secure]. On the phone that means:

- No WebGPU views (the browser hides WebGPU outside a secure context).
- No microphone and no speech push-to-talk.
- Upload from the phone, solving, and the views that do not need WebGPU should work [inference: not yet
  checked on a phone].

The laptop at `http://localhost:5173` is the full experience.

## With no internet at all

- Fonts: the page paints at once in system fonts; the web fonts load only when the internet is there
  [tested: the font stylesheet no longer blocks the first paint].
- Speech push-to-talk in Chrome sends audio to a speech service, so it needs the internet [inference]. Type the
  command in the command bar instead.
- The Claude voice reading is off unless `ALBEDIAN_ENABLE_LIVE_CLAUDE=1` and `ANTHROPIC_API_KEY` are set. With it
  on and no internet, the built-in rules answer instead [inference from code].
- No ephemeris or catalog is fetched while the app runs: the planet positions are computed on the laptop and the
  star catalog ships with the app [tested by code audit].
