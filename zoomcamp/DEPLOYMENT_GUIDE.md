# BigQuery & Bruin Cloud Deployment Guide

Your pipeline has been converted to BigQuery. Follow these steps to deploy.

## Step 1: Set Up GCP Project

### 1.1 Create/Select GCP Project
```bash
# List existing projects
gcloud projects list

# Or create a new one
gcloud projects create YOUR-PROJECT-ID --name="NYC Taxi Pipeline"
```

### 1.2 Enable BigQuery API
```bash
gcloud services enable bigquery.googleapis.com --project=YOUR-PROJECT-ID
```

### 1.3 Create BigQuery Datasets
```bash
# Set your project
gcloud config set project YOUR-PROJECT-ID

# Create datasets
bq mk --dataset --location=US ingestion
bq mk --dataset --location=US staging
bq mk --dataset --location=US reports
```

## Step 2: Configure Authentication

### Option A: Application Default Credentials (Recommended for Local)
```bash
gcloud auth application-default login
```

This is already configured in `.bruin.yml` with:
```yaml
use_application_default_credentials: true
```

### Option B: Service Account (For CI/CD)
1. Create service account:
```bash
gcloud iam service-accounts create bruin-pipeline \
    --display-name="Bruin Pipeline Service Account"
```

2. Grant BigQuery permissions:
```bash
gcloud projects add-iam-policy-binding YOUR-PROJECT-ID \
    --member="serviceAccount:bruin-pipeline@YOUR-PROJECT-ID.iam.gserviceaccount.com" \
    --role="roles/bigquery.admin"
```

3. Download key:
```bash
gcloud iam service-accounts keys create ~/bruin-sa-key.json \
    --iam-account=bruin-pipeline@YOUR-PROJECT-ID.iam.gserviceaccount.com
```

4. Update `.bruin.yml`:
```yaml
google_cloud_platform:
  - name: "gcp-default"
    project_id: "YOUR-PROJECT-ID"
    location: "US"
    service_account_file: "/path/to/bruin-sa-key.json"
```

## Step 3: Update Configuration

**Edit `.bruin.yml`** and replace `your-gcp-project-id` with your actual project ID:
```yaml
project_id: "YOUR-ACTUAL-PROJECT-ID"
```

## Step 4: Test Locally with BigQuery

```bash
# Validate the pipeline
bruin validate zoomcamp/pipeline

# Run the pipeline (this will use BigQuery now)
bruin run zoomcamp/pipeline --start-date 2022-01-01 --end-date 2022-02-01
```

## Step 5: Deploy to Bruin Cloud

### 5.1 Sign Up
1. Go to [getbruin.com](https://getbruin.com)
2. Click **Sign Up**
3. Complete onboarding

### 5.2 Connect Repository
1. During onboarding, connect your GitHub repository
2. Select the repo containing this pipeline
3. Complete setup

### 5.3 Configure Secrets (if using Service Account)
If you're using a service account, you'll need to add the credentials to Bruin Cloud:
1. Navigate to **Settings** → **Secrets**
2. Add your service account JSON as a secret
3. Reference it in your `.bruin.yml`

### 5.4 Enable and Run
1. Go to **Pipelines** page
2. Find `nyc-taxi-pipeline`
3. Click to enable it
4. Create a run to test

## What Changed?

### Files Modified:
- ✅ **`.bruin.yml`** - Created with GCP connection
- ✅ **`pipeline/pipeline.yml`** - Changed from `duckdb: duckdb-default` to `bigquery: gcp-default`
- ✅ **`pipeline/assets/ingestion/trips.py`** - Changed connection to `gcp-default`
- ✅ **`pipeline/assets/staging/trips.sql`** - Changed type to `bq.sql`, updated MD5/CONCAT syntax
- ✅ **`pipeline/assets/reports/trips_report.sql`** - Changed type to `bq.sql`
- ✅ **`pipeline/assets/ingestion/payment_lookup.asset.yml`** - Changed type to `bq.seed`

### SQL Dialect Changes:
- `MD5()` → `TO_HEX(MD5())` (BigQuery returns bytes, need hex string)
- `VARCHAR` → `STRING`
- Rest of the SQL is compatible

## Switching Back to DuckDB

To switch back to local DuckDB development:

1. Edit `pipeline/pipeline.yml`:
```yaml
default_connections:
  duckdb: duckdb-default
  # bigquery: gcp-default
```

2. Change asset types back:
   - `bq.sql` → `duckdb.sql`
   - `bq.seed` → `duckdb.seed`
   - Python connection: `duckdb-default`

## Troubleshooting

### "Permission denied" errors
- Check your service account has `roles/bigquery.admin`
- Verify datasets exist in the correct location

### "Dataset not found"
```bash
bq ls --project_id=YOUR-PROJECT-ID
```

### SQL syntax errors
- BigQuery is stricter about column references
- Use backticks for reserved keywords: `` `table` ``
- Check timestamp/date formatting

## Free Tier Limits

Bruin Cloud free tier includes:
- Limited number of pipelines
- Smaller compute instances
- Usage constraints

Check [Bruin Cloud docs](https://getbruin.com/docs) for current limits.

## Next Steps

1. Test locally with BigQuery first
2. Commit and push your changes to GitHub
3. Deploy to Bruin Cloud
4. Set up monitoring and alerts
5. Configure schedule (currently set to `monthly`)

---

**Need help?** Check the Bruin docs:
- [BigQuery Platform](https://getbruin.com/docs/bruin/platforms/bigquery)
- [Secrets Management](https://getbruin.com/docs/bruin/secrets/bruinyml)
- [Bruin Cloud](https://getbruin.com)
