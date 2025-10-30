from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from pyspark.sql.types import *
import traceback

# -----------------------------
# Configuration Spark
# -----------------------------
spark = SparkSession.builder \
    .appName("KafkaToPostgresFixed") \
    .config("spark.jars.packages",
            "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
            "org.postgresql:postgresql:42.6.0") \
    .config("spark.sql.streaming.forceDeleteTempCheckpointLocation", True) \
    .getOrCreate()

spark.sparkContext.setLogLevel("WARN")

# -----------------------------
# CORRECTION : Schéma JSON adapté à la structure réelle des données
# -----------------------------
full_schema = StructType([
    StructField("_id", StringType()),
    StructField("name", StringType()),
    StructField("icaoCode", StringType()),
    StructField("iataCode", StringType()),
    StructField("type", IntegerType()),
    StructField("country", StringType()),
    StructField("geometry", StructType([
        StructField("type", StringType()),
        StructField("coordinates", ArrayType(DoubleType()))
    ])),
    StructField("elevation", StructType([
        StructField("value", DoubleType()),
        StructField("unit", IntegerType()),
        StructField("referenceDatum", IntegerType())
    ])),
    StructField("ppr", BooleanType()),
    StructField("private", BooleanType()),
    StructField("skydiveActivity", BooleanType()),
    StructField("winchOnly", BooleanType()),
    StructField("frequencies", ArrayType(StructType([
        StructField("_id", StringType()),
        StructField("name", StringType()),
        StructField("value", StringType()),
        StructField("unit", IntegerType()),
        StructField("type", IntegerType()),
        StructField("primary", BooleanType()),
        StructField("publicUse", BooleanType())
    ]))),
    StructField("runways", ArrayType(StructType([
        StructField("_id", StringType()),
        StructField("designator", StringType()),
        StructField("pilotCtrlLighting", BooleanType()),
        StructField("takeOffOnly", BooleanType()),
        StructField("landingOnly", BooleanType()),
        StructField("surface", StructType([
            StructField("composition", ArrayType(IntegerType())),
            StructField("mainComposite", IntegerType()),
            StructField("condition", IntegerType())
        ])),
        StructField("dimension", StructType([
            StructField("length", StructType([StructField("value", DoubleType())])),
            StructField("width", StructType([StructField("value", DoubleType())]))
        ]))
    ])))
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
# Test de connexion (inchangé)
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
        .load().show(5)
    print("✅ PostgreSQL connection successful!")
except Exception as e:
    print("❌ PostgreSQL connection failed:")
    traceback.print_exc()
    raise SystemExit("⛔️ Stopping: cannot connect to PostgreSQL")

# -----------------------------
# Lecture depuis Kafka (inchangé)
# -----------------------------
df_kafka = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "kafka:9092") \
    .option("subscribe", "airportInt") \
    .option("startingOffsets", "latest") \
    .load()

df_parsed = df_kafka.select(from_json(col("value").cast("string"), full_schema).alias("data")).select("data.*")

# -----------------------------
# Fonction d’écriture batch
# -----------------------------
def process_and_write_batch(batch_df, batch_id):
    print(f"\n--- Processing batch ID: {batch_id} ---")
    
    # Cache le DataFrame pour éviter les recalculs
    batch_df.cache()
    
    try:
        # -----------------------------
        # CORRECTION : Extraction des aéroports avec les bons chemins de colonnes
        # -----------------------------
        df_airports = batch_df.select(
            col("_id"),
            col("name"),
            col("iataCode").alias("iata_code"),
            col("icaoCode").alias("icao_code"),
            col("type"),
            lit(None).cast("array<int>").alias("traffic_type"),
            lit(None).cast("numeric").alias("magnetic_declination"),
            col("country"),
            to_json(col("geometry")).alias("geometry"),
            col("elevation.value").alias("elevation_value"), # CORRECTION: Accès à la valeur imbriquée
            col("elevation.unit").alias("elevation_unit"), # CORRECTION: Accès à l'unité imbriquée
            col("elevation.referenceDatum").alias("elevation_reference_datum"), # CORRECTION
            col("ppr"),
            col("private"),
            col("skydiveActivity").alias("skydive_activity"),
            col("winchOnly").alias("winch_only"),
            lit(0).alias("geoid_height"),
            lit(0).alias("hae"),
            current_timestamp().alias("created_at"),
            current_timestamp().alias("updated_at"),
            lit("spark").alias("created_by"),
            lit("spark").alias("updated_by"),
            lit(1).alias("v_version")
        ).dropDuplicates(["_id"])

        # -----------------------------
        # CORRECTION : Extraction des fréquences avec les bons noms de champs
        # -----------------------------
        df_frequencies = batch_df \
            .filter(col("frequencies").isNotNull()) \
            .select(explode(col("frequencies")).alias("freq"), col("_id").alias("airport_id")) \
            .select(
                col("freq._id").alias("_id"), # CORRECTION: Utiliser l'_id existant si possible
                col("airport_id"),
                col("freq.value").alias("value"), # CORRECTION: Le champ s'appelle 'value'
                col("freq.unit").alias("unit"),
                col("freq.type").alias("type"),
                col("freq.name").alias("name"), # CORRECTION: Le champ s'appelle 'name'
                col("freq.primary").alias("primary_freq"),
                col("freq.publicUse").alias("public_use")
            )

        # -----------------------------
        # CORRECTION : Extraction des pistes avec la logique adaptée
        # -----------------------------
        df_runways = batch_df \
            .filter(col("runways").isNotNull()) \
            .select(explode(col("runways")).alias("rw"), col("_id").alias("airport_id")) \
            .select(
                col("rw._id").alias("_id"), # CORRECTION
                col("airport_id"),
                col("rw.designator"), # CORRECTION: Le champ existe déjà
                lit(None).cast("numeric").alias("true_heading"),
                lit(False).alias("aligned_true_north"),
                lit(None).cast("integer").alias("operations"),
                lit(False).alias("main_runway"),
                lit(None).cast("integer").alias("turn_direction"),
                col("rw.takeOffOnly").alias("take_off_only"), # CORRECTION
                col("rw.landingOnly").alias("landing_only"), # CORRECTION
                col("rw.pilotCtrlLighting").alias("pilot_ctrl_lighting"), # CORRECTION
                lit(None).cast("array<integer>").alias("visual_approach_aids"),
                to_json(col("rw.surface")).alias("surface"), # CORRECTION: La structure source est déjà un objet
                to_json(struct(
                    col("rw.dimension.length.value").alias("length"), # CORRECTION: Accès imbriqué
                    col("rw.dimension.width.value").alias("width")) # CORRECTION: Accès imbriqué
                ).alias("dimension"),
                lit(None).cast("string").alias("declared_distance")
            )

        # -----------------------------
        # Comptage et écriture (logique inchangée)
        # -----------------------------
        airports_count = df_airports.count()
        freq_count = df_frequencies.count()
        rw_count = df_runways.count()

        print(f"🛬 {airports_count} airports | 📡 {freq_count} freqs | 🛣️ {rw_count} runways in batch")

        if airports_count > 0:
            print("✏️ Writing airportsInt...")
            df_airports.write.jdbc(url=jdbc_url, table="airportsInt", mode="append", properties=db_properties)

        if freq_count > 0:
            print("✏️ Writing frequencies...")
            df_frequencies.write.jdbc(url=jdbc_url, table="frequencies", mode="append", properties=db_properties)

        if rw_count > 0:
            print("✏️ Writing runways...")
            df_runways.write.jdbc(url=jdbc_url, table="runways", mode="append", properties=db_properties)

        print(f"✅ Batch {batch_id} written successfully!\n")
        
        # Libère le cache
        batch_df.unpersist()

    except Exception as e:
        print(f"❌ Error writing batch {batch_id}: {e}")
        traceback.print_exc()

# -----------------------------
# Lancement du streaming (inchangé)
# -----------------------------
query = df_parsed.writeStream \
    .outputMode("append") \
    .foreachBatch(process_and_write_batch) \
    .option("checkpointLocation", "/tmp/checkpoints_airports") \
    .start()

query.awaitTermination()