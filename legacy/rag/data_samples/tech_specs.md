# Especificaciones de Infraestructura y Red

El servicio Qdrant corre en un contenedor Docker con persistencia de almacenamiento montada en `/qdrant/storage`.
Para entornos de producción, se recomienda configurar HNSW m=16, ef_construct=100.
El servicio Ollama se ejecuta de forma nativa en localhost:11434 y procesa modelos como `gpt-oss` y `qwen3.5`.
La tasa de peticiones concurrentes soportada para embeddings es de hasta 200 req/s cuando se utiliza el pipeline por lotes.
El cortafuegos corporativo permite tráfico en el rango de puertos 6333-6335 únicamente dentro de la subred privada 10.0.0.0/24.
