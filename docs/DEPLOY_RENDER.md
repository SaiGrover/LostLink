# Deploy LostLink from GitHub to Render

GitHub hosts the source code; Render runs the Django application. Use a new
production PostgreSQL database. Do not import the local demo database or run
`seed_demo` in production: the README documents its test credentials.

## Create the services

1. Push the latest `main` branch to `SaiGrover/LostLink`.
2. In Render, create a **paid PostgreSQL** database. Keep its **internal database
   URL** for the web service.
3. Create a **paid Python web service** from the GitHub repository, branch
   `main`, with these commands:

   | Setting | Value |
   | --- | --- |
   | Build command | `bash build.sh` |
   | Pre-deploy command | `python manage.py migrate --noinput && python manage.py seed_taxonomy --if-empty` |
   | Start command | `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --timeout 180` |

4. Attach a persistent disk mounted at `/var/data`. Only one web instance may
   use the disk. Item images will live under `/var/data/media`; model cache
   files will live under `/var/data/huggingface`.

Render's free web service does not support a persistent disk. Uploaded photos
would disappear on restart or redeploy, so it is unsuitable for LostLink's
normal report workflow. The selected ResNet profile also needs enough memory
for PyTorch and the model; check the service metrics after the first match.

## Environment variables

Set these in the Render web service's **Environment** page:

| Key | Value |
| --- | --- |
| `DJANGO_DEBUG` | `false` |
| `DJANGO_SECRET_KEY` | A generated, private random value; at least 50 characters |
| `DATABASE_URL` | The Render PostgreSQL internal database URL |
| `DJANGO_ALLOWED_HOSTS` | Your exact Render hostname, e.g. `lostlink.onrender.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Its HTTPS origin, e.g. `https://lostlink.onrender.com` |
| `LOSTLINK_MEDIA_ROOT` | `/var/data/media` |
| `HF_HOME` | `/var/data/huggingface` |
| `LOSTLINK_ALLOWED_EMAIL_DOMAINS` | The institution's email domain(s), e.g. `college.edu` (subdomains included). **Required**: the app refuses to start with it empty (`accounts.E001`) unless `LOSTLINK_REQUIRE_CAMPUS_DOMAIN=false` is set for a deliberately open deployment |
| `DJANGO_EMAIL_HOST` | SMTP server of the institution's or a transactional mail provider |
| `DJANGO_EMAIL_PORT` | Usually `587` (STARTTLS) or `465` with `DJANGO_EMAIL_USE_SSL=true` |
| `DJANGO_EMAIL_HOST_USER` / `DJANGO_EMAIL_HOST_PASSWORD` | SMTP credentials, entered only in Render's secret environment |
| `DJANGO_DEFAULT_FROM_EMAIL` | Sender, e.g. `LostLink <no-reply@college.edu>` |
| `LOSTLINK_PUBLIC_URL` | `https://lostlink.onrender.com` (base of the links in emails) |
| `LOSTLINK_TRUSTED_PROXY_COUNT` | `1` behind Render's proxy, after checking the header (see below); `0` ignores `X-Forwarded-For` |
| `REDIS_URL` or `LOSTLINK_CACHE=database` | Shared cache so rate limits hold across workers (with the database cache add `python manage.py createcachetable` to the pre-deploy command) |

The operator supplies the campus domain and the SMTP credentials; neither
belongs in the repository. Without SMTP every sign-up stays unverified and no
one can reset a password: `manage.py check` warns `accounts.W001`.

**Client addresses for rate limits.** The first value of `X-Forwarded-For` can
be written by the client, so it is never used. With
`LOSTLINK_TRUSTED_PROXY_COUNT=n`, the address added by the n-th proxy from the
right is used; with `0` the connection address is used. To confirm Render's
behaviour, send a request with a made-up `X-Forwarded-For: 203.0.113.9` header
and check (for example in a temporary log line or the Render request logs)
that the right-most entry is your real address; only then set it to `1`.

