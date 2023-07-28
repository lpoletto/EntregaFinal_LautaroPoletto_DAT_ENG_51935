# Este script está pensado para correr en Spark y hacer el proceso de ETL de la tabla popular_songs

from datetime import datetime
from os import environ as env
from pyspark.sql import Row, Window
from pyspark.sql.functions import concat, col, lit, monotonically_increasing_id, row_number, to_timestamp
from pyspark.sql.types import StructType,StructField, StringType, IntegerType

from commons import ETL_Spark
from helpers import get_token, search_for_artist, get_artist_top_tracks, repeat_list_items


COUNTRY_CODES=["AR", "BR", "US", "MX"]
ARTISTS_LIST=["Phill Collins", "Soda Stereo", "Arctic Monkeys", "Tears For Fears", "Wos"]

class ETL_Spotify(ETL_Spark):
    def __init__(self, job_name=None):
        super().__init__(job_name)
        self.process_date = datetime.now().strftime("%Y-%m-%d")

    def run(self):
        process_date = datetime.now().strftime("%Y-%m-%d")
        self.execute(process_date)

    def extract(self):
        """
        Extrae datos de la API
        """
        print(">>> [E] Extrayendo datos de la Web API Spotify...")


        # Obtenemos el token de Spotify
        token = get_token()

        # Buscamos el id de cada artista
        id_artists = []
        songs = []
        
        for artist in ARTISTS_LIST:
            result = search_for_artist(token, artist_name=artist)
            id_artists.append(result["id"])

        # Obtenemos las canciones de cada artista por pais  
        for country in COUNTRY_CODES:
            for id in id_artists:
                # Guardamos la data   
                songs.append(get_artist_top_tracks(token, id_artist=id, country=country))

        return songs
    

    def transform(self, raw_data):
        """
        Transforma los datos
        """
        print(">>> [T] Transformando datos...")
        
        # Aplanamos la lista de listas de diccionarios
        flattened_data = [item for sublist in raw_data for item in sublist]

        # Itera sobre cada diccionario en la lista y obtén los valores deseados
        rows = []
        for record in flattened_data:
            row = Row(
                id=record['id'],
                name=record['name'],
                artist=record['artists'][0]['name'],
                album=record['album']['name'],
                popularity=record['popularity'],
                duration_ms=record['duration_ms'],
                external_url=record['external_urls']['spotify']
            )
            rows.append(row)

        my_schema = StructType([ StructField("id_song", StringType(), False)\
                      ,StructField("song_name", StringType(), False)\
                      ,StructField("artist", StringType(), False)\
                      ,StructField("album", StringType(), False)\
                      ,StructField("popularity", IntegerType(), False)\
                      ,StructField("duration_ms", IntegerType(), False)\
                      ,StructField("song_link", StringType(), False)\
                    ])
        
        # Crea el DataFrame con los nombres de las columnas especificadas
        df = self.spark.createDataFrame(rows, schema=my_schema)

        df.printSchema()
        df.show()

        # Por cada pais de la lista, se debe consultar el artista
        number_of_repeats = len(ARTISTS_LIST) * 10
        country_code = repeat_list_items(COUNTRY_CODES, number_of_repeats)
        
        # Crea un DataFrame aux con los country_code
        df_aux = self.spark.createDataFrame([(c,) for c in country_code], ['country_code'])

        df = df.withColumn("row_idx", row_number().over(Window.orderBy(monotonically_increasing_id())))
        df_aux = df_aux.withColumn("row_idx", row_number().over(Window.orderBy(monotonically_increasing_id())))

        df_final = df.join(df_aux, df.row_idx == df_aux.row_idx).drop("row_idx")

        # Crear una columna personalizada para la clave primaria
        df_final = df_final.withColumn("alternate_key", concat(col("id_song"), col("country_code")))
        
        df_final.printSchema()
        df_final.show()
        
        # Comprobar si el DataFrame esta vacio
        if df_final.rdd.isEmpty():
            raise Exception("The DataFrame is empty")

        # Comprobar si la Primary Key es unica
        if not df_final.select("alternate_key").distinct().count() == df_final.count():
            raise Exception("A value from id is not unique")
        
        df_final.printSchema()
        df_final.show()

        return df_final
    

    def load(self, df_final):
        """
        Carga los datos transformados en Redshift
        """
        print(">>> [L] Cargando datos en Redshift...")

        today = datetime.now()
        # add timestamp_ column
        df_to_write = df_final.withColumn("timestamp_", to_timestamp(lit(today), "yyyy-MM-dd HH:mm:ss"))


        df_to_write.write \
            .format("jdbc") \
            .option("url", env['REDSHIFT_URL']) \
            .option("dbtable", f"{env['REDSHIFT_SCHEMA']}.popular_songs") \
            .option("user", env['REDSHIFT_USER']) \
            .option("password", env['REDSHIFT_PASSWORD']) \
            .option("driver", "org.postgresql.Driver") \
            .mode("append") \
            .save()
        
        print(">>> [L] Datos cargados exitosamente")


if __name__ == "__main__":
    print("Corriendo script")
    etl = ETL_Spotify()
    etl.run()