/* @bruin

name: reports.trips_report

type: bq.sql

depends:
  - staging.trips

materialization:
  type: table

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
