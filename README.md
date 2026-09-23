# cig_scraper

Downloads the [Cigarette Pack NFT](https://opensea.io/collection/cigarette-pack) images from IPFS into a Cloudflare R2 bucket, using [Modal](https://modal.com) to run the downloads in parallel.

The bucket feeds the random-cig button on [alexkan.xyz](https://alexkan.xyz) ([akan72/xyz](https://github.com/akan72/xyz)).

## How alexkan.xyz uses the bucket

The site's Worker (`src/lib.rs` in `akan72/xyz`) assumes the following:

| | Value |
|---|---|
| R2 bucket | `cigawrette-packs` (binding `BUCKET` in `wrangler.toml`) |
| Object key | `cig-collection/{id}.jpg` |
| IDs served | `1` to `9996` (`CIG_MIN` / `CIG_MAX`) |
| Content type | served as `image/jpeg` |

`GET /image` returns a random `/cig/{id}` path, and `GET /cig/{id}` streams `cig-collection/{id}.jpg` from R2. Any ID outside `1`–`9996`, or a missing object, returns the site's 404 page.

The scraper writes `cig-collection/{id}.jpg` for IDs `0` to `9997` (`range(0, TOTAL_NFTS)` with `TOTAL_NFTS = 9998`), which covers every ID the site can request. IDs `0` and `9997` are downloaded but never shown. If you change the scraper's range or folder, update `CIG_MIN`, `CIG_MAX` or the key format in the Worker to match.

## How it works

- `main()` splits the IDs into batches of 100 and fans them out with `download_batch.starmap()`.
- Each container mounts the bucket at `/r2` with a `CloudBucketMount` and writes images straight into it.
- An image that already exists in the bucket is skipped, so re-running the scraper only fills in the gaps.
- Each image is tried up to 3 times with exponential backoff (1s, 2s, 4s) before the scraper logs it as failed.
- Modal settings: 300s timeout per batch, 1 retry per batch, at most 20 containers.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A [Modal](https://modal.com) account, logged in with `uv run modal setup`
- A Cloudflare R2 bucket and an R2 API token with **Object Read & Write** permission on that bucket
  - Don't give the token admin permissions, and don't add IP filtering. Modal containers run from changing IPs.
  - See Modal's guide to [mounting Cloudflare R2 buckets](https://modal.com/docs/guide/cloud-bucket-mounts#mounting-cloudflare-r2-buckets).

## Setup

1. Install dependencies:

   ```bash
   git clone https://github.com/akan72/cig_scraper.git
   cd cig_scraper
   uv sync
   ```

2. Configure the bucket. `scraper.py` reads these at import time, so they must be set in the shell that runs `modal run`:

   ```bash
   cp .env.local.template .env.local
   ```

   Fill in `.env.local` (it's gitignored):

   ```bash
   export R2_BUCKET_NAME=cigawrette-packs
   export R2_ACCOUNT_ID=<your-cloudflare-account-id>
   ```

   Then load it:

   ```bash
   source .env.local
   ```

3. Create the Modal secret with the R2 API token's S3 credentials:

   ```bash
   uv run modal secret create cig-r2-credentials \
     AWS_ACCESS_KEY_ID=<r2-access-key-id> \
     AWS_SECRET_ACCESS_KEY=<r2-secret-access-key> \
     AWS_REGION=auto
   ```

## Run

```bash
uv run modal run scraper.py
```

The downloads run on Modal, and progress plus a final success/failure count are logged to your terminal. Any failed IDs are listed at the end. Run the command again to retry them, since existing images are skipped.

## Verify

Count the objects in the bucket with any S3 client pointed at R2, for example the AWS CLI:

```bash
aws s3 ls s3://cigawrette-packs/cig-collection/ \
  --endpoint-url https://<your-cloudflare-account-id>.r2.cloudflarestorage.com | wc -l
```

Then spot-check an image through the site:

```bash
curl -sI https://alexkan.xyz/cig/1
```
