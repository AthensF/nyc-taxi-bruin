/* @bruin

name: reports.trips_report

type: duckdb.sql

depends:
  - staging.trips

materialization:
  type: table
  strategy: time_interval
  incremental_key: pickup_date
  time_granularity: date

columns:
  - name: pickup_date
    type: date
    description: Date of trip pickup
    primary_key: true
  - name: taxi_type
    type: string
    description: Type of taxi (yellow or green)
    primary_key: true
  - name: payment_type_name
    type: string
    description: Payment type name
    primary_key: true
  - name: trip_count
    type: bigint
    description: Number of trips
    checks:
      - name: non_negative
  - name: total_passengers
    type: bigint
    description: Total number of passengers
    checks:
      - name: non_negative
  - name: total_distance
    type: double
    description: Total trip distance in miles
    checks:
      - name: non_negative
  - name: total_fare_amount
    type: double
    description: Total fare amount
    checks:
      - name: non_negative
  - name: total_amount
    type: double
    description: Total amount collected
    checks:
      - name: non_negative
  - name: avg_trip_distance
    type: double
    description: Average trip distance
    checks:
      - name: non_negative
  - name: avg_fare_amount
    type: double
    description: Average fare amount
    checks:
      - name: non_negative

@bruin */

SELECT
  CAST(pickup_datetime AS DATE) as pickup_date,
  taxi_type,
  payment_type_name,
  COUNT(*) as trip_count,
  SUM(CAST(passenger_count AS BIGINT)) as total_passengers,
  SUM(trip_distance) as total_distance,
  SUM(fare_amount) as total_fare_amount,
  SUM(total_amount) as total_amount,
  AVG(trip_distance) as avg_trip_distance,
  AVG(fare_amount) as avg_fare_amount
FROM staging.trips
WHERE pickup_datetime >= '{{ start_datetime }}'
  AND pickup_datetime < '{{ end_datetime }}'
GROUP BY
  CAST(pickup_datetime AS DATE),
  taxi_type,
  payment_type_name
