"""@bruin

name: ingestion.trips

type: python

image: python:3.11

connection: duckdb-default

materialization:
  type: table
  strategy: append

columns:
  - name: VendorID
    type: integer
    description: Taxi vendor identifier
  - name: pickup_datetime
    type: timestamp
    description: Trip pickup datetime
  - name: dropoff_datetime
    type: timestamp
    description: Trip dropoff datetime
  - name: passenger_count
    type: double
    description: Number of passengers
  - name: trip_distance
    type: double
    description: Trip distance in miles
  - name: RatecodeID
    type: double
    description: Rate code identifier
  - name: store_and_fwd_flag
    type: string
    description: Store and forward flag
  - name: PULocationID
    type: integer
    description: Pickup location ID
  - name: DOLocationID
    type: integer
    description: Dropoff location ID
  - name: payment_type
    type: integer
    description: Payment type identifier
  - name: fare_amount
    type: double
    description: Fare amount
  - name: extra
    type: double
    description: Extra charges
  - name: mta_tax
    type: double
    description: MTA tax
  - name: tip_amount
    type: double
    description: Tip amount
  - name: tolls_amount
    type: double
    description: Tolls amount
  - name: improvement_surcharge
    type: double
    description: Improvement surcharge
  - name: total_amount
    type: double
    description: Total amount
  - name: congestion_surcharge
    type: double
    description: Congestion surcharge
  - name: airport_fee
    type: double
    description: Airport fee
  - name: taxi_type
    type: string
    description: Type of taxi (yellow or green)
  - name: extracted_at
    type: timestamp
    description: Timestamp when data was extracted

@bruin"""

import os
import json
import pandas as pd
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dateutil.parser import parse
import ssl
import urllib.request

ssl._create_default_https_context = ssl._create_unverified_context


def materialize():
    start_date = parse(os.environ['BRUIN_START_DATE'])
    end_date = parse(os.environ['BRUIN_END_DATE'])
    
    bruin_vars = json.loads(os.environ.get('BRUIN_VARS', '{}'))
    taxi_types = bruin_vars.get('taxi_types', ['yellow', 'green'])
    
    base_url = "https://d37ci6vzurychx.cloudfront.net/trip-data/"
    
    current_date = start_date
    all_dataframes = []
    extracted_at = datetime.utcnow()
    
    while current_date < end_date:
        year_month = current_date.strftime('%Y-%m')
        
        for taxi_type in taxi_types:
            filename = f"{taxi_type}_tripdata_{year_month}.parquet"
            url = base_url + filename
            
            try:
                print(f"Fetching {url}")
                response = requests.get(url, timeout=60)
                response.raise_for_status()
                
                df = pd.read_parquet(url)
                
                df['taxi_type'] = taxi_type
                df['extracted_at'] = extracted_at
                
                pickup_col = 'tpep_pickup_datetime' if taxi_type == 'yellow' else 'lpep_pickup_datetime'
                dropoff_col = 'tpep_dropoff_datetime' if taxi_type == 'yellow' else 'lpep_dropoff_datetime'
                
                df = df.rename(columns={
                    pickup_col: 'pickup_datetime',
                    dropoff_col: 'dropoff_datetime'
                })
                
                all_dataframes.append(df)
                print(f"Successfully fetched {len(df)} rows from {filename}")
                
            except Exception as e:
                print(f"Warning: Could not fetch {url}: {e}")
        
        current_date += relativedelta(months=1)
    
    if not all_dataframes:
        raise ValueError("No data was fetched for the given date range and taxi types")
    
    final_df = pd.concat(all_dataframes, ignore_index=True)
    
    return final_df


