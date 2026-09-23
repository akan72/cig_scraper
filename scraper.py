import os
import logging
import modal
import requests
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = modal.App("cig-scraper")

# Constants
IPFS_BASE_URL = "https://ipfs.io/ipfs/bafybeigvhgkcqqamlukxcmjodalpk2kuy5qzqtx6m4i6pvb7o3ammss3y4"
TOTAL_NFTS = 9998  # Downloads IDs 0-9997
BATCH_SIZE = 100
SUBFOLDER = "cig-collection"  # Images stored as cig-collection/0.jpg, etc.

cig_r2_credentials = modal.Secret.from_name(
    "cig-r2-credentials",
    required_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
    ]
)

# Image to use
image = (
    modal.Image.debian_slim().pip_install("requests").env({
        "R2_BUCKET_NAME": os.environ["R2_BUCKET_NAME"],
        "R2_ACCOUNT_ID": os.environ["R2_ACCOUNT_ID"]
    })
)

# Configure CloudBucketMount for R2
r2_mount = modal.CloudBucketMount(
    bucket_name=os.environ["R2_BUCKET_NAME"],
    bucket_endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
    secret=cig_r2_credentials
)

@app.function(
    image=image,
    volumes={"/r2": r2_mount},
    timeout=300,  # 5 min timeout per batch
    retries=1,
    max_containers=20,
)
def download_batch(start_id: int, end_id: int, batch_num: int):
    """Download a batch of NFT images and save to R2."""

    results = {"success": [], "failed": [], "batch_num": batch_num}

    for nft_id in range(start_id, end_id):
        url = f"{IPFS_BASE_URL}/{nft_id}.jpg"
        output_path = Path(f"/r2/{SUBFOLDER}/{nft_id}.jpg")

        # Ensure subfolder exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Skip if already exists
        if output_path.exists():
            logger.info(f"NFT {nft_id} already exists, skipping")
            results["success"].append(nft_id)
            continue

        # Download with retry logic
        logger.info(f"Attempting to download NFT {nft_id}")
        success = download_with_retry(url, output_path, nft_id)
        if success:
            results["success"].append(nft_id)
        else:
            results["failed"].append(nft_id)

    return results


def download_with_retry(url: str, output_path: Path, nft_id: int, max_retries: int = 3):
    """Download a single image with exponential backoff retry."""
    import time

    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            output_path.write_bytes(response.content)
            logger.info(f"Successfully downloaded NFT {nft_id}")
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 1, 2, 4 seconds
                logger.warning(f"NFT {nft_id} download failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                logger.error(f"Failed to download NFT {nft_id} after {max_retries} attempts: {e}")
                return False
    return False


@app.local_entrypoint()
def main():
    """Orchestrate the parallel batch downloads."""
    batches = []
    for i, start in enumerate(range(0, TOTAL_NFTS, BATCH_SIZE)):
        end = min(start + BATCH_SIZE, TOTAL_NFTS)
        batches.append((start, end, i + 1, -1))  # batch_num is 1-indexed

    total_batches = len(batches)
    # Update total_batches in all tuples
    batches = [(s, e, bn) for s, e, bn, _ in batches]

    logger.info(f"Starting download of {TOTAL_NFTS} NFTs in {total_batches} batches...")

    # Run all batches in parallel using Modal's .starmap()
    total_success = 0
    total_failed = 0
    failed_ids = []

    for result in download_batch.starmap(batches):
        batch_success = len(result["success"])
        batch_failed = len(result["failed"])
        total_success += batch_success
        total_failed += batch_failed
        failed_ids.extend(result["failed"])

        logger.info(f"Completed batch {result['batch_num']}/{total_batches} "
                    f"({total_success + total_failed}/{TOTAL_NFTS} images processed)")

    logger.info(f"Download complete: {total_success} succeeded, {total_failed} failed")

    if total_failed > 0:
        logger.warning(f"Failed IDs: {failed_ids}")
