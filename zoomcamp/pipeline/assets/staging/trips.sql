/* @bruin

name: staging.trips

type: bq.sql

depends:
  - ingestion.trips
  - ingestion.payment_lookup

materialization:
  type: table

columns:
  - name: trip_id
    type: string
    checks:
      - name: not_null
  - name: taxi_type
    type: string
    checks:
      - name: not_null
  - name: pickup_datetime
    type: timestamp
    checks:
      - name: not_null
  - name: dropoff_datetime
    type: timestamp
    checks:
      - name: not_null
  - name: payment_type_name
    type: string
    checks:
      - name: not_null

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
        CAST(PASSENGER_COUNT AS STRING)
      ORDER BY EXTRACTED_AT DESC
    ) as row_num
  FROM ingestion.trips
  WHERE PICKUP_DATETIME >= '{{ start_datetime }}'
    AND PICKUP_DATETIME < '{{ end_datetime }}'
    AND PICKUP_DATETIME IS NOT NULL
    AND VENDOR_ID IS NOT NULL
)
SELECT
  TO_HEX(MD5(
    CONCAT(
      COALESCE(CAST(TAXI_TYPE AS STRING), ''),
      COALESCE(CAST(PICKUP_DATETIME AS STRING), ''),
      COALESCE(CAST(DROPOFF_DATETIME AS STRING), ''),
      COALESCE(CAST(PU_LOCATION_ID AS STRING), ''),
      COALESCE(CAST(DO_LOCATION_ID AS STRING), '')
    )
  )) as trip_id,
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
