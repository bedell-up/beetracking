# Bee Cam Central Gallery — Setup

## 1. Server side (VPS, bee.pscapps.com)

1. Copy `server/bee_gallery.py` and `server/templates/bee_gallery.html` into your
   existing bee.pscapps.com Flask app (templates folder can be merged with
   whatever you already use, Flask will find both).

2. Generate 8 random API keys (one per station) and fill them into a real
   copy of `stations.example.json` → save as e.g. `/etc/beecam/stations.json`
   (keep it out of the git repo — it's a secrets file).

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(24))"
   ```

3. In your app's `__init__.py` / main entrypoint, register the blueprint:

   ```python
   from bee_gallery import bee_gallery_bp, init_gallery

   init_gallery(
       app,
       storage_root="/var/www/bee_images",   # pick a disk with room for a season of full-res photos
       stations_file="/etc/beecam/stations.json",
   )
   app.register_blueprint(bee_gallery_bp)
   ```

4. Make sure the directory you pick for `storage_root` is owned by whatever
   user runs Gunicorn, and has enough disk — full-res images across 8
   stations for a season add up, so check `df -h` periodically.

5. Nginx: raise the upload size limit (default is 1MB, too small for full-res
   photos) in the bee.pscapps.com server block:

   ```nginx
   client_max_body_size 20M;
   ```

   Reload nginx after: `sudo systemctl reload nginx`.

6. Restart the Gunicorn service for bee.pscapps.com to pick up the blueprint.

7. Visit `https://bee.pscapps.com/gallery` — it'll be empty until step 2 below
   is running on at least one Pi.

## 2. Pi side (each of the 8 stations)

1. Copy `pi-uploader/` to each Pi, e.g. `/home/pi/beecam-uploader/`.

2. `pip install requests pillow`

3. Copy `config.example.ini` → `config.ini`, fill in that station's letter,
   its API key (matching what you put in `stations.json`), and the directory
   the camera actually writes images to.

4. Test it manually first:

   ```bash
   python3 uploader.py --config config.ini
   ```

   Watch the log output — you should see thumbnail uploads start
   immediately, then full-res uploads trickle in a few at a time.

5. Once it's working, install as a systemd service so it survives reboots:

   ```bash
   sudo cp beecam-uploader.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now beecam-uploader
   ```

   Repeat for all 8 Pis, swapping in each station's own `config.ini`.

## Notes

- If a Pi's network drops for a while, the uploader just keeps retrying
  with backoff (up to 1 hr between attempts) — nothing is lost, and it
  catches up automatically once connectivity returns.
- Thumbnails always take priority over full-res, so the gallery stays
  current even if the full-res queue is backed up.
- Images without a full-res upload yet show a "thumb only" badge in the
  gallery; clicking them just won't open a full-res view until it arrives.
- `MAX_FULL_PER_CYCLE` and `POLL_INTERVAL` in `uploader.py` are the two
  knobs to tune if the IoT link turns out to be more/less constrained than
  expected.
