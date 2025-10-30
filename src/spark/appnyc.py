from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from pyspark.sql.types import *
import traceback

# -----------------------------
# Configuration Spark
# -----------------------------
spark = SparkSession.builder \
    .appName("KafkaToPostgres_AirportNYC") \
    .config("spark.jars.packages",
            "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
            "org.postgresql:postgresql:42.6.0") \
    .config("spark.sql.streaming.forceDeleteTempCheckpointLocation", True) \
    .getOrCreate()

spark.sparkContext.setLogLevel("WARN")

# -----------------------------
# Définition du schéma JSON des vols
# -----------------------------
flight_schema = StructType([
    StructField("flight_date", StringType()),
    StructField("flight_status", StringType()),

    StructField("departure", StructType([
        StructField("airport", StringType()),
        StructField("timezone", StringType()),
        StructField("iata", StringType()),
        StructField("icao", StringType()),
        StructField("terminal", StringType()),
        StructField("gate", StringType()),
        StructField("delay", IntegerType()),
        StructField("scheduled", StringType()),
        StructField("estimated", StringType()),
        StructField("actual", StringType()),
        StructField("estimated_runway", StringType()),
        StructField("actual_runway", StringType())
    ])),

    StructField("arrival", StructType([
        StructField("airport", StringType()),
        StructField("timezone", StringType()),
        StructField("iata", StringType()),
        StructField("icao", StringType()),
        StructField("terminal", StringType()),
        StructField("gate", StringType()),
        StructField("baggage", StringType()),
        StructField("scheduled", StringType()),
        StructField("delay", IntegerType()),
        StructField("estimated", StringType()),
        StructField("actual", StringType()),
        StructField("estimated_runway", StringType()),
        StructField("actual_runway", StringType())
    ])),

    StructField("airline", StructType([
        StructField("name", StringType()),
        StructField("iata", StringType()),
        StructField("icao", StringType())
    ])),

    StructField("flight", StructType([
        StructField("number", StringType()),
        StructField("iata", StringType()),
        StructField("icao", StringType()),
        StructField("codeshared", StructType([
            StructField("airline_name", StringType()),
            StructField("airline_iata", StringType()),
            StructField("airline_icao", StringType()),
            StructField("flight_number", StringType()),
            StructField("flight_iata", StringType()),
            StructField("flight_icao", StringType())
        ]))
    ])),

    StructField("aircraft", StringType()),
    StructField("live", StringType())
])

# -----------------------------
# Connexion PostgreSQL
# -----------------------------
db_properties = {
    "user": "user",
    "password": "password",
    "driver": "org.postgresql.Driver",
    "stringtype": "unspecified"
}

jdbc_url = "jdbc:postgresql://db_postgres:5432/airdata"

# -----------------------------
# Test connexion PostgreSQL
# -----------------------------
print("🔍 Testing PostgreSQL connection...")
try:
    spark.read \
        .format("jdbc") \
        .option("url", jdbc_url) \
        .option("dbtable", "information_schema.tables") \
        .option("user", db_properties["user"]) \
        .option("password", db_properties["password"]) \
        .option("driver", db_properties["driver"]) \
        .load().show(3)
    print("✅ PostgreSQL connection successful!")
except Exception as e:
    print("❌ PostgreSQL connection failed:")
    traceback.print_exc()
    raise SystemExit("⛔️ Stopping Spark job")

# -----------------------------
# Lecture depuis Kafka
# -----------------------------
df_kafka = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "kafka:9092") \
    .option("subscribe", "airportnyc") \
    .option("startingOffsets", "latest") \
    .load()

# Parse la valeur JSON du message Kafka
df_json = df_kafka.select(from_json(col("value").cast("string"), 
    StructType([StructField("data", ArrayType(flight_schema))])
).alias("payload"))

# On extrait chaque vol de la liste `data`
df_flights = df_json.select(explode(col("payload.data")).alias("f"))

# Aplatir les sous-objets en colonnes
df_flat = df_flights.select(
    col("f.flight_date").cast("date").alias("flight_date"),
    col("f.flight_status"),

    col("f.departure.airport").alias("departure_airport"),
    col("f.departure.timezone").alias("departure_timezone"),
    col("f.departure.iata").alias("departure_iata"),
    col("f.departure.icao").alias("departure_icao"),
    col("f.departure.terminal").alias("departure_terminal"),
    col("f.departure.gate").alias("departure_gate"),
    col("f.departure.delay").alias("departure_delay"),
    to_timestamp(col("f.departure.scheduled")).alias("departure_scheduled"),
    to_timestamp(col("f.departure.estimated")).alias("departure_estimated"),
    to_timestamp(col("f.departure.actual")).alias("departure_actual"),
    to_timestamp(col("f.departure.estimated_runway")).alias("departure_estimated_runway"),
    to_timestamp(col("f.departure.actual_runway")).alias("departure_actual_runway"),

    col("f.arrival.airport").alias("arrival_airport"),
    col("f.arrival.timezone").alias("arrival_timezone"),
    col("f.arrival.iata").alias("arrival_iata"),
    col("f.arrival.icao").alias("arrival_icao"),
    col("f.arrival.terminal").alias("arrival_terminal"),
    col("f.arrival.gate").alias("arrival_gate"),
    col("f.arrival.baggage").alias("arrival_baggage"),
    to_timestamp(col("f.arrival.scheduled")).alias("arrival_scheduled"),
    col("f.arrival.delay").alias("arrival_delay"),
    to_timestamp(col("f.arrival.estimated")).alias("arrival_estimated"),
    to_timestamp(col("f.arrival.actual")).alias("arrival_actual"),
    to_timestamp(col("f.arrival.estimated_runway")).alias("arrival_estimated_runway"),
    to_timestamp(col("f.arrival.actual_runway")).alias("arrival_actual_runway"),

    col("f.airline.name").alias("airline_name"),
    col("f.airline.iata").alias("airline_iata"),
    col("f.airline.icao").alias("airline_icao"),

    col("f.flight.number").alias("flight_number"),
    col("f.flight.iata").alias("flight_iata"),
    col("f.flight.icao").alias("flight_icao"),

    col("f.flight.codeshared.airline_name").alias("codeshared_airline_name"),
    col("f.flight.codeshared.airline_iata").alias("codeshared_airline_iata"),
    col("f.flight.codeshared.airline_icao").alias("codeshared_airline_icao"),
    col("f.flight.codeshared.flight_number").alias("codeshared_flight_number"),
    col("f.flight.codeshared.flight_iata").alias("codeshared_flight_iata"),
    col("f.flight.codeshared.flight_icao").alias("codeshared_flight_icao"),

    to_json(col("f")).alias("raw_json")
)

# -----------------------------
# Fonction d’écriture batch dans PostgreSQL
# -----------------------------
def write_to_postgres(batch_df, batch_id):
    print(f"✈️ Processing batch {batch_id}")
    try:
        batch_df.write.jdbc(
            url=jdbc_url,
            table="flights",
            mode="append",
            properties=db_properties
        )
        print(f"✅ Batch {batch_id} inserted into flights")
    except Exception as e:
        print(f"❌ Error writing batch {batch_id}: {e}")
        traceback.print_exc()

# -----------------------------
# Lancement du streaming
# -----------------------------
query = df_flat.writeStream \
    .outputMode("append") \
    .foreachBatch(write_to_postgres) \
    .option("checkpointLocation", "/tmp/checkpoints_airportnyc") \
    .start()

query.awaitTermination()