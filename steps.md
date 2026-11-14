Add earthdata login credentials in .env file.
export EARTHDATA_USERNAME=<username>
export EARTHDATA_PASSWORD=<passwd>

Run: uv run nisar-fetch auth succeeds:

Once uv run nisar-fetch auth succeeds:

# 1. See what is available
uv run nisar-fetch products --region toronto --start 2026-10-21 --end 2026-11-08

# 2. Preview granules and sizes
uv run nisar-fetch search --region toronto --start 2026-10-21 --end 2026-11-08 --max-results 3

# 3. Confirm what a download would fetch
uv run nisar-fetch download --region toronto --start 2026-10-21 --end 2026-11-08 --limit 1 --dry-run

# 4. Download one GCOV granule (~6 GB)
uv run nisar-fetch download --region toronto --start 2026-10-21 --end 2026-11-08 --limit 1 --max-gb 10