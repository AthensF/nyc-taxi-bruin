/* @bruin

name: staging.trips

type: duckdb.sql

depends:
  - ingestion.trips
  - ingestion.payment_lookup

materialization:
  type: table
  strategy: time_interval
  incremental_key: pickup_datetime
  time_granularity: timestamp

columns:
  - name: trip_id
    type: string
    description: Unique trip identifier
    primary_key: true
    nullable: false
    checks:
      - name: not_null
  - name: taxi_type
    type: string
    description: Type of taxi (yellow or green)
    checks:
      - name: not_null
  - name: pickup_datetime
    type: timestamp
    description: Trip pickup datetime
    checks:
      - name: not_null
  - name: dropoff_datetime
    type: timestamp
    description: Trip dropoff datetime
  - name: passenger_count
    type: double
    description: Number of passengers
    checks:
      - name: non_negative
  - name: trip_distance
    type: double
    description: Trip distance in miles
    checks:
      - name: non_negative
  - name: payment_type
    type: integer
    description: Payment type identifier
  - name: payment_type_name
    type: string
    description: Payment type name from lookup
  - name: fare_amount
    type: double
    description: Fare amount
    checks:
      - name: non_negative
  - name: total_amount
    type: double
    description: Total amount
    checks:
      - name: non_negative

@bruin */

WITH deduplicated AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY 
        TAXI_TYPE,
        PICKUP_DATETIME,
        DROPOFF_DATETIME,
        PU_LOCATION_ID,
        DO_LOCATION_ID,
        PASSENGER_COUNT
      ORDER BY EXTRACTED_AT DESC
    ) as row_num
  FROM ingestion.trips
  WHERE PICKUP_DATETIME >= '{{ start_datetime }}'
    AND PICKUP_DATETIME < '{{ end_datetime }}'
    AND PICKUP_DATETIME IS NOT NULL
    AND VENDOR_ID IS NOT NULL
)
SELECT
  MD5(
    CONCAT(
      COALESCE(CAST(TAXI_TYPE AS VARCHAR), ''),
      COALESCE(CAST(PICKUP_DATETIME AS VARCHAR), ''),
      COALESCE(CAST(DROPOFF_DATETIME AS VARCHAR), ''),
      COALESCE(CAST(PU_LOCATION_ID AS VARCHAR), ''),
      COALESCE(CAST(DO_LOCATION_ID AS VARCHAR), '')
    )
  ) as trip_id,
  TAXI_TYPE as taxi_type,
  PICKUP_DATETIME as pickup_datetime,
  DROPOFF_DATETIME as dropoff_datetime,
  PASSENGER_COUNT as passenger_count,
  TRIP_DISTANCE as trip_distance,
  PAYMENT_TYPE as payment_type,
  COALESCE(p.payment_type_name, 'unknown') as payment_type_name,
  FARE_AMOUNT as fare_amount,
  TOTAL_AMOUNT as total_amount
FROM deduplicated d
LEFT JOIN ingestion.payment_lookup p
  ON d.PAYMENT_TYPE = p.payment_type_id
WHERE row_num = 1
  AND FARE_AMOUNT >= 0
  AND TOTAL_AMOUNT >= 0
  AND TRIP_DISTANCE >= 0
