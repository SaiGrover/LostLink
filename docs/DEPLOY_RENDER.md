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
   | Pre-deploy command | `python manage.py migrate --noinput` |
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

The repo's `.python-version` chooses Python 3.12 on Render. Leave the generated
secret out of GitHub. For a custom domain, add its hostname to
`DJANGO_ALLOWED_HOSTS` and its HTTPS origin to
`DJANGO_CSRF_TRUSTED_ORIGINS` (comma-separated values).

## First launch

1. Wait for the build, migration and start commands to succeed.
2. Use Render Shell to run `python manage.py createsuperuser` with a strong,
   unique password. The Django admin URL is `/django-admin/`.
3. Run `python manage.py check --deploy` in the Shell. Check any warnings against
   the Render proxy and this project's settings.
4. Test the live signup, lost/found reporting with an image, private image
   access, matching, claim verification, and moderation. Restart the service
   and confirm the uploaded image is still available to its authorized users.

Static assets are collected during the build and served by WhiteNoise. Uploaded
images are **not** served by WhiteNoise: the existing `/media/` Django view
checks access rights before returning them. The first model request may download
pretrained weights to `HF_HOME`; allow for startup time and disk space.

## References

- [Render Django guide](https://render.com/docs/deploy-django)
- [Render deploy commands](https://render.com/docs/deploys)
- [Render persistent disks](https://render.com/docs/disks)
- [Render Python version](https://render.com/docs/python-version)