The settings refuse to start in production (`DJANGO_DEBUG=false`) when the
secret key is shorter than 50 characters or is the development key, when
`DJANGO_ALLOWED_HOSTS` is unset or `*`, when a CSRF origin is not `https://`,
when `DATABASE_URL` is unset, or when the media directory overlaps the static
directories. Because Render sets `RENDER`, leaving `DJANGO_DEBUG` unset is also
an error rather than a silent debug deployment. Optional: `LOSTLINK_UPLOAD_MAX_MB`
(default 5), `DJANGO_HSTS_SECONDS`, `DJANGO_SECURE_SSL_REDIRECT`.

The repo's `.python-version` chooses Python 3.12 on Render. Leave the generated
secret out of GitHub. For a custom domain, add its hostname to
`DJANGO_ALLOWED_HOSTS` and its HTTPS origin to
`DJANGO_CSRF_TRUSTED_ORIGINS` (comma-separated values).

## First launch

After any deploy that changes `matching/profile.json` (the engine version is
currently `synopsis-v3`), run `python manage.py rebuild_matches` in the Render
Shell. Until then, matches from the previous version are hidden rather than
shown with outdated scores.

1. Wait for the build, migration and start commands to succeed. The
   pre-deploy command creates the default categories and campus zones on an
   empty database (`seed_taxonomy --if-empty` creates no accounts and never
   changes existing rows). Never run `seed_demo` here; it refuses to run with
   `DJANGO_DEBUG=false`.
2. Use Render Shell to run `python manage.py createsuperuser` with a strong,
   unique password. The Django admin URL is `/django-admin/`.
3. Run `python manage.py check --deploy` in the Shell. Check any warnings against
   the Render proxy and this project's settings.
4. **Launch checks for email (required before real users):**
   - `python manage.py sendtestemail you@college.edu` in the Render Shell
     delivers a message;
   - register a new account with a campus address and open the verification
     link from the real inbox; an address outside the allowed domains is
     refused;
   - request a password reset for that account and complete it from the email.
5. Test lost/found reporting with an image, private image access, matching,
   claim verification with a one-time handover code, and moderation. Restart
   the service and confirm the uploaded image is still available to its
   authorized users.

Before the first real users, `python scripts/smoke_first_run.py` (run locally,
with `DATABASE_URL` unset) exercises the same first-run path on a temporary
database. Steps that need SMTP are listed as `external` and are not run.

## Uploaded media and privacy

Static assets are collected during the build and served by WhiteNoise. Uploaded
images are **not** served by WhiteNoise or the web server: the `/media/` Django
view (`items/media.py`) applies a deny-by-default rule:

- The path is normalised; `..`, `.`, empty segments, backslashes, absolute
  paths and NUL bytes are a 404.
- Only prefixes with a registered rule are served. `items/` is the only one:
  the file must be the current photo of a report the viewer may see (public
  once approved; otherwise reporter, claim participants and staff). Replaced
  photos, orphaned files and photos of deleted reports are never served.
- Sensitive photos reach the reporter, staff and a claimant whose claim a
  moderator approved; everyone else who may see the report gets a blurred,
  low-resolution preview generated on the fly.
- Any other prefix, e.g. future claim evidence under `claims/`, is private
  until its app calls `items.media.register_media_rule('claims', rule)` with an
  explicit authorisation rule.
- Responses send `Cache-Control: private, no-store`, `Vary: Cookie`,
  `X-Content-Type-Options: nosniff`, an inline `Content-Disposition` with a
  neutral name, `Content-Security-Policy: default-src 'none'; sandbox` and
  `Cross-Origin-Resource-Policy: same-origin`. The content type comes from the
  file signature; a stored non-image is refused.

New uploads are re-encoded on arrival. For photos uploaded before this was in
place, run `python manage.py sanitize_photos --dry-run`, then without
`--dry-run` (add `--restrict-suggested` to blur existing ID-card-like photos
until a moderator reviews them). The first model request may download
pretrained weights to `HF_HOME`; allow for startup time and disk space.

## References

- [Render Django guide](https://render.com/docs/deploy-django)
- [Render deploy commands](https://render.com/docs/deploys)
- [Render persistent disks](https://render.com/docs/disks)
- [Render Python version](https://render.com/docs/python-version)
